# Viewer 触发与路由

本规则只决定正式计划何时交给只读 viewer 展示。`plan.json` 仍是唯一事实来源；viewer 状态、当前标签页和 fixture 都不是计划事实。

## 先判断交接级别

### 主动打开或聚焦

满足任一项时，在操作成功后主动展示：

- 用户明确说“打开看看”“让我看日程”“让我看任务结构”等；
- 第一次建立出已有可展示内容的正式计划；
- 新增 Goal，或重组 Node、Todo、父子关系、依赖关系；
- 首次生成周日程，或重排造成多个日程条目新增、移动、取消；
- 新的时间约束显著改变了本周安排；
- 结果存在冲突、未排入事项或必须由用户目视判断的重要 warning。

必须等正式提交成功后再打开。用户只是明确要求查看时，可以不修改计划，但必须先确认正式计划存在。

### 静默刷新

viewer 已经运行且改动很小时，不抢焦点，也不新开标签页。页面会自行轮询正式计划。典型情况：

- 单个 Todo 完成或恢复；
- 标题、情境、剩余估时等局部事实更新；
- 不影响排期的单个状态变化；
- 用户没有要求查看的一处小幅日程调整。

viewer 尚未运行时，不为这些小改动启动它；收尾只说明已经更新。

### 不打开

以下情况不启动、不聚焦 viewer：

- 普通查询、解释、讨论或方案比较，且用户没有要求查看；
- 正在澄清信息，或候选仍待确认；
- 只做校验，没有正式提交；
- 校验或提交失败；
- 正式计划不存在；
- 仅改变内部元数据，用户可见内容没有变化。

## 选择落点

使用最贴近本次结果的一个落点，不同时打开多个页面：

| 变化 | 路由 |
| --- | --- |
| 单日日程 | `/?page=schedule&view=day&date=YYYY-MM-DD` |
| 多日或整周排期、重排 | `/?page=schedule&view=week&date=YYYY-MM-DD` |
| 当日关注顺序变化 | `/?page=schedule&view=day&date=YYYY-MM-DD&priorities=1` |
| 多个 Goal 或整体任务结构 | `/?page=relations` |
| 单个 Goal 的结构 | `/?page=relations&relations=focus&focus=goal:<id>` |
| 独立 Todo 的结构 | `/?page=relations&relations=focus&focus=todo:<id>` |

日期使用受影响日期；周视图可使用该周内任一天。`focus` 参数中的 ID 必须进行 URL 编码。若同一次结果既改变任务结构又改变日程，默认展示周日程，因为它更直接呈现用户接下来要做什么；用户明确要求看结构时例外。

## 启动与浏览器交接

1. 将 Skill 目录和 workspace 解析成绝对路径。
2. 运行 `python3 <skill>/scripts/ensure_viewer.py <workspace> --route '<route>'`。
3. 只有脚本返回 `ok: true` 时，才把其中的 `url` 交给浏览器。
4. 浏览器里已有该 workspace 的 viewer 标签页时复用并导航；没有时才新开。一次用户请求最多主动聚焦一次。
5. 脚本返回 `plan_missing`、`plan_invalid`、`viewer_missing`、`dependencies_missing`、`port_conflict` 或 `start_failed` 时，不退回 fixture，也不自行安装依赖。简短报告正式计划是否已提交以及 viewer 为什么没有打开。

`ensure_viewer.py` 只核对或启动服务，不负责打开浏览器。启动成功后复用同一服务；不为每次修改重启。打开或启动失败是展示层问题，不能撤销或误报已经成功的正式计划提交。
