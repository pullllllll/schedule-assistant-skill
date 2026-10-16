import test from 'node:test';
import assert from 'node:assert/strict';
import { buildModel, todoDeadlineLabel, layoutDayEntries, weekSlotHeight } from '../src/plan-model.js';

function planWith(starts_at, ends_at, timezone = 'Asia/Shanghai') {
  return {
    plan_meta: { timezone, plan_version: 1 },
    task_structure: { goals: [], nodes: [], todos: [] },
    time_constraints: [{ id: 'meeting', kind: 'fixed', title: '会议' }],
    schedule: { planning_window: { starts_on: '2026-10-05', ends_on: '2026-10-11' },
      entries: [{ id: 'original', source_type: 'time_constraint', time_constraint_id: 'meeting', starts_at, ends_at }] },
  };
}
const modelFor = (...args) => buildModel(planWith(...args), new Date('2026-10-08T08:00:00+08:00'));
const minutes = entries => entries.map(e => [e.startMinutes, e.endMinutes]);

test('cross-midnight display preserves identity, full detail and original count', () => {
  const plan = planWith('2026-10-08T23:30:00+08:00', '2026-10-09T01:00:00+08:00');
  const before = JSON.stringify(plan);
  const model = buildModel(plan);
  assert.deepEqual(minutes(model.entriesOn('2026-10-08')), [[1410, 1440]]);
  assert.deepEqual(minutes(model.entriesOn('2026-10-09')), [[0, 60]]);
  assert.equal(model.entriesOn('2026-10-09')[0].id, 'original');
  assert.equal(model.entryById.size, 1);
  assert.equal(model.counts.entries, 1);
  assert.equal(model.entryById.get('original').fullTimeLabel, '2026-10-08 23:30–2026-10-09 01:00');
  assert.equal(model.entryById.get('original').totalDurationMinutes, 90);
  assert.equal(JSON.stringify(plan), before);
});

test('midnight end does not create empty next-day entry', () => {
  const model = modelFor('2026-10-08T23:30:00+08:00', '2026-10-09T00:00:00+08:00');
  assert.deepEqual(minutes(model.entriesOn('2026-10-08')), [[1410, 1440]]);
  assert.deepEqual(model.entriesOn('2026-10-09'), []);
});

test('multiple days and next week retain intermediate full days', () => {
  const model = modelFor('2026-10-11T23:30:00+08:00', '2026-10-14T01:00:00+08:00');
  const days = model.weekDaysFor('2026-10-12');
  assert.deepEqual(minutes(days[0].entries), [[0, 1440]]);
  assert.deepEqual(minutes(days[1].entries), [[0, 1440]]);
  assert.deepEqual(minutes(days[2].entries), [[0, 60]]);
});

test('display dates use plan timezone rather than source timestamp offset', () => {
  const model = modelFor('2026-10-08T15:30:00Z', '2026-10-08T17:00:00Z');
  assert.deepEqual(minutes(model.entriesOn('2026-10-08')), [[1410, 1440]]);
  assert.deepEqual(minutes(model.entriesOn('2026-10-09')), [[0, 60]]);
});

test('DST spring crossing keeps local display and elapsed detail separate', () => {
  const model = modelFor('2026-03-07T23:30:00-05:00', '2026-03-08T03:30:00-04:00', 'America/New_York');
  assert.deepEqual(minutes(model.entriesOn('2026-03-08')), [[0, 210]]);
  assert.equal(model.entryById.get('original').totalDurationMinutes, 180);
});

test('DST autumn crossing retains next-day segment', () => {
  const model = modelFor('2026-10-31T23:30:00-04:00', '2026-11-01T03:30:00-05:00', 'America/New_York');
  assert.deepEqual(minutes(model.entriesOn('2026-11-01')), [[0, 210]]);
  assert.equal(model.entryById.get('original').totalDurationMinutes, 300);
});

