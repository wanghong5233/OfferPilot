# Module Development SOP

业务 module 采用"单领域入口 + 配置装配 + 确定性 workflow"形态。`core/` 提供调度、记忆、LLM、通知、安全、事件能力;`modules/<domain>/` 只表达领域语义。

## 1. 目录契约

```text
src/pulse/modules/<domain>/
  __init__.py
  module.py
  skill.py
  intent.py
  config.py
  store.py
  pipeline/
  _connectors/ or sources/
  <entities>/
  docs/
```

| 路径 | 负责 | 不负责 |
|---|---|---|
| `module.py` | 装配依赖、注册 patrol、暴露 HTTP / IntentSpec | 业务流程细节 |
| `intent.py` | 三段式 IntentSpec:`<domain>.<capability>.<action>` | 自然语言 regex 分发 |
| `config.py` | `PULSE_<DOMAIN>_*` 设置,集中读取环境变量 | 业务代码内 `os.getenv()` |
| `store.py` | PostgreSQL DAL、schema 校验、幂等写入 | 评分、规划、外部 IO |
| `pipeline/` | 分阶段 workflow,单步输入输出明确 | 连接器协议细节 |
| `_connectors/` / `sources/` | 外部平台 / 信源 driver | 调 LLM、写库、发通知 |
| `<entities>/` | YAML schema 与实例配置,如 `topics/` / `games/` | 跨实体流程 |
| `docs/` | 当前架构、接入教程、driver/source 类型、风险边界 | 对话记录、调试流水账 |

## 2. 架构不变式

| 不变式 | 要求 |
|---|---|
| 单 module | 用户视角主题 / 游戏 / 平台实例不是新 module,用 YAML 装配 |
| 确定性 workflow | 控制流由 Python orchestrator 决定,LLM 只进入单步 |
| Protocol/ABC driver | 业务流程依赖抽象,不直接 import 具体平台实现 |
| 字典返回契约 | 外部 IO 返回至少含 `ok` / `source`;失败含 `error` / `error_message`;未实现用 `status="not_implemented"` |
| fail loud | schema 缺列、依赖缺失、多设备歧义、认证缺失直接返回失败或抛明确错误 |
| 单点失败隔离 | 批量任务中单 source / 单 task 失败不污染其它项,orchestrator 聚合为 `partial` |

## 3. Workflow 模板

```text
run(entity_id, dry_run=False, filters=None) -> WorkflowResult
```

| 阶段 | 输入 | 输出 | 不变式 |
|---|---|---|---|
| prepare | entity config + settings | runtime handle | 环境不满足时 fail loud |
| fetch/capture | runtime handle | raw observations | 失败带可审计错误,不伪造空成功 |
| identify/dedup | raw observations | normalized candidates | 输出与下游索引 1:1 对齐 |
| score/plan | candidates + config | ranked/planned items | LLM 失败走显式 fallback |
| execute | plan + driver | task results | `dry_run=True` 在真实副作用前短路 |
| verify | task results + evidence | verified results | 外部副作用必须二次校验 |
| publish | verified results | persisted result + notification | 写库、通知、记忆晋升顺序明确 |

Intel 使用 `fetch → dedup → score → summarize → diversify → publish`。Game 使用 `prepare → capture → identify → execute → verify → assess → publish`。阶段名称可以按领域变化,但每步必须有输入、输出、失败行为。

## 4. 配置与 YAML

| 类型 | 规范 |
|---|---|
| 运行设置 | `BaseSettings` 子类,`env_prefix="PULSE_<DOMAIN>_"` |
| 实体配置 | `modules/<domain>/<entities>/<id>.yaml` |
| 示例配置 | 放 `<entities>/_examples/`,默认不自动启用 |
| 新依赖 | 先确认必要性,再写入 `pyproject.toml`;不能在代码里临时 import 后吞失败 |
| 环境变量 | `.env.example` 与文档同步 |

YAML 只表达业务事实,不承载 Python 无法实现的假能力。调度窗口必须能直接映射到 `AgentRuntime.register_patrol(...)`;若内核不支持固定分钟级时间点,不要写 `preferred_run_time` 这类假字段。

## 5. IntentSpec

| 规则 | 要求 |
|---|---|
| 命名 | `<domain>.<capability>.<action>` |
| 新模块 | 只暴露 `intents`,不写粗粒度 `handle_intent` 分发 |
| schema | 参数必须是 JSON Schema object |
| 风险 | 有真实副作用的 intent 标 `mutates=True`,必要时声明 `risk_level` |
| 暴露链路 | `ModuleRegistry.as_tools()` → `ToolRegistry` → `MCPServerAdapter` 自动暴露 |

## 6. AgentRuntime

