// Exercise the production shortcut handler: form keys must not scrub playback.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const listeners = {};
const element = () => ({value: 0, min: 0, max: 95, style: {},
  addEventListener() {}, setAttribute() {}, dataset: {}, classList: {toggle() {}}});
const body = {dataset: {task: 'scenario'}, classList: {contains: () => false}};
const state = {qi: 0, toggle() { this.toggles++; }, toggles: 0,
  setQI(qi) {this.qi = qi;}, setSpeed() {}};
const context = vm.createContext({
  State: state,
  document: {body, getElementById: element, querySelectorAll: () => []},
  window: {addEventListener(type, handler) { listeners[type] = handler; }},
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../../web/controls.js'), 'utf8')
  + '\nControls.init({});', context);
const key = (name, tag = 'div', extra = {}) => listeners.keydown({key: name,
  target: {closest: selector => selector.includes(tag) ? {} : null},
  preventDefault() {}, ...extra});
for (const tag of ['input', 'button', 'select', 'textarea', 'summary', 'a', '[contenteditable]']) {
  key('ArrowRight', tag); key(' ', tag);
}
assert.equal(state.qi, 0); assert.equal(state.toggles, 0);
for (const task of ['home', 'history']) {
  body.dataset.task = task; key('ArrowRight'); key(' ');
}
assert.equal(state.qi, 0); assert.equal(state.toggles, 0);
body.dataset.task = 'scenario';
body.classList.contains = () => true;
key('ArrowRight'); key(' ');
assert.equal(state.qi, 0); assert.equal(state.toggles, 0);
body.classList.contains = () => false;
key('ArrowRight', 'div', {ctrlKey: true});
assert.equal(state.qi, 0);
key('ArrowRight'); assert.equal(state.qi, 1);
key('ArrowLeft', 'div', {shiftKey: true}); assert.equal(state.qi, -95);
key(' '); assert.equal(state.toggles, 1);
console.log('controls keyboard checks passed');
