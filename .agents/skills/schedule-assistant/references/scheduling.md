# 统一日程生成规则

首次生成具体日程、为新增 Todo 排时间、重新安排受影响 Todo、重算某日关注顺序，或解释任务为何处于当前时间和顺序时，读取本文。

本文回答：给定正式 Todo、已经解释清楚的时间条件和当前 Schedule 后，哪些 Todo 还需要新增计划时间，应排到哪些合法时段，以及同一天的 Todo 应按什么顺序关注。

本文不建立 Goal、Node 或 Todo 结构，不解释日期和时间事实，不从执行反馈恢复进度，也不决定现实变化后哪些旧条目进入重排。

## 主流程

```text
1. 读取正式 Todo、Schedule 和当前 planning_window
2. 确定需保留和需替换的旧条目，汇总保留条目的未来覆盖量
3. 识别已结束／正在进行事实，按固定顺序完成 Todo 分组
4. 只为 scheduling_backlog 生成满足硬约束的候选时段
5. 在合法候选中综合 DDL、依赖、Goal 优先级和软偏好进行排布
6. 为每个自然日生成确定性的关注顺序
7. 与 TimeConstraint 投影汇总成完整候选 Schedule，校验后按权限提交
```

## 按问题查找

| 当前问题 | 读取章节 |
| --- | --- |
| 为什么某个 Todo 没有进入待排 | 2～3 |
| 怎样计算已有未来条目覆盖了多少工作量 | 2 |
| 临时分组是否需要写成文件 | 2 |
| 一个 Todo 还需要新排多少分钟 | 3 |
| AI 编排器可以看到哪些信息 | 4 |
| 多个合法 Todo 谁先排 | 5 |
| single_block、splittable 和 Habit 怎样排 | 6 |
| 当日关注顺序怎样生成和解释 | 7 |
| 完整 Schedule 怎样生成并交接提交 | 8～9 |

## 1. 生成前的必要输入

每轮排期至少读取：

- `TaskStructure` 中的 Goal、Node 和 Todo；
- 当前正式 `Schedule.entries`；
- 当前 `planning_window`、计划时区和计算时刻 `calculated_at`；
- AvailabilityRules、TimeConstraints 和受影响日期的日历分类；
- 当前处理所依赖的可信进度信息。

任务结构读取 `task-structure.md`，字段和正式数据读取 `plan-data-contract.md`，每天基础画像、TimeConstraint 展开和 Todo 合法候选时间读取 `calendar-and-time-rules.md`。

只为当前自然周生成新的正式具体日程，不提前生成以后各周。远期任务结构、周期模板和时间约束继续由其权威对象表达。

如果没有足以解释本轮日期的可用时间和已知承诺，不得生成看似精确到小时的正式日程。信息不足时是追问、只提供建议还是停止提交，读取 `decision-and-confirmation.md`。

本文假定任务语义已经在进入排期前解决。用户要求“简单日程”只影响结果展示，不减少任务结构、行为意图、时间分类和硬约束检查。程序化校验只能证明候选符合数据与时间规则，不能证明系统已经理解用户为何做这些事。

本次处理曾触发 `discovery-loop.md` 定义的首次、增量或修复发现时，排期前必须将全部当前分支重建为一次性发现状态，使用 `gate = scheduling` 运行 `scripts/validate_discovery.py`。只有校验成功才表示语义输入就绪；有任一 `open` 分支、未完成广度扫描、未完成关系检查或未确认的 Agent 推导结构时，不得构造周日程候选。

### 1.1 正式周日程的就绪门槛

首次生成正式周日程，或后续新信息使相关事实发生变化时，在生成候选 Schedule 前检查：

- 本轮使用的任务结构已经是正式事实；如果结构由 Agent 新推导，已按确认规则处理；
- 当前输入已经完成首次或增量语义检查：明显相关事项已尝试向上归纳，会议类行动已检查准备与后续，重复行为已明确是占用、Habit、Recurring 还是其他目标的手段；
- 用户此前未回答的发现问题没有被默认解释为“没有”；每个近期相关方向都已明确可建模或暂不纳入；
- 新发现的 Goal、Node、Todo、共享归属和依赖已经确认，不存在会改变结构的未知目的或动机；
- 当前候选 Todo 有可用的 `remaining_estimate_minutes`，且本轮依赖的过去真实进度可信；
- 所有影响本轮的非空 DDL 都已确认到具体时刻；
- 影响本轮的依赖和协作条件不存在会改变可行性的未知信息；
- 计划时区、正常日历、工作日与休息日可用画像足以解释本轮日期；
- 影响本轮的已知 TimeConstraint、`date_overrides` 和需保留 ScheduleEntry 已纳入候选计算；
- 不存在尚未由用户决定的重大取舍。

