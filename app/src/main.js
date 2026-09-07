import "./styles.css";
import { buildModel } from "./plan-model.js";

const PLAN_URL = "/plan.json";
const POLL_MS = 5000;
const AUTO_FIT_MIN_ZOOM = 0.6;

const params = new URLSearchParams(location.search);
const initialRelationMode = params.get("relations") === "focus" ? "focus" : "canvas";
const relationCanvasStates = {
  canvas: { x: 36, y: 28, zoom: 0.72, fitted: false, viewportWidth: null, viewportHeight: null },
  focus: { x: 36, y: 28, zoom: 0.72, fitted: false, viewportWidth: null, viewportHeight: null },
};
const state = {
  page: params.get("page") === "relations" ? "relations" : "schedule",  // 页面：日程 / 关系
  view: params.get("view") === "week" ? "week" : "day",   // 日程内的视图维度：日 / 周
  anchorDate: null,                                        // 时间维度：正在看哪一天
  showPriorities: params.get("priorities") === "1",
  selected: null,
  drawerOpen: false,
  collapsedGoals: new Set(),
  relationInitialized: false,
  relationFocusGoal: null,
  relationFreshGoal: null,
  relationPreviousLayout: null,
  relationMode: initialRelationMode,
  relationFocusKind: "goal",
  relationFocusId: null,
  canvas: relationCanvasStates[initialRelationMode],
  highlightGoal: null,
  status: "loading",
  error: null,
  planSource: "official",
  refreshing: false,
  returnFocus: null,
  weekScrollKey: null,
};

let model = null;
const app = document.querySelector("#app");
const esc = v => String(v ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const STATUS_LABEL = { pending: "待开始", in_progress: "进行中", completed: "已完成", cancelled: "已取消", active: "进行中", paused: "已暂停" };
const TYPE_LABEL = { linear: "线性目标", recurring: "循环目标", habit: "习惯目标" };
const WEEKDAY = ["一", "二", "三", "四", "五", "六", "日"];
const scheduleEntryLabel = entry => entry.kindLabel;
// 颜色 token 注入：彩色只表达 Goal 归属；独立行动与固定安排用白底深色，不可用时间使用保留灰色。
const toneVars = key => key
  ? `--tone:var(--${key}-stroke);--tone-soft:var(--${key}-soft);--tone-ink:var(--${key}-ink)`
  : "--tone:var(--goal-neutral-stroke);--tone-soft:var(--goal-neutral-soft);--tone-ink:var(--goal-neutral-ink)";
const entryToneVars = entry => entry.primaryColorKey ? toneVars(entry.primaryColorKey)
  : entry.entryKind === "unavailable"
    ? "--tone:var(--unavailable-stroke);--tone-soft:var(--unavailable-soft);--tone-ink:var(--unavailable-ink)"
    : toneVars(null);
const entryStroke = entry => entry.primaryColorKey ? `var(--${entry.primaryColorKey}-stroke)`
  : entry.entryKind === "unavailable" ? "var(--unavailable-stroke)" : "var(--goal-neutral-stroke)";
const goalToneVars = key => key ? `--goal-tone:var(--${key}-stroke);--goal-tone-ink:var(--${key}-ink)` : "";
const goalBars = refs => `<i class="goalbars" aria-hidden="true">${refs.slice(0, 2)
  .map(ref => `<b style="--bar:var(--${ref.colorKey}-stroke)"></b>`).join("")}</i>`;

const icon = name => {
  const paths = {
    prev: '<path d="m15 18-6-6 6-6"/>', next: '<path d="m9 18 6-6-6-6"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>',
    check: '<path d="m5 12 4 4L19 6"/>', arrow: '<path d="m9 18 6-6-6-6"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    share: '<circle cx="7" cy="12" r="3"/><circle cx="17" cy="12" r="3"/>',
    plus: '<path d="M12 5v14M5 12h14"/>', minus: '<path d="M5 12h14"/>',
    fit: '<path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    network: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="7" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="m8.3 7 7.2-.1M7.4 8.2l3.4 7.5m5.7-6.6-3.2 6.6"/>',
    route: '<circle cx="6" cy="19" r="3"/><path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15"/><circle cx="18" cy="5" r="3"/>',
    refresh: '<path d="M20 11a8.1 8.1 0 0 0-14.8-4M4 4v5h5M4 13a8.1 8.1 0 0 0 14.8 4M20 20v-5h-5"/>',
    sprout: '<path d="M7 20h10M10 20c5.5-2.5.8-6.4 3-10M9.5 9.4c1.2.7 2.1 1.6 2.8 2.6-1.8.3-3.4 0-4.6-.7-1.3-.8-2.1-2.1-2.3-4 1.7-.1 3 .5 4.1 2.1ZM14.1 6.1c-1 1-1.6 2.2-1.7 3.5 1.8-.2 3.2-.9 4.1-1.9 1-1 1.5-2.5 1.2-4.2-1.5.2-2.7 1.1-3.6 2.6Z"/>',
    unlink: '<path d="m18.8 5.2-.9.9M6.2 17.8l-.9.9M8.5 8.5l7 7M5.5 13.5l-1.1 1.1a4 4 0 0 0 5.7 5.7l2.4-2.4M18.5 10.5l1.1-1.1a4 4 0 0 0-5.7-5.7l-2.4 2.4"/>',
    waypoints: '<circle cx="12" cy="4.5" r="2.5"/><circle cx="5" cy="19.5" r="2.5"/><circle cx="19" cy="19.5" r="2.5"/><path d="M12 7v4.5M7.3 18l3.3-4.1a1.8 1.8 0 0 1 2.8 0l3.3 4.1"/>',
    pin: '<path d="M12 17v5M5 17h14M7 17l1-8-3-3h14l-3 3 1 8"/>',
    lock: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
    panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16M8 9h3M8 13h3"/>',
  };
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name]}</svg>`;
};

const scheduleTypeIcon = entry => entry.entryKind === "unavailable" ? "lock"
  : entry.entryKind === "fixed" ? "pin"
  : entry.shared ? "waypoints"
  : !entry.primaryColorKey ? "unlink"
  : entry.goalRefs[0]?.goalType === "habit" ? "sprout"
  : entry.goalRefs[0]?.goalType === "recurring" ? "refresh" : "route";

const isSelected = (kind, id) => state.selected?.kind === kind && state.selected?.id === id;
const shiftDate = (iso, days) => { const d = new Date(`${iso}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + days); return d.toISOString().slice(0, 10); };
const weekdayOf = iso => WEEKDAY[(new Date(`${iso}T00:00:00Z`).getUTCDay() + 6) % 7];
const daysBetween = (from, to) => Math.round((new Date(`${to}T00:00:00Z`) - new Date(`${from}T00:00:00Z`)) / 86400000);
const isWithinWindow = date => date >= model.window.starts_on && date <= model.window.ends_on;
const clampToWindow = date => date < model.window.starts_on ? model.window.starts_on
  : date > model.window.ends_on ? model.window.ends_on : date;
