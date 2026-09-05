import { readFile, mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.setDefaultTimeout(6000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') console.error(message.text()); });
  const names = ['StarryAI', 'codex-taskboard', 'taskboard-exp', 'Onelap2AppleHealth', 'pixel3mf'];
  const projects = names.map((name, i) => ({ id: `db-${i}`, codexProjectId: `codex-${i}`, name, key: `P${i}`, workspacePath: `/tmp/${name}`, version: 1 }));
  let syncs = 0;
  let submitted = null;
  const tasks = Array.from({ length: 16 }, (_, i) => ({ id: `task-${i}`, projectId: 'db-0', identifier: `CODEX-TASKBOARD-${i + 1}`, title: `前置任务 ${i + 1}`, description: '', status: 'todo', priority: 'none', version: 1, blockedBy: [], blocks: [], ready: true }));
  tasks[0].activity = Array.from({ length: 15 }, (_, i) => ({ id: `event-${i}`, kind: i === 1 ? 'mcpToolCall' : 'agentMessage', message: i === 1 ? 'imagegen' : `过程反馈 ${i + 1}：${'这是一段应当自动换行且不挤压时间的执行进展。'.repeat(6)}`, status: 'completed', createdAt: new Date(Date.now() - 120000).toISOString(), ...(i === 1 ? { data: { tool: 'imagegen', arguments: { prompt: '一棵树' } } } : {}) }));
  tasks.push({ ...tasks[0], id: 'canceled-1', identifier: 'CANCELED-1', title: '已取消的示例任务', status: 'canceled' });
  await page.route('http://127.0.0.1:47825/**', async route => {
    const url = new URL(route.request().url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': 'content-type', 'access-control-allow-methods': 'GET, POST, OPTIONS' };
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 200, headers });
    if (url.pathname === '/api/codex/projects/sync') {
      syncs++;
      await new Promise(resolve => setTimeout(resolve, 1200));
      return route.fulfill({ headers, json: { projects } });
    }
    if (url.pathname === '/api/events') return route.fulfill({ headers, contentType: 'text/event-stream', body: ': ready\n\n' });
    if (url.pathname === '/api/codex/models') return route.fulfill({ headers, json: { models: ['gpt-6-astra', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna', 'gpt-5.5', 'gpt-5.4-mini'].map(model => ({ model, displayName: model, defaultReasoningEffort: 'low', supportedReasoningEfforts: [{ reasoningEffort: 'low' }, { reasoningEffort: 'high' }] })) } });
    if (url.pathname.endsWith('/tasks') && route.request().method() === 'POST') {
      submitted = route.request().postDataJSON();
      return route.fulfill({ headers, status: 201, json: { ...tasks[0], ...submitted, id: 'created' } });
    }
    if (url.pathname.startsWith('/api/tasks/')) return route.fulfill({ headers, json: tasks.find(task => url.pathname.endsWith(task.id)) || tasks[0] });
    if (url.pathname.startsWith('/api/')) return route.fulfill({ headers, json: { tasks: url.pathname.includes('/db-1/') ? [] : tasks } });
    if (url.pathname.startsWith('/assets/')) return route.fulfill({ headers, path: `dist/web${url.pathname}` });
    return route.fulfill({ contentType: 'text/html', body: '<iframe sandbox="allow-scripts allow-forms" style="width:1200px;height:760px;border:0"></iframe>' });
  });
  await page.goto('http://127.0.0.1:47825/');
  await page.evaluate(projects => {
    window.context = { projects: projects.map(p => ({ id: p.codexProjectId, name: p.name, workspacePath: p.workspacePath, projectKind: 'local' })), projectId: projects[0].codexProjectId };
    addEventListener('message', e => {
      if (e.data.type === 'taskboard:frame-awaiting-challenge') e.source.postMessage({ type: 'taskboard:frame-challenge', payload: { challenge: 'test' } }, '*');
    });
    setInterval(() => document.querySelector('iframe').contentWindow.postMessage({ type: 'taskboard:host-context', payload: window.context }, '*'), 100);
  }, projects);
  const cdp = await page.context().newCDPSession(page);
  const tree = await cdp.send('Page.getFrameTree');
  const html = (await readFile('dist/web/index.html', 'utf8')).replace('<head>', '<head><base href="http://127.0.0.1:47825/?host=codex&embedded=1"><script>globalThis.__CODEX_TASKBOARD_FRAME_CAPABILITY__="test"</script>');
  await cdp.send('Page.setDocumentContent', { frameId: tree.frameTree.childFrames[0].frame.id, html });
  const ui = page.frameLocator('iframe');
  await ui.getByRole('button', { name: '新建任务', exact: true }).waitFor();
  await mkdir('output/playwright', { recursive: true });
  await ui.locator('.column-header').first().screenshot({ path: 'output/playwright/column-plus.png' });
  await ui.getByRole('button', { name: '已取消', exact: true }).click();
  const canceled = ui.getByRole('complementary', { name: '已取消任务' });
  await canceled.getByRole('button', { name: '打开 CANCELED-1: 已取消的示例任务', exact: true }).waitFor();
  assert.equal(await canceled.evaluate(el => getComputedStyle(el).visibility), 'visible');
  await page.screenshot({ path: 'output/playwright/canceled-panel.png' });
  await canceled.getByRole('button', { name: '关闭已取消任务' }).click();
  await canceled.waitFor({ state: 'hidden' });
  await ui.getByRole('button', { name: '切换项目', exact: true }).click();
  await ui.getByRole('menuitemradio', { name: 'codex-taskboard', exact: true }).click();
  await ui.locator('.task-card').first().waitFor({ state: 'hidden' });
  await ui.getByRole('button', { name: '已取消', exact: true }).click();
  await canceled.getByText('没有已取消任务', { exact: true }).waitFor();
  await canceled.getByRole('button', { name: '关闭已取消任务' }).click();
  await ui.getByRole('button', { name: '切换项目', exact: true }).click();
  await ui.getByRole('menuitemradio', { name: 'StarryAI', exact: true }).click();
  await ui.locator('.task-card').first().waitFor();
  await ui.getByRole('button', { name: '新建任务', exact: true }).click();
  const dialog = ui.getByRole('dialog', { name: '新建任务', exact: true });
  const popup = ui.locator('.taskboard-popover');
  const box = () => dialog.evaluate(el => { const r = el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; });
  const checkBounds = async locator => {
    const r = await locator.evaluate(el => { const r = el.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: innerWidth, height: innerHeight }; });
    assert.ok(r.left >= 0 && r.right <= r.width + 1 && r.top >= 0 && r.bottom <= r.height + 1, JSON.stringify(r));
  };
  await dialog.getByLabel('任务标题', { exact: true }).fill('验证任务');
  const initial = await box();
  await dialog.getByRole('button', { name: '阻塞于', exact: true }).click();
  const dependencies = ui.getByRole('dialog', { name: '选择前置任务', exact: true });
  await dependencies.waitFor();
  assert.deepEqual(await box(), initial, 'dependency popover must not reflow composer');
  await checkBounds(popup);
  assert.equal(await dependencies.getByRole('checkbox').count(), 16);
  const scroll = await dependencies.locator('.dependency-picker-list').evaluate(el => ({ height: el.clientHeight, scroll: el.scrollHeight }));
  assert.ok(scroll.scroll > scroll.height && scroll.height <= 220);
  await page.screenshot({ path: 'output/playwright/dependency-open.png' });
  await dependencies.getByLabel('搜索前置任务').fill('前置任务 16');
  await dependencies.getByRole('checkbox').check();
  await page.keyboard.press('Escape');
  await popup.waitFor({ state: 'hidden' });
  assert.ok(await dialog.isVisible(), 'Escape closes only the floating panel');
  await dialog.getByRole('button', { name: '模型', exact: true }).click();
  await ui.getByRole('listbox', { name: '模型', exact: true }).waitFor();
  await page.screenshot({ path: 'output/playwright/model-popover.png' });
  assert.deepEqual(await box(), initial, 'model picker must not reflow composer');
  await ui.getByRole('option', { name: 'gpt-6-astra', exact: true }).click();
  await dialog.getByRole('button', { name: '推理强度', exact: true }).click();
  await ui.getByRole('option', { name: '高 · high', exact: true }).click();
  await dialog.getByRole('button', { name: '优先级', exact: true }).click();
  await page.keyboard.press('End');
  await page.keyboard.press('Enter');
  await popup.waitFor({ state: 'hidden' });
  for (const width of [1200, 600, 400]) {
    await page.evaluate(width => { document.querySelector('iframe').style.width = `${width}px`; }, width);
    await checkBounds(dialog);
    for (const name of ['模型', '推理强度', '阻塞于']) {
      await dialog.getByRole('button', { name, exact: true }).click();
      await popup.waitFor();
      await checkBounds(popup);
      await page.keyboard.press('Escape');
      await popup.waitFor({ state: 'hidden' });
      assert.ok(await dialog.isVisible(), `dialog stays open after ${name} Escape at ${width}`);
    }
  }
  await page.evaluate(() => { document.querySelector('iframe').style.width = '1200px'; });
  await dialog.getByRole('button', { name: '模型', exact: true }).click();
  await page.screenshot({ path: 'output/playwright/popover-outside.png' });
  await dialog.locator('.composer-description').click({ position: { x: 500, y: 40 } });
  await popup.waitFor({ state: 'hidden' });
  await page.screenshot({ path: 'output/playwright/task-controls.png' });
  await dialog.getByRole('button', { name: '创建任务', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(submitted.model, 'gpt-6-astra');
  assert.equal(submitted.reasoningEffort, 'high');
  assert.deepEqual(submitted.blockedByIds, ['task-15']);
  await ui.getByRole('button', { name: '打开 CODEX-TASKBOARD-1: 前置任务 1', exact: true }).click();
  await ui.locator('.issue-properties').waitFor();
  const alignment = await ui.locator('.issue-detail-main').evaluate(el => ['.issue-title-input', '.issue-description-read', '.activity-heading'].map(selector => el.querySelector(selector).getBoundingClientRect().left));
  assert.ok(Math.max(...alignment) - Math.min(...alignment) < 2, `detail alignment: ${alignment}`);
  assert.equal(await ui.locator('.activity-entry').count(), 15, 'history is not truncated to 12 entries');
  await ui.getByText('查看调用详情', { exact: true }).click();
  await ui.locator('.activity-content pre').waitFor();
  for (const width of [1200, 600]) {
    await page.evaluate(width => { document.querySelector('iframe').style.width = `${width}px`; }, width);
    const times = await ui.locator('.activity-entry time').evaluateAll(elements => elements.map(el => ({ height: el.getBoundingClientRect().height, nowrap: getComputedStyle(el).whiteSpace })));
    assert.ok(times.every(time => time.nowrap === 'nowrap' && time.height <= 25));
  }
  await page.evaluate(() => { document.querySelector('iframe').style.width = '1200px'; });
  await page.screenshot({ path: 'output/playwright/task-detail.png' });
  assert.deepEqual(errors, []);
  console.log('passed: floating pickers, no composer reflow, keyboard/outside dismissal, narrow bounds, canceled populated/empty/close, model/dependency submission');
} finally { await browser.close(); }
