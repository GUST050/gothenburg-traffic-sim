// Executable checks for the monthly form's time-band validation.
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(path.join(ROOT, 'web', 'app.js'), 'utf8');

function extractFunction(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notStrictEqual(start, -1, `${name} must remain executable UI logic`);
  const bodyStart = source.indexOf('{', start);
  let depth = 0;
  for (let index = bodyStart; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1;
    if (source[index] === '}') depth -= 1;
    if (depth === 0) return source.slice(start, index + 1);
  }
  throw new Error(`unterminated function ${name}`);
}

const context = vm.createContext({ console });
vm.runInContext([
  extractFunction('monthlyBandMinutes'),
  extractFunction('validateMonthlySpec'),
].join('\n'), context, { filename: 'app.js:monthly-validation' });
const validate = vm.runInContext('validateMonthlySpec', context);

function spec(overrides = {}) {
  return {
    permitted_date_start: '2027-05-01',
    permitted_date_end: '2027-05-31',
    min_consecutive_start_days: 1,
    max_consecutive_start_days: 2,
    required_work_minutes: 4 * 60,
    permitted_daily_band: {
      earliest_start: '23:00',
      latest_end: '01:00',
    },
    allowed_weekdays: [0, 1, 2, 3, 4, 5, 6],
    ...overrides,
  };
}

assert.strictEqual(validate(spec()), null,
  '23:00-01:00 has two hours per work date and must be accepted');

assert.match(validate(spec({ required_work_minutes: 4 * 60 + 15 })),
  /ryms inte/,
  'overnight capacity must use two hours per day, not a negative band');

assert.match(validate(spec({
  permitted_daily_band: { earliest_start: '23:00', latest_end: '23:00' },
})), /positiv längd/,
'equal clock times remain invalid; Heldag is the explicit 24-hour control');

console.log('3/3 monthly validation checks passed');
