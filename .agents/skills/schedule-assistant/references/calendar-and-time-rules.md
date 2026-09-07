# 时间事实、可用时间与日历规则

任何涉及 DDL、Todo 弹性时间窗、AvailabilityRules、TimeConstraint、日期例外、循环占用或跨日计算的操作，读取本文。生成或重新编排具体日程时也必须读取。

本文把时间规则组织成一条处理流程：先判断用户说的是什么时间事实，再得到每天基础可用画像，展开必须扣除的硬占用，最后计算某个 Todo 的合法候选时间。

本文不选择本轮要排的 Todo，不决定 Todo 在候选中的具体位置或当日顺序，也不决定现实变化后哪些已有条目进入重排。

## 主流程

```text
1. 识别时间事实的对象和语义
2. 为受影响日期得到完整基础可用画像
3. 展开并扣除 TimeConstraint 硬占用
4. 计算 Todo 的运行时有效窗口和合法候选时间
5. 将合法候选交给排期规则，或将时间变化交给重排规则
```

## 按问题查找

| 当前问题 | 读取章节 |
| --- | --- |
| 用户说的时间应写到哪个对象 | 1 |
| 某天默认允许排什么时间 | 2 |
| 首次怎样建立可用时间基线 | 2.2 |
| 工作日、节假日和调休怎样查询 | 2.3～2.4 |
| TimeConstraint 和循环占用怎样展开 | 3 |
| 单次跳过、改期或永久修改系列 | 3.3～3.4 |
| Todo 最终哪些时间合法 | 4 |
| 时间事实修改后应当做什么 | 5 |
| 跨午夜、旅行或修改计划时区 | 附录 A～C |

## 1. 识别时间事实

### 1.1 统一时间基准

整份计划使用 `plan_meta.timezone` 作为用户时区。该值必须是 IANA 时区，用于：

- 解释“今天”“明天”“周一”等相对日期；
- 划分自然日和当周计划窗口；
- 展开循环 TimeConstraint 和周期任务；
- 判断条目是否跨日和应在哪些日期展示；
- 按 `scheduling.md` 的确定性规则计算每日 Todo 关注顺序；时间画像只提供可行性输入，不能把开始时间直接当排名。

所有正式 datetime 必须包含时区偏移。字段格式和持久化要求读取 `plan-data-contract.md`。

空计划初次建立时，可以根据运行环境提出时区默认值，并让用户可以纠正。当用户输入、环境时区和计划已有时间事实之间存在明显冲突时，不得静默采用环境默认值。

### 1.2 选择正确对象

| 用户描述的事实 | 应使用的对象 | 例子 |
| --- | --- | --- |
| 为某个目标需要完成、可判断结果的行动 | Todo | 目标内需要完成的固定时间答辩 |
| 确定的独立事件，该时间不能安排 Todo | `commitment` TimeConstraint | 已确认的聚餐、航班、只需保护占用的外部会议 |
| 没有具体事件，但用户不允许排任务 | `unavailable` TimeConstraint | 今晚不工作、旅行全程不可用 |
| 对各类任务普遍适用的默认作息和精力画像 | AvailabilityRules | 工作日 19:30–23:00 可排任务 |
| 某天或连续几天整体采用不同作息 | `date_overrides` | 旅行期间每天只有 08:00–09:00 可用 |

同一件事不得同时作为 Todo 和 TimeConstraint 保存。固定时间不会自动把 Todo 变成 TimeConstraint；关键是它是需要完成的行动，还是一段独立占用。

时间对象分类必须发生在任务语义定位之后。开会、汇报、评审、对进度或和老板沟通如果推进某个目标，需要准备、形成决定或跟踪后续，应作为 Todo 理解并安排到固定时间；只有系统只需保护独立事件占用时，才作为 `commitment`。信息不足且两种解释会改变任务结构时，先读取 `task-structure.md` 并追问，不能仅因已有时刻就建立 TimeConstraint。

只要该事项所在的发现分支仍是 `open`，就不得抢先用 Todo、TimeConstraint、Habit 或日程条目把一种解释固化下来。用户回答了另一条分支，不等于本分支为“没有”；继续按 `discovery-loop.md` 追问或明确延后。

