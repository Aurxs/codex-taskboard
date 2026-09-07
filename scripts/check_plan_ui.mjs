// Isolated fixture only: no live Taskboard, Codex renderer, or network service.
import { readFile, mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1040 } });
  page.setDefaultTimeout(6000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const project = { id: 'project', codexProjectId: 'native-project', key: 'TEST', name: 'Fixture', workspacePath: '/tmp/fixture', version: 1 };
  const task = { id: 'task', projectId: project.id, identifier: 'TEST-1', title: '计划模式样式验证', description: '检查项目并澄清需求，生成可编辑的实施计划。', kind: 'task', status: 'todo', priority: 'draft', runState: null, threadId: null, version: 1, blockedBy: [], blocks: [], ready: false, plan: { hold: false }, activity: [] };
  let fail = false;
  let submitted;
  await page.route('http://127.0.0.1:47825/**', async route => {
    const url = new URL(route.request().url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': 'content-type', 'access-control-allow-methods': 'GET, POST, PATCH, OPTIONS' };
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 200, headers });
    if (url.pathname === '/api/codex/projects/sync') return route.fulfill({ headers, json: { projects: [project] } });
    if (url.pathname === '/api/codex/models') return route.fulfill({ headers, json: { models: [] } });
    if (url.pathname === '/api/tasks/task/actions') {
      submitted = route.request().postDataJSON();
      if (fail) return route.fulfill({ headers, status: 409, json: { error: { message: '模拟发送失败' } } });
      if (submitted.action === 'plan_start') task.plan = { hold: true, operationId: 'plan', state: 'agent_running', threadId: 'planning-thread' };
      if (submitted.action === 'plan_save') { task.plan.text = submitted.feedback; if (!task.plan.hold) task.plan.acceptedText = submitted.feedback; }
      task.version++;
      return route.fulfill({ headers, json: task });
    }
    if (url.pathname.includes('/interactions/')) {
      submitted = route.request().postDataJSON();
      task.interactions = []; task.version++;
      return route.fulfill({ headers, json: task });
    }
    if (url.pathname === '/api/tasks/task') {
      if (route.request().method() === 'PATCH') {
        const { attachments = [], removeAttachmentIds = [], ...changes } = route.request().postDataJSON();
        task.attachments = [...(task.attachments ?? []).filter(file => !removeAttachmentIds.includes(file.id)), ...attachments.map(file => ({ id: `file-${task.version}-${file.name}`, name: file.name, size: Buffer.from(file.content, 'base64').length }))];
        Object.assign(task, changes, { version: task.version + 1 });
      }
      return route.fulfill({ headers, json: task });
    }
    if (url.pathname.startsWith('/api/')) return route.fulfill({ headers, json: { tasks: [task] } });
    if (url.pathname.startsWith('/assets/')) return route.fulfill({ headers, path: `dist/web${url.pathname}` });
    return route.fulfill({ contentType: 'text/html', body: '<iframe sandbox="allow-scripts allow-forms" style="width:1200px;height:960px;border:0"></iframe>' });
  });
  await page.goto('http://127.0.0.1:47825/');
  await page.evaluate(project => {
    window.messages = [];
    addEventListener('message', e => {
      window.messages.push(e.data);
      if (e.data.type === 'taskboard:frame-awaiting-challenge') e.source.postMessage({ type: 'taskboard:frame-challenge', payload: { challenge: 'fixture' } }, '*');
    });
    setInterval(() => document.querySelector('iframe').contentWindow.postMessage({ type: 'taskboard:host-context', payload: { language: 'zh-CN', projectId: project.codexProjectId, projects: [{ id: project.codexProjectId, name: project.name, workspacePath: project.workspacePath, projectKind: 'local' }] } }, '*'), 100);
  }, project);
  const cdp = await page.context().newCDPSession(page);
  const tree = await cdp.send('Page.getFrameTree');
  const html = (await readFile('dist/web/index.html', 'utf8')).replace('<head>', '<head><base href="http://127.0.0.1:47825/?host=codex&embedded=1"><script>globalThis.__CODEX_TASKBOARD_FRAME_CAPABILITY__="fixture";window.EventSource=class{constructor(){window.fixtureStream=this}close(){}}</script>');
  await cdp.send('Page.setDocumentContent', { frameId: tree.frameTree.childFrames[0].frame.id, html });
  const ui = page.frameLocator('iframe');
  await ui.locator('.task-card').first().click();
  await mkdir('output/playwright', { recursive: true });
  const capture = async name => {
    await ui.locator('.notice-stack').getByText('模拟发送失败', { exact: true }).waitFor({ state: 'hidden' }).catch(() => {});
    await ui.locator('.notice-stack .notice').last().waitFor({ state: 'hidden' });
    if (await ui.locator('.issue-detail-main-scroll').count()) await ui.locator('.issue-detail-main-scroll').evaluate(el => { el.scrollTop = 0; });
    await page.locator('iframe').screenshot({ path: `output/playwright/plan-${name}.png` });
  };
  const refresh = async () => {
    task.version++;
    await page.frames()[1].evaluate(() => window.fixtureStream.onmessage({ data: JSON.stringify({ type: 'task.updated' }) }));
  };
  const start = ui.getByRole('button', { name: '开始计划', exact: true });
  assert.ok(await start.isEnabled(), 'draft tasks can plan');
  assert.equal(await start.evaluate(el => el.parentElement.classList.contains('property-row')), true);
  assert.equal(await ui.locator('.task-plan-document').count(), 0);
  await capture('start');
  await start.click();
  const composer = ui.getByRole('textbox', { name: '补充计划要求', exact: true });
  await composer.waitFor();
  await composer.fill('保留输入草稿');
  await ui.getByRole('button', { name: '在 Codex 中打开', exact: true }).click();
  await page.waitForFunction(() => window.messages.some(m => m.type === 'taskboard:open-thread' && m.payload.threadId === 'planning-thread'));
  task.activity = [{ id: 'progress', kind: 'agentMessage', message: '正在检查项目并整理计划步骤', status: 'running' }];
  await refresh();
  await ui.locator('.activity-stream').getByText('正在检查项目并整理计划步骤', { exact: true }).waitFor();
  await capture('running');
  task.interactions = [{ id: 'question', taskId: task.id, version: 1, kind: 'user_input', status: 'pending', payload: { threadId: 'planning-thread' }, questions: [
    { id: 'scope', question: '需要哪种实现范围？', options: [{ label: '当前页面', description: '仅调整任务详情' }, { label: '全部页面' }] },
    { id: 'height', question: '计划卡片高度？', options: [{ label: '360px' }] }
  ] }];
  await refresh();
  const question = ui.locator('.plan-interaction-composer');
  await question.waitFor();
  assert.equal(await composer.count(), 0, 'questions replace the composer');
  assert.ok(await ui.locator('.activity-stream').getByText('正在检查项目并整理计划步骤', { exact: true }).isVisible());
  assert.ok(await question.getByRole('button', { name: /当前页面/ }).evaluate(el => el.classList.contains('is-active')));
  assert.ok(await question.getByText('1 of 2', { exact: true }).isVisible());
  await capture('question');
  await question.getByRole('button', { name: /当前页面/ }).click();
  await question.getByText('2 of 2', { exact: true }).waitFor();
  await question.getByRole('textbox', { name: '计划卡片高度？', exact: true }).fill('360px，支持滚动');
  await question.getByRole('button', { name: '发送', exact: true }).click();
  await composer.waitFor();
  assert.equal(await composer.inputValue(), '保留输入草稿');
  assert.deepEqual(submitted.response.answers, { scope: { answers: ['当前页面'] }, height: { answers: ['360px，支持滚动'] } });
  fail = true;
  await composer.press('Enter');
  await ui.locator('.notice-error').waitFor();
  assert.equal(await composer.inputValue(), '保留输入草稿');
  await page.frames()[1].waitForFunction(() => !document.querySelector('.task-followup-send').disabled);
  fail = false;
  await composer.press('Enter');
  await page.frames()[1].waitForFunction(() => document.querySelector('.task-followup textarea').value === '');
  assert.equal(submitted.action, 'plan_continue');
  task.plan.state = 'confirmed'; task.plan.hold = false; task.plan.text = '目标：让任务计划与现有详情页交互保持一致。\n\n' + '1. 入口与状态\n将开始计划放在属性按钮行，草稿也可以开启。\n\n2. 规划过程\n持续展示执行记录，底部允许补充要求，遇到问题切换为问答卡。\n\n3. 最终计划\n保存完整内容，允许直接编辑，长计划在卡片内滚动。\n\n4. 验证\n检查草稿规划、问答切换、保存失败重试和原生会话跳转。\n\n'.repeat(5);
  task.plan.acceptedText = task.plan.text;
  await refresh();
  const document = ui.locator('.task-plan-document');
  await document.waitFor();
  assert.ok(await ui.locator('.task-plan-content').evaluate(el => el.clientHeight <= 88 && el.scrollHeight > el.clientHeight));
  assert.ok(await document.evaluate(el => el.previousElementSibling.classList.contains('property-row')));
  await document.getByRole('button', { name: '展开计划', exact: true }).click();
  assert.ok(await ui.locator('.task-plan-content').evaluate(el => el.clientHeight > 88 && el.clientHeight <= 480));
  assert.equal(await ui.getByRole('textbox', { name: '编辑计划', exact: true }).count(), 0, 'expanding does not open the editor');
  await document.getByRole('button', { name: '收起计划', exact: true }).click();
  assert.ok(await ui.locator('.task-plan-content').evaluate(el => el.clientHeight <= 88));
  await ui.getByRole('button', { name: '编辑计划', exact: true }).click();
  const editor = ui.getByRole('textbox', { name: '编辑计划', exact: true });
  await editor.fill('手工调整后的计划\n\n' + task.plan.text);
  await capture('editing');
  fail = true;
  await ui.getByRole('button', { name: '保存计划', exact: true }).click();
  assert.ok((await editor.inputValue()).startsWith('手工调整后的计划'));
  await page.frames()[1].waitForFunction(() => !document.querySelector('.task-plan-document .primary-button').disabled);
  fail = false;
  await ui.getByRole('button', { name: '保存计划', exact: true }).click();
  await editor.waitFor({ state: 'hidden' });
  assert.equal(submitted.action, 'plan_save');
  await capture('ready');
  await page.evaluate(() => { document.querySelector('iframe').style.width = '600px'; });
  assert.ok(await document.evaluate(el => el.getBoundingClientRect().right <= innerWidth));
  await capture('narrow');
  await page.evaluate(() => { document.querySelector('iframe').style.width = '1200px'; });
  await composer.waitFor({ state: 'hidden' });
  assert.equal(await ui.getByRole('button', { name: '确认计划', exact: true }).count(), 0, 'final plan is saved automatically without a confirmation step');
  assert.ok(await ui.getByRole('button', { name: '编辑计划', exact: true }).isVisible());
  task.plan.hold = true; task.plan.state = 'agent_running';
  await refresh();
  await composer.waitFor();
  await ui.locator('.issue-detail').getByRole('button', { name: '返回议题看板', exact: true }).click();
  await ui.locator('.task-processing-row.is-running').waitFor();
  assert.ok(await ui.locator('.task-processing-row').getByText(/^正在执行计划模式/).isVisible());
  await capture('board');
  assert.deepEqual(errors, []);
  console.log('PASS: draft plan button, native link, live history, question replacement, draft preservation, edit/save and automatic final saving, bounded scrolling, narrow layout and board progress');
} finally { await browser.close(); }
