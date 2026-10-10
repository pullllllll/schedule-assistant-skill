import test from 'node:test';
import assert from 'node:assert/strict';
import { habitDisplayGroups, habitPeriodLabel } from '../src/plan-model.js';

const rule = (unit, interval = 1, anchor = '2026-10-12') => ({
  unit, interval, first_period_starts_on: anchor,
});

const todo = (date, index = 1) => ({ id: `${date}-${index}`, parent_refs: [{
  goal_id: 'habit', node_id: null, habit_period_starts_on: date, habit_occurrence_index: index,
}] });

test('seven daily occurrences form one weekly display group without changing daily identities', () => {
  const goal = { id: 'habit', habit_rule: rule('day') };
  const todos = Array.from({ length: 7 }, (_, i) => todo(`2026-10-${18 - i}`));
  const before = JSON.stringify({ goal, todos });
  const groups = habitDisplayGroups(goal, todos);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].title, '10/12 – 10/18');
  assert.equal(groups[0].displayKind, 'week');
  assert.deepEqual(groups[0].todos.map(t => t.id), Array.from({ length: 7 }, (_, i) => `2026-10-${12 + i}-1`));
  assert.equal(JSON.stringify({ goal, todos }), before);
});

test('weekly display boundaries separate Sunday and Monday, preserving extra daily occurrences', () => {
  const groups = habitDisplayGroups({ id: 'habit', habit_rule: rule('day') },
    [todo('2026-10-19'), todo('2026-10-18', 2), todo('2026-10-18')]);
  assert.deepEqual(groups.map(g => [g.title, g.todos.length]), [['10/12 – 10/18', 2], ['10/19 – 10/25', 1]]);
  assert.equal(groups[0].todos[0].id, '2026-10-18-1');
});

test('multi-week and monthly habits retain their actual periods', () => {
  for (const unit of ['week', 'month']) {
    const groups = habitDisplayGroups({ id: 'habit', habit_rule: rule(unit, 2) }, [todo('2026-10-12', 2), todo('2026-10-12')]);
    assert.equal(groups.length, 1);
    assert.equal(groups[0].displayKind, 'period');
    assert.equal(groups[0].title, unit === 'week' ? '10/12 – 10/25' : '10/12 – 12/11');
  }
});

test('daily periods show each date instead of repeating the containing week', () => {
  const dates = Array.from({ length: 7 }, (_, i) => `2026-10-${12 + i}`);
  assert.deepEqual(dates.map(date => habitPeriodLabel(date, rule('day'))),
    ['10/12', '10/13', '10/14', '10/15', '10/16', '10/17', '10/18']);
});

test('multi-day periods span month boundaries', () => {
  assert.equal(habitPeriodLabel('2026-10-31', rule('day', 3)), '10/31 – 11/02');
});

test('weekly labels use the actual start and interval, not the containing calendar week', () => {
  assert.equal(habitPeriodLabel('2026-10-12', rule('week')), '10/12 – 10/18');
  assert.equal(habitPeriodLabel('2026-10-14', rule('week', 2, '2026-10-14')), '10/14 – 10/27');
});

test('month-end periods retain the original anchor after a short month', () => {
  const monthly = rule('month', 1, '2026-01-31');
  assert.equal(habitPeriodLabel('2026-01-31', monthly), '01/31 – 02/27');
  assert.equal(habitPeriodLabel('2026-02-28', monthly), '02/28 – 03/30');
  assert.equal(habitPeriodLabel('2028-02-29', monthly), '02/29 – 03/30');
  assert.equal(habitPeriodLabel('2026-01-31', rule('month', 2, '2026-01-31')), '01/31 – 03/30');
});

test('cross-year periods include years; unknown rules do not fabricate a weekly period', () => {
  assert.equal(habitPeriodLabel('2026-12-28', rule('week')), '2026/12/28 – 2027/01/03');
  assert.equal(habitPeriodLabel('2026-10-12', null), '10/12');
  assert.equal(habitPeriodLabel(null, rule('day')), '未分期');
});