// planning_window 表示当前周；Schedule 常态只展示它与紧邻的上一自然周。
const weekBrowseBounds = () => {
  const currentMonday = model.mondayOf(model.window.starts_on);
  return { startsOn: shiftDate(currentMonday, -7), endsOn: shiftDate(currentMonday, 6) };
};
const isWeekWithinBrowseRange = anchor => {
  const week = model.weekDaysFor(anchor);
  const bounds = weekBrowseBounds();
  return week[0].date >= bounds.startsOn && week.at(-1).date <= bounds.endsOn;
};
const clampToWeekBrowseRange = date => {
  const bounds = weekBrowseBounds();
  const monday = model.mondayOf(date);
  if (monday < bounds.startsOn) return bounds.startsOn;
  if (monday > shiftDate(bounds.endsOn, -6)) return shiftDate(bounds.endsOn, -6);
  return date;
};
const canStep = delta => {
  const target = shiftDate(state.anchorDate, state.view === "day" ? delta : delta * 7);
  if (state.view === "day") return isWithinWindow(target);
  return isWeekWithinBrowseRange(target);
};
const relativeWeekLabel = anchor => {
  const offset = Math.round(daysBetween(model.mondayOf(model.window.starts_on), model.mondayOf(anchor)) / 7);
  if (offset === 0) return "本周";
  if (offset === -1) return "上周";
  if (offset === 1) return "下周";
  return offset < 0 ? `${Math.abs(offset)} 周前` : `${offset} 周后`;
};
/* ── 顶栏：应用级，贴边满宽 ─────────────────────────────── */
function topbar() {
  const days = model.weekDaysFor(state.anchorDate);
  const label = state.view === "day"
    ? `${Number(state.anchorDate.slice(5, 7))} 月 ${Number(state.anchorDate.slice(8))} 日 · 周${weekdayOf(state.anchorDate)}`
    : `${days[0].date.slice(5).replace("-", "/")} – ${days.at(-1).date.slice(5).replace("-", "/")}`;
  const isNow = state.view === "day" ? state.anchorDate === model.today
    : days.some(d => d.date === model.today);
  const periodLabel = state.view === "week" ? relativeWeekLabel(state.anchorDate) : "今天";
  const nav = `<nav class="pagenav" aria-label="页面">
      <button class="${state.page === "schedule" ? "is-active" : ""}" data-page="schedule" ${state.page === "schedule" ? 'aria-current="page"' : ""}>${icon("calendar")}日程</button>
      <button class="${state.page === "relations" ? "is-active" : ""}" data-page="relations" ${state.page === "relations" ? 'aria-current="page"' : ""}>${icon("network")}任务</button>
    </nav>`;
  const brand = `<div class="brand-lockup"><span class="brand-mark">今</span><strong>日程小助手</strong></div>`;
  const right = `<button class="icon-button refresh-button ${state.refreshing ? "is-busy" : ""}" data-retry aria-label="重新读取正式计划">↻</button>`;
  if (state.page === "relations") {
    const goals = model.goals.filter(goal => goal.status !== "cancelled");
    return `<header class="topbar relation-topbar">
      <div class="topbar-left relation-topbar-left">${brand}${nav}
        <span class="relation-counts"><strong>${goals.length}</strong> 个目标 · <strong>${model.nodes.length}</strong> 个节点 · <strong>${model.todos.length}</strong> 个行动</span>
      </div>
      ${relationModeSwitch()}
      <div class="topbar-right relation-topbar-right">${relationCanvasActions()}${right}</div>
    </header>`;
  }
  return `
    <header class="topbar">
      <div class="topbar-left">
        ${brand}${nav}
        <div class="timenav">
          <button class="icon-button" data-step="-1" aria-label="上一${state.view === "day" ? "天" : "周"}" ${canStep(-1) ? "" : 'disabled aria-disabled="true"'}>${icon("prev")}</button>
          <button class="today-button ${isNow ? "is-now" : ""}" data-today>${periodLabel}</button>
          <button class="icon-button" data-step="1" aria-label="下一${state.view === "day" ? "天" : "周"}" ${canStep(1) ? "" : 'disabled aria-disabled="true"'}>${icon("next")}</button>
        </div>
        <strong class="timelabel">${label}</strong>
      </div>
      <div class="viewswitch" role="group" aria-label="视图">
        <button class="${state.view === "day" ? "is-active" : ""}" data-view="day" aria-pressed="${state.view === "day"}">日</button>
        <button class="${state.view === "week" ? "is-active" : ""}" data-view="week" aria-pressed="${state.view === "week"}">周</button>
      </div>
      <div class="topbar-right">
        <button class="toggle-button ${state.showPriorities ? "is-on" : ""}" data-panel="priorities" aria-pressed="${state.showPriorities}" aria-expanded="${state.showPriorities}" aria-controls="priorities-panel">${icon("list")}今日关注顺序</button>
        ${right}
      </div>
    </header>`;
}

/* ── 日视图：时间脊柱铺满可用宽度 ───────────────────────── */
function dayView() {
  const entries = model.entriesOn(state.anchorDate);
  const days = model.weekDaysFor(state.anchorDate);
  let previousEnd = entries.length ? entries[0].startMinutes : 0;
  const rows = entries.map((entry, index) => {
    const gapMinutes = index ? Math.max(0, entry.startMinutes - previousEnd) : 0;
    const gapHeight = index ? Math.min(112, Math.max(14, Math.round(gapMinutes * 0.18))) : 0;
    const nodeHeight = Math.min(94, Math.max(52, Math.round(entry.durationMinutes * 0.8)));
    previousEnd = Math.max(previousEnd, entry.endMinutes);
    const extraGoals = entry.shared && entry.goalRefs.length > 2 ? ` +${entry.goalRefs.length - 2}` : "";
    return `<li style="--gap:${gapHeight}px;--node-h:${nodeHeight}px">
      <button class="timeline-row kind-${entry.entryKind} ${isSelected("entry", entry.id) ? "is-selected" : ""}" data-entry="${entry.id}" style="${entryToneVars(entry)}">
        <time>${entry.startLabel}<small>${entry.endLabel}</small></time>
        <span class="timeline-node" aria-hidden="true">${entry.shared ? goalBars(entry.goalRefs) : ""}${icon(scheduleTypeIcon(entry))}</span>
        <span class="timeline-copy">
          <small class="timeline-meta">${esc(scheduleEntryLabel(entry))}${entry.priority ? " · " + entry.priority : ""}${entry.shared ? " · 共享" + extraGoals : ""}</small>
          <strong>${esc(entry.title)}</strong>
          ${model.parentLabel(entry) ? `<span class="timeline-parent">${esc(model.parentLabel(entry))}</span>` : ""}
          <small class="timeline-duration">${entry.startLabel}–${entry.endLabel} · ${entry.durationMinutes} 分钟</small>
        </span>
      </button>
    </li>`;
  }).join("");
  return `
    <section class="main-view day-view" aria-label="当日时间轴">
      <div class="daystrip">
        ${days.map(d => `<button class="daystrip-cell ${d.date === state.anchorDate ? "is-current" : ""} ${d.date === model.today ? "is-today" : ""}" data-goto="${d.date}" aria-label="${d.date} 周${d.weekdayLabel}" ${d.date === state.anchorDate ? 'aria-current="date"' : ""} ${isWithinWindow(d.date) ? "" : 'disabled aria-disabled="true"'}>
            <span>周${d.weekdayLabel}</span><strong>${d.dayNumber}</strong>
            <i>${model.entriesOn(d.date).slice(0, 4).map(e => `<b style="background:${entryStroke(e)}"></b>`).join("")}</i>
          </button>`).join("")}
      </div>
      <div class="timeline-scroll">
        ${entries.length ? `<ol class="timeline">${rows}</ol>
        <p class="timeline-tail"><span>${entries.at(-1).endLabel} 之后留白 · 给变化留一点余地</span></p>`
        : `<p class="view-empty">这一天没有已排期条目。</p>`}
      </div>
    </section>`;
}

