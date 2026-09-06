// Build first. All requests and host actions are mocked; no real Taskboard is used.
import { readFile, mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1800, height: 900 }, locale: 'zh-CN' });
  page.setDefaultTimeout(6000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const project = { id: 'demo', codexProjectId: 'codex-demo', name: '示例项目', workspacePath: '/tmp/demo', version: 1 };
  const statuses = ['todo', 'in_progress', 'in_review', 'done', 'canceled'];
  const tasks = statuses.map((status, i) => ({ id: status, projectId: 'demo', identifier: `DEMO-${i}`, title: `示例任务 ${i}`, description: '', status, priority: 'none', version: 1, blockedBy: [], blocks: [], ready: true }));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1:47825') return route.abort();
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': 'content-type, accept-language', 'access-control-allow-methods': 'GET, POST, OPTIONS' };
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 200, headers });
    if (url.pathname === '/api/codex/projects/sync') return route.fulfill({ headers, json: { projects: [project] } });
    if (url.pathname === '/api/codex/models') return route.fulfill({ headers, json: { models: [] } });
    if (url.pathname === '/api/events') return route.fulfill({ headers, contentType: 'text/event-stream', body: ': ready\n\n' });
    if (url.pathname.endsWith('/tasks')) return route.fulfill({ headers, json: { tasks } });
    if (url.pathname.startsWith('/api/tasks/')) return route.fulfill({ headers, json: tasks.find(task => url.pathname.endsWith(task.id)) });
    if (url.pathname.startsWith('/assets/')) return route.fulfill({ headers, path: `dist/web${url.pathname}` });
    assert.equal(url.pathname, '/', `Unexpected request: ${url}`);
    return route.fulfill({ contentType: 'text/html', body: '<main style="height:900px"><div data-app-shell-main-content-layout style="height:900px"><div class="app-shell-main-content-frame" style="height:900px">Native</div></div></main>' });
  });
  const source = await readFile('injector/inject.js', 'utf8');
  const built = await readFile('dist/web/index.html', 'utf8');
  const cdp = await page.context().newCDPSession(page);
  await page.exposeBinding('loadHarnessFrame', async (_, request) => {
    const tree = await cdp.send('Page.getFrameTree');
    const find = node => node.frame.name === request.frameName ? node.frame : (node.childFrames || []).map(find).find(Boolean);
    const frame = find(tree.frameTree);
    const html = built.replace('<head>', `<head><base href="http://127.0.0.1:47825/?host=codex"><script>globalThis.__CODEX_TASKBOARD_FRAME_CAPABILITY__=${JSON.stringify(request.frameCapability)}</script>`);
    await cdp.send('Page.setDocumentContent', { frameId: frame.id, html });
    return { id: request.id, ok: true };
  });
  const mount = async () => {
    await page.evaluate(() => {
      window.__CODEX_TASKBOARD_HOST_CAPABILITY__ = 'harness';
      addEventListener('message', async event => {
        if (event.data.type !== '__codexTaskboardHostRequestV1') return;
        const request = event.data.payload;
        const response = request.action === 'language' ? { id: request.id, ok: true } : await window.loadHarnessFrame(request);
        postMessage({ type: '__codexTaskboardHostResponseV1', capability: 'harness', response }, location.origin);
      });
    });
    await page.addScriptTag({ content: source });
    await page.evaluate(() => window.__codexTaskboardInjection__.open());
    await page.frameLocator('iframe').locator('.task-card').first().waitFor();
  };
  await page.goto('http://127.0.0.1:47825/');
  await mount();
  const ui = page.frameLocator('iframe');
  const columnOrder = () => ui.locator('.board-column').evaluateAll(els => els.map(el => el.className.match(/status-(\w+)/)[1]));
  const toggle = async (label, checked) => {
    await ui.getByRole('button', { name: '显示选项卡', exact: true }).click();
    await ui.getByRole('checkbox', { name: label, exact: true }).setChecked(checked);
    await page.keyboard.press('Escape');
  };
  assert.deepEqual(await columnOrder(), statuses.slice(0, 4));
  assert.equal(await ui.getByRole('button', { name: '已取消', exact: true }).count(), 0);
  await ui.getByRole('button', { name: '显示选项卡', exact: true }).click();
  for (const label of ['等待认领', '处理中']) {
    const checkbox = ui.getByRole('checkbox', { name: new RegExp(label) });
    assert.ok(await checkbox.isChecked());
    assert.ok(await checkbox.isDisabled());
  }
  await page.keyboard.press('Escape');
  await toggle('已取消', true);
  assert.deepEqual(await columnOrder(), statuses);
  const canceled = ui.locator('.status-canceled.board-column');
  assert.equal(await canceled.locator('.add-task-button').count(), 0);
  assert.equal(await canceled.locator('.task-card').getAttribute('draggable'), 'false');
  await canceled.getByRole('button', { name: '打开 DEMO-4: 示例任务 4' }).click();
  await ui.locator('.issue-detail').waitFor();
  await ui.getByRole('button', { name: '返回议题看板', exact: true }).first().click();
  await ui.locator('input[type="search"]').fill('不存在');
  await canceled.getByText('没有已取消任务', { exact: true }).waitFor();
  await ui.getByRole('button', { name: '清除搜索' }).click();
  await toggle('等你确认', false);
  await toggle('已完成', false);
  await toggle('已取消', false);
  assert.deepEqual(await columnOrder(), statuses.slice(0, 2));
  await page.waitForFunction(() => localStorage.getItem('codex-taskboard.board-columns') === '["todo","in_progress"]');
  await page.reload();
  await mount();
  assert.deepEqual(await columnOrder(), statuses.slice(0, 2), 'preferences survive host reload');
  await toggle('已取消', true);
  await toggle('已完成', true);
  await toggle('等你确认', true);
  assert.deepEqual(await columnOrder(), statuses, 'reopening columns preserves fixed order');
  await mkdir('output/playwright', { recursive: true });
  await page.screenshot({ path: 'output/playwright/board-columns.png' });
  await page.setViewportSize({ width: 400, height: 800 });
  await ui.getByRole('button', { name: '显示选项卡', exact: true }).click();
  const settings = ui.getByRole('dialog', { name: '显示选项卡' });
  const bounds = await settings.evaluate(el => { const r = el.getBoundingClientRect(); return { left: r.left, right: r.right, width: innerWidth }; });
  assert.ok(bounds.left >= 0 && bounds.right <= bounds.width, JSON.stringify(bounds));
  await settings.getByRole('checkbox', { name: '已完成', exact: true }).uncheck();
  await page.screenshot({ path: 'output/playwright/board-columns-narrow.png' });
  await ui.locator('.view-tab').click();
  await settings.waitFor({ state: 'hidden' });
  await canceled.scrollIntoViewIfNeeded();
  assert.ok(await canceled.isVisible());
  assert.deepEqual(errors, []);
  console.log('passed: defaults, required columns, 2–5 columns, fixed order, canceled detail/search, persisted preferences, narrow layout');
} finally {
  await browser.close();
}