对空计划的首次周日程，输入理解阶段还应完成主要事务、目的、重复行为、稳定作息和时间竞争的明显漏项检查，并达到 `discovery-loop.md` 和 `task-structure.md` 定义的停止标准。对已有计划，如果受影响分支命中坏基线信号，先完成修复发现。本文只检查排期输入是否就绪，不规定具体追问话术。

## 2. 运行时结果与未来覆盖量

本轮的七个 Todo 主分组、未来覆盖量、`minutes_to_schedule` 和 Schedule 校正要求共同构成运行时派生结果。它们可以存在模型当前上下文、内存对象、工具参数或一次性临时文件中，但：

- 不要建立固定的 `scheduling-backlog.json` 或其他正式业务文件；
- 不得写入 `plan.json`，也不得被解释为正式计划已改变；
- 只和用户讨论方案时，可以直接用自然语言推理和说明，不强制生成临时 JSON；
- 准备正式提交时，必须基于最新正式计划重新计算，再构造完整结构化候选计划。

Schedule 校正要求与七个主分组正交，例如清理 `terminal` Todo 的未来条目、减少 `overcovered` 条目，或替换不合法的 `single_block` 条目。它只表示生成完整候选 Schedule 时还要同步处理什么，不是新的持久化数据模块。能确定处理的校正直接进入完整候选；存在多种会造成不同用户影响的处理方式时，交给 `replanning-and-conflicts.md` 形成取舍方案，再按 `decision-and-confirmation.md` 判断授权。

先确定本轮的覆盖基线：

- 首次排期或纯新增排期时，基线是当前正式 Schedule 中尚未开始的 Todo 条目；
- 重新编排时，`replanning-and-conflicts.md` 先确定需保留和需移除或替换的旧条目；只有保留条目计入覆盖量，待替换条目不得继续冒充未来覆盖。

计算覆盖量前先确定性检查 `single_block` 的尚未开始条目：只有一个合法连续块且其时长等于当前剩余工作量时才能保留；已有覆盖不足、过多、被拆成多块或已不合法时，将该 Todo 相关的尚未开始条目全部标记为待替换。这些条目不计入覆盖基线，因此后续仍使用统一的差额公式，不需要为 `single_block` 另建待排字段。

对覆盖基线扫描一次，按 `todo_id` 汇总尚未开始的 Todo 条目计划时长：

```text
entry_future_minutes
= entry.ends_at - entry.starts_at

future_coverage_minutes(todo_id)
= 同一 todo_id 的全部 entry_future_minutes
```

一个条目只在同时满足以下条件时计入覆盖量：

- `source_type = todo`；
- 引用的 Todo 存在；
- `starts_at > calculated_at`；
- 条目仍属于当前正式 Schedule，且本轮没有将它标记为移除或替换。

因此：

- 尚未开始的条目计入全部时长；
- 已结束条目对未来覆盖量贡献为 0，不能冒充实际投入；
- 正在进行的条目不进入覆盖量计算，但它的剩余时间继续占用日历；
- TimeConstraint 不参与 Todo 工作量覆盖。

已过去条目的计划时长不自动从 `remaining_estimate_minutes` 扣除。实际还剩多少必须先按进度核对规则恢复事实。

## 3. 按固定顺序分组 Todo

每个 Todo 只进入以下一个主要分组，并必须按顺序判断：

### 3.1 `terminal`

条件：

- Todo `status` 是 `completed` 或 `cancelled`；或
- 所属 Goal/Node 已取消并使该 Todo 失效。

处理：不进入编排，并在本轮完整候选计划中清理其尚未开始的未来 ScheduleEntry；不得绕过候选、校验和原子提交直接改正式数据。

### 3.2 `in_progress_schedule`

条件：Todo 存在 `starts_at <= calculated_at < ends_at` 的正在进行 ScheduleEntry。

