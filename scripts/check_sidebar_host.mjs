/** Regression for 26.928's fixed navigation outside the task scroll area. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const source = readFileSync(new URL('../injector/inject.js', import.meta.url), 'utf8');
const fn = name => source.match(new RegExp(`  function ${name}\\([^]*?\\n  }`))[0];
const hidden = { getBoundingClientRect: () => ({width:0,height:0}) };
const visible = { getBoundingClientRect: () => ({width:200,height:32}) };
let modern = [hidden, visible];
let newChats = [];
const plugin = {textContent:'Plugins', parentElement:{}};
const context = vm.createContext({
  PLUGIN_LABELS: ['plugins'], normalizedLabel: text => text.trim().toLowerCase(),
  document: {
    querySelectorAll: selector => selector.includes('builtin:orbit') ? modern : newChats,
    querySelector: () => ({querySelectorAll: () => [plugin]}),
  },
});
vm.runInContext(`${fn('buttonMatches')}\n${fn('findReferenceButton')}`, context);
assert.equal(vm.runInContext('findReferenceButton()', context), visible);
modern = [];
const chat = {...visible, textContent:'New chat'};
newChats = [chat];
assert.equal(vm.runInContext('findReferenceButton()', context), chat);
newChats = [];
assert.equal(vm.runInContext('findReferenceButton()', context), plugin);
console.log('Modern visible navigation, hidden rows, optional orbit, and legacy sidebar: PASS');
