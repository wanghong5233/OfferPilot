# Agent 工程概念笔记

> 定位：把 Pulse 建设过程中反复出现的 Agent 工程**概念 / 术语 / 竞品做法**整理成一份长期参考。
> 目标读者：(1) 未来维护 Pulse 的自己；(2) 写博客 / 面试时需要讲"Agent 架构"的自己；(3) 接手 Pulse 的新人。
> 为什么不放 ADR：按 `./adr-guide.md` §12，产品对比、概念辨析、趋势综述**不属于** ADR，属于这里。

---

## 1. Agent Harness

### 1.1 一句话定义

> **Harness = 除了模型本身之外，让 Agent 真正能在产线上跑起来的所有工程基础设施。**

术语源于 Claude Code 泄露后 Agent 圈的流行用法，Anthropic 内部叫法。和 "Agent framework"（LangChain / AutoGen 等）指相同的东西，但视角不同——Harness 更强调 "model-external engineering stack"。

### 1.2 Harness 的标准组件

```text
         ┌──────────────────────── Harness ────────────────────────┐
         │                                                          │
Input ──▶│  Agent Loop      → 反复 LLM 调用 + 工具调用 + 反思          │──▶ Output
         │  Tool Registry    → 工具注册、签名、参数校验、执行编排     │
         │  Permission/Safety→ 授权边界、人在环路、规则引擎           │
         │  Context Engin.   → Prompt 构造、记忆压缩、上下文窗管理   │
         │  Cost Control     → token 预算、模型路由、回退策略         │
         │  Error Recovery   → 重试、回滚、断路器、降级               │
         │  Observability    → trace、审计、事件流、可回放            │
         │                                                          │
         └──────────────────────────────────────────────────────────┘
```

### 1.3 为什么 Harness 比 Model 更重要

- Model 每 3–6 个月升级一代；Harness 改一次痛三年。
- 同一个 Claude-3.5 在 Claude Code harness 里能改代码，在裸 API 里连一个完整 bug 都修不完。差距 95% 来自 harness。
- 工业界"让 Agent 跑起来"的所有难点（授权、记忆、成本、错误恢复）都在 Harness，不在 Model。

### 1.4 Pulse 的 Harness 对应

| Harness 组件 | Pulse 实现 | 证据 |
|---|---|---|
| Agent Loop | `core/brain.py` ReAct 循环 | `Brain._react_loop` |
| Tool Registry | 三环模型（Ring 1 核心 / Ring 2 领域 / Ring 3 SkillGen 生成） | `core/tool_registry.py` + `SkillGen` |
| Permission / Safety | **SafetyPlane**（ADR-006，MVP 设计中） | `core/safety/` |
| Context Engineering | PromptContract + MemoryRuntime 五层 | `docs/Pulse-MemoryRuntime设计.md` |
| Cost Control | LLM Router + token budgeting | `core/llm/router.py` |
| Error Recovery | TakeoverState 三态 + Circuit Breaker | `core/runtime.py` |
| Observability | EventBus + JSONL（ADR-005） | `core/observability/` |

## 2. Agent Autonomy Spectrum

Agent 自主性在学术与工程界的通用分层。Pulse 的核心设计目标是 **Level 3**。

| Level | 名称 | 典型产品 | 人的位置 |
|---|---|---|---|
| 0 | 纯对话 | ChatGPT、Claude.ai | Agent 只说不动 |
| 1 | 建议式 | Cursor Tab、GitHub Copilot | Agent 提议，人来执行 |
| 2 | 每步确认式 | Claude Code 默认模式 | 每次工具调用人都拍板 |
| 3 | **策略式** | Claude Code "Auto" 模式、Cursor Agent、Pulse | 人预设边界，Agent 在边界内自主，越界才问 |
| 4 | 完全自主 | Claude Code YOLO mode、Devin | Agent 全权决定，只报告结果 |

**为什么 Level 3 最难**：

- 需要清晰的**边界定义**（authority boundary / permission rules）
- 需要完整的**越界升级**通路（escalation）
- 需要可靠的**恢复**机制（resume after human reply）