处理：在条目结束，或用户主动报告中断、提前完成之前，冻结该 Todo。不推断已完成多少，不重算待排工作量，也不新增补排条目。新确认的硬约束必然与条目的未来部分重叠时，交给 `replanning-and-conflicts.md` 按中断边界处理，并询问剩余工作是否需要另行安排。

用户在执行中报告中断、提前完成或取消时，先由 `progress-review.md` 更新权威进度事实，再由 `replanning-and-conflicts.md` 决定当前条目剩余时间如何释放和重排；本文不把原定时长当成实际投入。

### 3.3 `needs_progress_review`

条件：本轮需要为该 Todo 新增或重算具体日程，且它存在尚未核对的已结束 ScheduleEntry。正在进行条目不触发本分组。

处理：只暂停该 Todo 的新增排期，先读取 `progress-review.md` 取得当前处理所需的进度信息。少量 Todo 可以局部询问；生成下一段滚动计划或全局重排时，先完成完整进度核对。无关事实、无关 Todo 和只读查询不因此被全局阻塞。

### 3.4 `deferred_or_blocked`

条件包括：

- 所属 Goal 已暂停，或所有非空归属路径都当前无效；
- Todo 原始弹性窗口与本轮 `planning_window` 没有交集；
- 必要前置 Todo 在本轮窗口内无法形成可行的预计完成时间；
- 其他硬约束使 Todo 本轮不可执行。

处理：本轮不排，但保留 Todo 和原始事实，不将“本周不排”误解为取消或遗忘。下一轮重新计算。

如果前置 Todo 能在本轮窗口内先完成，后续 Todo 可以继续作为候选；其最早可排时间取前置 Todo 的预计完成时间，不必等到前置 Todo 已经是 `completed`。

### 3.5 `fully_covered`

条件：

```text
future_coverage_minutes = remaining_estimate_minutes
```

处理：已有未来 ScheduleEntry 已覆盖当前需求，不新增条目。

对 `single_block` Todo，仅有原条目总分钟数相等还不够；必须确认保留覆盖由一个合法连续块完成。被分成多块或其他不合法的旧覆盖已在第 2 章被标记为待替换，不会进入本分组。

### 3.6 `overcovered`

条件：

```text
future_coverage_minutes > remaining_estimate_minutes
```

处理：不新增条目，并形成缩短或移除多余尚未开始条目的 Schedule 校正结果。该校正能否连同其他改动直接提交，继续按 `decision-and-confirmation.md` 判断。

### 3.7 `scheduling_backlog`

条件：Todo 当前可执行，且：

```text
future_coverage_minutes < remaining_estimate_minutes

minutes_to_schedule
= remaining_estimate_minutes - future_coverage_minutes
```

只有这个分组会作为本轮新增或替换排期的候选交给编排器。`minutes_to_schedule` 不得小于 0，它表示当前尚未被保留日程覆盖的总工作量，也是本轮可新增安排的上限；它不表示所有 `splittable` Todo 都必须在本周排完。

`single_block` 因旧覆盖不合法而进入本分组时，本轮不是在旧条目上追加差额：应替换相关旧条目，并为当前全部 `remaining_estimate_minutes` 重新形成一个连续块。

当前周期全部未结束 Habit Todo 都必须参与上述分组。用户额外增加的当期 Habit 次数即使不改变 `minimum_count`，对应 Todo 也必须进入本轮待排工作量判断。

## 4. 只把必要信息交给语义编排

确定性分组完成后，AI 编排器只接收 `scheduling_backlog` 中的 Todo 及形成可行计划所需的相关上下文：

- Todo 标题和当前剩余工作语义；
- `minutes_to_schedule`；
- 与该 Todo 相关的 Schedule 校正要求，包括哪些旧条目需保留、移除或替换；
- 运行时有效可排窗口和合法候选时段；
- Todo 自身及有效归属路径的 DDL；
- 相关前置、可解锁的后续 Todo 和 Goal `priority`；
- `execution_mode`、`intensity` 和 Habit 软硬偏好；
- 当天精力画像、TimeConstraints 以及本轮需要保留的已有条目。

