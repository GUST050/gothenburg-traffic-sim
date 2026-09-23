'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'web', 'app.js'), 'utf8');
const match = source.match(/function scheduleLabel\(schedule\) \{[\s\S]*?\n        \}/);
assert.ok(match, 'scheduleLabel must remain executable UI logic');
const scheduleLabel = vm.runInNewContext(`${match[0]}; scheduleLabel`);
const spanMatch = source.match(/function scheduleCalendarSpanDays\(intervals\) \{[\s\S]*?\n        \}/);
assert.ok(spanMatch, 'scheduleCalendarSpanDays must remain executable UI logic');
const scheduleCalendarSpanDays = vm.runInNewContext(
  `${spanMatch[0]}; scheduleCalendarSpanDays`);

assert.equal(scheduleLabel({
  daily_end: '24:00',
  intervals: [{
    start_time: '2027-09-17T00:00:00',
    end_time: '2027-09-18T00:00:00',
  }],
}), '2027-09-17 00:00–24:00');

assert.equal(scheduleLabel({
  daily_end: '01:00',
  intervals: [{
    start_time: '2027-09-17T23:00:00',
    end_time: '2027-09-18T01:00:00',
  }],
}), '2027-09-17 23:00–01:00');

assert.equal(scheduleCalendarSpanDays([{
  start_time: '2027-09-17T23:00:00',
  end_time: '2027-09-18T01:00:00',
}]), 2, 'an overnight closure needs demand for the next calendar day');
assert.equal(scheduleCalendarSpanDays([{
  start_time: '2027-09-17T00:00:00',
  end_time: '2027-09-18T00:00:00',
}]), 1, 'a half-open midnight end does not need another day');
assert.equal(scheduleCalendarSpanDays([{
  start_time: '2027-09-17T23:00:00',
  end_time: '2027-09-18T01:00:00',
}, {
  start_time: '2027-09-18T23:00:00',
  end_time: '2027-09-19T01:00:00',
}]), 3, 'the final overnight pass extends a multi-day schedule');