/* ── 周视图：占满高度，装不下才滚动 ─────────────────────── */
function weekView() {
  const days = model.weekDaysFor(state.anchorDate);
  const total = days.reduce((n, d) => n + d.entries.length, 0);
  const todayIndex = days.findIndex(d => d.date === model.today);
  const highlightTitle = state.highlightGoal ? model.goalById.get(state.highlightGoal)?.title : null;
  const cards = days.flatMap((day, i) => day.entries.map(entry => {
    const dim = highlightTitle && !entry.parents?.some(p => p.goalTitle === highlightTitle);
    const slot = Math.max(0, model.slotOf(entry.startMinutes));
    const span = Math.max(1, Math.round(entry.durationMinutes / 30));
    const kindClass = entry.entryKind !== "action" ? `card-${entry.entryKind}`
      : entry.shared ? "card-shared" : entry.primaryColorKey ? "card-action" : "card-standalone";
    const densityClass = span <= 1 ? "is-compact" : span === 2 ? "is-standard" : "is-roomy";
    const typeIcon = scheduleTypeIcon(entry);
    const parentLabel = model.parentLabel(entry);
    return `<button class="schedule-card ${kindClass} ${densityClass} ${isSelected("entry", entry.id) ? "is-selected" : ""} ${dim ? "is-dimmed" : ""}"
        data-entry="${entry.id}" aria-label="${esc(`${entry.startLabel} ${entry.title} ${scheduleEntryLabel(entry)}`)}" style="--day:${i + 1};--start:${slot};--span:${span};${entryToneVars(entry)}">
        ${entry.shared ? goalBars(entry.goalRefs) : ""}
        <span class="card-head"><i class="card-kind-icon" aria-hidden="true">${icon(typeIcon)}</i><span class="card-time">${entry.startLabel}</span></span>
        <strong>${esc(entry.title)}</strong>
        ${parentLabel ? `<small>${esc(parentLabel)}</small>` : ""}
      </button>`;
  })).join("");
  return `
    <section class="main-view week-view" aria-label="周日程">
      ${highlightTitle ? `<div class="filter-bar">正在高亮「${esc(highlightTitle)}」的时间块 <button data-locate="clear">显示全部</button></div>` : ""}
      <div class="calendar-scroll">
        <div class="week-calendar" style="--slots:${model.grid.slots}">
          <div class="corner">${esc(model.timezone.split("/")[1] || model.timezone)}</div>
          ${days.map(d => `<div class="day-head ${d.date === model.today ? "is-today" : ""}"><span>周${d.weekdayLabel}</span><strong>${d.dayNumber}</strong></div>`).join("")}
          ${model.hourLabels.map((h, i) => `<div class="hour ${i === model.hourLabels.length - 1 ? "is-edge" : ""}" style="--slot:${h.slot}">${h.label}</div>`).join("")}
          ${Array.from({ length: model.grid.slots }, (_, i) => `<div class="grid-line ${i % 2 === 0 ? "is-hour" : ""}" style="--slot:${i}"></div>`).join("")}
          ${todayIndex >= 0 && model.nowMinutes >= model.grid.startMinutes && model.nowMinutes <= model.grid.endMinutes
            ? `<div class="now-line" style="--day-index:${todayIndex};--now-slot:${model.slotOf(model.nowMinutes)}"><span>现在 ${model.clock(model.nowMinutes)}</span></div>` : ""}
          ${cards}
        </div>
      </div>
      ${total === 0 ? `<p class="view-empty">${model.hasWindow ? "这一周没有任何已排期条目。"
        : `正式计划还没有生成过周日程。当前有 ${model.counts.todos} 个行动，其中 ${model.counts.unscheduled} 个尚未排期。`}</p>` : ""}
    </section>`;
}

/* ── 关注顺序面板：按钮触发，不常驻 ─────────────────────── */
function prioritiesPanel() {
  const list = model.entriesOn(state.anchorDate).filter(e => e.sourceType === "todo")
    .sort((a, b) => (a.rank ?? Number.POSITIVE_INFINITY) - (b.rank ?? Number.POSITIVE_INFINITY) || a.startMinutes - b.startMinutes);
  return `
    <aside class="side-panel" id="priorities-panel" aria-label="今日关注顺序">
      <div class="panel-head">
        <strong>今日关注顺序</strong>
        <button class="icon-button" data-panel="priorities" aria-label="收起">${icon("close")}</button>
      </div>
      <ol class="priority-list">
        ${list.length ? list.map(e => `<li class="${e.status === "completed" ? "is-done" : ""} ${e.status === "in_progress" ? "is-current" : ""}">
          <button data-entry="${e.id}"><span class="rank">${e.rank ?? "—"}</span>
            <span class="body"><strong>${esc(e.title)}</strong><small>${e.startLabel}–${e.endLabel} · ${esc(e.actionability)}</small>
              <small class="priority-reason">${e.rankingException ? "你指定的顺序 · " : ""}${esc(e.priorityReason || "同等条件下按稳定顺序排列")}</small></span></button></li>`).join("")
        : `<li class="is-empty">这一天没有已排期的行动</li>`}
      </ol>
    </aside>`;
}

/* ── 关系面板：底部展开，与日程同屏 ─────────────────────── */
/* ── 详情抽屉 ───────────────────────────────────────────── */
const ddlTime = object => object?.deadline ? new Date(object.deadline).getTime() : Number.POSITIVE_INFINITY;
const byDDL = (a, b) => ddlTime(a) - ddlTime(b) || String(a.created_at || "").localeCompare(String(b.created_at || ""));
const shortDate = value => value ? new Intl.DateTimeFormat("zh-CN", {
  timeZone: model.timezone, month: "numeric", day: "numeric",
}).format(new Date(value)) : "未设 DDL";
const periodLabel = startsOn => {
  if (!startsOn) return "未分期";
  const week = model.weekOf(startsOn);
  return `${week.starts_on.slice(5).replace("-", "/")} – ${week.ends_on.slice(5).replace("-", "/")}`;
};

function cleanCircle(object, kind, { meta = "", expandable = false, visualOnly = false } = {}) {
  const done = object.status === "completed";
  const shared = kind === "todo" && object.parent_refs?.length > 1;
  const independent = kind === "todo" && !object.parent_refs?.length;
  const status = STATUS_LABEL[object.status] || object.status || "";
  const identity = kind === "goal"
    ? `${icon(object.type === "habit" ? "sprout" : object.type === "recurring" ? "refresh" : "route")}<span>${esc(TYPE_LABEL[object.type] || "目标")}</span>`
    : kind === "node"
      ? "<span>节点</span>"
      : kind === "period"
        ? "<span>习惯周期</span>"
        : shared
          ? `${icon("share")}<span>共享行动</span>`
          : independent
            ? `${icon("unlink")}<span>独立行动</span>`
            : "<span>行动</span>";
  const derivedMeta = kind === "goal"
    ? [object.priority, status].filter(Boolean).join(" · ")
    : kind === "period"
      ? status
      : [status, object.deadline ? `DDL ${shortDate(object.deadline)}` : ""].filter(Boolean).join(" · ");
  const visibleMeta = meta || derivedMeta;
  const attrs = expandable
    ? `data-expand-goal="${object.id}" aria-expanded="${!state.collapsedGoals.has(object.id)}"`
    : visualOnly ? "" : `data-object="${kind}:${object.id}"`;
  return `<button class="relation-circle circle-${kind} ${done ? "is-done" : ""} ${shared ? "is-shared" : ""} ${isSelected(kind, object.id) ? "is-active" : ""}"
      ${attrs} title="${esc(object.title)}">
      <span class="circle-identity">${identity}</span>
      <strong>${esc(object.title)}</strong>
      ${visibleMeta ? `<small class="circle-meta">${esc(visibleMeta)}</small>` : ""}
    </button>`;
}

function todoCircle(todo) {
  return cleanCircle(todo, "todo");
}

function goalDetailTrigger(goal) {
  return `<button class="goal-detail-trigger" data-goal-detail="${goal.id}"
      aria-label="查看“${esc(goal.title)}”详情" title="查看详情" tabindex="-1">${icon("panel")}</button>`;
}

function cleanTreeData(goal) {
  const nodes = model.nodes.filter(node => node.goal_id === goal.id).sort(byDDL);
  const directTodos = model.todos.filter(todo => todo.parent_refs.some(ref => ref.goal_id === goal.id && ref.node_id == null));
  const rows = [];
  if (goal.type === "habit") {
    const periods = new Map();
    for (const todo of directTodos) {
      const ref = todo.parent_refs.find(item => item.goal_id === goal.id);
      const key = ref?.habit_period_starts_on || "none";
      if (!periods.has(key)) periods.set(key, []);
      periods.get(key).push(todo);
    }
    for (const [startsOn, todos] of [...periods.entries()].sort(([a], [b]) => a.localeCompare(b))) {
      rows.push({
        kind: "period",
        object: { id: `${goal.id}-${startsOn}`, title: startsOn === "none" ? "当前周期" : periodLabel(startsOn), status: "pending" },
        todos: todos.sort(byDDL),
      });
    }
  } else {
    const main = [
      ...nodes.map(object => ({ kind: "node", object })),
      ...directTodos.map(object => ({ kind: "todo", object })),
    ].sort((a, b) => byDDL(a.object, b.object));
    for (const item of main) {
      if (item.kind === "node") {
        rows.push({ ...item,
          todos: model.todos.filter(todo => todo.parent_refs.some(ref => ref.goal_id === goal.id && ref.node_id === item.object.id)).sort(byDDL),
        });
      } else rows.push({ ...item, todos: [] });
    }
  }
  const maxTodos = Math.max(0, ...rows.map(row => row.todos.length));
  // Goal 保持圆形；Node 使用横向圆角矩形；Todo 使用胶囊。这里按真实形状
  // 预留横向空间，避免只改 CSS 后长标题卡片与后续行动、连线互相挤压。
  return { rows, width: Math.max(620, 320 + maxTodos * 220), height: Math.max(320, 188 + rows.length * 146) };
}

