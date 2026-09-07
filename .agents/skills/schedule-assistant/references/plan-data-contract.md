# 正式计划数据合同

在初始化 `plan.json`、读取或修改正式数据、构造待提交候选、解释字段、开发校验脚本，或让 viewer 消费计划时读取本文。

本文只回答：已经确定的正式事实怎样保存在 `plan.json`。任务为什么这样建模、Todo 怎样排时间、过去实际做了多少、哪些变化需要确认，分别由其他 reference 决定。

## 按问题查找

| 当前问题 | 读取章节 |
| --- | --- |
| 数据模块全貌、依赖关系或空计划 | 1～2 |
| 版本、时区、进度确认点 | 3 |
| Goal / Recurring / Habit 字段 | 4 |
| Node 字段 | 5 |
| Todo、归属、依赖字段 | 6 |
| TimeConstraint 与循环例外 | 7 |
| AvailabilityRules | 8 |
| Schedule / ScheduleEntry | 9 |
| 哪些内容不得持久化 | 10 |
| 修改后要同步更新什么 | 11 |

## 1. 合同约定

- `plan.json` 是正式计划的唯一事实来源。聊天记录、自然语言建议和临时候选都不能替代它。
- 顶层始终保留 `plan_meta`、`task_structure`、`time_constraints`、`availability_rules` 和 `schedule` 五个模块；空计划也不省略模块。
- `datetime` 使用带 UTC 偏移的 RFC 3339 字符串，例如 `2026-08-20T18:00:00+08:00`。
- `date` 使用 `YYYY-MM-DD`；本地 `time` 使用 `HH:MM`，并按 `plan_meta.timezone` 解释。
- Goal、Node、Todo 的正式 DDL 只有在具体时刻已经确认时才写入 `deadline`；只有日期或尚未确认时保持 `null`。
- 未确定且允许为空的单值使用 `null`；集合没有内容时使用空数组，不省略字段。
- 同一事实只在一个权威模块保存。其他模块通过引用或运行时计算消费，不复制源事实。
- 对象 ID 在各自类型内稳定且唯一。ScheduleEntry ID 只要求在当前 Schedule 内唯一。
- `created_at` 在对象首次正式创建时写入；`updated_at` 只在该对象正式内容改变时更新。

## 2. 数据模块总览与合法空计划

### 2.1 五个顶层模块

`plan.json` 由五个长期稳定的顶层模块组成。判断一个事实应该写在哪里时，先看它回答的是哪类问题：

| 模块 | 负责回答 | 保存的权威事实 | 不负责 |
| --- | --- | --- | --- |
| `plan_meta` | 这份正式计划是什么版本，按哪个时区解释，进度核对到哪里 | 正式版本、时区、进度核对截止点、整份计划更新时间 | 任务内容和具体排期 |
| `task_structure` | 用户要推进什么，行动之间是什么关系 | Goal、Node、Todo 及其状态、约束、归属和依赖 | Todo 具体安排在几点 |
| `time_constraints` | 哪些时间已被事件占用或明确不可用 | 独立事件、不可用时间、循环规则和例外 | 用户通常何时愿意工作 |
| `availability_rules` | 在没有其他占用时，用户通常有哪些可工作时间 | 工作日/休息日的默认可用画像，以及特定日期的完整覆盖画像 | 动态空闲时间和具体日程 |
| `schedule` | 当前正式生效的近期计划具体放在什么时间 | 当前排期窗口、生成时间、Todo 和 TimeConstraint 的具体时间投影，以及单条 Todo 的可用时间例外 | 复制任务标题、状态、DDL 等源事实 |

### 2.2 模块如何相互依赖

排期时的数据流如下：

```text
                       plan_meta
                  （时区和正式版本）
                          │
                          ▼
task_structure ─────┐
（要安排的 Todo）    │
                    ├──► schedule
time_constraints ───┤   （当前正式生效的具体排布）
（先扣除的占用）      │
                    │
availability_rules ─┘
（剩余候选时间边界）
```