通勤、做饭、学习或运动等重复行为也不能仅因稳定发生就自动建立循环 TimeConstraint。只需保护时间且不关心逐次完成时使用循环 TimeConstraint；用户希望坚持、打卡并判断周期达标时，由任务结构建立 Habit Goal 和 Habit Todo；每周期重复交付结果时使用 Recurring Goal。若 Habit Todo 同时推进减重等其他 Goal，通过共享归属表达。Todo 的 ScheduleEntry 已经占用时间，同一次行为不得再复制为 TimeConstraint。

### 1.3 DDL 和 Todo 原始弹性窗口

DDL 是 Goal、Node 或 Todo 必须产生约定结果的最晚时刻。所有非空正式 DDL 都是硬约束，不是系统可以为了排得下而自行移动的预计日期。

用户只给 DDL 日期而没有具体时刻时，不能自行补成 18:00、23:59 或其他时刻。是否追问以及能否提交，读取 `decision-and-confirmation.md`。

“晚上前”“这周内”“尽快”等模糊时间表达也不是精确正式 DDL。可以在不改变用户承诺的前提下据此推导更早的执行窗口或优先安排，并让用户看见该假设；不能把推导出的窗口端点反向冒充为用户确认的 deadline。若精确时刻会改变近期合法性或重大取舍，必须追问。

Recurring Goal 的周期、首轮开始和首轮精确 DDL 确认后，后续周期 DDL 可按已确认的锚点和规则推导，不需要逐轮确认。周期建模和实例生成读取 `task-structure.md`。

Todo `execution_window` 表示该行动原本允许被安排或移动的范围。它是 Todo 本身的稳定边界，不等于当前一定有空的时间，也不等于 DDL。弹性窗口结束不代表交付已延迟；只有 DDL 已过且约定结果尚未完成，才构成交付延迟。

用户未提供 `execution_window` 时，Agent 可以根据 DDL、依赖、剩余工作量、可用时间和协作条件推导并写入，同时向用户说明。只有窗口选择会改变可执行性或产生重要取舍时，才追问或随任务结构方案确认。

推导 `execution_window` 只说明系统允许把行动安排在哪个范围，不创建新的现实承诺，也不解决尚未确认的目标、行为动机或对象类型。语义仍不明确时，不能先用一个看似合理的窗口绕过任务结构追问。

## 2. 得到每日基础可用画像

### 2.1 AvailabilityRules 的语义

AvailabilityRules 是自动编排 Todo 的默认时间和预计精力画像，不等于用户每天真实的全部空闲时间，也不从 Schedule 反向推导。

MVP 只使用两套默认画像：

- `workday`：正常工作日和调休上班日；
- `holiday`：普通周末和法定休息日。

每个 segment 位于计划时区下的同一自然日，不得跨午夜。segment 的 `energy_level` 是软排序信号，不是硬合法性条件。Todo `intensity` 与时段精力不匹配可以降低候选质量并在需要时向用户解释，但不因此成为非法日程。

### 2.2 建立可用时间基线

空计划可以先保留 `calendar_id = null` 和两套空画像，但在首次生成正式具体日程前，必须确定：

- 用户的正常日历，或者为当前编排窗口内每个日期建立完整 `date_overrides`；
- 普通工作日的完整可用时间画像；
- 普通休息日的完整可用时间画像。

已存在正式计划时，复用已建立的时区、日历和默认画像，不因新增 Goal 或 Todo 重复询问。只有用户表达了新的长期作息、当前日期需要例外，或已有信息无法解释受影响日期时，才补充或修改时间事实。

用户只提供可用时段、没有描述精力差异时，可以将相应 segment 的 `energy_level` 暂解释为 `medium`，并作为可见假设让用户纠正；不得因为精力信息缺失而把可用时间采集扩展成精细画像问卷。

### 2.3 解析某个日期的完整基础画像

对每个受影响日期，按以下顺序处理：

```text
1. 检查该日是否命中 date_overrides
2. 命中时，直接使用 override 的完整 segments
3. 未命中时，按 calendar_id 查询该日是 workday 还是 holiday
4. 读取对应默认 segments
```

`date_overrides` 表示某个具体日期或连续日期范围最终采用的完整时间和精力画像，不是对默认规则保存增删补丁。某日命中 override 时，无需为了选择默认画像再查询它原本的日期类型。空 segments 表示该日默认完全不排 Todo。