不把 `terminal`、本轮无关的长期 Todo 或完整历史 ScheduleEntry 全量提供给 AI。AI 不得改写确定性分组、未来覆盖量或 `minutes_to_schedule`；但对 `splittable` Todo，可以根据本轮容量在 `0..minutes_to_schedule` 内决定实际新排时长。程序化校验仍对完整候选负责。

## 5. 在合法候选中决定排布

排布前先排除任何会产生以下结果的候选：

- 与 TimeConstraint 或需保留的 ScheduleEntry 冲突；
- 位于 Todo 运行时有效窗口之外；
- 违反依赖顺序；
- `single_block` 无法容纳当前全部剩余工作量的一个合法连续块；
- 突破默认 AvailabilityRules 且没有该具体条目的合法 `availability_exception`；
- 会导致已确认 DDL 不可满足或其他硬约束冲突。

只在合法候选中综合以下层次作决定：

1. 优先保护已经逼近 DDL、继续延迟将导致无解的条目。
2. 其次优先能解锁后续 Todo 或处于关键交付路径的条目。
3. 可行性相近时，优先更高 Goal `priority`。
4. 再考虑弹性窗口剩余余量、待排工作量、精力匹配、上下文切换和对现有日程的扰动。

Goal `priority` 表示跨日、跨周的长期相对重要性，不表示紧急程度。一个临近硬 DDL 的 P1 可以先于暂时没有时间压力的 P0；P0 也不能授权系统违反 DDL、依赖、TimeConstraint 或可用容量。

在 Goal 长期相对重要性这一层内，`P0 > P1 > P2`：P0 是容量冲突时优先保护的目标，P1 是正常重要，P2 是容量不足时相对可让步。这组领域优先级不等于开发范围的功能 P0/P1/P2。

不要把上述层次强制实现成固定数字评分。AI 可以结合语义判断，但必须能说明主要压力来源，并由确定性校验保证硬约束。

共享 Todo 只从当前有效归属路径读取最高 Goal `priority` 和最早非空 DDL。多条归属可以提高其解锁价值，但不得为同一行动重复生成日程或重复计算工作量。

本轮只在当前 `planning_window` 内分配时间。对 `splittable` Todo，可以只安排当前窗口能合理容纳的一部分；本轮实际新排量直接从候选 ScheduleEntry 汇总，不新增持久化字段。如果 Todo 的 DDL 或有效窗口要求它必须在本轮窗口内完成，则候选 Schedule 必须完整覆盖届时所需的剩余工作量；排不下时进入冲突处理，不能用部分安排伪装成可行。

## 6. 执行模式、Habit 偏好和覆盖校正

### 6.1 `single_block` 和 `splittable`

- `single_block` Todo 必须用一个连续时间块覆盖当前剩余工作量。
- `splittable` Todo 可以对应一个或多个 ScheduleEntry；本轮可只排一部分，但保留覆盖与新增覆盖之和不得超过当前剩余工作量。

`single_block` Todo 已有的未来覆盖只有一部分、被拆成多个块，或无法与新时间合并为一个连续块时，不能简单再追加一个块。应将它作为 Schedule 校正，为当前全部剩余工作量重新形成一个合法连续块；如果无法形成，不得伪造已完整覆盖的日程。

### 6.2 Habit 偏好

Habit 专属的长期星期和时段偏好由 `habit_rule` 提供：

- `required` 偏好作为硬候选边界，不满足的时段不可用；
- `preferred` 偏好作为软排序信号，应尽量满足，但不满足不使日程非法；
- 当期 Todo 的临时 `execution_window` 始终参与合法性计算；
- 用户对所有任务普遍适用的作息仍来自 AvailabilityRules。

### 6.3 精力匹配

高强度 Todo 与低精力时段不匹配属于软排序信号。可以降低该候选的优先度并向用户说明，但不因此判定 ScheduleEntry 非法，也不需要为精力不匹配保存 `availability_exception`。

## 7. 生成当日关注顺序

每次生成或重新编排 Schedule 时，必须运行 `scripts/rank_plan.py`，对每个自然日内所有 `source_type = todo` 的 ScheduleEntry 生成从 1 开始、唯一且连续的 `daily_rank` 和对应 `priority_reason`。`1` 表示当天最应保护注意力的安排，不表示最早开始，也不自动表示此刻可执行。