function cleanTree(goal, placement, { expandable = true } = {}) {
  const data = cleanTreeData(goal);
  const lastCenter = data.rows.length ? 188 + (data.rows.length - 1) * 146 + 56 : 144;
  const finalElbowHeight = 24;
  return `<section class="relation-tree ${state.relationFreshGoal === goal.id ? "is-fresh" : ""}" data-layout-id="goal:${goal.id}" style="--tree-x:${placement.x}px;--tree-y:${placement.y}px;--tree-w:${data.width}px;--tree-h:${data.height}px;${goalToneVars(model.colorKeyOf(goal.id))}">
    ${cleanCircle(goal, "goal", { expandable })}
    ${expandable ? goalDetailTrigger(goal) : ""}
    ${data.rows.length ? `<span class="tree-spine" style="--spine-h:${Math.max(0, lastCenter - 144 - finalElbowHeight)}px" aria-hidden="true"></span>` : ""}
    ${data.rows.map((row, index) => `<div class="clean-tree-row" style="--row-y:${188 + index * 146}px;--row-order:${index}">
      <span class="row-branch" aria-hidden="true"></span>
      ${row.kind === "period"
        ? cleanCircle(row.object, "period", { visualOnly: true })
        : cleanCircle(row.object, row.kind)}
      ${row.todos.length ? `<div class="clean-todo-chain">${row.todos.map(todoCircle).join("")}</div>` : ""}
    </div>`).join("")}
  </section>`;
}

function relationModeSwitch() {
  return `<div class="relation-mode-switch" role="tablist" aria-label="任务查看方式">
    <button role="tab" aria-selected="${state.relationMode === "canvas"}" tabindex="${state.relationMode === "canvas" ? "0" : "-1"}" class="${state.relationMode === "canvas" ? "is-active" : ""}" data-relation-tab="canvas">总览</button>
    <button role="tab" aria-selected="${state.relationMode === "focus"}" tabindex="${state.relationMode === "focus" ? "0" : "-1"}" class="${state.relationMode === "focus" ? "is-active" : ""}" data-relation-tab="focus">聚焦</button>
  </div>`;
}

function relationCanvasActions() {
  return `<div class="canvas-actions" role="group" aria-label="画布缩放控制">
    <button data-canvas-action="minus" aria-label="缩小">${icon("minus")}</button>
    <output data-zoom-label aria-label="当前缩放比例">${Math.round(state.canvas.zoom * 100)}%</output>
    <button data-canvas-action="plus" aria-label="放大">${icon("plus")}</button>
    <button data-canvas-action="fit" aria-label="适应画布">${icon("fit")}<span>适应画布</span></button>
  </div>`;
}

function relationFocusItem(kind, object) {
  const active = state.relationFocusKind === kind && state.relationFocusId === object.id;
  const secondary = kind === "goal"
    ? `${TYPE_LABEL[object.type] || object.type}${object.priority ? ` · ${object.priority}` : ""}`
    : (object.deadline ? `DDL ${shortDate(object.deadline)}` : "无 DDL");
  return `<button class="relation-focus-item ${active ? "is-active" : ""} ${object.status === "completed" ? "is-done" : ""}"
      data-focus-object="${kind}:${object.id}" aria-current="${active ? "true" : "false"}">
    <span class="focus-item-orb is-${kind}"${kind === "goal" ? ` style="--orb-tone:var(--${model.colorKeyOf(object.id)}-stroke);--orb-soft:var(--${model.colorKeyOf(object.id)}-soft)"` : ""} aria-hidden="true"></span>
    <span><strong>${esc(object.title)}</strong><small>${esc(secondary)}</small></span>
  </button>`;
}

function focusedRelationCanvas(goals, standalone) {
  let kind = state.relationFocusKind;
  let object = kind === "goal"
    ? goals.find(goal => goal.id === state.relationFocusId)
    : standalone.find(todo => todo.id === state.relationFocusId);
  if (!object) {
    object = goals[0] || standalone[0];
    kind = goals[0] ? "goal" : "todo";
    state.relationFocusKind = kind;
    state.relationFocusId = object?.id ?? null;
  }
  if (!object) return { width: 720, height: 480, content: `<p class="focus-empty">还没有可展示的目标或独立行动</p>` };
  if (kind === "goal") {
    const tree = cleanTreeData(object);
    const placement = { x: 112, y: 72 };
    return {
      width: Math.max(780, tree.width + 260),
      height: Math.max(560, tree.height + 170),
      content: cleanTree(object, placement, { expandable: false }),
    };
  }
  return {
    width: 680,
    height: 480,
    content: `<div class="focus-single-circle floating-circle" data-layout-id="todo:${object.id}" style="--float-x:249px;--float-y:210px;--float-delay:-1.4s;--float-duration:7.4s;--float-drift-x:4px;--float-drift-y:-6px">${todoCircle(object)}</div>`,
  };
}

function relationFocusView(goals, standalone) {
  const focus = focusedRelationCanvas(goals, standalone);
  return `<div class="relation-focus-layout">
    <aside class="relation-focus-rail" aria-label="任务对象列表">
      <section class="focus-list-section">
        <h2><span>目标</span><b>${goals.length}</b></h2>
        <div class="focus-object-list">${goals.map(goal => relationFocusItem("goal", goal)).join("")}</div>
      </section>
      ${standalone.length ? `<section class="focus-list-section">
        <h2><span>独立行动</span><b>${standalone.length}</b></h2>
        <div class="focus-object-list">${standalone.map(todo => relationFocusItem("todo", todo)).join("")}</div>
      </section>` : ""}
    </aside>
    <div class="relation-canvas relation-focus-stage" data-canvas-stage tabindex="0" aria-label="单项任务画布">
      <div class="canvas-world clean-canvas-world focus-canvas-world" style="--world-w:${focus.width}px;--world-h:${focus.height}px">${focus.content}</div>
    </div>
  </div>`;
}

function cleanCanvasLayout(goals, standalone) {
  const all = [
    ...goals.map(goal => ({ kind: "goal", object: goal, width: 144, height: 144 })),
    ...standalone.map(todo => ({ kind: "todo", object: todo, width: 182, height: 60 })),
  ];
  const compactHomes = [
    [250, 230], [620, 118], [826, 390], [500, 510], [930, 150], [170, 500],
    [1040, 510], [420, 72], [720, 580], [90, 110], [1110, 280], [310, 650],
  ];
  const components = all.map((item, index) => {
    const fallbackRing = Math.floor(index / compactHomes.length) + 1;
    const fallbackAngle = index * 2.399963;
    const home = compactHomes[index] ?? [
      620 + Math.cos(fallbackAngle) * (430 + fallbackRing * 150),
      360 + Math.sin(fallbackAngle) * (250 + fallbackRing * 120),
    ];
    const homeX = Math.round(home[0]);
    const homeY = Math.round(home[1]);
    const expanded = item.kind === "goal" && !state.collapsedGoals.has(item.object.id);
    const treeSize = expanded ? cleanTreeData(item.object) : null;
    return { ...item, floatIndex: index, expanded, homeX, homeY, x: homeX, y: homeY,
      width: treeSize?.width ?? item.width, height: treeSize?.height ?? item.height };
  });
  const focused = components.find(item => item.expanded && item.object.id === state.relationFocusGoal);
  const ordered = [
    ...(focused ? [focused] : []),
    ...components.filter(item => item !== focused && item.expanded),
    ...components.filter(item => !item.expanded),
  ];
  const placed = [];
  const gap = 42;
  const overlaps = (a, b) => a.x < b.x + b.width + gap && a.x + a.width + gap > b.x
    && a.y < b.y + b.height + gap && a.y + a.height + gap > b.y;
  for (const item of ordered) {
    if (item !== focused) {
      let guard = 0;
      while (guard++ < 80) {
        const blocker = placed.find(other => overlaps(item, other));
        if (!blocker) break;
        const rightX = blocker.x + blocker.width + gap;
        const downY = blocker.y + blocker.height + gap;
        const rightCost = (rightX - item.homeX) ** 2 + (item.y - item.homeY) ** 2;
        const downCost = (item.x - item.homeX) ** 2 + (downY - item.homeY) ** 2;
        if (rightCost <= downCost) item.x = rightX;
        else item.y = downY;
      }
    }
    placed.push(item);
  }
  const trees = components.filter(item => item.expanded).map(item => ({ goal: item.object, x: item.x, y: item.y, width: item.width, height: item.height }));
  const floats = components.filter(item => !item.expanded);
  const maxRight = Math.max(1040, ...components.map(item => item.x + item.width));
  const maxBottom = Math.max(620, ...components.map(item => item.y + item.height));
  return { trees, floats, width: maxRight + 150, height: maxBottom + 150 };
}