任意日期最多命中一条 override。修改已有例外时，通过替换、缩短、拆分或合并保持日期不重叠；用户撤销例外时删除对应记录。

### 2.4 正常日历查询与失败处理

对未命中完整 override 的日期，首次生成、跨周生成或重新编排时都必须按 `calendar_id` 查询真实分类。查询必须包含该日历的特殊工作日和休息日；例如 `CN` 不能只根据星期几判断，必须识别法定休息日和调休上班日。

查询结果只作为本次候选计划的计算输入，同一生成事务内可以复用，但不将国家日历、查询响应或本地缓存持久化到正式计划。

查询失败、来源冲突或无法确认某些日期时：

1. 不得静默降级成“周一至周五是工作日”；
2. 不得基于未知分类提交受影响日期的新正式排期；
3. 列出无法确认的日期，请用户确认它们最终采用的完整画像；
4. 把用户确认后的完整画像写入 `date_overrides`，再继续编排。

`calendar_id = null` 只是尚未配置的合法空状态。自动生成正式周计划前，必须配置正常日历，或者受影响的每个日期都已有用户确认的完整 `date_overrides`。

## 3. 展开并扣除 TimeConstraint 硬占用

### 3.1 TimeConstraint 的语义

TimeConstraint 表示一段不能用于安排 Todo 的时间：

- `commitment`：已经有一件确定的独立事件；
- `unavailable`：没有具体事件，但用户不允许安排 Todo。

两者在容量计算中效果相同，都必须先于 Todo 扣除。`kind` 只保留业务语义和展示差别。

TimeConstraint 没有完成、部分完成或剩余工作量语义，也不进入 Todo 进度核对。时间过去后它只是历史占用。

一次性 TimeConstraint 直接使用 `starts_at` 与 `ends_at`。循环 TimeConstraint 以首次 `starts_at` 作为统一锚点，并使用首次起止时间的差值作为每次持续时长。循环支持 `day`、`week`、`month` 和大于 0 的间隔。

同一周需要在多个不同星期重复时，MVP 使用多个 TimeConstraint，不在单个 recurrence 中表达多星期选择。

### 3.2 展开当前计划窗口

输入是全部 `status = active` 的 TimeConstraint、当前 `planning_window` 和计划时区。按以下顺序确定窗口内的硬占用：

```text
筛选 active TimeConstraint
→ 一次性约束检查是否与 planning_window 相交
→ 循环约束从 starts_at 锚点直接计算候选 occurrence
→ 过滤 recurrence.ends_on 之后的 occurrence
→ 应用 recurrence.exceptions
→ 将最终发生时间与 planning_window 相交的 occurrence 投影为 ScheduleEntry
```

后续 occurrence 必须从原始锚点直接计算，不从上一次 occurrence 逐次累加，避免日期漂移。每次 occurrence 沿用首次的本地开始时间和持续时长。

月循环的锚点日在目标月份不存在时，当月使用最后一天；下一轮仍从原始锚点推导，不得把月末降级结果变成新锚点。

只要 `reschedule` 后的替代时间与当前 `planning_window` 相交，就必须在该窗口投影；不能因原 occurrence 日期在窗口外而遗漏已改期的占用。

TimeConstraint occurrence 在 Schedule 中是可重建投影，不在权威对象中保存“本周已生成”或 occurrence 状态。投影字段读取 `plan-data-contract.md`。

### 3.3 单次跳过与改期

单次跳过或改期必须先写入权威 `recurrence.exceptions`，再重建对应 Schedule 投影；不能只删除或移动 ScheduleEntry，否则下次展开会让原 occurrence 复活。

`occurrence_starts_at` 始终是基础循环原本生成的开始时间，是单次例外的稳定匹配键：

- 同一键最多一条例外；
- 再次修改同一次时，替换原记录，不追加第二条；
- 不同 occurrence 的例外分别累积，不得互相覆盖；
- `skip` 和 `reschedule` 互相改动时，替换同一条例外；
- 修改已改期的替代时间时，匹配键不变；
- 恢复该次原定安排时，删除对应例外。

### 3.4 永久修改、取消与历史

