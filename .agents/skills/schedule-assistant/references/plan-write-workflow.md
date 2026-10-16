# 正式计划候选与提交操作

按当前阶段读取：正式文件缺失且已授权创建读 §1；讨论或精确预览读 §2；获准保存后读 §3及 §4 适用命令。通用提交顺序在 [Skill 入口](../SKILL.md#修改与提交要求)，本文件补充条件与参数；已经读过且仍适用的内容可以复用。只读查询不需要本文。业务授权由 [确认规则](decision-and-confirmation.md) 决定，字段由 [数据合同](plan-data-contract.md) 定义。

## 1. 初始化

正式计划不存在，且用户已授权建立计划、录入事实或生成正式排期时，读取数据合同 §1～3，获得明确的 IANA 时区后调用 `init_plan.py`。可以依据环境提出时区默认值让用户纠正，存在冲突时不得静默采用。初始化只创建合法空壳，不表示首批任务结构或日程已确认，不覆盖已有文件。纯查询、解释或建议不创建文件。

符合首次介绍条件时，先按 [发现规则 §1.1](discovery-loop.md) 使用完整介绍；介绍不增加信息门槛。

## 2. 讨论、提案与临时候选

普通讨论和早期备选使用自然语言。临时分类、影响集、分组可存在上下文、内存、工具参数或一次性临时文件中，不构成业务模块或第二事实源。

仅当精确日程在展示前需要硬性校验、用户已确认方案，或修改在自主权限内时，才构造完整结构化候选。需展示推导结构提案时，先按 [发现规则 §5](discovery-loop.md) 以 `gate = proposal` 验证一次性发现状态。失败先定位缺少事实或状态错误，已有信息可修正时不重复问用户。

精确方案校验使用完整临时候选，先 rank 再 validate；候选通过不等于用户确认，也不改变正式计划。本次触发发现时，先取得适用 gate 成功结果，再做正式候选 validate/apply；gate 不证明理解或授权真实。

## 3. 正式提交的条件分支

执行入口中的提交顺序，按实际状态补充以下内容：

- **首次、增量或修复发现**：读 [发现规则 §5～6](discovery-loop.md)，重建一次性状态，如实标记已确认的推导结构；scheduling gate 成功后才能构造正式提交候选，并给 apply 传对应模式的 state。首次结构/规划窗口、相关坏基线修复、新增/修改 Goal、Node、归属或依赖等结构变化按该规则触发。纯时间移动、明确独立 Todo、无结构变化的局部事实不强制重做发现；首次门禁仍保留。
- **Todo 截止**：新增或更新截止语义时显式写 deadline_status，并保持与 deadline 一致；pending 可用于已授权的事实保存，排期条件见确认规则 §7.1。读取旧记录按合同兼容，不批量补字段；校验通过不证明截止建议已经获用户确认。
- **接受方案、部分接受或确认时有新事实**：读确认规则 §8～9，核实仍有效的方案、合并重算与原子范围，不能夹带未确认内容。
- **完整核对或跨周**：读 [进度规则 §2～3、§7](progress-review.md)，范围内进度覆盖完整后整组提交。固定的核对截止点不随后续提交时钟前移；当前计算/提交时间与进度确认点分别处理。
- **日期分类或校验问题**：按 §4 获取实际需要的输入，查相应领域规则处理 warning/error；预检通过不代替 apply 的版本检查和完整校验。

## 4. 脚本调用与日期输入

脚本路径相对 Skill 目录解析后使用绝对路径；以下 `<official>` 是已配置的唯一正式文件，`<candidate>` 是临时完整候选。所有时间参数使用带偏移的 RFC 3339。

日期分类按 [时间规则 §2.3～2.4](calendar-and-time-rules.md) 获取；完整 date_overrides 命中时不需要为了默认画像再查该日。日期辅助脚本只按星期生成基线，不能替代真实日历查询；分类未知时不能靠基线继续排期，按时间规则请用户提供完整画像。调休与法定节假日仍按下述既有用户确认要求处理。

调用初始化、校验和提交脚本前，从运行环境读取真实当前时间并转换为计划时区。测试或用户指定模拟时间除外，不猜测当前时间，不让 `generated_at` 或提交时间无理由落在未来。

- `scripts/init_plan.py <official> --timezone <IANA timezone> [--now <RFC 3339 datetime>]`：只在正式计划缺失时使用，不得覆盖已有文件。
- `scripts/date_classifications.py <start> <end> [--override <DATE=classification,...>] [--out <path>]`：为区间生成 workday/holiday 基线（周一至周五为 workday，周末为 holiday），供 validate/apply 使用；多个覆盖值在同一个 `--override` 中用逗号分隔，如 `2026-10-01=holiday,2026-10-10=workday`。调休和法定节假日必须由用户确认后用 `--override` 指定，不得自行编造。
- `scripts/rank_plan.py <candidate> --out <ranked-candidate> [--calculated-at <RFC 3339 datetime>]`：只重算 Todo ScheduleEntry 的 `daily_rank` 和 `priority_reason`，不移动时间。DDL、依赖解锁、Goal 优先级和窗口余量依次参与排序，开始时间只作最终稳定兜底。用户明确指定某日顺序时，该日所有 Todo 条目必须带同一份 `ranking_exception`，命令会保留该日顺序。
- `scripts/validate_plan.py <candidate> [--calculated-at <RFC 3339 datetime>] [--date-classifications <JSON file>]`：用于完整候选或诊断校验。当受影响规则需要时，提供计算时点和日期分类。warning 需要判断，error 会阻止提交。`required_coverage_gap` 提示当前周硬边界前的覆盖缺口，`future_coverage_exceeds_remaining` 提示余量或疑似超排；按 `scheduling.md` 第 2.1、3.6 节处理，不将提醒自动当作扩圈授权，也不把有缺口的安排宣称为完整保障。
- `scripts/apply_plan.py <candidate> <official> --expected-version <version> [--committed-at <RFC 3339 datetime>] [--date-classifications <JSON file>] [--discovery-state <JSON file>]`：只在已获授权后使用。它重新校验、检查版本、推进元数据并原子替换正式文件。首次建立结构或规划窗口要求 `first_discovery`；相关结构或关键执行条件影响旧无归属依赖链时要求 `repair_discovery`；其他新增 Goal、Node、归属或依赖，以及 Goal 类型/循环规则/模板、节点类型/周期绑定的语义变化要求 `incremental_discovery`，均使用 scheduling gate。局部范围详见发现规则第 6 节。仅改具体时刻或无关事实不自动触发修复；首次门禁仍保留。程序允许提交不扩大用户授权。

发现 JSON 格式与 `validate_discovery.py` 的完整用法在 [发现规则 §5](discovery-loop.md)。需要实际页面交接时才读 [viewer-handoff](viewer-handoff.md) 的服务核对、启动参数与浏览器失败停止条件；一次内部候选或预检不触发展示。不重复运行同条件失败动作，也不把工具接受输入解释为事实或授权已成立。