function relationsPage() {
  const goals = model.goals.filter(goal => goal.status !== "cancelled");
  const standalone = model.todos.filter(todo => !todo.parent_refs.length && todo.status !== "cancelled").sort(byDDL);
  const layout = state.relationMode === "canvas" ? cleanCanvasLayout(goals, standalone) : null;
  return `<section class="main-view relation-page clean-relation-page" aria-label="任务总览无限画布">
    ${state.relationMode === "focus" ? relationFocusView(goals, standalone) : `<div class="relation-canvas" data-canvas-stage tabindex="0">
      <div class="canvas-world clean-canvas-world" style="--world-w:${layout.width}px;--world-h:${layout.height}px">
        ${layout.trees.map(item => cleanTree(item.goal, item)).join("")}
        ${layout.floats.map(item => `<div class="floating-circle" data-layout-id="${item.kind}:${item.object.id}" style="--float-x:${item.x}px;--float-y:${item.y}px;--float-delay:${-(item.floatIndex % 7) * .73}s;--float-duration:${5.8 + (item.floatIndex % 4) * .6}s;--float-drift-x:${item.floatIndex % 2 ? 22 : -18}px;--float-drift-y:${item.floatIndex % 3 ? -24 : 20}px;${item.kind === "goal" ? goalToneVars(model.colorKeyOf(item.object.id)) : ""}">
          ${item.kind === "goal" ? `${cleanCircle(item.object, "goal", { expandable: true })}${goalDetailTrigger(item.object)}` : todoCircle(item.object)}
        </div>`).join("")}
      </div>
    </div>`}
  </section>`;
}

function drawerBody() {
  const sel = state.selected;
  if (!sel) return null;
  if (sel.kind === "entry") {
    const e = model.entryById.get(sel.id);
    if (!e) return null;
    return {
      kicker: e.kindLabel,
      title: e.title, status: e.status, shared: e.shared, sharedCount: e.parents?.length ?? 0,
      actions: e.sourceType === "todo" ? [["relations:" + e.entityId, "在任务中查看"]] : [],
      rows: [
        ["类别", e.kindLabel],
        ["时间", `${e.date} ${e.startLabel}–${e.endLabel} · ${e.durationMinutes} 分钟`],
        e.priority ? ["优先级", `${e.priority}（取有效所属目标中最高）`] : null,
        e.parents?.length ? ["所属结构", e.parents.map(p => `${esc(p.goalTitle)}${p.nodeTitle ? " › " + esc(p.nodeTitle) : ""}`).join("<br/>")] : null,
        e.remainingMinutes != null ? ["剩余工作量", `${e.remainingMinutes} 分钟`] : null,
        e.deadline ? ["DDL", new Date(e.deadline).toLocaleString("zh-CN", { timeZone: model.timezone })] : null,
        e.context ? ["背景", esc(e.context)] : null,
      ].filter(Boolean),
    };
  }
  const found = model.structureById.get(sel.id);
  if (!found) return null;
  const o = found.object;
  if (found.kind === "goal") {
    return { kicker: "目标", title: o.title, status: o.status, actions: [["goal:" + o.id, "高亮它的时间块"]], rows: [
      ["类型", TYPE_LABEL[o.type]], ["目标优先级", o.priority], ["完成结果", esc(o.outcome)],
      o.deadline ? ["DDL", new Date(o.deadline).toLocaleString("zh-CN", { timeZone: model.timezone })] : null,
      o.habit_rule ? ["习惯规则", `每 ${o.habit_rule.interval} ${o.habit_rule.unit} 至少 ${o.habit_rule.minimum_count} 次 · 每次 ${o.habit_rule.session_minutes} 分钟`] : null,
      o.recurrence ? ["循环规则", `每 ${o.recurrence.interval} ${o.recurrence.unit}`] : null,
      o.context ? ["背景", esc(o.context)] : null,
    ].filter(Boolean) };
  }
  if (found.kind === "node") {
    return { kicker: "节点", title: o.title, status: o.status, rows: [
      ["所属目标", esc(model.goalById.get(o.goal_id)?.title ?? o.goal_id)], ["完成结果", esc(o.outcome)],
      o.deadline ? ["DDL", new Date(o.deadline).toLocaleString("zh-CN", { timeZone: model.timezone })] : null,
      o.depends_on_node_ids.length ? ["前置节点", o.depends_on_node_ids.map(id => esc(model.nodeById.get(id)?.title ?? id)).join("<br/>")] : null,
    ].filter(Boolean) };
  }
  const parents = model.effectiveParentsOf(o);
  const scheduled = model.entriesOfTodo(o.id);
  return { kicker: "行动", title: o.title, status: o.status, shared: parents.length > 1, sharedCount: parents.length,
    actions: scheduled.length ? [["schedule:" + scheduled[0].id, `在日程中查看（${scheduled.length} 段安排）`]] : [],
    rows: [
      ["剩余工作量", `${o.remaining_estimate_minutes} 分钟`],
      ["执行方式", o.execution_mode === "single_block" ? "需要整块时间" : "可拆分"], ["强度", o.intensity],
      parents.length ? ["所属结构", parents.map(p => `${esc(p.goalTitle)}${p.nodeTitle ? " › " + esc(p.nodeTitle) : ""}`).join("<br/>")] : ["所属", "独立行动"],
      o.deadline ? ["DDL", new Date(o.deadline).toLocaleString("zh-CN", { timeZone: model.timezone })] : null,
      o.depends_on_todo_ids.length ? ["前置行动", o.depends_on_todo_ids.map(id => esc(model.todoById.get(id)?.title ?? id)).join("<br/>")] : null,
      ["已排期", scheduled.length ? scheduled.map(e => `${e.date} ${e.startLabel}–${e.endLabel}`).join("<br/>") : "尚未排期"],
      o.context ? ["背景", esc(o.context)] : null,
    ].filter(Boolean) };
}

function drawer() {
  const body = drawerBody();
  if (!body) return "";
  return `<div class="drawer-backdrop ${state.drawerOpen ? "is-open" : ""}" data-close-drawer></div>
    <aside class="detail-drawer ${state.drawerOpen ? "is-open" : ""}" role="dialog" aria-modal="true" aria-hidden="${!state.drawerOpen}" aria-labelledby="detail-drawer-title">
      <button class="drawer-close" data-close-drawer aria-label="关闭详情">${icon("close")}</button>
      <div class="section-kicker">${body.kicker}</div>
      <h2 id="detail-drawer-title">${esc(body.title)}</h2>
      ${body.status ? `<div class="detail-status"><span></span>${STATUS_LABEL[body.status] ?? body.status}</div>` : ""}
      ${body.shared ? `<div class="detail-shared">${icon("share")} 共享行动：同时推进 ${body.sharedCount} 个所属结构</div>` : ""}
      <dl>${body.rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>
      ${(body.actions ?? []).map(([t, l]) => `<button class="primary-action" data-locate="${t}">${l} ${icon("arrow")}</button>`).join("")}
      <p class="drawer-note">只读视图。所有事实来自 plan.json，页面不写回。</p>
    </aside>`;
}

/* ── 连线 ───────────────────────────────────────────────── */
function applyCanvasTransform() {
  const world = app.querySelector(".canvas-world");
  if (!world) return;
  world.style.transform = `translate(${state.canvas.x}px, ${state.canvas.y}px) scale(${state.canvas.zoom})`;
  world.style.setProperty("--inverse-zoom", String(1 / state.canvas.zoom));
  world.dataset.zoomLevel = state.canvas.zoom < 0.22 ? "overview" : state.canvas.zoom < 0.72 ? "medium" : "detail";
  const label = app.querySelector("[data-zoom-label]");
  if (label) label.textContent = `${Math.round(state.canvas.zoom * 100)}%`;
}

