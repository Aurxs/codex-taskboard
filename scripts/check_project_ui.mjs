import { readFile, mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') console.error(message.text()); });
  const names = ['StarryAI', 'codex-taskboard', 'taskboard-exp', 'Onelap2AppleHealth', 'pixel3mf'];
  const projects = names.map((name, i) => ({ id: `db-${i}`, codexProjectId: `codex-${i}`, name, key: `P${i}`, workspacePath: `/tmp/${name}`, version: 1 }));
  let syncs = 0;
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
    if (url.pathname.startsWith('/api/')) return route.fulfill({ headers, json: { tasks: [] } });
    if (url.pathname.startsWith('/assets/')) return route.fulfill({ headers, path: `dist/web${url.pathname}` });
    return route.fulfill({ contentType: 'text/html', body: '<iframe sandbox="allow-scripts" style="width:1200px;height:760px;border:0"></iframe>' });
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
  await page.waitForTimeout(2000);
  console.log('syncs', syncs, 'errors', errors, 'body', await ui.locator('body').innerText());
  await ui.getByRole('button', { name: '切换项目', exact: true }).filter({ hasText: names[0] }).waitFor({ timeout: 5000 });
  assert.equal(syncs, 1, 'identical host envelopes must not cancel/restart sync');
  await ui.getByRole('button', { name: '切换项目', exact: true }).click();
  assert.equal(await ui.getByRole('menuitemradio').count(), 5);
  await ui.getByRole('menuitemradio', { name: names[1], exact: true }).click();
  await page.evaluate(() => { window.context = { projects: [] }; });
  await page.waitForTimeout(1500);
  assert.match(await ui.getByRole('button', { name: '切换项目', exact: true }).innerText(), /codex-taskboard/);
  await ui.getByRole('button', { name: '切换项目', exact: true }).click();
  assert.equal(await ui.getByRole('menuitemradio').count(), 5, 'empty transient context keeps persisted local projects');
  await ui.getByRole('menuitemradio', { name: names[1], exact: true }).click();
  await page.evaluate(() => { window.context = { projects: [{ id: 'codex-0', projectKind: 'remote' }] }; });
  await page.waitForTimeout(1500);
  await ui.getByRole('button', { name: '切换项目', exact: true }).click();
  assert.equal(await ui.getByRole('menuitemradio').count(), 4, 'explicit remote project is excluded');
  assert.deepEqual(errors, []);
  await mkdir('output/playwright', { recursive: true });
  await page.screenshot({ path: 'output/playwright/project-ui.png' });
  console.log('passed: real React bundle in opaque iframe, slow sync, repeated context, five projects, switching, empty context fallback, remote exclusion');
} finally { await browser.close(); }
