import { habitDisplayGroups } from './plan-model.js';

const byDeadline = (a, b) =>
  (a.deadline ? Date.parse(a.deadline) : Infinity) - (b.deadline ? Date.parse(b.deadline) : Infinity) ||
  String(a.created_at || '').localeCompare(String(b.created_at || '')) || String(a.id).localeCompare(String(b.id));

// 在同一展示集合内按依赖排序；外部依赖不生成额外卡片。
function dependencyOrder(items, dependencies, compare = byDeadline) {
  const remaining = new Map(items.map(item => [item.id, item]));
  const result = [];
  while (remaining.size) {
    const ready = [...remaining.values()].filter(item =>
      !dependencies(item).some(id => remaining.has(id)));
    // 非法循环不使只读页面挂起；正式数据的合法性由提交校验负责。
    if (!ready.length) return [...result, ...[...remaining.values()].sort(compare)];
    const next = ready.sort(compare)[0];
    result.push(next);
    remaining.delete(next.id);
  }
  return result;
}

// 仅派生当前任务视图，不删源对象，也不影响日程历史的回查。
export function visibleRelations(model) {
  const goals = model.goals.filter(g => g.status !== 'cancelled');
  const goalIds = new Set(goals.map(g => g.id));
  const nodes = model.nodes.filter(n => n.status !== 'cancelled' && goalIds.has(n.goal_id));
  const nodeById = new Map(nodes.map(n => [n.id, n]));
  const todos = dependencyOrder(model.todos.filter(t => t.status !== 'cancelled' && (
    !t.parent_refs.length || t.parent_refs.some(r => goalIds.has(r.goal_id) &&
      (!r.node_id || nodeById.get(r.node_id)?.goal_id === r.goal_id))
  )), t => t.depends_on_todo_ids || []);
  return { goals, nodes, todos, standalone: todos.filter(t => !t.parent_refs.length) };
}

export function relationRows(model, goal) {
  const visible = visibleRelations(model);
  if (!visible.goals.some(g => g.id === goal.id)) return [];
  const belongs = (todo, nodeId) => todo.parent_refs.some(r => r.goal_id === goal.id && r.node_id == nodeId);
  const direct = visible.todos.filter(t => belongs(t, null));
  if (goal.type === 'habit') {
    return habitDisplayGroups(goal, direct).map(group => ({
      kind: 'period', object: { id: group.id, title: group.title, status: 'pending' }, todos: group.todos,
    }));
  }
  const rows = [
    ...visible.nodes.filter(n => n.goal_id === goal.id).map(object => ({
      kind: 'node', object, todos: visible.todos.filter(t => belongs(t, object.id)),
    })),
    ...direct.map(object => ({ kind: 'todo', object, todos: [] })),
  ].map(row => ({ ...row, id: `${row.kind}:${row.object.id}` }));
  // 将跨行的 Todo 依赖映射到所属行，保持节点分行、行动横排的现有布局。
  const owners = new Map();
  for (const row of rows) {
    for (const todo of row.kind === 'todo' ? [row.object] : row.todos) {
      owners.set(todo.id, [...(owners.get(todo.id) || []), row.id]);
    }
  }
  return dependencyOrder(rows, row => {
    const nodeDeps = (row.object.depends_on_node_ids || []).map(id => `node:${id}`);
    const todos = row.kind === 'todo' ? [row.object] : row.todos;
    const todoDeps = todos.flatMap(t => (t.depends_on_todo_ids || []).flatMap(id => owners.get(id) || []));
    return [...nodeDeps, ...todoDeps].filter(id => id !== row.id);
  }, (a, b) => byDeadline(a.object, b.object));
}

// 日期来自正式日程；不以执行窗口或当前时钟推断已排期、已完成。
export function todoScheduleDates(model, todo) {
  if (todo.status === 'completed') return '已完成';
  const dates = new Set();
  for (const entry of model.entryById.values()) {
    if (entry.sourceType !== 'todo' || entry.entityId !== todo.id) continue;
    for (let day = entry.date; day <= entry.endDate;) {
      if (day !== entry.endDate || entry.localEndMinutes > 0) dates.add(day);
      const next = new Date(`${day}T00:00:00Z`);
      next.setUTCDate(next.getUTCDate() + 1);
      day = next.toISOString().slice(0, 10);
    }
  }
  const days = [...dates].sort();
  if (!days.length) return '未排期';
  const crossYear = new Set(days.map(d => d.slice(0, 4))).size > 1;
  const label = day => `${crossYear ? day.slice(0, 4) + '/' : ''}${Number(day.slice(5, 7))}/${Number(day.slice(8, 10))}`;
  return days.length <= 2 ? days.map(label).join('、') : `${label(days[0])} 等 ${days.length} 天`;
}
