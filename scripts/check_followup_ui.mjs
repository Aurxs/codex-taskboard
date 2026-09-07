// Isolated fixture only: no live Taskboard, Codex renderer, or network service.
import { readFile, mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.setDefaultTimeout(6000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const project = { id: 'project', codexProjectId: 'native-project', key: 'TEST', name: 'Fixture', workspacePath: '/tmp/fixture', version: 1 };
  const task = { id: 'task', projectId: project.id, identifier: 'TEST-1', title: '会话跟进验证', description: '长描述内容\n'.repeat(100), status: 'in_progress', priority: 'none', runState: 'running', threadId: 'thread-fixture', version: 1, blockedBy: [], blocks: [], ready: false,
    activity: Array.from({ length: 12 }, (_, i) => ({ id: `item-${i}`, kind: i === 0 ? 'userMessage' : 'agentMessage', message: `消息 ${i}：${'任务进展。'.repeat(30)}`, status: 'completed' })) };
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
      task.version++;
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
    return route.fulfill({ contentType: 'text/html', body: '<iframe sandbox="allow-scripts allow-forms" style="width:1200px;height:760px;border:0"></iframe>' });
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
  const composer = ui.getByRole('textbox', { name: '跟进 Codex 会话' });
  await composer.waitFor();
  const box = await composer.boundingBox();
  const form = ui.locator('.task-followup');
  assert.ok((await form.boundingBox()).height <= 96, 'composer uses a compact height');
  assert.notEqual(await form.evaluate(el => getComputedStyle(el).boxShadow), 'none', 'composer has a subtle surrounding shadow');
  assert.equal(await form.evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(255, 255, 255)', 'composer uses the white raised surface');
  assert.equal(await composer.evaluate(el => getComputedStyle(el).resize), 'none', 'no resize handle');
  assert.equal(await ui.getByRole('button', { name: '发送跟进消息' }).isDisabled(), true);
  await composer.fill('第一行');
  await composer.press('Shift+Enter');
  await composer.press('a');
  assert.equal(await composer.inputValue(), '第一行\na');
  await composer.fill('多行内容\n'.repeat(20));
  assert.ok(await composer.evaluate(el => el.scrollHeight > el.clientHeight), 'long messages scroll inside composer');
  assert.ok((await form.boundingBox()).height <= 96, 'long drafts keep composer compact');
  await composer.fill('');
  await ui.locator('.issue-detail-main-scroll').evaluate(el => { el.scrollTop = el.scrollHeight; });
  assert.deepEqual(await composer.boundingBox(), box, 'composer stays fixed while history scrolls');
  assert.ok(box.y + box.height < 770, 'composer must be inside the visible panel');
  await ui.locator('.issue-description-read').click();
  const description = ui.locator('.issue-description-input');
  assert.ok(await description.evaluate(el => { el.scrollTop = el.scrollHeight; return el.scrollHeight > el.clientHeight && el.scrollTop > 0; }), 'long description editor scrolls');
  await description.blur();
  await composer.fill('保留这条补充');
  fail = true;
  await ui.getByRole('button', { name: '发送跟进消息' }).click();
  await ui.locator('.notice-error').waitFor();
  assert.equal(await composer.inputValue(), '保留这条补充');
  await page.frames()[1].waitForFunction(() => !document.querySelector('.task-followup-send').disabled);
  fail = false;
  await composer.press('Enter');
  await page.waitForFunction(() => document.querySelector('iframe') != null);
  await ui.getByRole('button', { name: '发送跟进消息' }).waitFor();
  await page.frames()[1].waitForFunction(() => document.querySelector('.task-followup textarea').value === '');
  assert.equal(submitted.action, 'follow_up');
  assert.equal(submitted.feedback, '保留这条补充');
  await form.locator('input[type=file]').setInputFiles({ name: 'context.txt', mimeType: 'text/plain', buffer: Buffer.from('context') });
  await form.getByText('context.txt', { exact: true }).waitFor();
  await composer.evaluate(el => {
    const clipboardData = new DataTransfer();
    clipboardData.items.add(new File(['notes'], 'notes.md', { type: 'text/markdown' }));
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData, bubbles: true, cancelable: true }));
  });
  await form.getByText('notes.md', { exact: true }).waitFor();
  await form.getByRole('button', { name: '移除 notes.md' }).click();
  await form.getByText('notes.md', { exact: true }).waitFor({ state: 'hidden' });
  fail = true;
  await form.getByRole('button', { name: '发送跟进消息' }).click();
  await ui.locator('.notice-error').waitFor();
  assert.ok(await form.getByText('context.txt', { exact: true }).isVisible(), 'failed sends retain attachments');
  await page.frames()[1].waitForFunction(() => !document.querySelector('.task-followup-send').disabled);
  fail = false;
  await composer.press('Enter');
  await form.getByText('context.txt', { exact: true }).waitFor({ state: 'hidden' });
  assert.equal(submitted.feedback, '请查看附件。', 'attachment-only follow-up is sent');
  await ui.getByRole('button', { name: '在 Codex 中打开' }).click();
  await page.waitForFunction(() => window.messages.some(m => m.type === 'taskboard:open-thread' && m.payload.threadId === 'thread-fixture'));
  for (const status of ['in_review', 'done']) {
    task.status = status; task.runState = null; task.version++;
    await page.frames()[1].evaluate(() => window.fixtureStream.onmessage({ data: JSON.stringify({ type: 'task.updated' }) }));
    await ui.locator('.issue-property-list').getByText(status === 'done' ? '已完成' : '等你确认', { exact: true }).waitFor();
    assert.ok(await composer.isVisible());
  }
  await mkdir('output/playwright', { recursive: true });
  await page.screenshot({ path: 'output/playwright/followup-desktop.png' });
  await page.evaluate(() => { document.querySelector('iframe').style.width = '600px'; });
  const narrow = await composer.boundingBox();
  assert.ok(narrow.x >= 0 && narrow.x + narrow.width <= 610 && narrow.y + narrow.height < 770);
  await page.screenshot({ path: 'output/playwright/followup-narrow.png' });
  assert.deepEqual(errors, []);
  console.log('PASS: pinned composer, long editor scroll, failed-send preservation, follow-up payload, native link, review/done states, narrow layout');
} finally { await browser.close(); }