Claude Code、Cursor Agent、Devin 都在这级，都各自解决过这个问题。Pulse SafetyPlane（ADR-006）本质上是在实现这层的通用骨架。

## 3. 四词辨析：HITL / Escalation / Interrupt / Elicitation

写博客 / 面试最容易混淆的四个词，精确含义完全不同：

| 概念 | 含义 | 粒度 | 典型出处 |
|---|---|---|---|
| **HITL** (Human-in-the-Loop) | "人必须在环路里参与"的**一类系统性设计** | 系统级 | Active Learning、强化学习从人反馈 |
| **Escalation** | "当前主体决策不了，把决策权上交"的**一次事件** | 事件级 | 任务管理、支持系统、权限系统 |
| **Interrupt** | 状态机层面的挂起原语 | 原语级 | LangGraph `interrupt()`、OS 中断 |
| **Elicitation** | 工具向用户追问缺失输入参数 | 工具级 | MCP 2025 spec |

**关系**：

```text
HITL (设计范畴)
  └─ Escalation (事件) ── 实现依赖 ──▶ Interrupt (原语) 
                                      + 某种 Ask 通道 (可以是 Elicitation)
```

**Pulse 取法**：用 Escalation 做顶层概念，底层用自研 `SuspendedTask` 当 Interrupt 原语，Ask 通道走 IM（主力企业微信，兼容飞书 / CLI）。

### Escalation 的三步原子

这是 SafetyPlane 的核心理解：

| 步 | 含义 | Pulse primitive |
|---|---|---|
| **Suspend** | 把当前任务 state/context 完整保存 | `WorkspaceMemory` + `TaskCheckpoint` |
| **Ask** | 通过某种通道把问题推给人 | IM Channel（企业微信 / 飞书 / CLI Adapter，主力企业微信） |
| **Resume** | 收到回答后从挂起点**继续**，而不是重开 | IntentSpec `system.task.resume` + Brain 读 checkpoint |

**三步缺一步就不是 Escalation**：只有 Ask 没有 Resume → 广播；只有 Suspend 没有 Ask → 死锁；只有 Resume 没有 Suspend → 重放。

### 3.2 一次完整 Escalation 的时序流（以 `job_chat` 为例）

静态组件结构见 `../adr/ADR-006-SafetyPlane.md` §4.0；这里画**动态时序**——HR 问"今天下午方便吗?"到用户回答后真的发回 HR 的完整路径：

```text
  HR          BOSS Web      Brain          SafetyPlane        WorkspaceMem       IM(企微)        User
   │   发消息    │             │                │                  │                 │            │
   │──────────▶│             │                │                  │                  │            │
   │            │  poll 新消息 │                │                  │                  │            │
   │            │◄─────────────│                │                  │                  │            │
   │            │              │  job_chat.reply intent            │                  │            │
   │            │              │──────────────▶│                  │                  │            │
   │            │              │                │  evaluate rules  │                  │            │
   │            │              │                │  evidence_keys 不全覆盖               │            │
   │            │              │                │─────┐            │                  │            │
   │            │              │                │     │            │                  │            │
   │            │              │                │◄────┘ Decision = Ask                │            │
   │            │              │                │                  │                  │            │
   │            │              │                │  Suspend(task_id, snapshot)         │            │
   │            │              │                │─────────────────▶│                  │            │
   │            │              │                │                  │                  │            │
   │            │              │                │    Ask(question, draft, cb_id)      │            │
   │            │              │                │─────────────────────────────────────▶│            │
   │            │              │                │                  │                  │   推送     │
   │            │              │                │                  │                  │──────────▶│
   │            │              │  BrainStep.status=suspended       │                  │            │
   │            │              │◄──────────────│                  │                  │            │
   │            │              │                │                  │                  │            │
  ═══════════════════════  用户何时回答都可以, 可挂 2 小时 ════════════════════════════════════════
   │            │              │                │                  │                  │            │
   │            │              │                │                  │                  │  回答消息  │
   │            │              │                │                  │                  │◄──────────│
   │            │              │                │  system.task.resume IntentSpec      │            │
   │            │              │                │◄─────────────────────────────────────│            │
   │            │              │                │  load SuspendedTask                 │            │
   │            │              │                │◄─────────────────│                  │            │
   │            │              │  Brain.resume(task, user_answer)  │                  │            │
   │            │              │◄──────────────│                  │                  │            │
   │            │              │  合成最终 reply_text               │                  │            │
   │            │              │  gate.check(job_chat.reply, ctx+approval) = Allow    │            │
   │            │              │─────┐         │                  │                  │            │
   │            │              │     │         │                  │                  │            │
   │            │              │◄────┘         │                  │                  │            │
   │            │              │                │  task.resumed 事件 → EventBus + JSONL             │
   │            │              │                │                  │                  │            │
   │            │  真发 reply  │                │                  │                  │            │
   │            │◄─────────────│                │                  │                  │            │
   │   收到回复  │              │                │                  │                  │            │
   │◄──────────│              │                │                  │                  │            │
```

