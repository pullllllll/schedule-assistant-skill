// 正式计划 → 视图模型。
// 这一层是纯数据换算，不依赖任何前端框架，将来可以原样搬到真实现里。
//
// 关键约束来自 plan-data-contract.md §9.5：
// ScheduleEntry 不保存标题、状态、归属、DDL，全部要回查 task_structure / time_constraints。

// Goal 颜色分配（HANDOFF-前端视觉编码 §5.2 过渡方案）：
// 权威顺序 = created_at + id 的稳定排序，与界面排序、过滤、显隐无关；
// 灰色（原 goal-07）保留给不可用时间，不再分配给 Goal。色板用完后循环。
export const GOAL_COLOR_KEYS = [
  "goal-01", "goal-02", "goal-03", "goal-04", "goal-05", "goal-06",
  "goal-08", "goal-09", "goal-10", "goal-11", "goal-12",
];

function assignGoalColors(goals) {
  const ranked = [...goals].sort((a, b) =>
    String(a.created_at || "").localeCompare(String(b.created_at || "")) || String(a.id).localeCompare(String(b.id)));
  const map = new Map();
  ranked.forEach((goal, index) => {
    const requested = goal.color_key && goal.color_key !== "goal-07" ? goal.color_key : null;
    map.set(goal.id, requested || GOAL_COLOR_KEYS[index % GOAL_COLOR_KEYS.length]);
  });
  return map;
}

const PRIORITY_ORDER = { P0: 0, P1: 1, P2: 2 };
const WEEKDAY = ["一", "二", "三", "四", "五", "六", "日"];

function zoned(date, timezone) {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone, hourCycle: "h23",
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
  const part = {};
  for (const item of formatter.formatToParts(date)) {
    if (item.type !== "literal") part[item.type] = item.value;
  }
  return {
    date: `${part.year}-${part.month}-${part.day}`,
    minutes: Number(part.hour) * 60 + Number(part.minute),
  };
}

const pad = value => String(value).padStart(2, "0");
const clock = minutes => `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`;
function eachDate(startsOn, endsOn) {
  const out = [];
  for (let d = new Date(`${startsOn}T00:00:00Z`); ; d.setUTCDate(d.getUTCDate() + 1)) {
    const iso = d.toISOString().slice(0, 10);
    out.push(iso);
    if (iso >= endsOn) break;
    if (out.length > 60) break;
  }
  return out;
}