| 项 | 规范 |
|---|---|
| 注册 | module 在 `on_startup()` 无条件 `register_patrol(enabled=False)` |
| 启停 | 用户通过 `system.patrol.enable/disable` 控制,不新增私有开关路径 |
| 窗口 | `weekday_windows` / `weekend_windows` 是北京时间整数小时半开区间 |
| async 桥接 | 同步 patrol handler 调 async workflow 时复用 Intel `_run_async` 约束:已有 event loop 时 fail loud |
| 事件 | orchestrator 只拿 `emit_stage_event` callable,不依赖 `BaseModule` 实例 |

## 7. LLMRouter

| 用途 | route | 方法 | 失败行为 |
|---|---|---|---|
| 分类 / 评分 / 判定 | `classification` | `invoke_json` | 返回 `None`,调用方显式降级 |
| 文案生成 / 摘要 | `generation` | `invoke_text` | 抛 `RuntimeError`,调用方给非空 fallback |
| 复杂规划 | `planning` | `invoke_json` / `invoke_structured` | 只用于单步,不接管 workflow 控制流 |
| 图像识别 | `vision` | `invoke_vision_json` | 返回 `default`,不得把图片写入事件 payload |

业务层不直接 import `langchain_openai`,不直接读模型 API key。

## 8. SafetyPlane

真实外部副作用在 service / execute 层过 policy,不能放在 Brain prompt 层。

| 要求 | 说明 |
|---|---|
| policy 位置 | 通用授权逻辑放 `pulse.core.safety.policies` |
| Intent | 新代码使用 `Intent(kind="mutation", ...)` |
| 判决 | `allow` 继续,`deny` 不触达 driver,`ask` 创建 `SuspendedTask` |
| Resume | 产生 `ask` 的 module 必须实现 `get_resumed_task_executor()` |
| dry run | `dry_run=True` 在真实副作用前短路,不进入 SafetyPlane |
| off 模式 | 尊重 `SAFETY_PLANE_OFF` |

## 9. 事件与日志

| 项 | 规范 |
|---|---|
| stage 命名 | 传给 `emit_stage_event` 的 `stage` 不重复写 module 名 |
| 事件名 | `BaseModule` 生成 `module.<module_name>.<stage>.<status>` |
| 异常 | `stage="exception"`,payload 写 `kind` 和短错误 |
| 日志 | `logging.getLogger(__name__)`,不打印密钥、图片原文、完整个人数据 |
| 观测字段 | 至少包含 entity id、run id / trace id、耗时、成功数、失败数 |

## 10. ArchivalMemory

| 项 | 规范 |
|---|---|
| subject | `"<domain>:<entity_id>"`,如 `intel:llm_frontier` / `game:shuailu_zhibin` |
| predicate | 领域稳定谓词,如 `high_score_signal` / `rare_pull` |
| object_value | 结构化 JSON,不要塞长正文 |
| evidence_refs | 指向业务库记录 id 或外部 URL |
| 失败 | 晋升失败不阻断 publish,但必须记录 warning / stage payload |

## 11. 文档

模块内文档至少包含:

| 文件 | 内容 |
|---|---|
| `docs/README.md` | 当前实现、入口契约、子文档、关键决策 |
| `docs/architecture.md` | workflow 图、分层职责、阶段契约、数据模型、LLM 契约、内核整合 |
| `docs/adding-a-<entity>.md` | 新实体接入步骤 |
| `docs/<driver-or-source>-types.md` | connector/source 类型与新增流程 |
| `docs/risk-and-tos.md` | 仅高风险外部副作用 module 需要 |

架构文档只写当前实现与为什么是这个形态;过程、调研流水、被删工件不进文档。

## 12. 测试

| 文件 | 覆盖 |
|---|---|
| `tests/pulse/modules/test_<domain>_pipeline.py` | 纯函数、schema、去重/识别/评分/verify |
| `tests/pulse/modules/test_<domain>_module.py` | module 装配、IntentSpec、patrol、store、fake driver 端到端 |
| `tests/pulse/modules/<domain>/` | 专项单元测试 |
| `tests/fixtures/<domain>/` | 可回放 fixture;真实截图/账号数据不进 git |

测试不连接真实外部平台。driver 用 fake 实现,验证返回契约与失败语义。

## 13. PR 顺序

| 顺序 | 内容 |
|---|---|
| 1 | 内核前置能力:调度、LLM route、SafetyPlane 原语 |
| 2 | SOP / 文档骨架 |
| 3 | module skeleton + schema + store + fake driver |
| 4 | pipeline MVP + dry_run |
| 5 | patrol / IntentSpec / HTTP |
| 6 | 真实 driver 或外部 source |
| 7 | 安全治理 / 记忆晋升 / 可观测补齐 |

每个 PR 必须有可运行测试。无法本地验证的外部依赖必须在文档写清预检命令与失败行为。