**读图要点**：

1. **同步阶段**（上半段）：一次 Ask **不阻塞 Brain 的其他任务**——`BrainStep.status=suspended` 返回后 Brain 可以接别的指令
2. **异步窗口**（中段虚线）：用户可以 2 秒回、2 小时回、不回都行；超时由 `AskRequest.timeout_seconds` 控制
3. **恢复阶段**（下半段）：用户在 IM 回答后,走 `system.task.resume` IntentSpec 回流到 Brain,Brain 带着 user_answer 重放原 intent——这次 `gate.check` 因为 `session_approvals` 新增了批准会返 `Allow`
4. **审计落盘**：`task.suspended` / `task.resumed` 两个事件全写 EventBus + JSONL,任意时刻可回放

对比 §4.2 LangGraph 的 `interrupt()` / `Command(resume=...)`:Pulse 的做法在原语层本质上一致,但通道走 IM(Pulse 已有优势),状态持久化走既有的 `WorkspaceMemory`(不新建 checkpoint 库)。

## 4. 五方案横向对比

覆盖了当前（2025）所有主流的 "Agent 需要问人" 实现思路。Pulse 设计时各取其长。

| 维度 | Claude Code | LangGraph | MCP Elicitation | OpenAI Assistants | AutoGen |
|---|---|---|---|---|---|
| 挂起机制 | Continue Site (可变 state + `transition` 字段) | Checkpointer (状态落库) | Tool response 字段 `elicitation` | `requires_action` 状态 | 消息路由到 UserProxy |
| 问用户通道 | Ink 终端对话框 | 用户自定义 | MCP client (Claude Desktop) | 开发者自定义 | UserProxyAgent |
| 恢复机制 | 循环 `continue` | `Command(resume=...)` | `callback_id` 回调 | `submit_tool_outputs` | 消息回流 |
| 规则层次 | 8 层级联 | Node 级 `if` | 无（业务自决） | 无 | Agent 角色配置 |
| 去哪里学 | **规则层次最成熟** | **原语抽象最干净** | **协议标准最简洁** | **状态机最清晰** | **"把人当 Agent"思路** |

### 4.1 Claude Code 的 5 个可迁移模式

摘自 Claude Code 泄露代码分析（见 `简历和面试/ClaudeCode_深度研究笔记.md`）：

1. **`CanUseTool` 回调**：每个工具执行前都过同一个闸门。**统一入口**，不在工具内部各自判断。
2. **`DeepImmutable<PermissionContext>`**：安全边界数据从类型系统层面防篡改。
3. **8 来源优先级级联**：规则可从 userSettings / projectSettings / growthbook / cliArg / sessionRules... 用优先级合并，而不是 if/else 遍地。
4. **Fail-open 到询问用户**：分类器不确定时 Ask，不是自动 Deny。
5. **连续拒绝降级**：3 次 Deny → 强制进手动审批模式。防止 Agent 绕来绕去磨死用户。

### 4.2 LangGraph `interrupt()` 示例

开源界最干净的 Interrupt 原语：