function fitCanvas(animate = true) {
  const stage = app.querySelector("[data-canvas-stage]");
  const world = app.querySelector(".canvas-world");
  if (!stage || !world) return;
  const worldWidth = parseFloat(getComputedStyle(world).width);
  const worldHeight = parseFloat(getComputedStyle(world).height);
  // 自动适配首先保证对象仍可读；窄屏只展示画布的一部分，由平移继续浏览。
  // 用户主动缩小仍可通过 zoomCanvas 到 12%，这里仅限制自动 fit。
  const zoom = Math.max(AUTO_FIT_MIN_ZOOM, Math.min(0.9, (stage.clientWidth - 56) / worldWidth, (stage.clientHeight - 56) / worldHeight));
  state.canvas.zoom = zoom;
  state.canvas.x = (stage.clientWidth - worldWidth * zoom) / 2;
  state.canvas.y = (stage.clientHeight - worldHeight * zoom) / 2;
  state.canvas.viewportWidth = stage.clientWidth;
  state.canvas.viewportHeight = stage.clientHeight;
  if (animate) world.classList.add("is-animating");
  applyCanvasTransform();
  if (animate) setTimeout(() => world.classList.remove("is-animating"), 260);
  state.canvas.fitted = true;
}

function preserveCanvasViewport() {
  const stage = app.querySelector("[data-canvas-stage]");
  if (!stage) return;
  const previousWidth = state.canvas.viewportWidth ?? stage.clientWidth;
  const previousHeight = state.canvas.viewportHeight ?? stage.clientHeight;
  // 以原视口中心为锚点扩缩可视区域，不改变用户选择的缩放比例。
  state.canvas.x += (stage.clientWidth - previousWidth) / 2;
  state.canvas.y += (stage.clientHeight - previousHeight) / 2;
  state.canvas.viewportWidth = stage.clientWidth;
  state.canvas.viewportHeight = stage.clientHeight;
  applyCanvasTransform();
}

function zoomCanvas(nextZoom, clientX = null, clientY = null) {
  const stage = app.querySelector("[data-canvas-stage]");
  if (!stage) return;
  const box = stage.getBoundingClientRect();
  const x = clientX ?? box.left + box.width / 2;
  const y = clientY ?? box.top + box.height / 2;
  const localX = x - box.left;
  const localY = y - box.top;
  const oldZoom = state.canvas.zoom;
  const zoom = Math.max(0.12, Math.min(1.6, nextZoom));
  const worldX = (localX - state.canvas.x) / oldZoom;
  const worldY = (localY - state.canvas.y) / oldZoom;
  state.canvas.x = localX - worldX * zoom;
  state.canvas.y = localY - worldY * zoom;
  state.canvas.zoom = zoom;
  state.canvas.fitted = true;
  applyCanvasTransform();
}

function bindCanvas() {
  const stage = app.querySelector("[data-canvas-stage]");
  if (!stage) return;
  let drag = null;
  stage.addEventListener("wheel", event => {
    event.preventDefault();
    const deltaScale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? stage.clientHeight : 1;
    let deltaX = event.deltaX * deltaScale;
    let deltaY = event.deltaY * deltaScale;

    // Browsers expose trackpad pinch as a ctrl-modified wheel event. Cmd/Ctrl +
    // mouse wheel follows the same path, so both zoom around the pointer.
    if (event.ctrlKey || event.metaKey) {
      const zoomDelta = Math.abs(deltaY) >= Math.abs(deltaX) ? deltaY : deltaX;
      zoomCanvas(state.canvas.zoom * Math.exp(-zoomDelta * 0.0021), event.clientX, event.clientY);
      return;
    }

    // Plain wheel/two-finger scrolling pans. Shift + a vertical mouse wheel is
    // treated as horizontal panning, while trackpads keep their native 2D delta.
    if (event.shiftKey && Math.abs(deltaX) < Math.abs(deltaY)) {
      deltaX = deltaY;
      deltaY = 0;
    }
    state.canvas.x -= deltaX;
    state.canvas.y -= deltaY;
    state.canvas.fitted = true;
    applyCanvasTransform();
  }, { passive: false });
  stage.addEventListener("pointerdown", event => {
    const isBlankPrimaryDrag = event.button === 0 && !event.target.closest("button");
    const isSecondaryDrag = event.button === 2;
    if (!isBlankPrimaryDrag && !isSecondaryDrag) return;
    event.preventDefault();
    drag = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, canvasX: state.canvas.x, canvasY: state.canvas.y };
    stage.setPointerCapture(event.pointerId);
    stage.classList.add("is-panning");
  });
  stage.addEventListener("pointermove", event => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    state.canvas.x = drag.canvasX + event.clientX - drag.x;
    state.canvas.y = drag.canvasY + event.clientY - drag.y;
    state.canvas.fitted = true;
    applyCanvasTransform();
  });
  const stop = event => {
    if (!drag || (event.pointerId != null && drag.pointerId !== event.pointerId)) return;
    drag = null; stage.classList.remove("is-panning");
  };
  stage.addEventListener("pointerup", stop);
  stage.addEventListener("pointercancel", stop);
  stage.addEventListener("contextmenu", event => event.preventDefault());
  if (!state.canvas.fitted) requestAnimationFrame(() => fitCanvas(false));
  else requestAnimationFrame(preserveCanvasViewport);
}

function drawEdges() { applyCanvasTransform(); }

function captureRelationLayout() {
  const items = app.querySelectorAll("[data-layout-id]");
  if (!items.length) return null;
  return new Map([...items].map(element => {
    const box = element.getBoundingClientRect();
    return [element.dataset.layoutId, { x: box.x, y: box.y }];
  }));
}

function animateRelationLayout() {
  const previous = state.relationPreviousLayout;
  if (!previous || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const zoom = state.canvas.zoom || 1;
  for (const element of app.querySelectorAll("[data-layout-id]")) {
    const from = previous.get(element.dataset.layoutId);
    if (!from) continue;
    const box = element.getBoundingClientRect();
    const dx = (from.x - box.x) / zoom;
    const dy = (from.y - box.y) / zoom;
    if (Math.abs(dx) < .5 && Math.abs(dy) < .5) continue;
    element.animate([
      { translate: `${dx}px ${dy}px` },
      { translate: "0 0" },
    ], { duration: 520, easing: "cubic-bezier(.16,1,.3,1)", fill: "both" });
  }
}

function bindCircleMotion() {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  app.querySelectorAll(".relation-circle").forEach(circle => {
    let frame = 0;
    let nextX = 0;
    let nextY = 0;
    circle.addEventListener("pointermove", event => {
      const box = circle.getBoundingClientRect();
      nextX = ((event.clientX - box.left) / box.width - .5) * 7;
      nextY = ((event.clientY - box.top) / box.height - .5) * 7;
      if (frame) return;
      frame = requestAnimationFrame(() => {
        circle.style.setProperty("--mag-x", `${nextX.toFixed(2)}px`);
        circle.style.setProperty("--mag-y", `${nextY.toFixed(2)}px`);
        frame = 0;
      });
    });
    circle.addEventListener("pointerleave", () => {
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      circle.style.setProperty("--mag-x", "0px");
      circle.style.setProperty("--mag-y", "0px");
    });
  });
}

/* ── 渲染 ───────────────────────────────────────────────── */
const SCROLL_TARGETS = [".calendar-scroll", ".timeline-scroll", ".side-panel", ".relation-focus-rail"];
function captureScrollPositions() {
  return SCROLL_TARGETS.flatMap(selector => [...app.querySelectorAll(selector)].map((element, index) => ({
    selector, index, left: element.scrollLeft, top: element.scrollTop,
  })));
}

function restoreScrollPositions(snapshot) {
  for (const item of snapshot) {
    const element = app.querySelectorAll(item.selector)[item.index];
    if (element) element.scrollTo({ left: item.left, top: item.top });
  }
}

function initializeWeekScroll() {
  if (state.page !== "schedule" || state.view !== "week") return;
  const weekKey = model.weekDaysFor(state.anchorDate)[0]?.date;
  if (!weekKey || state.weekScrollKey === weekKey) return;
  state.weekScrollKey = weekKey;
  requestAnimationFrame(() => {
    const scroller = app.querySelector(".calendar-scroll");
    const calendar = app.querySelector(".week-calendar");
    if (!scroller || !calendar) return;
    const gridHeight = calendar.scrollHeight - 40;
    scroller.scrollTop = (8 * 60 / (24 * 60)) * gridHeight;
  });
}

function focusSelectionTrigger() {
  const ref = state.returnFocus;
  if (!ref) return;
  const selector = ref.kind === "entry" ? "[data-entry]" : ref.kind === "expand-goal" ? "[data-expand-goal]" : "[data-object]";
  const target = [...app.querySelectorAll(selector)].find(element => ref.kind === "entry"
    ? element.dataset.entry === ref.id
    : ref.kind === "expand-goal"
      ? element.dataset.expandGoal === ref.id
      : element.dataset.object === `${ref.kind}:${ref.id}`);
  target?.focus({ preventScroll: true });
}

function trapDrawerFocus(event) {
  if (event.key !== "Tab") return;
  const drawer = app.querySelector(".detail-drawer.is-open");
  if (!drawer) return;
  const focusable = [...drawer.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])")]
    .filter(element => !element.disabled && element.getClientRects().length);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}