用户明确要求永久修改整个系列时，更新基础锚点或 recurrence，不为以后每一次追加例外。更新前先找出尚未发生的 future exceptions：

1. 按旧规则确定每条例外对应的 occurrence 序号；
2. 按新规则尝试迁移到相同序号；
3. 如果频率、起止范围或语义变化使迁移无法可靠完成，列出受影响例外并请用户确认，不得静默丢弃或错配。

用户永久取消整个循环系列时，将 TimeConstraint 设为 `status = cancelled`，停止生成并移除未来 occurrence 投影。“只取消这一次”仍使用 `skip` exception，不取消整个系列。

取消整个系列只阻止未来 occurrence；已过去的 ScheduleEntry 不因此被删除，继续按 Schedule 历史保留规则处理。已过期 exception 不再参与未来编排，但在历史归档策略正式建立前继续保留；处理其他 occurrence 时不得顺带删除或覆盖它们。

用户是否已授权“仅这一次”或“整个系列”的修改，以及迁移失败时如何确认，读取 `decision-and-confirmation.md`。

### 3.5 合并硬占用

TimeConstraint 之间可以语义重叠，例如“今晚不工作”中同时包含一场聚餐。容量计算时按所有 TimeConstraint 时间的并集扣除，不得因多条语义重叠重复扣减时长。

前端可以优先突出更具体的 `commitment`，但不能为了简化展示而删除或覆盖 `unavailable` 权威事实。

TimeConstraint 始终优先于 `date_overrides`、单条 Todo 的 `availability_exception` 和普通排期候选。任何可用时间例外都不能穿过已占用时间。

## 4. 计算 Todo 的合法候选时间

### 4.1 运行时有效可排窗口

有效可排窗口是运行时结果，不要回写覆盖 Todo 原始 `execution_window`。

依赖计算中的“前置 Todo 预计完成时间”也只是运行时结果，不保存新字段：

- 前置 Todo 已完成时，依赖已经满足，不再需要一个未来完成时刻；
- 前置 Todo 尚未完成时，将本轮保留和候选的合法未来 ScheduleEntry 按时间排序，并用其覆盖当前 `remaining_estimate_minutes`；只有覆盖完整时，才取完成所需最后一个条目的 `ends_at` 作为预计完成时间；
- `single_block` Todo 只有存在一个完整合法连续块时，才能形成预计完成时间；
- `splittable` Todo 如果当前窗口只覆盖部分剩余工作，则预计完成时间在当前窗口内未知，不能把依赖它的后续 Todo 当作本周已可开始；
- 正在进行的 ScheduleEntry 本身不是完成证据；除非用户已经报告完成并更新 Todo 事实，否则不能仅按它的原计划结束时间认定依赖已满足。

```text
有效开始 = max(
  execution_window.starts_at,
  所有前置 Todo 的预计完成时间
)

有效结束 = min(
  execution_window.ends_at,
  Todo 自身 DDL,
  所有有效 Node/Goal 归属路径上的 DDL
)
```

只纳入存在的非空 DDL。暂停、完成或取消的上层路径不继续提供当前 DDL；共享 Todo 必须同时检查每条有效归属路径。

有效窗口为空、剩余时间容量不足，或无法满足对应 DDL 时，不得通过改写原始窗口或 DDL 伪造可执行结果。

### 4.2 合成合法候选

对每个 Todo 和受影响自然日，按以下顺序处理：

```text
1. 按第 2 章得到当天完整基础可用画像
2. 与当前 Todo 的运行时有效可排窗口取交集
3. 扣除第 3 章展开并合并后的 TimeConstraint 时间并集
4. 扣除本轮不应移动、需要保留的 ScheduleEntry
5. 得到合法候选时间
```

这里只产生可供选择的合法时间。哪些 Todo 进入本轮、应放到哪个候选中，以及当日实际执行顺序，由 `scheduling.md` 决定。

### 4.3 单条 Todo 突破默认可用时间

AvailabilityRules 是自动排期的默认硬边界。只针对单条 Todo 突破该边界时，不扩大 AvailabilityRules，而是按确认规则在对应 ScheduleEntry 保存窄范围 `availability_exception`。