- `daily_rank` 只在同一日期内比较，不跨日期排名；
- TimeConstraint 条目不参与排名，仍按固定时间展示；
- `Schedule.entries` 数组顺序不承载执行顺序语义；
- 同一 Todo 同日有多个时间块时，每个 ScheduleEntry 都是独立的当日顺序项；
- `daily_rank` 不替代 `starts_at` 和 `ends_at`，也不替代当前可行动性判断；固定时间未到或当前依赖未满足的条目不会因此提前执行；
- 不得按 `starts_at`、数组顺序或 viewer 展示顺序直接生成排名；开始时间只能在所有语义信号相同后作为稳定兜底。

确定性排序按以下层次比较：

1. 有效 DDL 压力：已到、24 小时内、7 天内、较远、无 DDL；同档取更早的有效 DDL。
2. 依赖与解锁价值：能解锁更多未结束后续 Todo 的条目优先；同日存在前置与后续时必须保持拓扑顺序。
3. 有效归属路径中最高 Goal `priority`，按 `P0 > P1 > P2`；独立行动按正常重要性处理。
4. 弹性时间窗余量，窗口更早收窄的优先。
5. 只有以上相同才按 `starts_at` 和条目 ID 稳定排序。

`priority_reason` 必须由同一排序模块生成，说明实际生效的 DDL、解锁价值和目标优先级，不得用“时间更早”冒充关注原因。`validate_plan.py` 会重新派生顺序并做硬比较；连续但语义错误的排名同样非法。

用户明确要求覆盖某日关注顺序时，不把该偏好写成 Todo 或 Goal 的永久优先级。该日每个 Todo ScheduleEntry 都必须保存内容相同的 `ranking_exception = {confirmed_at, reason}`，从而明确这是用户覆盖而不是算法漏算；局部、无理由或不同内容的覆盖非法。是否可直接提交本次变化，继续按 `decision-and-confirmation.md` 判断。

## 8. 完整 Schedule 生成顺序

一次完整生成或重新编排按以下顺序处理：

```text
0. 确认任务语义、对象分类、新结构确认和提交前语义质量检查已经完成
1. 从正式任务结构中初步筛选本轮相关 Todo
2. 确定需保留、移除或替换的旧条目，汇总保留条目的未来覆盖量
3. 展开当前 planning_window 内的 TimeConstraint occurrence
4. 按日期画像、Todo 有效窗口和硬占用生成合法候选时间
5. 结合状态、进度门槛、时间可行性和未来覆盖量，按第 3 章的固定顺序完成分组
6. 将 scheduling_backlog 和相关 Schedule 校正要求交给语义编排
7. 在当前 planning_window 内生成新条目或替换条目，splittable 允许只覆盖部分剩余工作量
8. 运行 `scripts/rank_plan.py`，为每个日期的 Todo 条目生成关注顺序和理由
9. 将 Todo 条目、TimeConstraint 投影和需保留条目汇总为完整候选 Schedule
10. 校验时间、引用、覆盖上限、依赖、DDL、容量和关注顺序语义
11. 根据当前授权直接提交，或展示整组方案等待用户确认
```

首次排期和重新排期共用上述排布和校验规则。两者的差别在于：重排前由 `replanning-and-conflicts.md` 先确定受影响集和需要保留的条目；每周首次正式排期能否直接提交，由 `decision-and-confirmation.md` 决定。

## 9. 结果解释与失败边界

排期成功后的说明至少应让用户理解：

- 哪些 Todo 进入了当前日程；
- 它们为什么被安排在该日期和时段；
- 主要压力来自 DDL、依赖、窗口、Goal 优先级还是容量；
- 哪些任务因为已覆盖、当前阻塞或容量原因没有新排；
- 哪些 `splittable` Todo 本周只安排了一部分，还有多少未被未来日程覆盖；
- 如果移动或延后了其他条目，原因和影响是什么。

不需要暴露内部打分或逐条复述 JSON，但应说明会改变用户判断的关键原因。`priority_reason` 可以保存简短说明，但不是新的权威任务事实。

如果在已有硬约束内无法形成完整可执行日程，不得伪造看似已完整覆盖的 Schedule，也不得自行改动 DDL、承诺或任务去留。冲突识别和取舍方案读取 `replanning-and-conflicts.md`，确认权限读取 `decision-and-confirmation.md`。
