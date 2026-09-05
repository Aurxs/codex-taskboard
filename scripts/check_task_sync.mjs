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
  let failSave = false;
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
    if (url.pathname.startsWith('/api/tasks/') && route.request().method() === 'PATCH') {
      submitted = route.request().postDataJSON();
      if (failSave) return route.fulfill({ headers, status: 409, json: { error: 'Conflict' } });
      Object.assign(tasks[0], submitted, { version: tasks[0].version + 1 });
      return route.fulfill({ headers, json: tasks[0] });
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
  const html = (await readFile('dist/web/index.html', 'utf8')).replace('<head>', '<head><base href="http://127.0.0.1:47825/?host=codex&embedded=1"><script>globalThis.__CODEX_TASKBOARD_FRAME_CAPABILITY__="test"; window.EventSource = class { constructor() { window.testStream = this; } close() {} };</script>');
  await cdp.send('Page.setDocumentContent', { frameId: tree.frameTree.childFrames[0].frame.id, html });
  const ui = page.frameLocator('iframe');
  await ui.getByRole('button', { name: '新建任务', exact: true }).waitFor();
  await ui.locator('.task-card').first().click();
  const title = ui.locator('.issue-title-input');
  await title.fill('未保存的标题');
  const refresh = async () => {
    tasks[0].version++;
    tasks[0].lastMessage = `同步版本 ${tasks[0].version}`;
    await page.frames()[1].evaluate(() => window.testStream.onmessage({ data: JSON.stringify({ type: 'task.updated' }) }));
    await ui.getByText(tasks[0].lastMessage, { exact: false }).waitFor();
  };
  await refresh();
  assert.equal(await title.inputValue(), '未保存的标题');
  await title.blur();
  await page.waitForFunction(() => true);
  await ui.locator('.issue-description-read').click();
  const description = ui.locator('.issue-description-input');
  await description.fill('描述草稿不能被同步覆盖');
  await refresh();
  assert.equal(await description.inputValue(), '描述草稿不能被同步覆盖');
  failSave = true;
  await description.blur();
  await ui.locator('.notice-error').waitFor();
  assert.equal(await description.inputValue(), '描述草稿不能被同步覆盖');
  failSave = false;
  await description.focus();
  await description.blur();
  await ui.locator('.issue-description-read').waitFor();
  assert.equal(tasks[0].description, '描述草稿不能被同步覆盖');
  assert.equal(submitted.title, undefined, 'description saves must not overwrite title');
  const nav = ui.locator('.issue-detail > .issue-parent-link');
  const before = await nav.boundingBox();
  await ui.locator('.issue-detail-scroll').evaluate(el => { el.scrollTop = el.scrollHeight; });
  assert.deepEqual(await nav.boundingBox(), before);
  assert.ok(await nav.evaluate(el => parseFloat(getComputedStyle(el).paddingTop) >= 12));
  assert.equal(await ui.locator('.board-sync-indicator').count(), 0);
  await page.frames()[1].evaluate(() => window.testStream.onerror());
  await ui.getByRole('alert').filter({ hasText: '无法连接后端' }).waitFor();
  await page.frames()[1].evaluate(() => window.testStream.onopen());
  await ui.locator('.board-sync-indicator').waitFor({ state: 'hidden' });
  assert.deepEqual(errors, []);
  console.log('PASS: draft preservation, failed-save retry, independent field saves, fixed navigation, silent sync and connection recovery');
} finally {
  await browser.close();
}