```python
from langgraph.types import interrupt, Command

def chat_node(state):
    if needs_user_decision(state):
        # 整个 graph 挂起 + 状态落 checkpoint 库
        answer = interrupt({"question": "HR 问今天下午方便吗?", "draft": "..."})
        return {"reply": answer}

# 外部用户回答后
graph.invoke(None, command=Command(resume="下午 3 点后都行"))
# graph 从 checkpoint 恢复, answer 变量被赋值, 继续执行
```

核心抽象：**`Checkpointer` + `interrupt` primitive + `Command(resume=...)`**。

### 4.3 MCP 2025 的 Elicitation 字段

协议级最小方案：

```json
{
  "content": [...],
  "elicitation": {
    "schema": {"type": "string", "description": "你今天下午是否方便?"},
    "callback_id": "evt_xxx"
  }
}
```

Server 返 tool result 时可带 `elicitation` 请求，Client（Claude Desktop）把请求转给用户，拿到答案后 Server 继续。

### 4.4 OpenAI Assistants `requires_action`

Run 状态机里多一个 `requires_action` 状态：
- Run 从 `in_progress` 转 `requires_action`
- 调 `submit_tool_outputs` 填入外部答案后回到 `in_progress`

### 4.5 AutoGen `UserProxyAgent`

把人当成一个特殊的 Agent。Agent 之间对话 = 消息传递。需要用户时消息发给 `UserProxyAgent`，该 Agent 有模式配置：`ALWAYS` / `NEVER` / `TERMINATE`。

### 4.6 Pulse 的融合取法

- 规则层次：取 Claude Code 的"统一闸门 + 多源级联"，MVP 只做 3 源（core / domain / session）
- 原语抽象：取 LangGraph 的"Checkpointer + resume"，复用 Pulse 既有 `WorkspaceMemory` + `TaskCheckpoint`
- 通道：自研，走 IM（主力企业微信，兼容飞书 / CLI），Pulse 已有优势
- 协议：不走 MCP Elicitation（Pulse 的 MCP Server 是给别人接的，不走这个方向）

详见 `../adr/ADR-006-SafetyPlane.md`。

## 5. Pulse 架构总览（文字版）

这是一份**长期版本**架构图，和 `README.md` 的简化图互为参照。Pulse 当前内核 + SafetyPlane 设计完成后的状态：

```text
┌───────────────── IM 通道 (企业微信 主力 / 飞书 / CLI) ─────────────────────┐
│                        用户 ⇄ Pulse 的唯一双向入口                          │
└──────────────────────────────────┬─────────────────────────────────────────┘
                                   │
                         ┌─────────▼──────────┐
                         │   Adapter Layer    │  解析 IM 消息 → IntentSpec
                         └─────────┬──────────┘
                                   │
                                   ▼
┌────────────────────────── Pulse Core (Python 主进程) ───────────────────────┐
│                                                                             │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐ │
│  │  Brain (ReAct)   │  │  AgentRuntime    │  │  SafetyPlane (ADR-006)   │ │
│  │  逐步推理 + 工具  │  │  Patrol 调度     │  │  PermissionGate          │ │
│  │  调用            │  │  后台守护线程    │  │  Allow / Deny / Ask      │ │
│  └────────┬─────────┘  └────────┬─────────┘  └──────────┬───────────────┘ │
│           │                     │                        │                  │
│           ▼                     ▼                        ▼                  │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │              MemoryRuntime (五层 × Scope 二维)                      │   │
│  │  Operational / Recall / Workspace / Archival / Core                │   │
│  └────────────────────────────────────────────────────────────────────┘   │
│                                   │                                         │
│           ┌───────────────────────┼──────────────────────┐                 │
│           ▼                       ▼                      ▼                 │
│  ┌────────────────┐  ┌────────────────┐   ┌──────────────────────┐       │
│  │ Tool Registry  │  │  EventBus +    │   │  LLM Router          │       │
│  │ Ring 1/2/3     │  │  JSONL Audit   │   │  (model selection)   │       │
│  └────────┬───────┘  └────────────────┘   └──────────────────────┘       │
│           │                                                                │
└───────────┼────────────────────────────────────────────────────────────────┘
            │
            │  tool invocation 可能跨进程
            │
    ┌───────┴───────────────┬──────────────────────┬──────────────────┐
    ▼                       ▼                      ▼                  ▼
┌────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐
│ 外部 MCP Server│  │  Chromium 子进程 │  │  PostgreSQL      │  │  LLM API     │
│ (stdio 子进程) │  │  (Patchright)    │  │  业务数据 + 审计  │  │  (远端 HTTP) │
└────────────────┘  └──────────────────┘  └──────────────────┘  └──────────────┘
```