- `plan_meta.timezone` 参与所有自然日、时间和循环计算；任一正式修改提交后，再统一更新 `plan_version` 和 `updated_at`。
- `schedule` 通过 ID 引用 `task_structure` 中的 Todo 和 `time_constraints` 中的对象，不复制它们的标题、状态、DDL 或循环定义。
- `availability_rules` 不被 Schedule 消耗或改写。运行时用它生成基础候选时间，再扣除 TimeConstraint 和需要保留的 ScheduleEntry。
- Goal、Node、Todo 和 TimeConstraint 可以在尚未排期时独立存在；没有 ScheduleEntry 不代表对象无效或已完成。
- 上游权威事实变化时，应重新判断受影响的 ScheduleEntry；只移动 ScheduleEntry 时，不应反向修改任务、占用或可用时间规则。
- Schedule 大部分是可重建投影；`availability_exception` 是窄例外，由对应 Todo ScheduleEntry 权威保存。用户同时修改 DDL、事件或其他上游事实时，先更新对应权威模块，再生成新的 Schedule。

### 2.3 合法空计划

初始化脚本创建以下稳定空结构。`timezone` 和初始化时间由运行环境或初始化参数给出；它们不表示已经建立任何任务或日程。

```json
{
  "plan_meta": {
    "plan_version": 0,
    "timezone": "Asia/Shanghai",
    "progress_reviewed_through": null,
    "updated_at": "2026-08-19T10:00:00+08:00"
  },
  "task_structure": {
    "goals": [],
    "nodes": [],
    "todos": []
  },
  "time_constraints": [],
  "availability_rules": {
    "calendar_id": null,
    "workday": {
      "id": "availability-workday",
      "segments": [],
      "context": null,
      "created_at": "2026-08-19T10:00:00+08:00",
      "updated_at": "2026-08-19T10:00:00+08:00"
    },
    "holiday": {
      "id": "availability-holiday",
      "segments": [],
      "context": null,
      "created_at": "2026-08-19T10:00:00+08:00",
      "updated_at": "2026-08-19T10:00:00+08:00"
    },
    "date_overrides": []
  },
  "schedule": {
    "planning_window": null,
    "generated_at": null,
    "entries": []
  }
}
```

空状态的条件规则：

- `progress_reviewed_through` 在整份计划第一次建立正式周日程前为 `null`；第一次建立时初始化为该次提交时间，之后只能向后推进。
- `calendar_id` 在尚未配置正常日历时可以为 `null`；自动生成正式周计划前必须配置或由用户为受影响日期提供 `date_overrides`。
- `planning_window` 和 `generated_at` 在尚未生成正式周计划时为 `null`。
- 空数组和空可用时间画像表示“目前没有正式事实”，不表示模型可以假设任意时间可用。

## 3. PlanMeta

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `plan_version` | integer | 是 | 非负正式版本。每次原子提交成功后只递增一次；候选计算不改变它 |
| `timezone` | string | 是 | IANA 时区，用于自然日、循环、跨日和当日排名计算 |
| `progress_reviewed_through` | datetime/null | 是 | 已完成完整进度核对的截止点；首次正式周计划前为 `null` |
| `updated_at` | datetime | 是 | 最近一次正式原子提交完成时间 |

正式提交时，`plan_version` 和 `updated_at` 必须一起更新。聊天内容、等待确认状态或临时候选不得改变版本。

## 4. Goal

### 4.1 公共字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 稳定且唯一的 Goal ID |
| `title` | string | 是 | 简短名称 |
| `type` | enum | 是 | `linear`、`recurring` 或 `habit` |
| `status` | enum | 是 | `active`、`paused`、`completed` 或 `cancelled`；Recurring/Habit 不得使用 `completed` |
| `priority` | enum | 是 | `P0`、`P1` 或 `P2`，表示长期相对重要性 |
| `outcome` | string | 是 | Goal 期望结果或持续状态 |
| `deadline` | datetime/null | 是 | Linear Goal 经确认的精确 DDL；没有时为 `null` |
| `recurrence` | object/null | 是 | Recurring 专属日期锚点规则 |
| `cycle_template` | object/null | 是 | Recurring 每轮任务模板 |
| `habit_rule` | object/null | 是 | Habit 周期、次数和长期偏好 |
| `context` | string/null | 是 | 影响理解但不独立建模的背景 |
| `created_at` | datetime | 是 | 创建时间 |
| `updated_at` | datetime | 是 | 最后修改时间 |

类型条件：