function shiftDate(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

// 有效归属路径：只算 active Goal 与未完成/未取消的 Node（DS-008，2026-08-14）
function effectiveParents(todo, goals, nodes) {
  return (todo.parent_refs || []).flatMap(ref => {
    const goal = goals.get(ref.goal_id);
    if (!goal || goal.status !== "active") return [];
    const node = ref.node_id ? nodes.get(ref.node_id) : null;
    if (node && ["completed", "cancelled"].includes(node.status)) return [];
    return [{ ref, goal, node }];
  });
}

// 关系图：Goal → Node → Todo 三层，位置由数量推导，不写死坐标
export function buildGraph(model, scope, focusGoalId) {
  const goals = scope === "full" || !focusGoalId
    ? model.goals.filter(g => g.status === "active")
    : model.goals.filter(g => g.id === focusGoalId);
  const goalIds = new Set(goals.map(g => g.id));
  const nodes = model.nodes.filter(n => goalIds.has(n.goal_id));
  const nodeIds = new Set(nodes.map(n => n.id));
  const todos = model.todos.filter(t => {
    if (!t.parent_refs.length) return scope === "full";
    return t.parent_refs.some(r => goalIds.has(r.goal_id) && (!r.node_id || nodeIds.has(r.node_id)));
  });

  const columns = [
    { key: "goal", items: goals, label: "目标" },
    { key: "node", items: nodes, label: "节点" },
    { key: "todo", items: todos, label: "行动" },
  ];
  const rowHeight = 96;
  const rows = Math.max(1, ...columns.map(c => c.items.length));
  const height = rows * rowHeight + 40;

  const placed = [];
  columns.forEach((column, columnIndex) => {
    column.items.forEach((item, index) => {
      placed.push({
        id: item.id, layer: column.key,
        title: item.title,
        status: item.status,
        meta: column.key === "goal" ? `${item.priority} · ${item.type}` : (item.status || ""),
        shared: column.key === "todo" && item.parent_refs && item.parent_refs.length > 1,
        x: [6, 38, 70][columnIndex],
        y: ((index + 1) / (column.items.length + 1)) * 100,
      });
    });
  });

  const present = new Set(placed.map(p => p.id));
  const edges = [];
  for (const node of nodes) if (present.has(node.goal_id)) edges.push([node.goal_id, node.id]);
  for (const todo of todos) {
    for (const ref of todo.parent_refs) {
      const from = ref.node_id && present.has(ref.node_id) ? ref.node_id
        : (present.has(ref.goal_id) ? ref.goal_id : null);
      if (from) edges.push([from, todo.id]);
    }
  }
  return { nodes: placed, edges, height, counts: { goals: goals.length, nodes: nodes.length, todos: todos.length } };
}

export function buildModel(plan, now = new Date()) {
  const timezone = plan.plan_meta.timezone;
  const structure = plan.task_structure;
  const goals = new Map(structure.goals.map(g => [g.id, g]));
  const nodes = new Map(structure.nodes.map(n => [n.id, n]));
  const todos = new Map(structure.todos.map(t => [t.id, t]));
  const constraints = new Map(plan.time_constraints.map(c => [c.id, c]));
  const goalColors = assignGoalColors(structure.goals);
  const current = zoned(now, timezone);

  function actionability(todo, local, localEnd) {
    if (todo.status === "completed") return "已完成";
    if (todo.status === "cancelled") return "已取消";
    const blocked = (todo.depends_on_todo_ids || []).some(id => todos.get(id)?.status !== "completed");
    if (blocked) return "等待前置行动";
    if (local.date > current.date || (local.date === current.date && local.minutes > current.minutes)) return "未到安排时间";
    if (local.date < current.date || (local.date === current.date && localEnd.minutes <= current.minutes)) return "待核对进度";
    return todo.status === "in_progress" ? "正在进行" : "现在可行动";
  }

  function resolve(entry) {
    const start = new Date(entry.starts_at);
    const end = new Date(entry.ends_at);
    const local = zoned(start, timezone);
    const localEnd = zoned(end, timezone);
    // 同一自然日内结束才用真实分钟，跨日按当天 24:00 截断展示
    const endMinutes = localEnd.date === local.date ? localEnd.minutes : 24 * 60;
    const base = {
      id: entry.id,
      sourceType: entry.source_type,
      date: local.date,
      startMinutes: local.minutes,
      endMinutes,
      startLabel: clock(local.minutes),
      endLabel: clock(endMinutes),
      durationMinutes: endMinutes - local.minutes,
      rank: null, priorityReason: null, shared: false, parents: [], priority: null, status: null,
    };

    if (entry.source_type === "time_constraint") {
      const constraint = constraints.get(entry.time_constraint_id);
      const unavailable = constraint?.kind === "unavailable";
      return {
        ...base,
        entityId: entry.time_constraint_id,
        title: constraint ? constraint.title : "(未知固定安排)",
        entryKind: unavailable ? "unavailable" : "fixed",
        kindLabel: unavailable ? "不可用时间" : "固定安排",
        goalRefs: [], primaryColorKey: null,
        context: constraint ? constraint.context : null,
        constraintKind: constraint ? constraint.kind : null,
      };
    }

    const todo = todos.get(entry.todo_id);
    if (!todo) {
      return { ...base, entityId: entry.todo_id, title: "(未知行动)", entryKind: "action", kindLabel: "行动安排", goalRefs: [], primaryColorKey: null, context: null };
    }
    const parents = effectiveParents(todo, goals, nodes);
    const goalRefs = parents.map(p => ({
      goalId: p.goal.id, goalTitle: p.goal.title, goalType: p.goal.type, goalPriority: p.goal.priority,
      colorKey: goalColors.get(p.goal.id), nodeTitle: p.node ? p.node.title : null,
    }));
    const shared = goalRefs.length > 1;
    const priority = parents.length
      ? parents.map(p => p.goal.priority).sort((a, b) => PRIORITY_ORDER[a] - PRIORITY_ORDER[b])[0]
      : null;
    return {
      ...base,
      entityId: todo.id,
      title: todo.title,
      // entryKind 决定日程形态；颜色只来自 Goal 归属（共享用中性 + 多段色条）
      entryKind: "action",
      kindLabel: goalRefs.length ? "行动安排" : "独立行动",
      status: todo.status,
      priority,
      rank: entry.daily_rank,
      priorityReason: entry.priority_reason,
      rankingException: entry.ranking_exception,
      actionability: actionability(todo, local, localEnd),
      shared,
      goalRefs,
      primaryColorKey: !shared && goalRefs.length ? goalRefs[0].colorKey : null,
      parents: goalRefs.map(p => ({ goalTitle: p.goalTitle, goalType: p.goalType, goalPriority: p.goalPriority, nodeTitle: p.nodeTitle, colorKey: p.colorKey })),
      context: todo.context,
      remainingMinutes: todo.remaining_estimate_minutes,
      deadline: todo.deadline,
    };
  }

  const entries = plan.schedule.entries.map(resolve);
  const byDate = new Map();
  for (const entry of entries) {
    if (!byDate.has(entry.date)) byDate.set(entry.date, []);
    byDate.get(entry.date).push(entry);
  }
  for (const list of byDate.values()) list.sort((a, b) => a.startMinutes - b.startMinutes);

  // 周视图始终保留完整 24 小时骨架。可用时间仍约束排期，但不再裁掉时间轴。
  const gridStart = 0;
  const gridEnd = 24 * 60;
  const slots = 48;

  const declaredWindow = plan.schedule.planning_window;
  const today = zoned(now, timezone).date;
  // 还没生成过正式周计划时，用当前自然周当骨架，页面仍然可读
  const mondayOffset = (new Date(`${today}T00:00:00Z`).getUTCDay() + 6) % 7;
  const window = declaredWindow || {
    starts_on: shiftDate(today, -mondayOffset),
    ends_on: shiftDate(today, 6 - mondayOffset),
  };
  const nowMinutes = zoned(now, timezone).minutes;

  function buildDays(startsOn, endsOn) {
    return eachDate(startsOn, endsOn).map(date => {
      const weekday = (new Date(`${date}T00:00:00Z`).getUTCDay() + 6) % 7;
      return {
        date, weekdayLabel: WEEKDAY[weekday], dayNumber: Number(date.slice(8, 10)),
        isToday: date === today, entries: byDate.get(date) || [],
      };
    });
  }

  const mondayOf = iso => shiftDate(iso, -((new Date(`${iso}T00:00:00Z`).getUTCDay() + 6) % 7));
  const weekDaysFor = anchor => {
    const monday = mondayOf(anchor);
    return buildDays(monday, shiftDate(monday, 6));
  };
  const weekDays = buildDays(window.starts_on, window.ends_on);

  // 今天不在当前窗口内时（样例日期过期），退回窗口内最后一天并标注
  const todayInWindow = weekDays.some(d => d.date === today);
  const focusDate = todayInWindow ? today : (weekDays.length ? weekDays[weekDays.length - 1].date : today);

  const focusEntries = byDate.get(focusDate) || [];
  const focusTodos = focusEntries.filter(e => e.sourceType === "todo")
    .sort((a, b) => a.rank - b.rank);

  return {
    timezone,
    hasWindow: Boolean(declaredWindow),
    isEmpty: !structure.goals.length && !structure.todos.length && !plan.time_constraints.length,
    counts: {
      goals: structure.goals.length, nodes: structure.nodes.length,
      todos: structure.todos.length, entries: entries.length,
      unscheduled: structure.todos.filter(t =>
        !["completed", "cancelled"].includes(t.status) &&
        !entries.some(e => e.entityId === t.id && e.sourceType === "todo")).length,
    },
    version: plan.plan_meta.plan_version,
    updatedAt: plan.plan_meta.updated_at,
    window,
    grid: { startMinutes: gridStart, endMinutes: gridEnd, slots },
    hourLabels: Array.from({ length: (gridEnd - gridStart) / 60 + 1 },
      (_, i) => ({ slot: i * 2, label: clock(gridStart + i * 60) })),
    weekDays, weekDaysFor, mondayOf,
    entriesOn(date) { return byDate.get(date) || []; },
    // 归属路径说明；无 Goal / Node 归属时不补类型占位文案。
    parentLabel(entry) {
      if (!entry.parents?.length) return "";
      return entry.parents.map(p => p.nodeTitle ? `${p.goalTitle} › ${p.nodeTitle}` : p.goalTitle).join("　·　");
    },
    today, todayInWindow, focusDate, focusEntries, focusTodos,
    nowMinutes,
    goals: structure.goals, nodes: structure.nodes, todos: structure.todos,
    entryById: new Map(entries.map(e => [e.id, e])),
    structureById: new Map([
      ...structure.goals.map(g => [g.id, { kind: "goal", object: g }]),
      ...structure.nodes.map(n => [n.id, { kind: "node", object: n }]),
      ...structure.todos.map(t => [t.id, { kind: "todo", object: t }]),
    ]),
    goalById: goals, nodeById: nodes, todoById: todos,
    colorKeyOf(goalId) { return goalColors.get(goalId) ?? null; },
    // 依赖高亮：前置与后继都靠反向扫描得到，合同规定不保存反向列表
    blockersOf(todoId) {
      const todo = todos.get(todoId);
      return todo ? todo.depends_on_todo_ids : [];
    },
    blockedBy(todoId) {
      return structure.todos.filter(t => t.depends_on_todo_ids.includes(todoId)).map(t => t.id);
    },
    weekOf(date) {
      const monday = shiftDate(date, -((new Date(`${date}T00:00:00Z`).getUTCDay() + 6) % 7));
      return { starts_on: monday, ends_on: shiftDate(monday, 6) };
    },
    entriesOfTodo(todoId) { return entries.filter(e => e.sourceType === "todo" && e.entityId === todoId); },
    effectiveParentsOf(todo) {
      return effectiveParents(todo, goals, nodes).map(p => ({
        goalId: p.goal.id, nodeId: p.node ? p.node.id : null,
        goalTitle: p.goal.title, goalType: p.goal.type, goalPriority: p.goal.priority,
        nodeTitle: p.node ? p.node.title : null,
      }));
    },
    slotOf(minutes) { return (minutes - gridStart) / 30; },
    clock,
  };
}