function render() {
  if (state.status === "loading") {
    app.innerHTML = `<div class="app-shell is-plain"><div class="boot"><span class="boot-spinner"></span>正在读取正式计划…</div></div>`;
    return;
  }
  if (state.status === "error") {
    app.innerHTML = `<div class="app-shell is-plain"><div class="boot boot-error"><strong>读取正式计划失败</strong>
      <p>${esc(state.error)}</p><button class="ghost-button" data-retry>重新加载</button></div></div>`;
    bindEvents(); return;
  }
  if (model.isEmpty) {
    app.innerHTML = `<div class="app-shell is-plain"><div class="boot"><strong>正式计划是空的</strong>
      <p>plan v${model.version}，还没有任何目标、行动或时间占用。<br/>通过对话把事项交给系统，提交成功后这里会自动更新。</p>
      <button class="ghost-button" data-retry>重新读取</button></div></div>`;
    bindEvents(); return;
  }
  const showSide = state.page === "schedule" && state.showPriorities;
  app.innerHTML = `<div class="app-shell">
    ${topbar()}
    <main class="body-area ${showSide ? "with-side" : ""}">
      <h1 class="sr-only">${state.page === "relations" ? "任务关系" : state.view === "week" ? "周日程" : "当日日程"}</h1>
      ${state.page === "relations" ? relationsPage() : (state.view === "day" ? dayView() : weekView())}
      ${showSide ? prioritiesPanel() : ""}
    </main>
    ${drawer()}
  </div>`;
  if (state.drawerOpen) {
    app.querySelector(".topbar")?.setAttribute("inert", "");
    app.querySelector(".body-area")?.setAttribute("inert", "");
  }
  bindEvents();
  initializeWeekScroll();
  drawEdges();
  animateRelationLayout();
  state.relationPreviousLayout = null;
  state.relationFreshGoal = null;
}

function bindEvents() {
  app.querySelectorAll("[data-view]").forEach(b => b.addEventListener("click", () => setView(b.dataset.view)));
  app.querySelectorAll("[data-step]").forEach(b => b.addEventListener("click", () => step(Number(b.dataset.step))));
  app.querySelector("[data-today]")?.addEventListener("click", () => {
    state.anchorDate = state.view === "week" ? model.window.starts_on : model.focusDate;
    if (state.view === "week") state.weekScrollKey = null;
    sync();
  });
  app.querySelectorAll("[data-goto]").forEach(b => b.addEventListener("click", () => { state.anchorDate = b.dataset.goto; sync(); }));
  app.querySelectorAll("[data-page]").forEach(b => b.addEventListener("click", () => setPage(b.dataset.page)));
  app.querySelectorAll("[data-panel]").forEach(b => b.addEventListener("click", () => togglePanel(b.dataset.panel)));
  app.querySelectorAll("[data-entry]").forEach(b => b.addEventListener("click", () => select("entry", b.dataset.entry)));
  app.querySelectorAll("[data-object]").forEach(b => b.addEventListener("click", () => { const [k, i] = b.dataset.object.split(":"); select(k, i); }));
  app.querySelectorAll("[data-goal-detail]").forEach(button => button.addEventListener("click", event => {
    event.stopPropagation();
    const id = button.dataset.goalDetail;
    select("goal", id, "expand-goal");
  }));
  app.querySelectorAll("[data-relation-tab]").forEach(button => {
    button.addEventListener("click", () => changeRelationMode(button.dataset.relationTab));
    button.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const mode = event.key === "ArrowLeft" || event.key === "Home" ? "canvas" : "focus";
      changeRelationMode(mode, true);
    });
  });
  app.querySelectorAll("[data-focus-object]").forEach(button => button.addEventListener("click", () => {
    const [kind, id] = button.dataset.focusObject.split(":");
    if (kind === state.relationFocusKind && id === state.relationFocusId) return;
    state.relationFocusKind = kind;
    state.relationFocusId = id;
    state.relationFreshGoal = kind === "goal" ? id : null;
    state.canvas.fitted = false;
    sync();
  }));
  app.querySelectorAll("[data-toggle]").forEach(b => b.addEventListener("click", () => {
    const id = b.dataset.toggle;
    state.collapsedGoals.has(id) ? state.collapsedGoals.delete(id) : state.collapsedGoals.add(id);
    // 展开/收起只改变任务结构，保留用户当前的缩放和画布位置。
    // 新增分支从被点击 Goal 的原锚点展开，不再触发整张画布自动 fit。
    render();
  }));
  app.querySelectorAll("[data-expand-goal]").forEach(button => button.addEventListener("click", () => {
    const id = button.dataset.expandGoal;
    state.relationPreviousLayout = captureRelationLayout();
    if (state.collapsedGoals.has(id)) {
      state.collapsedGoals.delete(id);
      state.relationFocusGoal = id;
      state.relationFreshGoal = id;
    } else {
      state.collapsedGoals.add(id);
      state.relationFocusGoal = null;
    }
    // 保留用户当前视口，避免展开后因整图重新适配而缩放、跳位。
    render();
  }));
  app.querySelectorAll("[data-canvas-action]").forEach(button => button.addEventListener("click", () => {
    const action = button.dataset.canvasAction;
    if (action === "fit") fitCanvas();
    else zoomCanvas(state.canvas.zoom * (action === "plus" ? 1.32 : 0.76));
  }));
  app.querySelectorAll("[data-locate]").forEach(b => b.addEventListener("click", () => locate(b.dataset.locate)));
  app.querySelectorAll("[data-close-drawer]").forEach(b => b.addEventListener("click", closeDrawer));
  app.querySelector("[data-retry]")?.addEventListener("click", () => load());
  bindCanvas();
  bindCircleMotion();
}

function changeRelationMode(mode, focusTab = false) {
  if (mode === state.relationMode) {
    if (focusTab) app.querySelector(`[data-relation-tab="${mode}"]`)?.focus();
    return;
  }
  state.relationMode = mode;
  state.canvas = relationCanvasStates[mode];
  state.canvas.fitted = false;
  if (mode === "focus" && state.relationFocusKind === "goal") state.relationFreshGoal = state.relationFocusId;
  sync();
  if (focusTab) requestAnimationFrame(() => app.querySelector(`[data-relation-tab="${mode}"]`)?.focus());
}

function setView(view) {
  if (view === state.view) return;
  state.view = view;
  state.anchorDate = view === "day" ? clampToWindow(state.anchorDate) : clampToWeekBrowseRange(state.anchorDate);
  if (view === "week") state.weekScrollKey = null;
  sync();
}
function setPage(page) {
  if (page === state.page) return;
  state.page = page;
  if (page === "schedule" && state.view === "week") state.weekScrollKey = null;
  sync();
}
function step(delta) {
  if (!canStep(delta)) return;
  state.anchorDate = shiftDate(state.anchorDate, state.view === "day" ? delta : delta * 7);
  if (state.view === "week") state.weekScrollKey = null;
  sync();
}
function togglePanel(name) {
  const scrollSnapshot = captureScrollPositions();
  if (name === "priorities") state.showPriorities = !state.showPriorities;
  sync();
  requestAnimationFrame(() => restoreScrollPositions(scrollSnapshot));
}
function select(kind, id, returnKind = kind) {
  const scrollSnapshot = captureScrollPositions();
  state.selected = { kind, id };
  state.returnFocus = { kind: returnKind, id };
  state.drawerOpen = true;
  render();
  requestAnimationFrame(() => {
    restoreScrollPositions(scrollSnapshot);
    app.querySelector(".detail-drawer.is-open .drawer-close")?.focus({ preventScroll: true });
  });
}
function closeDrawer() {
  if (!state.drawerOpen) return;
  const scrollSnapshot = captureScrollPositions();
  state.drawerOpen = false;
  state.selected = null;
  render();
  requestAnimationFrame(() => {
    restoreScrollPositions(scrollSnapshot);
    focusSelectionTrigger();
  });
}

