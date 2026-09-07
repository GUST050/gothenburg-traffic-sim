'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(path.join(ROOT, 'web', 'polling.js'), 'utf8');
const context = vm.createContext({ console });
vm.runInContext(source, context, { filename: 'polling.js' });
const Polling = vm.runInContext('Polling', context);

const response = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

async function testBackoffAndRecovery() {
  const replies = [
    new Error('offline'),
    response(500, { error: 'serverfel' }),
    response(200, { status: 'running', elapsed_s: 3 }),
    response(200, { status: 'done', result: 7 }),
  ];
  const delays = [];
  const progress = [];
  const result = await Polling.pollStatus('/status', {
    pollMs: 100,
    maxConsecutiveFailures: 5,
    sleep: async ms => delays.push(ms),
    fetchImpl: async () => {
      const reply = replies.shift();
      if (reply instanceof Error) throw reply;
      return reply;
    },
    onProgress: status => progress.push(status),
  });

  assert.deepStrictEqual(delays, [100, 200, 400, 100]);
  assert.deepStrictEqual(progress, [{ status: 'running', elapsed_s: 3 }]);
  assert.deepStrictEqual(result, { status: 'done', result: 7 });
}

async function testFailureLimit() {
  let calls = 0;
  await assert.rejects(
    Polling.pollStatus('/status', {
      pollMs: 1,
      maxConsecutiveFailures: 3,
      sleep: async () => {},
      fetchImpl: async () => {
        calls += 1;
        throw new Error('offline');
      },
    }),
    /jobbet kan fortfarande köras/,
  );
  assert.strictEqual(calls, 3);
}

Promise.resolve()
  .then(testBackoffAndRecovery)
  .then(testFailureLimit)
  .then(() => console.log('2/2 polling checks passed'))
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