**关键事实**：
- Pulse Core 本身是**单 Python 进程**，内部用 `asyncio` + 后台守护线程并发
- 外部 MCP Server 是**独立 OS 子进程**，通过 stdio 交互
- 浏览器任务走 Patchright → Chromium 独立进程
- 持久化走 PostgreSQL + JSONL（双写：结构化事件 + 审计日志）

详细分层见 `../Pulse-内核架构总览.md`。

## 6. Pulse 已有 Primitive 清单（对照事实）

这份清单是"写 ADR-006 之前我们查过的事实"，保留下来方便后续 ADR 复用。

| Primitive | 状态 | 证据位置 |
|---|---|---|
| Agent Loop（Brain + ReAct） | ✅ 已有 | `src/pulse/core/brain.py` |
| Tool Registry（三环模型） | ✅ 已有 | `src/pulse/core/tool_registry.py` + SkillGen |
| Checkpoint 落盘 | ✅ 已有 | `AgentRuntime._checkpoints` + `TaskCheckpoint` |
| TakeoverState 三态 | ✅ 已有 | `TakeoverState(autonomous/human_control/paused)` |
| WorkspaceMemory（scope 级） | ✅ 已有 | `src/pulse/core/memory/workspace_memory.py` |
| EventBus + JSONL 审计 | ✅ 已有 | ADR-005 |
| IM 双向通道 | ✅ 已有 | 企业微信 / 飞书 / CLI Adapter + IntentSpec（主力企业微信，代码 `wechat_work_*` / `feishu.py`） |
| ActionReport | ✅ 已有 | `src/pulse/core/action_report.py` + ADR-003 |
| ToolUseContract A/B/C | ✅ 已有 | ADR-001 + `core/tool_use/verifier.py` |
| Patrol 调度 + 控制面 | ✅ 已有 | ADR-004 §6.1 |
| **PermissionGate**（统一闸门） | ❌ **缺（MVP 设计中）** | ADR-006 §4.2 |
| **PermissionContext**（深不可变） | ❌ **缺** | ADR-006 §4.2 |
| **PermissionRules**（多源级联） | ❌ **缺** | ADR-006 §4.3 |
| **Ask primitive**（Suspend-Ask-Resume） | ❌ **缺** | ADR-006 §4.4–4.5 |
| 连续拒绝降级 | ❌ MVP 不做 | ADR-006 §6.2 |
| Kill Switch | ❌ MVP 不做 | ADR-006 §6.2 |

**关键结论**（引自 ADR-006 设计调研）：**Pulse 90% 的 primitive 都在，缺的是一个把它们装配成"授权边界"的统一层 —— 即 SafetyPlane 本身**。

SafetyPlane 五件组件的**静态结构图**（PermissionGate / PermissionContext / Rules / Ask Primitive / Audit 的位置与连线）见 [`../adr/ADR-006-SafetyPlane.md`](../adr/ADR-006-SafetyPlane.md) §4.0；**动态时序图**（一次完整 Suspend-Ask-Resume 的时间流）见上文 §3.2。两张图互补：静态讲"谁跟谁连"，动态讲"先后谁做什么"。

## 7. 扩展方向（未来的 ADR 种子）

按优先级列，每条都可能演化成一份独立 ADR。

