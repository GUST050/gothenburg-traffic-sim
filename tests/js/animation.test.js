'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(path.join(ROOT, 'web', 'animation.js'), 'utf8');
const context = vm.createContext({ console });
vm.runInContext(source, context, { filename: 'animation.js' });
const Animation = vm.runInContext('Animation', context);

assert.strictEqual(Animation.shouldRenderFrame(false, false), false);
assert.strictEqual(Animation.shouldRenderFrame(false, true), true);
assert.strictEqual(Animation.shouldRenderFrame(true, false), true);
assert.strictEqual(Animation.frameDelta(1000, 1016, false), 0);
assert.strictEqual(Animation.frameDelta(1000, 1016, true), 0.016);

let styleCalls = 0;
const marker = { setStyle: () => { styleCalls += 1; } };
assert.strictEqual(Animation.applyMarkerStyle(
  marker, 'hidden', { opacity: 0 }), true);
assert.strictEqual(Animation.applyMarkerStyle(
  marker, 'hidden', { opacity: 0 }), false);
assert.strictEqual(styleCalls, 1);
assert.strictEqual(Animation.applyMarkerStyle(
  marker, 'visible:red', { fillColor: 'red' }), true);
assert.strictEqual(styleCalls, 2);

console.log('10/10 animation checks passed');
