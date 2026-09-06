// Executable checks for the provider seam's "carries no traffic" predicate.
//
// These run the REAL web/provider.js — loaded into a vm context with a stubbed
// fetch, so load() computes _maxByEdge exactly as the browser does. A
// source-string assertion could not have caught the regression below, because
// the bug was in WHICH provider answered, not in whether the code was present.
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(path.join(ROOT, 'web', 'provider.js'), 'utf8');

function makeContext(payloadsByUrl) {
  const context = vm.createContext({
    console,
    fetch: async (url) => {
      const key = String(url).split('?')[0];
      if (!(key in payloadsByUrl)) {
        return { ok: false, status: 404, json: async () => ({}) };
      }
      return { ok: true, status: 200, json: async () => payloadsByUrl[key] };
    },
  });
  vm.runInContext(source, context, { filename: 'provider.js' });
  return context;
}

function scenarioPayload(flows, nQuarters = 4) {
  return {
    epoch: '2025-09-16T00:00:00',
    interval_minutes: 15,
    n_quarters: nQuarters,
    flows,
    scenario: { label: 'test', closed_edges: [] },
  };
}

async function build(context, flowsByUrl) {
  const loaded = {};
  for (const [url, flows] of Object.entries(flowsByUrl)) {
    loaded[url] = await vm.runInContext(
      `(async (u) => new HistoricalProvider().load(u))`, context)(url);
  }
  return loaded;
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('an edge the scenario never fills reports no traffic', async () => {
  const flows = { empty: [0, 0, 0, 0], busy: [1, 5, 2, 0] };
  const context = makeContext({ 'a.json': scenarioPayload(flows) });
  const [p] = Object.values(await build(context, { 'a.json': flows }));

  assert.strictEqual(p.carriesNoTraffic('empty'), true);
  assert.strictEqual(p.carriesNoTraffic('busy'), false);
});

test('an edge the provider does not carry is not "no traffic"', async () => {
  const flows = { busy: [1, 1, 1, 1] };
  const context = makeContext({ 'a.json': scenarioPayload(flows) });
  const [p] = Object.values(await build(context, { 'a.json': flows }));

  // Absent from this artifact is a different statement from modelled-as-empty,
  // and only the second may be drawn as a modelling boundary.
  assert.strictEqual(p.carriesNoTraffic('never-heard-of-it'), false);
});

test('an all-null edge is missing data, not zero traffic', async () => {
  const flows = { gap: [null, null, null, null] };
  const context = makeContext({ 'a.json': scenarioPayload(flows) });
  const [p] = Object.values(await build(context, { 'a.json': flows }));

  assert.strictEqual(p.carriesNoTraffic('gap'), false);
});

test('REGRESSION: a PARTIAL gap is missing data, not a window of zeros',
  async () => {
    // null means missing under the data contract, never zero. The maximum of
    // [0, null, 0, 0] is 0, so a max-based predicate answers "no traffic
    // anywhere in this window" while one quarter is simply unknown — and the
    // tooltip then asserts something the artifact does not support.
    const flows = {
      partial: [0, null, 0, 0],
      undef: [0, undefined, 0, 0],
      complete: [0, 0, 0, 0],
    };
    const context = makeContext({ 'a.json': scenarioPayload(flows) });
    const [p] = Object.values(await build(context, { 'a.json': flows }));

    assert.strictEqual(p.maxFlow('partial'), 0,
      'the maximum really is 0 — which is exactly why it cannot be the test');
    assert.strictEqual(p.carriesNoTraffic('partial'), false);
    assert.strictEqual(p.carriesNoTraffic('undef'), false);
    assert.strictEqual(p.carriesNoTraffic('complete'), true);
  });

test('a series shorter than the window is a gap too', async () => {
  // Quarters past the end of the array read back as null through flowAt(), so
  // an edge with three zeros in a four-quarter window says nothing about the
  // fourth.
  const flows = { short: [0, 0, 0], full: [0, 0, 0, 0] };
  const context = makeContext({ 'a.json': scenarioPayload(flows, 4) });
  const [p] = Object.values(await build(context, { 'a.json': flows }));

  assert.strictEqual(p.flowAt('short', 3), null);
  assert.strictEqual(p.carriesNoTraffic('short'), false);
  assert.strictEqual(p.carriesNoTraffic('full'), true);
});

test('a gap in EITHER arm keeps the comparison from claiming no traffic',
  async () => {
    const baselineFlows = { street: [0, null, 0, 0] };
    const closureFlows = { street: [0, 0, 0, 0] };
    const context = makeContext({
      'base.json': scenarioPayload(baselineFlows),
      'close.json': scenarioPayload(closureFlows),
    });
    const loaded = await build(context, {
      'base.json': baselineFlows, 'close.json': closureFlows });
    const delta = await vm.runInContext(
      `((b, c) => new DeltaProvider(b, c, {}))`, context)(
        loaded['base.json'], loaded['close.json']);

    assert.strictEqual(delta.carriesNoTraffic('street'), false);
  });

test('REGRESSION: a closure that empties a street is NOT "no traffic"', async () => {
  // The whole point of the comparison view. maxFlow() on a DeltaProvider
  // delegates to the closure arm, so asking it alone reports 0 here and the
  // largest reduction the map can express gets drawn as an unsimulated road.
  const baselineFlows = { street: [10, 10, 10, 10] };
  const closureFlows = { street: [0, 0, 0, 0] };
  const context = makeContext({
    'base.json': scenarioPayload(baselineFlows),
    'close.json': scenarioPayload(closureFlows),
  });
  const loaded = await build(context, {
    'base.json': baselineFlows, 'close.json': closureFlows });
  const delta = await vm.runInContext(
    `((b, c) => new DeltaProvider(b, c, {}))`, context)(
      loaded['base.json'], loaded['close.json']);

  assert.strictEqual(delta.carriesNoTraffic('street'), false,
    'an emptied street must stay visible as a reduction');
  assert.strictEqual(delta.maxFlow('street'), 0,
    'maxFlow still delegates to the closure arm — that is why the predicate ' +
    'may not be built on it');
  assert.strictEqual(delta.deltaAt('street', 0), -10);
  assert.strictEqual(delta.compare('street', 0).delta, -10);
});

test('a street empty in BOTH arms does report no traffic', async () => {
  const flows = { street: [0, 0, 0, 0] };
  const context = makeContext({
    'base.json': scenarioPayload(flows), 'close.json': scenarioPayload(flows) });
  const loaded = await build(context, {
    'base.json': flows, 'close.json': flows });
  const delta = await vm.runInContext(
    `((b, c) => new DeltaProvider(b, c, {}))`, context)(
      loaded['base.json'], loaded['close.json']);

  assert.strictEqual(delta.carriesNoTraffic('street'), true);
});

test('a street the CLOSURE fills but the baseline does not stays visible',
  async () => {
    const baselineFlows = { street: [0, 0, 0, 0] };
    const closureFlows = { street: [0, 7, 3, 0] };
    const context = makeContext({
      'base.json': scenarioPayload(baselineFlows),
      'close.json': scenarioPayload(closureFlows),
    });
    const loaded = await build(context, {
      'base.json': baselineFlows, 'close.json': closureFlows });
    const delta = await vm.runInContext(
      `((b, c) => new DeltaProvider(b, c, {}))`, context)(
        loaded['base.json'], loaded['close.json']);

    // Diverted traffic arriving on a previously empty street is a real
    // redistribution effect and must not be hidden either.
    assert.strictEqual(delta.carriesNoTraffic('street'), false);
    assert.strictEqual(delta.deltaAt('street', 1), 7);
  });

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log(`ok   ${name}`);
    } catch (error) {
      failed += 1;
      console.log(`FAIL ${name}\n     ${error.message}`);
    }
  }
  console.log(`\n${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
