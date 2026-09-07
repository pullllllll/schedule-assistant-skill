# Schedule Assistant

通过自然语言整理目标和任务、建立本地计划、安排当周日程，并根据进度和现实变化重新调整。包含完整 Skill 和配套的只读日程、任务关系 viewer。

## 使用方式

获取整个目录，并将本目录作为计划工作区。不要只复制 Skill 子目录，否则配套 viewer 不在脚本预期的位置。

Skill 入口是 [SKILL.md](.agents/skills/schedule-assistant/SKILL.md)。让所用助手加载该文件，然后用自然语言描述需要安排的事情，例如：

> 帮我建立本周计划。我周五需要交一份方案，每天上午有两个小时可以集中工作。

助手会按现有规则澄清信息、建立计划、校验和提交，并在需要时启动配套 viewer。首次使用需明确计划时区。

## 运行环境

- Python 3.10 或更高版本，并具备 IANA 时区数据。
- 提交脚本使用 `fcntl`，需要 Unix 类环境；不支持直接在原生 Windows Python 中运行。
- viewer 使用 Node.js 和 npm；Node.js 版本须满足锁定的 Vite 版本要求。

首次启动 viewer 前，在本目录执行：

```sh
npm --prefix app ci
```

启动脚本会复用或启动本地 viewer，但不会替你安装依赖。手动启动：

```sh
npm --prefix app run dev
```

默认地址为 `http://127.0.0.1:5180`。公开版不附带演示计划，请先通过 Skill 建立正式计划，再查看页面。

## 文件与数据

```text
.agents/skills/schedule-assistant/  Skill、规则、脚本及测试
app/                               配套只读 viewer
data/plan.json                     使用时生成的正式计划（不入库）
```

正式计划是唯一事实来源，通过 Skill 脚本初始化、校验和提交，不手工修改 JSON。`data/`、依赖和构建产物均被 Git 忽略。

## 验证

在本目录执行：

```sh
python3 -m unittest discover -s .agents/skills/schedule-assistant/scripts/tests -v
npm --prefix app run build
```

当前目录是公开发布内容的源目录。首次对外发布前仍需审核公开内容。许可证暂未确定，可后续补充；公开可见不代表已授予开源许可。