test('same ID move and completion are resolved from current facts', () => {
  const plan = planWith('2026-10-08T09:00:00+08:00', '2026-10-08T09:10:00+08:00');
  plan.task_structure.todos = [{ id: 'todo', title: '行动', status: 'pending', parent_refs: [], depends_on_todo_ids: [] }];
  Object.assign(plan.schedule.entries[0], { source_type: 'todo', todo_id: 'todo' });
  const old = buildModel(plan);
  plan.task_structure.todos[0].status = 'completed';
  Object.assign(plan.schedule.entries[0], { starts_at: '2026-10-09T12:00:00+08:00', ends_at: '2026-10-09T12:10:00+08:00' });
  const model = buildModel(plan);
  assert.equal(old.entriesOn('2026-10-08').length, 1);
  assert.equal(model.entriesOn('2026-10-08').length, 0);
  assert.equal(model.entriesOfTodo('todo').length, 1);
  assert.equal(model.entriesOn('2026-10-09')[0].status, 'completed');
  assert.equal(model.entryById.get('original').startMinutes, 720);
});

const entry = (id, startMinutes, endMinutes) => ({ id, startMinutes, endMinutes });
test('real overlaps occupy different lanes', () => {
  const result = layoutDayEntries([entry('a', 840, 930), entry('b', 870, 945)]);
  assert.deepEqual(result.map(e => [e.lane, e.laneCount]), [[0, 2], [1, 2]]);
});
test('touching boundaries use one lane', () => {
  assert.deepEqual(layoutDayEntries([entry('a', 540, 550), entry('b', 550, 565)]).map(e => [e.lane, e.laneCount]), [[0, 1], [0, 1]]);
});
test('nested/transitive overlaps reuse lanes and separate disconnected groups', () => {
  const result = layoutDayEntries([entry('a', 0, 100), entry('b', 10, 30), entry('c', 20, 40), entry('d', 40, 120), entry('e', 120, 130)]);
  assert.deepEqual(result.map(e => [e.lane, e.laneCount]), [[0, 3], [1, 3], [2, 3], [1, 3], [0, 1]]);
  for (const a of result) for (const b of result) {
    if (a.id !== b.id && a.startMinutes < b.endMinutes && b.startMinutes < a.endMinutes) assert.notEqual(a.lane, b.lane);
  }
});
test('layout is stable under input order and does not mutate entries', () => {
  const entries = [entry('b', 0, 30), entry('a', 0, 30)];
  const before = structuredClone(entries);
  assert.deepEqual(layoutDayEntries(entries), layoutDayEntries([...entries].reverse()));
  assert.deepEqual(entries, before);
});
test('10/15 minute cards gain a line of height without changing time ratios', () => {
  const height = weekSlotHeight([10, 15, 45, 90].map(durationMinutes => ({ durationMinutes })));
  assert.ok(10 / 30 * height - 4 >= 24);
  assert.ok(15 / 30 * height - 4 >= 24);
  assert.equal((15 / 30 * height) / (10 / 30 * height), 1.5);
});
test('normal weeks keep existing scale and extreme short entries have bounded scale', () => {
  assert.equal(weekSlotHeight([]), 30);
  assert.equal(weekSlotHeight([{ durationMinutes: 90 }]), 30);
  assert.equal(weekSlotHeight([{ durationMinutes: 1 }]), 180);
});

