import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

// Pass an installed Playwright module path when it is outside this project.
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const source = await readFile(process.argv[2] || 'injector/inject.js', 'utf8');
try {
  const page = await browser.newPage();
  page.on('pageerror', error => console.error('renderer:', error.message));
  await page.route('http://127.0.0.1:47825/**', async route => {
    if (route.request().url().endsWith('/slow.svg')) {
      await new Promise(resolve => setTimeout(resolve, 500));
      return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg"/>' });
    }
    return route.fulfill({ contentType: 'text/html', body: '<main style="height:700px"><div id="surface" style="height:700px"><div data-app-shell-main-content-layout style="height:700px"><div class="app-shell-main-content-frame" style="height:700px">Native</div></div></div></main>' });
  });
  await page.goto('http://127.0.0.1:47825/');
  const cdp = await page.context().newCDPSession(page);
  let loads = 0;
  await page.exposeBinding('loadHarnessFrame', async (_, request) => {
    loads++;
    const tree = await cdp.send('Page.getFrameTree');
    const find = node => node.frame.name === request.frameName ? node.frame : (node.childFrames || []).map(find).find(Boolean);
    const frame = find(tree.frameTree);
    assert.ok(frame, 'target frame exists');
    await cdp.send('Page.setDocumentContent', { frameId: frame.id, html: `<html><body><h1>Taskboard harness</h1><script>
      const capability = ${JSON.stringify(request.frameCapability)};
      window.testCapability = capability;
      addEventListener('message', e => { if(e.data.type === 'taskboard:frame-challenge') window.testChallenge = e.data.payload.challenge; });
      addEventListener('message', e => { if(e.data.type === 'taskboard:frame-challenge') parent.postMessage({type:'taskboard:ready',capability,challenge:e.data.payload.challenge},'*'); });
      parent.postMessage({type:'taskboard:frame-awaiting-challenge',capability},'*');
      </script><img src="http://127.0.0.1:47825/slow.svg"></body></html>` });
    return { id: request.id, ok: true };
  });
  await page.evaluate(() => {
    window.reportedLanguages = [];
    window.__CODEX_TASKBOARD_HOST_CAPABILITY__ = 'harness';
    addEventListener('message', async event => {
      if (event.data.type !== '__codexTaskboardHostRequestV1') return;
      const request = event.data.payload;
      let response;
      if (request.action === 'language') {
        window.reportedLanguages.push(request.language);
        response = { id: request.id, ok: true };
      } else {
        response = await window.loadHarnessFrame(request);
      }
      postMessage({ type: '__codexTaskboardHostResponseV1', capability: 'harness', response }, location.origin);
    });
  });
  await page.addScriptTag({ content: source });
  await page.evaluate(() => { window.__codexTaskboardInjection__.open(); window.__codexTaskboardInjection__.open(); });
  await page.waitForTimeout(1000);
  console.log('initial', loads, await page.evaluate(() => window.__codexTaskboardInjection__.state));
  const visible = async () => page.evaluate(() => {
    const frame = document.getElementById('codex-taskboard-frame');
    return { ready: window.__codexTaskboardInjection__.ready, visible: !!frame && !frame.hidden && frame.getBoundingClientRect().height > 0 };
  });
  assert.deepEqual(await visible(), { ready: true, visible: true }, 'repeated open and delayed load remain visible');
  assert.equal(loads, 1, 'repeated open loads once');
  await page.evaluate(() => { const old = document.getElementById('surface'); const next = old.cloneNode(false); next.append(old.querySelector('[data-app-shell-main-content-layout]')); old.replaceWith(next); });
  await page.waitForTimeout(1200);
  assert.deepEqual(await visible(), { ready: true, visible: true }, 'surface replacement recovers');
  assert.equal(loads, 2, 'surface replacement reloads once');
  await page.evaluate(() => { window.__codexTaskboardInjection__.close(); window.__codexTaskboardInjection__.open(); });
  await page.waitForTimeout(700);
  assert.deepEqual(await visible(), { ready: true, visible: true }, 'close and reopen remain visible');
  assert.equal(loads, 2, 'reopen reuses connected frame');
  await page.evaluate(() => { window.notifications = []; addEventListener('message', e => { if (e.data.type === 'mcp-notification') window.notifications.push(e.data); }); });
  const child = page.frames().find(frame => frame.parentFrame());
  await child.evaluate(() => parent.postMessage({ type: 'taskboard:thread-available', capability: testCapability, challenge: testChallenge, payload: { thread: { id: 'thread-test', cwd: '/tmp/project', name: '[Taskboard]测试' } } }, '*'));
  await page.waitForFunction(() => window.notifications.length === 1);
  assert.equal(await page.evaluate(() => window.notifications[0].params.thread.name), '[Taskboard]测试');
  console.log('passed: repeated open, delayed load, surface recovery, close/reopen');
} finally {
  await browser.close();
}
