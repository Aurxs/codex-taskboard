import assert from 'node:assert/strict';
import { readFile, mkdir } from 'node:fs/promises';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.setDefaultTimeout(6000);
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  const project = { id: 'db-0', codexProjectId: 'codex-0', name: 'Parallel example', key: 'TEST', workspacePath: '/tmp/parallel-example', version: 1, automationEnabled: false, reviewRequired: true };
  const tasks = [];
  let submitted;
  let rejectCreate = true;
  const baseTask = { projectId: project.id, description: '', priority: 'none', status: 'todo', version: 1, blockedBy: [], blocks: [], ready: true, attachments: [], threadId: null, runState: null, model: null, reasoningEffort: null, executionMode: 'local', branch: null, kind: 'task', schedulingMode: 'exclusive', parentId: null, writeScopes: [], targetBranch: 'main', mergeState: 'none', parallel: {}, createdAt: '2026-09-06T00:00:00Z', updatedAt: '2026-09-06T00:00:00Z' };
  function detail(task) {
    const children = tasks.filter(child => child.parentId === task.id);
    return { ...task, children, operations: [], progress: { total: children.length, integrated: 0, running: 0, attention: 0 } };
  }
  await page.route('http://127.0.0.1:47825/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': 'content-type', 'access-control-allow-methods': 'GET, POST, PATCH, OPTIONS' };
    const fulfill = json => route.fulfill({ headers, json });
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 200, headers });
    if (url.pathname === '/api/codex/projects/sync') return fulfill({ projects: [project] });
    if (url.pathname === '/api/events') return route.fulfill({ headers, contentType: 'text/event-stream', body: ': ready\n\n' });
    if (url.pathname === '/api/codex/models') return fulfill({ models: [{ model: 'example-model', displayName: 'Example model', defaultReasoningEffort: 'low', supportedReasoningEfforts: [{ reasoningEffort: 'low' }, { reasoningEffort: 'high' }] }] });
    if (url.pathname.endsWith('/git-context')) return fulfill({ isGit: true, currentBranch: 'main', branches: ['main', 'develop'] });
    if (url.pathname === '/api/projects/db-0/tasks' && request.method() === 'POST') {
      submitted = request.postDataJSON();
      if (rejectCreate) { rejectCreate = false; return route.fulfill({ headers, status: 422, json: { error: { message: "修改范围需要确认", code: "VALIDATION_ERROR" } } }); }
      const task = { ...baseTask, ...submitted, id: `task-${tasks.length}`, identifier: `TEST-${tasks.length + 1}`, ...(submitted.kind === 'parallel_group' ? { groupPhase: 'preparing', ready: false, parallel: { managed: true } } : {}) };
      tasks.push(task); return fulfill(detail(task));
    }
    if (url.pathname === '/api/projects/db-0/tasks') return fulfill({ tasks: tasks.filter(task => !task.parentId).map(detail) });
    const childRoute = url.pathname.match(/^\/api\/tasks\/(.+)\/children$/);
    if (childRoute) {
      const parent = tasks.find(task => task.id === childRoute[1]);
      const input = request.postDataJSON();
      assert.equal(input.version, parent.version);
      const child = { ...baseTask, ...input, id: `task-${tasks.length}`, identifier: `TEST-${tasks.length + 1}`, parentId: parent.id, schedulingMode: 'parallel', executionMode: 'worktree' };
      tasks.push(child); parent.version++;
      return fulfill(detail(child));
    }
    const task = tasks.find(task => url.pathname === `/api/tasks/${task.id}`);
    if (task) return fulfill(detail(task));
    if (url.pathname.startsWith('/api/')) return fulfill({ tasks: [] });
    if (url.pathname.startsWith('/assets/')) return route.fulfill({ headers, path: `dist/web${url.pathname}` });
    return route.fulfill({ contentType: 'text/html', body: '<iframe sandbox="allow-scripts allow-forms" style="width:1200px;height:800px;border:0"></iframe>' });
  });
  await page.goto('http://127.0.0.1:47825/');
  await page.evaluate(project => {
    window.context = { language: 'zh-CN', projects: [{ id: project.codexProjectId, name: project.name, workspacePath: project.workspacePath, projectKind: 'local' }], projectId: project.codexProjectId };
    addEventListener('message', e => { if (e.data.type === 'taskboard:frame-awaiting-challenge') e.source.postMessage({ type: 'taskboard:frame-challenge', payload: { challenge: 'test' } }, '*'); });
    setInterval(() => document.querySelector('iframe').contentWindow.postMessage({ type: 'taskboard:host-context', payload: window.context }, '*'), 100);
  }, project);
  const cdp = await page.context().newCDPSession(page);
  const tree = await cdp.send('Page.getFrameTree');
  const html = (await readFile('dist/web/index.html', 'utf8')).replace('<head>', '<head><base href="http://127.0.0.1:47825/?host=codex&embedded=1"><script>globalThis.__CODEX_TASKBOARD_FRAME_CAPABILITY__="test"</script>');
  await cdp.send('Page.setDocumentContent', { frameId: tree.frameTree.childFrames[0].frame.id, html });
  const ui = page.frameLocator('iframe');
  await ui.getByRole('button', { name: '新建任务', exact: true }).click();
  const dialog = ui.getByRole('dialog', { name: '新建任务', exact: true });
  await dialog.getByLabel('任务标题', { exact: true }).fill('并行界面验证');
  await dialog.getByRole('button', { name: '执行方式', exact: true }).click();
  await ui.getByRole('option', { name: '允许并行', exact: true }).click();
  const initialHeight = await dialog.evaluate(el => el.getBoundingClientRect().height);
  const bounds = async locator => {
    const box = await locator.evaluate(el => { const r = el.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: innerWidth, height: innerHeight }; });
    assert.ok(box.left >= 0 && box.right <= box.width + 1 && box.top >= 0 && box.bottom <= box.height + 1, JSON.stringify(box));
  };
  await mkdir('output/playwright', { recursive: true });
  await dialog.screenshot({ path: 'output/playwright/parallel-create.png' });
  await dialog.getByRole('button', { name: '模型', exact: true }).click();
  await ui.getByRole('option', { name: 'Example model', exact: true }).click();
  await dialog.getByRole('button', { name: '推理强度', exact: true }).click();
  await ui.getByRole('option', { name: '高 · high', exact: true }).click();
  await dialog.getByRole('button', { name: '更多', exact: true }).click();
  const more = ui.getByRole('dialog', { name: '更多设置', exact: true });
  await more.getByRole('button', { name: /^合入目标：/ }).click();
  await more.getByLabel('合入目标', { exact: true }).fill('develop');
  await page.keyboard.press('Escape');
  assert.ok(await more.getByRole('button', { name: '合入目标：develop', exact: true }).isVisible());
  await more.getByRole('button', { name: /^合入目标：/ }).click();
  await more.getByRole('button', { name: '恢复默认', exact: true }).click();
  assert.ok(await more.getByRole('button', { name: '合入目标：main', exact: true }).isVisible());
  await more.getByRole('button', { name: /^修改范围：/ }).click();
  await more.getByLabel('修改范围', { exact: true }).fill('web/\nsrc/');
  await more.getByRole('button', { name: '应用', exact: true }).click();
  await more.getByLabel('搜索参数', { exact: true }).fill('阻塞于');
  assert.equal(await more.locator('.settings-row').count(), 1);
  await more.getByLabel('搜索参数', { exact: true }).fill('');
  await more.getByRole('button', { name: /^合入目标：/ }).click();
  await page.keyboard.press('Escape');
  assert.ok(await more.isVisible());
  assert.ok(await more.getByLabel('搜索参数').isVisible());
  await page.screenshot({ path: 'output/playwright/parallel-more.png' });
  assert.equal(await dialog.evaluate(el => el.getBoundingClientRect().height), initialHeight);
  await page.keyboard.press('Escape');
  assert.ok(await dialog.isVisible());
  for (const width of [600, 400]) {
    await page.evaluate(width => { document.querySelector('iframe').style.width = `${width}px`; }, width);
    await bounds(dialog);
    await dialog.getByRole('button', { name: '更多', exact: true }).click();
    await bounds(ui.locator('.taskboard-popover'));
    await page.keyboard.press('Escape');
  }
  await page.evaluate(() => { document.querySelector('iframe').style.width = '1200px'; });
  await dialog.getByRole('button', { name: '创建任务', exact: true }).click();
  await more.waitFor();
  assert.equal(await dialog.getByLabel('任务标题', { exact: true }).inputValue(), '并行界面验证');
  assert.equal(await more.getByLabel('修改范围', { exact: true }).inputValue(), 'web/\nsrc/');
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await dialog.getByRole('button', { name: '创建任务', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(submitted.schedulingMode, 'parallel');
  assert.equal(submitted.executionMode, 'worktree');
  assert.equal(submitted.model, 'example-model');
  assert.equal(submitted.reasoningEffort, 'high');
  assert.deepEqual(submitted.writeScopes, ['web/', 'src/']);
  await ui.getByRole('button', { name: '新建任务', exact: true }).click();
  await dialog.getByLabel('任务标题', { exact: true }).fill('父任务组');
  await dialog.getByRole('button', { name: '任务形态', exact: true }).click();
  await ui.getByRole('option', { name: '并行任务组', exact: true }).click();
  await dialog.getByRole('button', { name: '创建任务', exact: true }).click();
  await ui.getByRole('button', { name: '添加子任务', exact: true }).click();
  await dialog.getByLabel('任务标题', { exact: true }).fill('第一个子任务');
  await dialog.getByRole('button', { name: '创建任务', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  await ui.locator('.group-child-list').getByText('第一个子任务', { exact: true }).waitFor();
  assert.equal(tasks.filter(task => task.parentId).length, 1);
  assert.equal(tasks.find(task => task.kind === 'parallel_group').groupPhase, 'preparing');
  await page.screenshot({ path: 'output/playwright/parallel-group.png' });
  await page.evaluate(() => { window.context.language = 'en'; });
  await ui.getByRole('button', { name: 'Add subtask', exact: true }).click();
  const childDialog = ui.getByRole('dialog', { name: 'New task', exact: true });
  assert.ok(await childDialog.getByRole('button', { name: 'Model', exact: true }).isVisible());
  assert.ok(await childDialog.getByRole('button', { name: 'Reasoning effort', exact: true }).isDisabled());
  await childDialog.getByRole('button', { name: 'More', exact: true }).click();
  await ui.getByRole('dialog', { name: 'More settings', exact: true }).waitFor();
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await ui.locator('.merge-history summary').click();
  assert.ok(await ui.locator('.merge-history').getByText('main', { exact: true }).isVisible());
  assert.equal(errors.length, 0, errors.join('\n'));
  console.log('PASS: More settings, draft persistence, keyboard navigation, narrow layouts, group creation and bilingual UI');
} finally { await browser.close(); }