| 方向 | 触发条件 | 可能对应的 ADR |
|---|---|---|
| 第二个模块接入 SafetyPlane | ADR-006 MVP 完成后 | ADR-007 Mail SafetyPlane Rules |
| 连续 Deny 降级为手动模式 | `task.denied` 事件持续高频 | ADR-008 SafetyPlane Deny Degradation |
| YOLO Classifier（AI 审 AI） | 规则数超过 20 条仍覆盖不全 | ADR-009 LLM-based Permission Classifier |
| 多源级联（从 3 源扩到 8 源） | 出现"全局 kill switch"需求 | ADR-010 Multi-source Rule Cascade |
| Prompt Contract v2 | 多模型路由需要差异化 prompt | ADR-011 Prompt Contract v2 |
| SkillGen 沙箱升级 | SkillGen 生成的工具出现不可控副作用 | ADR-012 SkillGen Sandbox Isolation |

**重要原则**：不预先写上面任何一份。按宪法"等实际压力出现再决策"，避免投机性架构。

## 8. 术语速查表（一页纸）

| 术语 | 一句话定义 | Pulse 对应 |
|---|---|---|
| **ADR** | Architecture Decision Record，记录一次有架构意义的决定 | `docs/adr/ADR-NNN-*.md` |
| **Harness** | 让 Agent 跑起来的 model-external 基础设施 | Pulse Core 整体 |
| **Agent Autonomy Spectrum** | Agent 自主性 Level 0–4 的分层 | Pulse 目标 Level 3 |
| **HITL** | Human-in-the-Loop，人在环路的一类系统 | SafetyPlane 实现 |
| **Escalation** | 决策权上交的一次事件 | `SuspendedTask` 三态机 |
| **Interrupt** | 状态机层面的挂起原语 | `WorkspaceMemory` + `TaskCheckpoint` |
| **Elicitation** | 工具向用户追问缺失参数 | Pulse 不用（走 IM） |
| **PermissionGate** | 所有工具调用前必过的单一闸门 | `core/safety/gate.py`（设计中） |
| **PermissionContext** | 深不可变的授权上下文 | `core/safety/context.py`（设计中） |
| **ReAct Loop** | Reasoning + Acting 交替的 Agent 循环 | `Brain._react_loop` |
| **Patrol** | 主动巡检式长程任务模式 | `AgentRuntime` 调度的后台任务 |
| **TakeoverState** | Agent 自主度的三态机 | `autonomous` / `human_control` / `paused` |
| **ActionReport** | 长任务结构化执行报告 | ADR-003 |
| **ToolUseContract A/B/C** | 描述契约 / 调用契约 / 执行验证契约 | ADR-001 |
| **IntentSpec** | 意图识别声明式定义 | `system.patrol.*`、`system.task.resume` |
| **EventBus** | 全局事件总线（审计/可观测性基线） | `core/observability/event_bus.py` |
| **Fail-loud** | 异常必须显眼抛出，不得吞 | 编码宪法条款 |
| **Fail-to-Ask** | SafetyPlane 异常时降级为"问用户"，不是 deny | ADR-006 §3 |

## 9. 延伸阅读

**Agent 架构 / Harness**：
- Anthropic, [*Building effective agents*](https://www.anthropic.com/research/building-effective-agents)（2024）
- Claude Code 架构分析：`简历和面试/ClaudeCode_深度研究笔记.md`（项目内部笔记）

**权限 / 安全**：
- LangGraph, [*Human-in-the-loop patterns*](https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/)
- MCP spec, [*Elicitation*](https://modelcontextprotocol.io/specification/2025-06-18/client/elicitation)
- OpenAI, [*Assistants API — Tool calling*](https://platform.openai.com/docs/assistants/tools/function-calling)

**状态机 / 原语**：
- Microsoft AutoGen, [*UserProxyAgent*](https://microsoft.github.io/autogen/docs/Use-Cases/agent_chat)
- OS 中断模型 vs 用户态协程（经典教材任一即可）

**ADR / 工程文档**：
- 见 `./adr-guide.md` §14 参考阅读

---

**文档态度**：

> 这份文档是**活的索引**。每次 Pulse 新增 ADR、引入新 primitive、对比新竞品，都应该回来更新术语表与清单。不维护 = 不真实。
