// Focused presentation checks and a standalone, offline visual fixture (no Taskboard API).
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';
import { build, transform } from 'esbuild';

const source = await readFile('web/src/components/activityPresentation.ts', 'utf8');
const { code } = await transform(source, { loader: 'ts', format: 'esm' });
const { activityPresentation: present } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const items = [
  { kind: 'commandExecution', data: { command: '/bin/zsh -lc \'git status --short\'', aggregatedOutput: 'working tree clean\n'.repeat(100), exitCode: 0 } },
  { kind: 'commandExecution', data: { command: 'cat README.md', commandActions: [{ type: 'read', path: 'README.md' }], aggregatedOutput: '# Readme' } },
  { kind: 'fileChange', data: { changes: [{ path: 'web/src/App.tsx', diff: '- old\n+ new' }] } },
  { kind: 'mcpToolCall', data: { server: 'playwright', tool: 'browser_navigate', arguments: { url: 'https://example.com' }, result: { content: [{ type: 'text', text: 'Page loaded' }] } } },
  { kind: 'mcpToolCall', data: { server: 'plugin', tool: 'search', arguments: { query: 'test' }, result: 'Found a match' } },
  { kind: 'dynamicToolCall', data: { tool: 'read_file', arguments: '{"path":"docs/design.md"}', output: 'Design notes' } },
  { kind: 'commandExecution', status: 'completed', data: { command: 'false', exitCode: 1 } },
  { kind: 'mcpToolCall', status: 'running', data: { tool: 'imagegen', arguments: { prompt: 'tree' } } },
];
assert.deepEqual(items.map(item => present(item).category), ['command', 'read', 'files', 'browser', 'tool', 'read', 'command', 'tool']);
assert.equal(present(items[1]).summary, 'README.md');
assert.equal(present(items[2]).summary, 'web/src/App.tsx');
assert.match(present(items[3]).summary, /browser_navigate.*example.com/);
assert.equal(present(items[4]).summary, 'plugin · search');
assert.equal(present(items[5]).summary, 'docs/design.md');
assert.equal(present(items[6]).failed, true);
assert.equal(present(items[7]).running, true);
assert.equal(present({ kind: 'futureTool', message: 'Unknown tool' }).summary, 'Unknown tool');
assert.equal(present({ kind: 'mcpToolCall', data: { arguments: 'invalid json' } }).args, 'invalid json');
assert.equal(present({ kind: 'commandExecution', data: { command: 'cat a && rm b', commandActions: [{ type: 'read', path: 'a' }, { type: 'unknown' }] } }).category, 'command');

const temp = await mkdtemp(join(tmpdir(), 'activity-check-'));
try {
  const outfile = join(temp, 'render.cjs');
  await build({ stdin: { contents: `import React from 'react'; import {renderToStaticMarkup} from 'react-dom/server'; import {ActivityTool, ActivityToolIcon} from './web/src/components/ActivityTool'; import {setHostLanguage} from './web/src/i18n'; setHostLanguage('zh-CN'); export const html = renderToStaticMarkup(<main className="app-shell"><section style={{maxWidth: 850, margin: '40px auto', padding: 20}}><h2>执行记录</h2><div className="activity-stream">{${JSON.stringify(items)}.map((item, index) => <div className="activity-entry activity-tool-entry" key={index}><span className="activity-rail-icon"><ActivityToolIcon item={item}/></span><div className="activity-content"><ActivityTool item={item}/></div><time>刚刚</time></div>)}</div></section></main>);`, resolveDir: process.cwd(), loader: 'tsx' }, bundle: true, platform: 'node', format: 'cjs', outfile, loader: { '.css': 'empty' } });
  const { html } = createRequire(import.meta.url)(outfile);
  assert.equal((html.match(/<details /g) || []).length, items.length);
  assert.equal((html.match(/role="region"/g) || []).length, items.length);
  assert.ok(!html.includes('<details open'));
  assert.match(html, /调用失败/);
  const css = await readFile('web/src/styles.css', 'utf8') + await readFile('web/src/components/ActivityTool.css', 'utf8');
  await mkdir('output/playwright', { recursive: true });
  await writeFile('output/playwright/activity.html', `<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>${css}</style>${html}</html>`);
  console.log('Activity presentation and rendering checks passed; offline fixture: output/playwright/activity.html');
} finally { await rm(temp, { recursive: true, force: true }); }
