const test = require('node:test');
const assert = require('node:assert/strict');

require('../assets/js/admin/admin-schedule-guard.js');

const validate = globalThis.MacroWatchScheduleGuard.validateScheduleChange;

const items = [
  {
    workflow_id: 'liquidity.yml', cron: '30 4 * * *', kst_time: '13:30', name: '미국·한국 주식시장 자금환경',
    earliest_safe_time_kst: '13:30', dependency_reason: '한국은행 원천데이터 공개 이후에 실행되어야 합니다.',
  },
  {
    workflow_id: 'sector-flow-open.yml', cron: '10 0 * * 1-5', kst_time: '09:10', name: '주도섹터 흐름 · 장초반',
    earliest_safe_time_kst: '09:10', retry_minutes: 15, must_run_before_id: 'sector-flow-intraday.yml',
  },
  {
    workflow_id: 'sector-flow-intraday.yml', cron: '30 3 * * 1-5', kst_time: '12:30', name: '주도섹터 흐름 · 장중',
    earliest_safe_time_kst: '12:30', retry_minutes: 15, must_run_after_id: 'sector-flow-open.yml', must_run_before_id: 'sector-flow-close.yml',
  },
  {
    workflow_id: 'sector-flow-close.yml', cron: '40 6 * * 1-5', kst_time: '15:40', name: '주도섹터 흐름 · 종가',
    earliest_safe_time_kst: '15:40', retry_minutes: 15, must_run_after_id: 'sector-flow-intraday.yml',
  },
];

test('frontend guard preserves an unchanged legacy time but blocks a newly unsafe edit', () => {
  assert.equal(validate(items, 'liquidity.yml', '30 4 * * *', '13:30'), null);
  const issue = validate(items, 'liquidity.yml', '30 4 * * *', '13:29');
  assert.equal(issue.title, '실행 시간을 변경할 수 없습니다');
  assert.match(issue.message, /13:30 KST 이후/);
});

test('frontend guard includes retry time when protecting phase order', () => {
  const issue = validate(items, 'sector-flow-open.yml', '10 0 * * 1-5', '12:20');
  assert.equal(issue.title, '실행 시간을 변경할 수 없습니다');
  assert.match(issue.message, /재시도까지/);
  assert.match(issue.message, /12:30 KST/);
});

test('frontend guard allows a valid sector phase time', () => {
  assert.equal(validate(items, 'sector-flow-open.yml', '10 0 * * 1-5', '10:00'), null);
});