该例外必须建立在已解析出的当天完整基础画像上。某日未命中 override 且日历查询失败时，不能靠单条 `availability_exception` 继续正式排期。

`availability_exception` 只能突破默认 AvailabilityRules，不能绕过 Todo 有效窗口、DDL、依赖、TimeConstraint 或需要保留的日程条目。具体确认和时间改动后的例外生命周期，读取 `decision-and-confirmation.md`。

## 5. 时间事实变化后的交接

时间事实变化后，先用本文恢复新的时间语义和合法候选集：

- TimeConstraint 新增或扩大后，与其重叠或因容量受影响的 Todo 可能需要重排；
- TimeConstraint 取消或缩短后，不默认把释放的时间填满；
- AvailabilityRules 或 `date_overrides` 变化后，重新生成生效范围的基础画像，并检查已有 Todo 条目是否仍合法；
- TimeConstraint 规则或 exception 变化后，重新展开受影响计划窗口，使 Schedule 投影与权威数据保持一致。

交接规则：

- 需要确定哪些已有条目进入重排、如何扩大影响范围或处理无解冲突时，读取 `replanning-and-conflicts.md`；
- 需要判断变化能否直接提交，还是必须先让用户选择时，读取 `decision-and-confirmation.md`；
- 需要把已授权事实写入正式 JSON 时，读取 `plan-data-contract.md`。

## 附录 A：跨午夜与按日展示

### A.1 跨午夜 Availability

MVP 不保存跨午夜 Availability segment，也不把前一天的画像自动延伸到次日。跨午夜可用时间必须分别表达为午夜前后各自自然日内的 segment。

例如“工作日 22:00 到第二天 01:00 可以做事”中，次日 00:00–01:00 究竟按前一天还是按它自己的日期类型适用，可能产生不同结果。如果用户没有分别说清午夜前后适用哪些日期，进行最少必要追问，不自行机械拆分并写入。

### A.2 跨日 Schedule 投影

- Todo ScheduleEntry 不得跨越计划时区的自然日；需要跨日执行的工作拆成不同日期的条目。
- TimeConstraint 使用完整 datetime，可以跨午夜或跨多日。
- 跨日 TimeConstraint occurrence 在 Schedule 中仍保存为一个完整条目，不切成多个持久化片段。
- 前端按日展示时，根据计划时区和日边界临时裁切视图；这不产生新的 ScheduleEntry 或片段 ID。

## 附录 B：旅行期间

| 现实情况 | 表达方式 |
| --- | --- |
| 整个旅行期间完全不工作 | 一条覆盖整个范围的跨日 `unavailable` TimeConstraint |
| 旅行期间仍有少量临时可工作时间 | 使用 `date_overrides` 表达每天完整临时画像 |
| 航班、聚餐、确定活动 | 各自建立 TimeConstraint |
| 准备行李、订酒店等需要完成的行动 | Todo |

旅行中仍有可用时间时，不能再用一条跨日 unavailable 封住整段旅行，否则会把 `date_overrides` 给出的临时可用时间一并扣除。

如果用户后来改为旅行期间完全不工作，删除对应日期范围的旅行 `date_overrides`，改为一条覆盖整个范围的跨日 `unavailable` TimeConstraint，再由重排规则处理受影响条目。

## 附录 C：修改计划时区

MVP 将 `plan_meta.timezone` 视为初始化后稳定的整份计划基准，不支持把修改时区当成普通字段更新直接提交。用户要求修改时区时，把它视为整份计划的时间语义迁移。

迁移前必须让用户确认受影响的各类时间事实是：

- 保留原绝对时刻，只改变它在新时区下的本地显示与日期归属；
- 还是保留原当地钟表时间，并因此改变实际时刻。

一次性事件、循环 TimeConstraint、Todo 时间窗、DDL 和 AvailabilityRules 可能需要不同选择，不得使用一个默认策略静默批量改写。确认前保持正式计划不变；确认后基于最新正式计划生成整份候选、重新计算并原子提交。

时区迁移必须至少重新检查：

- AvailabilityRules 的本地时间段是否仍表达用户意图；
- 循环 TimeConstraint 在新时区下的本地开始时间和 occurrence；
- ScheduleEntry 的自然日分组和跨日判断；
- Todo 的 `daily_rank` 应属于哪一天。
