import test from 'node:test';
import assert from 'node:assert/strict';
import { relationRows, visibleRelations } from '../src/relation-view.js';

const goal = { id: 'g', type: 'linear', status: 'active' };
const node = (id, status = 'pending') => ({ id, goal_id: 'g', status });
const todo = (id, nodeId, extra = {}) => ({ id, status: 'pending',
  parent_refs: [{ goal_id: 'g', node_id: nodeId }], depends_on_todo_ids: [], ...extra });

test('cancelled child is hidden, revised chain follows dependencies rather than creation time', () => {
  const model = { goals: [goal], nodes: [node('n')], todos: [
    todo('obsolete', 'n', { status: 'cancelled' }),
    todo('sources', 'n', { created_at: '2026-10-10', depends_on_todo_ids: ['questions'] }),
    todo('questions', 'n', { created_at: '2026-10-10', depends_on_todo_ids: ['reread'] }),
    todo('reread', 'n', { created_at: '2026-10-12' }),
  ] };
  const before = JSON.stringify(model);
  assert.deepEqual(relationRows(model, goal)[0].todos.map(t => t.id), ['reread', 'questions', 'sources']);
  assert.equal(visibleRelations(model).todos.length, 3);
  assert.equal(JSON.stringify(model), before);
});

test('cancelled paths disappear while shared and completed tasks remain on visible paths', () => {
  const shared = todo('shared', 'gone', { parent_refs: [
    { goal_id: 'g', node_id: 'gone' }, { goal_id: 'g', node_id: 'kept' },
  ] });
  const model = { goals: [goal, { id: 'cancelled-goal', status: 'cancelled' }],
    nodes: [node('gone', 'cancelled'), node('kept'), { id: 'orphan', goal_id: 'cancelled-goal' }],
    todos: [shared, todo('hidden', 'gone'), todo('done', 'kept', { status: 'completed' }),
      todo('direct-gone', null, { status: 'cancelled' }),
      todo('standalone-gone', null, { status: 'cancelled', parent_refs: [] })] };
  const view = visibleRelations(model);
  assert.deepEqual(view.nodes.map(n => n.id), ['kept']);
  assert.deepEqual(relationRows(model, goal).map(r => r.object.id), ['kept']);
  assert.deepEqual(new Set(view.todos.map(t => t.id)), new Set(['shared', 'done']));
  assert.equal(view.standalone.length, 0);
});

test('cross-node dependencies and standalone dependencies precede DDL order', () => {
  const model = { goals: [goal], nodes: [node('later'), node('first')], todos: [
    todo('b', 'later', { depends_on_todo_ids: ['a'], deadline: '2026-10-12' }), todo('a', 'first'),
    todo('send', null, { parent_refs: [], depends_on_todo_ids: ['write'], deadline: '2026-10-12' }),
    todo('write', null, { parent_refs: [] }),
  ] };
  assert.deepEqual(relationRows(model, goal).map(r => r.object.id), ['first', 'later']);
  assert.deepEqual(visibleRelations(model).standalone.map(t => t.id), ['write', 'send']);
});

test('unrelated items use deadline then creation order; unavailable dependencies do not block display', () => {
  const model = { goals: [], nodes: [], todos: [
    todo('later', null, { parent_refs: [], deadline: '2026-10-15' }),
    todo('early', null, { parent_refs: [], deadline: '2026-10-12', depends_on_todo_ids: ['external'] }),
  ] };
  assert.deepEqual(visibleRelations(model).standalone.map(t => t.id), ['early', 'later']);
});

test('habit periods keep original grouping and identities while hiding cancelled occurrences', () => {
  const habit = { ...goal, type: 'habit', habit_rule: { unit: 'day', interval: 1, first_period_starts_on: '2026-10-12' } };
  const model = { goals: [habit], nodes: [], todos: [12, 13, 14].map(day => todo(`run-${day}`, null, {
    status: day === 13 ? 'cancelled' : 'pending',
    parent_refs: [{ goal_id: 'g', node_id: null, habit_period_starts_on: `2026-10-${day}`, habit_occurrence_index: 1 }],
  })) };
  const rows = relationRows(model, habit);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].object.title, '10/12 – 10/18');
  assert.deepEqual(rows[0].todos.map(t => t.id), ['run-12', 'run-14']);
  assert.equal(model.todos.length, 3);
});

test('node dependencies order empty rows and malformed cycles still render every object once', () => {
  const nodes = [node('later'), node('first')];
  nodes[0].depends_on_node_ids = ['first'];
  const model = { goals: [goal], nodes, todos: [] };
  assert.deepEqual(relationRows(model, goal).map(r => r.object.id), ['first', 'later']);
  nodes[1].depends_on_node_ids = ['later'];
  assert.equal(new Set(relationRows(model, goal).map(r => r.object.id)).size, 2);
});

import { todoScheduleDates } from '../src/relation-view.js';
test('schedule dates use actual entries, deduplicate days, retain past dates and summarize multiple days', () => {
  const t = { id: 'a', status: 'pending', execution_window: { starts_at: '2026-10-19T10:00:00+08:00' } };
  const model = { entryById: new Map() };
  assert.equal(todoScheduleDates(model, t), '未排期');
  const add = (id, date, endDate = date, localEndMinutes = 720, sourceType = 'todo') => model.entryById.set(id, { entityId: 'a', sourceType, date, endDate, localEndMinutes });
  add('1', '2026-10-19'); add('2', '2026-10-19');
  assert.equal(todoScheduleDates(model, t), '10/19');
  add('3', '2026-10-21');
  assert.equal(todoScheduleDates(model, t), '10/19、10/21');
  add('4', '2026-10-22');
  assert.equal(todoScheduleDates(model, t), '10/19 等 3 天');
  assert.equal(todoScheduleDates(model, { ...t, status: 'completed' }), '已完成');
  model.entryById.clear(); add('night', '2026-12-31', '2027-01-02', 0);
  assert.equal(todoScheduleDates(model, t), '2026/12/31、2027/1/1');
  model.entryById.clear(); add('fixed', '2026-10-19', '2026-10-19', 720, 'time_constraint');
  assert.equal(todoScheduleDates(model, t), '未排期');
});
