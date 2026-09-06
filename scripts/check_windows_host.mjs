/** Pure host geometry/path contracts. No browser or live Taskboard required. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const source = readFileSync(new URL('../injector/inject.js', import.meta.url), 'utf8');
const fn = name => source.match(new RegExp(`  function ${name}\\([^]*?\\n  }`))[0];
const context = vm.createContext({
  navigator: { windowControlsOverlay: { visible: true, getTitlebarAreaRect: () => ({ x: 0, y: 0, width: 862, height: 32 }) } },
  findPageMount: () => ({ surface: { getBoundingClientRect: () => ({ top: 0, right: 1000 }) } }),
});
vm.runInContext(`${fn('normalizeNativeRootPath')}\n${fn('titlebarRightInset')}`, context);
for (const [input, output] of [
  ['C:\\', 'c:/'], ['C:/', 'c:/'], ['C:\\项目 空格\\', 'c:/项目 空格'],
  ['\\\\server\\share\\project\\', '//server/share/project'], ['/Users/test/', '/Users/test'], ['/', '/'],
]) assert.equal(context.normalizeNativeRootPath(input), output);
assert.equal(context.titlebarRightInset(), 138);
context.navigator.windowControlsOverlay.visible = false;
assert.equal(context.titlebarRightInset(), 0);
context.navigator.windowControlsOverlay.visible = true;
context.findPageMount = () => ({ surface: { getBoundingClientRect: () => ({ top: 44, right: 1000 }) } });
assert.equal(context.titlebarRightInset(), 0);
console.log('Windows drive/UNC paths and caption geometry passed');