test('mixed recurring branches and old nodes preserve all edges without mutating data', () => {
  const plan = planWith('2026-10-08T09:00:00+08:00', '2026-10-08T10:00:00+08:00');
  plan.task_structure.goals = [{ id: 'podcast', type: 'recurring', status: 'active' }, { id: 'site', type: 'linear', status: 'active' }];
  plan.task_structure.nodes = [
    { id: 'old', goal_id: 'podcast', starts_on: '2026-10-05' },
    { id: 'setup', goal_id: 'podcast', kind: 'regular', cycle_starts_on: null },
    { id: 'pilot', goal_id: 'podcast', kind: 'cycle', cycle_starts_on: null, starts_on: '2026-10-08' },
    { id: 'acceptance', goal_id: 'site' },
  ];
  plan.task_structure.todos = [['reply', null], ['research', 'pilot'], ['buy', 'setup']].map(([id, node_id]) => ({
    id, parent_refs: [{ goal_id: 'podcast', node_id }], depends_on_todo_ids: [], status: 'pending',
  }));
  const before = JSON.stringify(plan), model = buildModel(plan);
  assert.equal(model.nodeById.get('old').cycle_starts_on, '2026-10-05');
  assert.equal(model.nodeById.get('old').kind, 'cycle');
  assert.equal(model.nodeById.get('acceptance').kind, 'regular');
  assert.equal(model.nodeById.get('pilot').cycle_starts_on, null);
  assert.equal(model.nodeById.get('setup').kind, 'regular');
  assert.equal(model.effectiveParentsOf(model.todoById.get('reply')).length, 1);
  assert.equal(model.nodes.length, 4);
  assert.equal(JSON.stringify(plan), before);
});

test('recurring details explain unknown cadence and template without null placeholders', async () => {
  const { recurringDetails } = await import('../src/plan-model.js');
  assert.deepEqual(recurringDetails({ recurrence: null, cycle_template: null }), [
    ['循环规则', '周期待定'], ['首个周期', '起始日期待定'], ['每期流程', '模板待定'],
  ]);
  assert.deepEqual(recurringDetails({ recurrence: { interval: 2, unit: 'week' }, cycle_template: null })[0], ['循环规则', '每 2 周']);
  assert.deepEqual(recurringDetails({ recurrence: { interval: null, unit: null, first_cycle_starts_on: '2026-10-12' }, cycle_template: {} })[1], ['首个周期', '2026-10-12']);
});

test('node outcomes and actual starts affect actionability while cycle identity does not', () => {
  const plan = planWith('2026-10-08T09:00:00+08:00', '2026-10-08T10:00:00+08:00');
  plan.task_structure.goals = [{ id: 'g', type: 'recurring', status: 'active' }];
  plan.task_structure.nodes = [
    { id: 'n', goal_id: 'g', kind: 'cycle', cycle_starts_on: '2026-10-12', starts_on: null, status: 'pending', depends_on_node_ids: ['setup'] },
    { id: 'setup', goal_id: 'g', kind: 'regular', cycle_starts_on: null, status: 'pending' },
  ];
  plan.task_structure.todos = [{ id: 't', title: '录制', status: 'pending', parent_refs: [{ goal_id: 'g', node_id: 'n' }], depends_on_todo_ids: [] }];
  Object.assign(plan.schedule.entries[0], { source_type: 'todo', todo_id: 't' });
  const current = new Date('2026-10-08T09:30:00+08:00');
  assert.equal(buildModel(plan, current).entryById.get('original').actionability, '等待前置成果');
  plan.task_structure.nodes[1].status = 'completed';
  assert.equal(buildModel(plan, current).entryById.get('original').actionability, '现在可行动');
  plan.task_structure.nodes[0].starts_on = '2026-10-09';
  assert.equal(buildModel(plan, current).entryById.get('original').actionability, '未到可开始日期');
});

test('Todo deadline details distinguish unresolved, explicitly absent and confirmed, including legacy', () => {
  assert.equal(todoDeadlineLabel({ deadline: null }, 'Asia/Shanghai'), '待确认');
  assert.equal(todoDeadlineLabel({ deadline: null, deadline_status: 'pending' }, 'Asia/Shanghai'), '待确认');
  assert.equal(todoDeadlineLabel({ deadline: null, deadline_status: 'none' }, 'Asia/Shanghai'), '不设截止');
  const deadline = '2026-10-13T04:00:00Z';
  const label = todoDeadlineLabel({ deadline }, 'Asia/Shanghai');
  assert.match(label, /12:00/);
  assert.equal(todoDeadlineLabel({ deadline, deadline_status: 'confirmed' }, 'Asia/Shanghai'), label);
});
