'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(path.join(ROOT, 'web', 'text.js'), 'utf8');
const context = vm.createContext({ console });
vm.runInContext(source, context, { filename: 'text.js' });
const WebText = vm.runInContext('WebText', context);

assert.strictEqual(
  WebText.escapeHtml('<img src=x onerror="boom"> & väg'),
  '&lt;img src=x onerror=&quot;boom&quot;&gt; &amp; väg',
);
assert.strictEqual(WebText.escapeHtml("'Göteborg'"), '&#39;Göteborg&#39;');
assert.strictEqual(WebText.escapeHtml(null), '');
// renderTextLines: the caveat panels' one formatting path. It must keep the
// two load-bearing markers (<b> emphasis and the amber warning span) while
// still refusing to turn any other value into markup.
function fakeDocument() {
  const make = tag => ({
    tag, className: '', textContent: '', children: [],
    appendChild(child) { this.children.push(child); return child; },
    replaceChildren() { this.children.length = 0; },
  });
  return { createElement: make };
}

const doc = fakeDocument();
const container = doc.createElement('div');

WebText.renderTextLines(container, [
  '<b>Detta är INTE en rekommendation</b> — syntetiska signalprogram.',
  '<span id="suggest-results-warn">⚠ Vägen har ingen anslutning</span>',
], doc);

const shape = container.children.map(
  node => `${node.tag}:${node.className}:${node.textContent}`);
assert.deepStrictEqual(shape, [
  'strong::Detta är INTE en rekommendation',
  'span:: — syntetiska signalprogram.',
  'br::',
  'span:suggest-warn:⚠ Vägen har ingen anslutning',
]);

// A stray angle bracket from a server message is text, never an element.
const hostile = doc.createElement('div');
WebText.renderTextLines(hostile, ['<img src=x onerror=alert(1)>'], doc);
assert.deepStrictEqual(
  hostile.children.map(node => [node.tag, node.textContent]),
  [['span', '<img src=x onerror=alert(1)>']]);

// An unmatched marker stays literal rather than swallowing the rest.
const unmatched = doc.createElement('div');
WebText.renderTextLines(unmatched, ['<b>halvöppen'], doc);
assert.deepStrictEqual(
  unmatched.children.map(node => [node.tag, node.textContent]),
  [['span', '<b>halvöppen']]);

console.log('6/6 text checks passed');