function revealRelationObject(kind, id, goalId = null) {
  const stage = app.querySelector("[data-canvas-stage]");
  const candidates = [...app.querySelectorAll("[data-object]")]
    .filter(element => element.dataset.object === `${kind}:${id}`);
  const target = goalId
    ? candidates.find(element => element.closest("[data-layout-id]")?.dataset.layoutId === `goal:${goalId}`)
    : candidates[0];
  if (!stage || !target) return;
  const stageBox = stage.getBoundingClientRect();
  const targetBox = target.getBoundingClientRect();
  state.canvas.x += stageBox.left + stageBox.width / 2 - (targetBox.left + targetBox.width / 2);
  state.canvas.y += stageBox.top + stageBox.height / 2 - (targetBox.top + targetBox.height / 2);
  state.canvas.fitted = true;
  state.canvas.viewportWidth = stage.clientWidth;
  state.canvas.viewportHeight = stage.clientHeight;
  applyCanvasTransform();
  target.classList.add("is-located");
  target.focus({ preventScroll: true });
  setTimeout(() => target.classList.remove("is-located"), 1100);
}

// 跨区域定位：关系面板与日程同屏，定位不再跳页
function locate(spec) {
  const [target, id] = spec.split(":");
  if (target === "relations") {
    const todo = model.todoById.get(id);
    // 跨页定位面对的是“关系页中仍可见的任务结构”，不能复用排期时的
    // effectiveParents（它会排除已完成 Node，导致历史日程中的 Todo 无法定位）。
    const primaryRef = todo?.parent_refs.find(ref => model.goalById.get(ref.goal_id)?.status !== "cancelled") ?? null;
    const primaryGoalId = primaryRef?.goal_id ?? null;
    state.page = "relations";
    state.selected = { kind: "todo", id };
    state.drawerOpen = false;
    if (primaryGoalId) {
      state.collapsedGoals.delete(primaryGoalId);
      state.relationFocusGoal = primaryGoalId;
      if (state.relationMode === "focus") {
        state.relationFocusKind = "goal";
        state.relationFocusId = primaryGoalId;
      }
    } else if (todo && !todo.parent_refs.length && state.relationMode === "focus") {
      state.relationFocusKind = "todo";
      state.relationFocusId = id;
    }
    // 跨页定位自身会把目标居中，不与首次自动 fit 竞争最后一次 transform。
    state.canvas.fitted = true;
    sync();
    requestAnimationFrame(() => revealRelationObject("todo", id, primaryGoalId));
    return;
  } else if (target === "schedule") {
    const entry = model.entryById.get(id);
    state.page = "schedule";
    if (entry) { state.anchorDate = entry.date; state.selected = { kind: "entry", id }; }
    state.highlightGoal = null; state.drawerOpen = false;
  } else if (target === "goal") {
    state.page = "schedule"; state.view = "week"; state.highlightGoal = id; state.drawerOpen = false;
  } else if (target === "clear") {
    state.highlightGoal = null;
  }
  sync();
  requestAnimationFrame(() => app.querySelector(".schedule-card.is-selected, .timeline-row.is-selected")
    ?.scrollIntoView({ block: "center", behavior: "smooth" }));
}

function sync() { updateUrl(); render(); }

function updateUrl() {
  const url = new URL(location.href);
  url.searchParams.set("page", state.page);
  url.searchParams.set("view", state.view);
  url.searchParams.set("date", state.anchorDate);
  state.showPriorities ? url.searchParams.set("priorities", "1") : url.searchParams.delete("priorities");
  url.searchParams.delete("variant");
  state.relationMode === "focus" ? url.searchParams.set("relations", "focus") : url.searchParams.delete("relations");
  state.relationMode === "focus" && state.relationFocusId
    ? url.searchParams.set("focus", `${state.relationFocusKind}:${state.relationFocusId}`)
    : url.searchParams.delete("focus");
  history.replaceState({}, "", url);
}

async function load({ silent = false } = {}) {
  if (!silent) state.status = "loading";
  state.refreshing = true;
  if (!silent) render();
  try {
    const response = await fetch(PLAN_URL, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.planSource = response.headers.get("X-Plan-Source") === "fixture" ? "fixture" : "official";
    const previousGoalIds = new Set(model?.goals.map(goal => goal.id) ?? []);
    model = buildModel(await response.json());
    const requestedDate = state.anchorDate || params.get("date") || model.focusDate;
    state.anchorDate = state.view === "week" ? clampToWeekBrowseRange(requestedDate) : clampToWindow(requestedDate);
    const visibleGoals = model.goals.filter(goal => goal.status !== "cancelled");
    const visibleGoalIds = new Set(visibleGoals.map(goal => goal.id));
    const standalone = model.todos.filter(todo => !todo.parent_refs.length && todo.status !== "cancelled").sort(byDDL);
    if (!state.relationInitialized) {
      state.collapsedGoals = new Set(visibleGoals.map(goal => goal.id));
      const requestedFocus = params.get("focus")?.split(":");
      const requestedObject = requestedFocus?.[0] === "goal"
        ? visibleGoals.find(goal => goal.id === requestedFocus[1])
        : requestedFocus?.[0] === "todo" ? standalone.find(todo => todo.id === requestedFocus[1]) : null;
      state.relationFocusKind = requestedObject ? requestedFocus[0] : (visibleGoals.length ? "goal" : "todo");
      state.relationFocusId = requestedObject?.id ?? visibleGoals[0]?.id ?? standalone[0]?.id ?? null;
      state.relationInitialized = true;
      state.canvas.fitted = false;
    } else {
      state.collapsedGoals = new Set([...state.collapsedGoals].filter(id => visibleGoalIds.has(id)));
      for (const goal of visibleGoals) if (!previousGoalIds.has(goal.id)) state.collapsedGoals.add(goal.id);
      const focusExists = state.relationFocusKind === "goal"
        ? visibleGoalIds.has(state.relationFocusId)
        : standalone.some(todo => todo.id === state.relationFocusId);
      if (!focusExists) {
        state.relationFocusKind = visibleGoals.length ? "goal" : "todo";
        state.relationFocusId = visibleGoals[0]?.id ?? standalone[0]?.id ?? null;
        state.canvas.fitted = false;
      }
    }
    state.status = "ready";
  } catch (error) {
    if (!silent) { state.error = error.message; state.status = "error"; }
  }
  state.refreshing = false;
  const scrollSnapshot = silent ? captureScrollPositions() : [];
  if (state.status === "ready") updateUrl();
  render();
  if (silent) requestAnimationFrame(() => restoreScrollPositions(scrollSnapshot));
}

async function pollVersion() {
  if (state.status !== "ready" || document.hidden) return;
  try {
    const response = await fetch(PLAN_URL, { cache: "no-store" });
    if (!response.ok) return;
    const next = await response.json();
    if (next.plan_meta.plan_version !== model.version) await load({ silent: true });
  } catch { /* 忽略抖动 */ }
}

setInterval(pollVersion, POLL_MS);
addEventListener("visibilitychange", () => { if (!document.hidden) pollVersion(); });
let resizeTimer = 0;
addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (state.status === "ready" && state.page === "relations") preserveCanvasViewport();
    else drawEdges();
  }, 120);
});
addEventListener("keydown", e => {
  if (state.drawerOpen) {
    if (e.key === "Escape") { e.preventDefault(); closeDrawer(); }
    else trapDrawerFocus(e);
    return;
  }
  if (state.status !== "ready" || e.target.matches("input, textarea, [contenteditable]")) return;
  if (state.page === "relations") {
    if (e.key === "0") fitCanvas();
    return;
  }
  if (e.key === "ArrowLeft") step(-1);
  if (e.key === "ArrowRight") step(1);
  if (e.key.toLowerCase() === "t") { state.anchorDate = model.focusDate; if (state.view === "week") state.weekScrollKey = null; sync(); }
});

load();