| Goal 类型 | `deadline` | `recurrence` | `cycle_template` | `habit_rule` |
| --- | --- | --- | --- | --- |
| `linear` | datetime/null | `null` | `null` | `null` |
| `recurring` | `null` | object | object | `null` |
| `habit` | `null` | `null` | `null` | object |

Goal 不保存 Node ID 列表、通用进度、是否已排期、逾期、风险、独立完成标准或具体执行时间。

### 4.2 Recurring `recurrence`

```json
{
  "interval": 2,
  "unit": "week",
  "first_cycle_starts_on": "2026-08-03",
  "first_deadline": "2026-08-13T20:00:00+08:00"
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `interval` | integer | 是 | 大于 0 |
| `unit` | enum | 是 | v0 支持 `week`、`month` |
| `first_cycle_starts_on` | date | 是 | 首轮开始日期锚点 |
| `first_deadline` | datetime | 是 | 首轮精确 DDL 及后续 DDL 锚点 |

`recurrence` 只保存日期规则，不保存每轮行动。

### 4.3 `cycle_template`

```json
{
  "node": {
    "title": "制作并发布一期播客",
    "outcome": "本期播客在主要平台上线",
    "context": null
  },
  "todos": [
    {
      "template_key": "write-script",
      "title": "完成播客脚本",
      "estimate_minutes": 120,
      "execution_mode": "splittable",
      "intensity": "high",
      "depends_on_template_keys": [],
      "context": null
    }
  ]
}
```

`node` 必须包含 `title`、`outcome`、`context`。每个 Todo 模板包含：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `template_key` | string | 是 | 模板内稳定且唯一；标题改变时不改变 |
| `title` | string | 是 | 生成 Todo 的标题 |
| `estimate_minutes` | integer | 是 | 大于 0；生成时作为初始剩余估时 |
| `execution_mode` | enum | 是 | `single_block` 或 `splittable` |
| `intensity` | enum | 是 | `low`、`medium`、`high` |
| `depends_on_template_keys` | array<string> | 是 | 模板内前置步骤；生成时转换为正式 Todo ID |
| `context` | string/null | 是 | 每轮都适用的背景 |

模板不保存正式对象 ID、状态、绝对日期或 ScheduleEntry。

### 4.4 `habit_rule`

```json
{
  "interval": 1,
  "unit": "week",
  "first_period_starts_on": "2026-08-03",
  "minimum_count": 3,
  "session_minutes": 60,
  "spacing_rule": {
    "min_rest_days": 1,
    "strictness": "preferred"
  },
  "preferred_windows": [
    {
      "weekdays": [2, 4],
      "starts_time": "07:00",
      "ends_time": "09:00",
      "strictness": "preferred"
    }
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `interval` | integer | 是 | 大于 0 |
| `unit` | enum | 是 | v0 支持 `day`、`week`、`month` |
| `first_period_starts_on` | date | 是 | 首个考核周期的开始锚点 |
| `minimum_count` | integer | 是 | 每周期最低次数，大于 0 |
| `session_minutes` | integer | 是 | 生成单次 Todo 的默认初始估时，大于 0 |
| `spacing_rule` | object/null | 是 | 可选间隔规则 |
| `preferred_windows` | array<object> | 是 | Habit 专属、跨周期复用的偏好星期与时段；没有时为空数组 |

`spacing_rule`：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `min_rest_days` | integer | 是 | 非负整数，表示两次执行间完整不执行日数量 |
| `strictness` | enum | 是 | `preferred` 为软偏好，`required` 为硬约束 |

每个 `preferred_windows` 条目：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `weekdays` | array<integer>/null | 是 | ISO 星期 1～7；`null` 表示周期内任意日期 |
| `starts_time` | time | 是 | 计划时区下的本地开始时间 |
| `ends_time` | time | 是 | 本地结束时间，必须晚于开始且不得跨午夜 |
| `strictness` | enum | 是 | 默认 `preferred`；用户明确“只能”“必须”时使用 `required` |

长期偏好只写在 `habit_rule`。只影响当前周期的临时边界写入该期 Todo 的 `execution_window`；普遍适用于所有任务的作息写入 AvailabilityRules。

## 5. Node

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 稳定且唯一的 Node ID |
| `goal_id` | string | 是 | 引用所属 Goal |
| `title` | string | 是 | 简短名称 |
| `outcome` | string | 是 | 该阶段或周期要达到的结果 |
| `status` | enum | 是 | `pending`、`in_progress`、`completed`、`cancelled` |
| `starts_on` | date/null | 是 | 可开始日期；Recurring Node 使用周期开始日 |
| `deadline` | datetime/null | 是 | 精确 DDL；Recurring Node 必填并由已确认规则推导 |
| `depends_on_node_ids` | array<string> | 是 | 前置 Node ID；无依赖为空数组 |
| `context` | string/null | 是 | 仅影响本 Node 的背景 |
| `created_at` | datetime | 是 | 创建时间 |
| `updated_at` | datetime | 是 | 最后修改时间 |

Recurring Node 的稳定身份是 `(goal_id, starts_on)`；已取消 Node 仍占用该身份。

Node 不保存类型、Todo ID 列表、进度比例、总估时、逾期、完成时间、独立完成规则或通用优先级。

## 6. Todo

### 6.1 公共字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 稳定且唯一的 Todo ID |
| `title` | string | 是 | 可直接执行并可判断完成的行动 |
| `parent_refs` | array<object> | 是 | 可为空；多个引用表示共享 Todo |
| `status` | enum | 是 | `pending`、`in_progress`、`completed`、`cancelled` |
| `deadline` | datetime/null | 是 | Todo 自身经确认的精确 DDL |
| `execution_window` | object | 是 | 原始弹性时间窗 |
| `remaining_estimate_minutes` | integer | 是 | 当前预计仍需安排的分钟数，非负 |
| `execution_mode` | enum | 是 | `single_block` 或 `splittable` |
| `intensity` | enum | 是 | `low`、`medium`、`high` |
| `depends_on_todo_ids` | array<string> | 是 | 只保存前置 Todo ID |
| `context` | string/null | 是 | 当前执行背景，不是流水日志 |
| `created_at` | datetime | 是 | 创建时间 |
| `updated_at` | datetime | 是 | 最后修改时间 |

`execution_window`：

| 字段 | 类型 | 必填 |
| --- | --- | --- |
| `starts_at` | datetime | 是 |
| `ends_at` | datetime | 是 |

用户没有提供 `execution_window` 时，Agent 可以根据已知 DDL、依赖、剩余工作量、可用时间和协作条件直接推导并写入，不因该字段必填而单独追问。窗口选择会改变可执行性或产生重要取舍时，才需要追问或随任务结构方案确认；直接推导时应向用户说明。

完成或取消的 Todo，`remaining_estimate_minutes` 必须为 0。

Todo 不保存 ScheduleEntry ID、具体执行时间、通用优先级、已排期、逾期、完成时间、初始总估时或累计实际时长。后续依赖通过反向扫描 `depends_on_todo_ids` 获得，不保存反向列表。

### 6.2 `parent_refs`

```json
{
  "goal_id": "goal-fitness",
  "node_id": null,
  "habit_period_starts_on": "2026-08-03",
  "habit_occurrence_index": 1
}
```

每个引用固定包含：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `goal_id` | string | 是 | 引用 Goal |
| `node_id` | string/null | 是 | 普通 Node 路径；Habit 必须为 `null` |
| `habit_period_starts_on` | date/null | 是 | Habit 周期开始日；普通路径为 `null` |
| `habit_occurrence_index` | integer/null | 是 | Habit 周期内从 1 开始的次数；普通路径为 `null` |

规则：

- `parent_refs = []` 表示独立 Todo。
- 普通引用按 `(goal_id, node_id)` 在同一 Todo 内唯一；Habit 字段必须同时为 `null`。
- 引用 Node 时，Node 必须存在且 `node.goal_id` 与 `goal_id` 一致。
- Habit 引用必须直接指向 Habit Goal，`node_id = null`。
- Habit 生成身份 `(goal_id, habit_period_starts_on, habit_occurrence_index)` 在全部 Todo 中唯一；已取消 Todo 仍占用原身份。

## 7. TimeConstraint

### 7.1 公共字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 稳定且唯一，供 ScheduleEntry 引用 |
| `kind` | enum | 是 | `commitment` 或 `unavailable` |
| `title` | string | 是 | 事件或不可用原因 |
| `status` | enum | 是 | `active` 或 `cancelled`；不表示执行进度 |
| `starts_at` | datetime | 是 | 一次性开始，或循环系列首次开始锚点 |
| `ends_at` | datetime | 是 | 一次性结束，并定义循环单次持续时长 |
| `recurrence` | object/null | 是 | 一次性为 `null` |
| `context` | string/null | 是 | 地点或解释背景 |
| `created_at` | datetime | 是 | 创建时间 |
| `updated_at` | datetime | 是 | 最后修改时间 |

### 7.2 循环字段与例外

`recurrence`：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `interval` | integer | 是 | 大于 0 |
| `unit` | enum | 是 | `day`、`week`、`month` |
| `ends_on` | date/null | 是 | 最后允许发生的日期；无限循环为 `null` |
| `exceptions` | array<object> | 是 | 单次跳过或改期 |

跳过例外：

```json
{
  "occurrence_starts_at": "2026-08-12T10:00:00+08:00",
  "action": "skip"
}
```

改期例外：

```json
{
  "occurrence_starts_at": "2026-08-12T10:00:00+08:00",
  "action": "reschedule",
  "starts_at": "2026-08-13T15:00:00+08:00",
  "ends_at": "2026-08-13T16:00:00+08:00"
}
```

`occurrence_starts_at` 必须保存基础循环原本生成的开始时间，是同一次 occurrence 的稳定匹配键；同一键最多一条例外。`skip` 不包含替代时间；`reschedule` 必须同时包含替代 `starts_at` 和 `ends_at`。

TimeConstraint 不保存完成、剩余工作量、当日排名、锁定、是否已生成本周 occurrence、occurrence 状态或反向 ScheduleEntry 列表。

## 8. AvailabilityRules

### 8.1 顶层字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `calendar_id` | string/null | 是 | 正常日历，例如 `CN`；仅未配置的空状态可为 `null` |
| `workday` | object | 是 | 工作日完整默认画像 |
| `holiday` | object | 是 | 休息日完整默认画像 |
| `date_overrides` | array<object> | 是 | 特定日期或连续日期范围的完整临时画像 |

`workday` 与 `holiday`：

| 字段 | 类型 | 必填 |
| --- | --- | --- |
| `id` | string | 是 |
| `segments` | array<object> | 是 |
| `context` | string/null | 是 |
| `created_at` | datetime | 是 |
| `updated_at` | datetime | 是 |

每个 `segment`：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `starts_time` | time | 是 | 计划时区下本地开始时间 |
| `ends_time` | time | 是 | 必须晚于开始，不得跨午夜 |
| `energy_level` | enum | 是 | `low`、`medium`、`high` |

### 8.2 `date_overrides`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 稳定且唯一 |
| `starts_on` | date | 是 | 包含当天 |
| `ends_on` | date | 是 | 包含当天，不早于开始日 |
| `segments` | array<object> | 是 | 该范围每天采用的完整画像；空数组表示完全不可排 Todo |
| `context` | string/null | 是 | 临时变化说明 |
| `created_at` | datetime | 是 | 创建时间 |
| `updated_at` | datetime | 是 | 最后修改时间 |

任意日期最多命中一个 override。它替换当天默认画像，但不能覆盖 TimeConstraint。单条 Todo 获准突破默认可用时间时，不修改 AvailabilityRules，而在该 ScheduleEntry 保存 `availability_exception`。

AvailabilityRules 不保存动态空闲时间、国家日历查询结果或查询缓存，也不从 Schedule 反推。`date_overrides` 保存最终完整画像，不保存增删补丁。

## 9. Schedule

### 9.1 顶层字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `planning_window` | object/null | 是 | 当前自然周生成范围；尚未生成周计划时为 `null` |
| `generated_at` | datetime/null | 是 | 最近一次生成或重排完成时间 |
| `entries` | array<ScheduleEntry> | 是 | 扁平条目；数组顺序不承载日期或优先级语义 |

非空 `planning_window` 包含 `starts_on` 和 `ends_on` 两个 date，均包含边界日。

Schedule 常态保留上一自然周和当前自然周条目；`planning_window` 只描述当前周。上一周条目只表示当时计划，不是实际执行记录、审计历史或可恢复版本。更早条目的清理由跨周提交流程处理。

Schedule 不保存自己的 ID、版本、时区或 `schedule_by_date`。日期分组从条目时间与计划时区派生。

### 9.2 ScheduleEntry 公共字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 当前 Schedule 内唯一 |
| `source_type` | enum | 是 | `todo` 或 `time_constraint` |
| `starts_at` | datetime | 是 | 具体开始时间 |
| `ends_at` | datetime | 是 | 具体结束时间 |

### 9.3 Todo ScheduleEntry

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `todo_id` | string | 是 | 引用 Todo；一个 Todo 可被 0..n 个条目引用 |
| `daily_rank` | integer | 是 | 所在日期内从 1 开始的关注顺序；不表示开始时间或当前可行动性 |
| `priority_reason` | string/null | 是 | 排序模块生成的关注原因，不是新的权威任务事实 |
| `ranking_exception` | object/null | 是 | 用户明确覆盖该日关注顺序时的确认记录；同日所有 Todo 条目必须相同 |
| `availability_exception` | object/null | 是 | 单条越界许可 |

`ranking_exception`：

| 字段 | 类型 | 必填 |
| --- | --- | --- |
| `confirmed_at` | datetime | 是 |
| `reason` | string | 是，且不得为空 |

`availability_exception`：

| 字段 | 类型 | 必填 |
| --- | --- | --- |
| `confirmed_at` | datetime | 是 |
| `reason` | string/null | 是 |

Todo 条目不得同时保存 `time_constraint_id`。Todo 可以对应多个 ScheduleEntry；多个条目表示多次计划投入，不是多个 Todo。

### 9.4 TimeConstraint ScheduleEntry

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `time_constraint_id` | string | 是 | 引用权威 TimeConstraint |

该类型不得保存 `todo_id`、`daily_rank`、`priority_reason`、`ranking_exception` 或 `availability_exception`。它保存应用循环例外后的实际时间；跨午夜仍为一个持久化条目，viewer 可按日期临时裁切。

TimeConstraint 投影 ID 重建后可以变化；稳定 occurrence 身份只保存在权威 exception 的 `occurrence_starts_at`。

### 9.5 Schedule 不复制的事实

ScheduleEntry 不保存源对象标题、状态、归属、DDL、弹性时间窗、完成状态、实际时长、锁定、循环 occurrence 身份、创建时间或更新时间。除 `availability_exception` 外，Schedule 应能从其他权威模块重建。

## 10. 不持久化的内容

正式计划不保存：

- `interaction_state`、`pending_proposal`、`base_plan_version` 或临时计划版本；
- 尚未确认的自然语言方案或结构化候选；
- `SchedulingBacklog`、动态有效窗口、动态压力、风险、逾期和是否已排期等派生视图；
- 逐次 ExecutionRecord、ExecutionSession、实际执行时长或从计划时长自动计算的执行进度；
- 历史执行反馈、计划变更日志、估时复盘或完整版本恢复数据；
- 国家日历查询响应、缓存和日历分类副本；
- 页面处理状态或 viewer 自己的展示状态。
- 完整规则引擎状态、多 Agent 路由状态或其他不属于当前正式计划的运行框架数据。

MVP 只保存每次修改生效后的当前正式快照，以及 Schedule 中按保留策略留下的上一周计划条目。

## 11. 权威更新关系

| 正式事实变化 | 权威写入位置 | 必须同步处理 |
| --- | --- | --- |
| Goal / Node / Todo 内容、状态、归属、依赖 | `task_structure` | 更新对象时间戳；必要时重算上层状态和 Schedule |
| Todo 当前剩余工作量 | Todo | 重新计算未来覆盖和待排工作量 |
| 独立事件或不可用时间 | `time_constraints` | 重建相应投影并检查 Schedule 冲突 |
| 通用作息或日期整体画像 | `availability_rules` | 重算受影响日期候选时间 |
| Todo 的具体时间 | `schedule.entries` | 不回写 Todo 的具体时间 |
| TimeConstraint occurrence 的具体投影 | `schedule.entries` | 必须与权威规则或 exception 一致 |
| 单条 Todo 突破默认可用时间 | 该 Todo ScheduleEntry | 不扩大 AvailabilityRules |
| 完整进度核对确认点 | `plan_meta.progress_reviewed_through` | 与 Todo 事实和后续 Schedule 同次提交 |

任何正式提交都以完整候选计划为校验对象。只有全部校验和写入成功后，才原子替换正式计划并更新 `plan_version`、`updated_at`；失败时原计划保持不变。
