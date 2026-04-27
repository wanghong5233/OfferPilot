# Game Module

低频游戏日常自动化域。单一 `GameModule` 按 `games/<game_id>.yaml` 装配 workflow,首发 `shuailu_zhibin`。

## 当前实现

| 维度 | 现状 |
|---|---|
| 业务形状 | 一个 `GameModule`,多个 game YAML;新增游戏 = 加 YAML + 模板 |
| 工作流 | `prepare → capture → identify → execute → verify → assess → publish` |
| Driver | `GameDriver` ABC + 字典返回;首发 `adb_airtest`,预留 `cloud_web` |
| 调度 | 每个 game 一个 `AgentRuntime` patrol,默认 disabled |
| LLM | `generation` 路由生成日报;PR3 接 `classification` / `vision` |
| 安全 | 默认 `dry_run=true`;风控模板命中直接 abort |

## 入口契约

| Intent | 行为 |
|---|---|
| `game.workflow.run` | 立即执行某个游戏 workflow,可指定 `dry_run` / `tasks_filter` |
| `game.runs.list` | 列出最近运行记录 |
| `game.runs.latest` | 返回某游戏最新一次运行 |

HTTP 暴露在 `/api/modules/game/{health,games,runs}` 与 `/api/modules/game/games/{game_id}/run`。

## 子文档

- [`architecture.md`](architecture.md) — workflow 契约、数据模型、LLM 与内核整合
- [`adding-a-game.md`](adding-a-game.md) — 新游戏接入流程
- [`driver-types.md`](driver-types.md) — driver 抽象与实现
- [`risk-and-tos.md`](risk-and-tos.md) — TOS 风险与不做范围
