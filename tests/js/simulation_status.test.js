'use strict';

// Regression 2026-10-10: /api/ping answered {"ok": true} from a server whose
// interpreter had no pandas/SUMO, so the UI offered "Byt dag" and every job
// failed. The page must now follow ping's "simulation" verdict.

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(path.join(ROOT, 'web', 'simulation_status.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source, context, { filename: 'simulation_status.js' });
const SimulationStatus = vm.runInContext('SimulationStatus', context);

function testNoServerOffersNoSimulation() {
  const access = SimulationStatus.access(false, null);
  assert.strictEqual(access.canSimulate, false);
  assert.strictEqual(access.recheck, false);
}

function testUnavailableIsRefusedWithTheServersOwnMessage() {
  const access = SimulationStatus.access(true, {
    status: 'unavailable', fix: 'make setup',
    message: "Simuleringen kan inte köras: saknade Python-paket: pandas. Kör `make setup`.",
  });
  assert.strictEqual(access.canSimulate, false);
  assert.ok(access.message.includes('pandas'));
  assert.strictEqual(access.recheck, false);
}

function testUnavailableWithoutAMessageStillNamesTheFix() {
  const access = SimulationStatus.access(true, { status: 'unavailable', fix: 'make setup' });
  assert.strictEqual(access.canSimulate, false);
  assert.ok(access.message.includes('make setup'));
}

function testReadyIsOffered() {
  const access = SimulationStatus.access(true, { status: 'ready' });
  assert.strictEqual(access.canSimulate, true);
  assert.strictEqual(access.message, '');
  assert.strictEqual(access.recheck, false);
}

function testCheckingIsNotReadyButRechecked() {
  // An unfinished check is not an answer: the page waits, says so, and asks
  // again until the server settles (changed 2026-10-10).
  const access = SimulationStatus.access(true, {
    status: 'checking', message: 'Kontrollerar simuleringsmiljön…' });
  assert.strictEqual(access.canSimulate, false);
  assert.strictEqual(access.recheck, true);
  assert.ok(access.message.includes('Kontrollerar'));
}

function testAnInconclusiveCheckIsNotReady() {
  const access = SimulationStatus.access(true, {
    status: 'unknown', fix: 'make setup',
    message: 'Simuleringsmiljön kunde inte kontrolleras färdigt.' });
  assert.strictEqual(access.canSimulate, false);
  assert.ok(access.message.includes('kontrolleras'));
  assert.strictEqual(access.recheck, false);
}

function testAMissingReadinessFieldIsNotReady() {
  // Review 2026-10-10 (P2): every local server reports readiness, so a ping
  // without it says nothing about whether this server can simulate.
  for (const missing of [undefined, null]) {
    const access = SimulationStatus.access(true, missing);
    assert.strictEqual(access.canSimulate, false);
    assert.ok(access.message.length > 0);
    assert.strictEqual(access.recheck, false);
  }
}

function testRecheckingIsBounded() {
  assert.ok(SimulationStatus.RECHECK_MS >= 500);
  assert.ok(SimulationStatus.MAX_RECHECKS * SimulationStatus.RECHECK_MS <= 180000);
}

[
  testNoServerOffersNoSimulation,
  testUnavailableIsRefusedWithTheServersOwnMessage,
  testUnavailableWithoutAMessageStillNamesTheFix,
  testReadyIsOffered,
  testCheckingIsNotReadyButRechecked,
  testAnInconclusiveCheckIsNotReady,
  testAMissingReadinessFieldIsNotReady,
  testRecheckingIsBounded,
].forEach(test => test());
console.log('simulation_status: 8 checks passed');
