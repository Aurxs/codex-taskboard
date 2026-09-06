/** Isolated UI regression check and reproducible English README captures.
 * Build first; every request is mocked, so no real Taskboard is contacted.
 * PLAYWRIGHT_MODULE may point to an existing Playwright installation.
 */
import { readFile, mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: 'en-US' });
  page.setDefaultTimeout(6000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const projects = [{ id: 'demo-project', codexProjectId: 'codex-demo', name: 'codex-taskboard', key: 'BOARD', workspacePath: '/tmp/taskboard-demo', version: 1, automationEnabled: false, reviewRequired: true, quotaAutoResumeEnabled: true }];
  const tasks = [
    ['Plan the next release', 'todo', 'high', 'Define the scope, acceptance criteria, and verification steps for the next release.'],
    ['Add keyboard shortcuts', 'todo', 'medium', 'Make the most common board actions accessible from the keyboard.'],
    ['Improve execution history', 'in_progress', 'high', 'Show command output and file changes as Codex works through each task.'],
    ['Refine the task detail layout', 'in_review', 'medium', 'Keep the description, dependencies, and execution history easy to scan.'],
    ['Polish the project switcher', 'done', 'low', 'Search local projects and keep the selected project when the sidebar refreshes.'],
  ].map(([title, status, priority, description], i) => ({ id: `demo-${i}`, projectId: 'demo-project', identifier: `BOARD-${i + 1}`, title, description, status, priority, version: 1, blockedBy: [], blocks: [], ready: true, threadId: i >= 2 ? `thread-${i}` : null, runState: status === 'in_progress' ? 'running' : null, updatedAt: new Date(Date.now() - 120000).toISOString(), createdAt: new Date(Date.now() - 3600000).toISOString() }));
  tasks[1].blockedBy = [{ id: tasks[0].id, identifier: tasks[0].identifier, title: tasks[0].title, status: 'todo' }];
  tasks[1].ready = false;
  tasks[3].activity = [
    { kind: 'userMessage', message: 'Keep follow-up messages visible while reading long execution histories.', createdAt: tasks[0].createdAt },
    { kind: 'commandExecution', message: 'npm run build', data: { command: 'npm run build', output: 'Typecheck and production build passed.' }, status: 'completed', createdAt: tasks[0].updatedAt },
    { kind: 'agentMessage', message: 'The task detail layout is ready for review. The description and activity scroll independently, with the follow-up composer pinned below.', createdAt: tasks[0].updatedAt },
  ];
  let submitted;
  let failCreate = false;
  let syncs = 0;
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1:47825') return route.abort();
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': 'content-type, accept-language', 'access-control-allow-methods': 'GET, POST, OPTIONS' };
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 200, headers });
    if (url.pathname === '/api/codex/projects/sync') { syncs++; return route.fulfill({ headers, json: { projects } }); }
    if (url.pathname === '/api/events') return route.fulfill({ headers, contentType: 'text/event-stream', body: ': ready\n\n' });
    if (url.pathname === '/api/codex/models') return route.fulfill({ headers, json: { models: [{ model: 'gpt-6-astra', displayName: 'gpt-6-astra', defaultReasoningEffort: 'high', supportedReasoningEfforts: [{ reasoningEffort: 'low' }, { reasoningEffort: 'high' }] }] } });
    if (url.pathname.endsWith('/tasks') && route.request().method() === 'POST') {
      submitted = route.request().postDataJSON();
      assert.equal(route.request().headers()['accept-language'], 'en');
      return failCreate
        ? route.fulfill({ headers, status: 422, json: { error: { code: 'VALIDATION_ERROR', message: '请先暂停任务，再修改模型或推理强度' } } })
        : route.fulfill({ headers, status: 201, json: { ...tasks[0], ...submitted, id: 'created-demo' } });
    }
    if (url.pathname.startsWith('/api/tasks/')) return route.fulfill({ headers, json: tasks.find(task => url.pathname.endsWith(task.id)) });
    if (url.pathname.endsWith('/tasks')) return route.fulfill({ headers, json: { tasks } });
    if (url.pathname.startsWith('/assets/')) return route.fulfill({ headers, path: `dist/web${url.pathname}` });
    if (url.pathname !== '/') throw new Error(`Unexpected request: ${url}`);
    return route.fulfill({ contentType: 'text/html', body: '<body style="margin:0"><iframe sandbox="allow-scripts allow-forms" style="width:1440px;height:900px;border:0"></iframe></body>' });
  });
  await page.goto('http://127.0.0.1:47825/');
  await page.evaluate(projects => {
    window.context = { language: 'en-US', projects: projects.map(p => ({ id: p.codexProjectId, name: p.name, workspacePath: p.workspacePath, projectKind: 'local' })), projectId: projects[0].codexProjectId };
    addEventListener('message', e => {
      if (e.data.type === 'taskboard:frame-awaiting-challenge') e.source.postMessage({ type: 'taskboard:frame-challenge', payload: { challenge: 'demo' } }, '*');
    });
    setInterval(() => document.querySelector('iframe').contentWindow.postMessage({ type: 'taskboard:host-context', payload: window.context }, '*'), 100);
  }, projects);
  const cdp = await page.context().newCDPSession(page);
  const tree = await cdp.send('Page.getFrameTree');
  const html = (await readFile('dist/web/index.html', 'utf8')).replace('<head>', '<head><base href="http://127.0.0.1:47825/?host=codex"><script>globalThis.__CODEX_TASKBOARD_FRAME_CAPABILITY__="demo"</script>');
  await cdp.send('Page.setDocumentContent', { frameId: tree.frameTree.childFrames[0].frame.id, html });
  const ui = page.frameLocator('iframe');
  const setLanguage = async language => {
    await page.evaluate(language => { window.context.language = language; }, language);
    await ui.getByRole('button', { name: ['zh-CN', 'zh-Hans', 'zh_SG'].includes(language) ? '新建任务' : 'New task', exact: true }).waitFor();
  };
  await ui.getByRole('button', { name: 'New task', exact: true }).waitFor();
  await ui.locator('.task-card').first().waitFor();
  const output = 'output/playwright/i18n';
  await mkdir(output, { recursive: true });
  const capture = async name => {
    assert.doesNotMatch(await ui.locator('body').innerText(), /\p{Script=Han}/u, `${name} has untranslated UI text`);
    await page.locator('iframe').screenshot({ path: `${output}/${name}.png` });
  };
  await capture('project-ui-en');
  await ui.getByRole('button', { name: 'Automation', exact: true }).click();
  await ui.getByRole('switch', { name: 'Human review', exact: true }).waitFor();
  await page.keyboard.press('Escape');
  await ui.getByRole('button', { name: 'New task', exact: true }).click();
  await ui.getByLabel('Task title', { exact: true }).fill('Document the release workflow');
  await ui.getByLabel('Task description', { exact: true }).fill('Describe how to build, verify, and package the next release.\n\nInclude a short checklist and expected outputs for each step.');
  // Live switching must update module-level labels and preserve the editor.
  for (const language of ['zh-CN', 'en-GB', 'zh-Hans', 'zh-TW', 'zh-Hant', 'ja-JP', 'fr-FR', '', 'zh_SG', 'en-US']) {
    await setLanguage(language);
    const chinese = ['zh-CN', 'zh-Hans', 'zh_SG'].includes(language);
    assert.equal(await ui.locator('html').getAttribute('lang'), chinese ? 'zh-CN' : 'en');
    assert.equal(await ui.getByLabel(chinese ? '任务标题' : 'Task title', { exact: true }).inputValue(), 'Document the release workflow');
    assert.equal(await ui.getByRole('button', { name: chinese ? '优先级' : 'Priority', exact: true }).innerText(), chinese ? '无优先级' : 'No priority');
  }
  assert.equal(syncs, 1, 'language changes must not resync projects');
  await ui.getByRole('button', { name: 'Priority', exact: true }).click();
  await ui.getByRole('option', { name: 'High', exact: true }).click();
  await ui.getByRole('button', { name: 'Model', exact: true }).click();
  await ui.getByRole('option', { name: 'gpt-6-astra', exact: true }).click();
  await ui.getByRole('button', { name: 'Reasoning effort', exact: true }).click();
  await ui.getByRole('option', { name: 'High · high', exact: true }).click();
  await capture('task-controls-en');
  await ui.getByRole('button', { name: 'Blocked by', exact: true }).click();
  await ui.getByRole('dialog', { name: 'Choose prerequisites', exact: true }).waitFor();
  await capture('dependency-open-en');
  await ui.getByRole('checkbox').first().check();
  await page.keyboard.press('Escape');
  failCreate = true;
  await ui.getByRole('button', { name: 'Create task', exact: true }).click();
  await ui.getByRole('alert').filter({ hasText: 'Pause the task before changing its model or reasoning effort' }).waitFor();
  failCreate = false;
  await ui.getByRole('button', { name: 'Create task', exact: true }).click();
  await ui.getByRole('dialog', { name: 'New task', exact: true }).waitFor({ state: 'hidden' });
  assert.equal(submitted.title, 'Document the release workflow');
  assert.equal(submitted.priority, 'high');
  assert.equal(submitted.model, 'gpt-6-astra');
  assert.equal(submitted.reasoningEffort, 'high');
  assert.deepEqual(submitted.blockedByIds, ['demo-0']);
  await ui.getByRole('button', { name: 'Open BOARD-4: Refine the task detail layout', exact: true }).click();
  await ui.getByRole('button', { name: 'Accept and complete', exact: true }).waitFor();
  await ui.getByLabel('Follow up with Codex').fill('The layout looks good. Please also check keyboard navigation.');
  // Wait for transient creation notice to clear before the documentation capture.
  await ui.locator('.notice').waitFor({ state: 'hidden' });
  await capture('task-detail-en');
  assert.match(await ui.locator('.activity-entry time').first().innerText(), /min ago/);
  await setLanguage('zh-CN');
  await ui.getByText('接受并完成', { exact: true }).waitFor();
  assert.match(await ui.locator('.activity-entry time').first().innerText(), /分钟前/);
  assert.equal(await ui.getByLabel('跟进 Codex 会话').inputValue(), 'The layout looks good. Please also check keyboard navigation.');
  // Exercise the real injector's authenticated language request without
  // opening a panel or connecting to a desktop app.
  const host = await browser.newPage();
  await host.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<html lang="en"><body>Isolated Codex host</body></html>' }));
  await host.goto('http://127.0.0.1:47825/');
  await host.evaluate(() => {
    window.reportedLanguages = [];
    window.__CODEX_TASKBOARD_HOST_CAPABILITY__ = 'language-demo';
    addEventListener('message', event => {
      const message = event.data;
      if (message.type !== '__codexTaskboardHostRequestV1') return;
      if (message.payload.action === 'language') window.reportedLanguages.push(message.payload.language);
      postMessage({ type: '__codexTaskboardHostResponseV1', capability: 'language-demo', response: { id: message.payload.id, ok: true } }, location.origin);
    });
  });
  await host.addScriptTag({ content: await readFile('injector/inject.js', 'utf8') });
  for (const language of ['zh-CN', 'en-US', 'zh-Hant', 'ja-JP']) {
    await host.evaluate(language => { document.documentElement.lang = language; }, language);
    await host.waitForFunction(language => window.reportedLanguages.at(-1) === language, language);
  }
  assert.deepEqual(errors, []);
  console.log(`Passed: locale routing, live switching, preserved drafts, localized errors/time, unchanged API values; four English screenshots in ${output}`);
} finally { await browser.close(); }
