# ADR-006: SafetyPlane — 授权边界与升级原语

| 字段 | 值 |
|---|---|
| 状态 | Proposed |
| 日期 | 2026-04-24 |
| 作用域 | `src/pulse/core/safety/`(新增)、`src/pulse/core/brain.py`、`src/pulse/core/runtime.py`、`src/pulse/core/memory/workspace_memory.py`、`src/pulse/modules/job/chat/`、`config/safety/`(新增)、`tests/pulse/core/safety/`(新增) |
| 关联 | `ADR-001-ToolUseContract.md`(契约 B 之前的闸门)、`ADR-003-ActionReport.md`(suspended 状态)、`ADR-005-Observability.md`(审计事件)、`docs/code-review-checklist.md` |

---

## 1. 现状

`job_chat` 场景 `2026-04-24 followup`:HR 对用户发"今天下午是否方便"与"请问具体时间",Agent 直接回"今天下午方便,请问具体时间?"。行为层面越权 —— 用户当日是否有空、愿不愿面试、偏好几点,均是用户本人的事实,Agent 无凭据且无授权。

该越权**并非单点 LLM 失误**,由 `src/pulse/modules/job/chat/` 五处模块级缺陷共同构成:

| # | 位置 | 缺陷 | 宪法判例 |
|---|---|---|---|
| 1 | `replier.py:109-116` | HITL 触发走**黑名单**硬编码("薪资数字/offer 比较/线下面试时间"三词),白名单以外遗漏 | Type B 补丁式兼容 |
| 2 | `service.py:_ensure_reply_text` | `ReplyDraft.needs_hitl=True` 信号**只在 reason 后缀加字符串**,action 仍为 REPLY;下游 `_maybe_execute_planned` 不消费该字段 | Type A 防御式逃避(信号被静默吞掉) |
| 3 | `ChatPolicy.hitl_required` | 现有 HITL 是**会话级特权批准**("要不要开启自动回复"),非**内容级内容决策**("这一条是否该问用户");两者被混为一谈 | 抽象错位 |
| 4 | `service.py:377-386` | `ChatAction.ESCALATE` 分支仅 `notifier.send(Notification(level="warning"))`,单向广播,无挂起、无恢复、无重入保护 | Type B(`"未来改为 escalate"` 注释滞留) |
| 5 | `core/` | 没有任何统一的授权闸门;每个 module 自建 `needs_hitl`/`escalate` 语义,无跨模块一致性 | 架构缺位 |

五条同属一个根因:**Pulse 当前没有"Agent 能答 vs 不能答"的统一模型**。继续在 `chat` 模块打补丁等价于把同一份畸形在 `mail` / `game` / `travel` / `intel` 模块再复制四遍。

本 ADR 约定的 SafetyPlane 是 Pulse 的"授权边界"层 —— **在 Brain ReAct 循环的每次工具调用之前、在 Module 每次 mutating 动作之前,返回 `Allow / Deny / Ask` 三值决策,并实现 `Ask` 的 Suspend-Ask-Resume 完整回路**。

---

## 2. 分层职责

| 层 | 负责 | 不负责 |
|---|---|---|
| **SafetyPlane core** (`core/safety/`) | `PermissionGate` 接口、`PermissionContext` 构造、`Decision` 判决、`Rules` 加载与合并、`SuspendedTask` 状态机、`Ask` 原语 | 任何业务词汇、任何 `job_chat`/`mail`/`game` 特定逻辑 |
| **Core Rules** (`config/safety/core.yaml`) | 跨模块通用规则:"无 profile 凭据的用户事实 → Ask"、"金钱支出 > 阈值 → Ask"、"首次联系第三方 → Ask" | 模块业务细节 |
| **Domain Rules** (`config/safety/<domain>.yaml`,各模块贡献) | 模块特化规则,继承 Core;只声明,不在代码里扩 | 通用能力 |
| **Brain / Module 调用侧** | 调用 `gate.check(intent, ctx)`;根据返回的 `Decision` 执行、跳过、或提交 `AskRequest` | 自建 HITL 黑名单/白名单 |
| **AgentRuntime / Memory** | 复用既有 `_checkpoints`/`TakeoverState` + `WorkspaceMemory` 承载 `SuspendedTask` 的持久化 | 决策语义 |
| **IM Channel (企业微信 / 飞书 / CLI Adapter)** | 发送 `AskRequest` 的呈现消息;接收用户回答,通过新增 IntentSpec `system.task.resume` 路由。主力企业微信(`WechatWorkChannelAdapter`),飞书 / CLI 同套协议兼容 | 判断是否要 Ask |

---

## 3. 第一性原理

| 维度 | 分析 | 结论 |
|---|---|---|
| **边界形态** | 黑名单式 `needs_hitl`(列举敏感词)每遇到新场景就要加一条,无穷尽 | 反转为**白名单式**:只有能从 profile / memory 举证的事实可自答,其他默认 `Ask` |
| **决策值域** | `bool needs_hitl` 只有两态,语义贫弱("需要人"但人怎么参与?);上游无法区分"禁止" vs "需要补充信息" | `Decision = Allow \| Deny \| Ask`,`Ask` 是一等公民,携带 question/draft/resume_handle |
| **规则来源** | 规则写在 prompt / Python if-else 里,每次调整要发版、无 diff、无审计 | 声明式 YAML + dataclass schema,规则是**数据**,支持多源级联合并(core + domain + session) |
| **上下文不变性** | 运行时若任何层次误改 `PermissionContext`(如误改 user_id 或 session rules),整个 SafetyPlane 判决失去可信度 | `@dataclass(frozen=True)` + `Mapping`(只读)+ `tuple`(不可变序列)构造深不可变;类型系统层面阻止误改 |
| **升级原子性** | `Ask` 是三步(Suspend → Ask → Resume);任何一步缺失都回到补丁态 | 用 `SuspendedTask` 状态机把三步绑定,缺一步则判决作废,`task.suspended` / `task.resumed` / `task.ask_timeout` 事件全部写 EventBus |
| **失败语义** | SafetyPlane 自身异常(YAML 损坏/Rule 解析失败/Memory 不可用)若 fail-open 则等于无闸门,若 fail-closed 则 Agent 死锁 | **Fail-to-Ask**:SafetyPlane 异常一律降级为"需要用户确认",Deny 路径必须来自**显式规则**,不得来自异常分支 |
| **可逆性** | 抽象若过早或过晚,迁移成本高 | MVP 仅 `PermissionGate` + `Ask primitive` + 2 套 Rules(core + job_chat),**不做** YOLO Classifier / Bash AST / Kill Switch;迁移第二个模块(mail/intel)验证通用性后再扩 |
| **落地成本** | Pulse 已有 `_checkpoints` / `TakeoverState` / `WorkspaceMemory` / `EventBus` / IntentSpec | SafetyPlane MVP 是"装配层",非"地基层";核心原语复用既有 primitive,新增代码量主要在 Rules 引擎与 AskRequest 协议 |

---

## 4. 接口契约

### 4.0 核心组件鸟瞰

```text
                  SafetyPlane (core-agnostic)
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  PermissionGate (Protocol)                           │   │
│  │    .check(intent, ctx) → Allow | Deny | Ask          │   │
│  └─────────────────────┬────────────────────────────────┘   │
│                        │                                     │
│  ┌─────────────────────▼────────────────────────────────┐   │
│  │  PermissionContext (frozen, DeepImmutable)           │   │
│  │    · module / task_id / user_id / trace_id           │   │
│  │    · accumulated rules (多源级联, 只读)              │   │
│  │    · profile_view (可举证字段的只读视图)             │   │
│  │    · session_approvals (会话内一次性授权)            │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Rules (声明式 YAML, 多源合并)                       │   │
│  │    · CoreRules:  profile-evidence-required, pay-ask  │   │
│  │    · DomainRules (各模块自己声明, 不改 core)         │   │
│  │       ├── job_chat: "only profile-backed facts"      │   │
│  │       ├── mail:     "read=allow, send=ask"           │   │
│  │       └── game:     "any paid action=ask"            │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Ask Primitive (Escalate 的原子三步)                 │   │
│  │    1. Suspend → WorkspaceMemory 存 SuspendedTask     │   │
│  │    2. Ask     → IM channel (企业微信 / 飞书) + draft │   │
│  │    3. Resume  → IntentSpec system.task.resume        │   │
│  │                 → Brain 读 SuspendedTask + answer    │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Audit (Fail-loud 可回放)                            │   │
│  │    · allow/deny/ask 全写 EventBus + JSONL            │   │
│  │    · rule_id + evidence 摘要, 支持事后回放           │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
                ▲                              ▲
                │ HookPoint.before_tool_use    │ 贡献 DomainRules
                │ (core/brain.py 已有钩点)       │
    ┌───────────┴─────────────┐    ┌──────────┴──────────┐
    │ Brain / AgentRuntime    │    │ job_chat / mail /   │
    │ _safety_before_tool hook│    │ game / travel ...   │
    └─────────────────────────┘    └─────────────────────┘
```

**读图要点**:

- 上方五个盒子是 SafetyPlane 的**五件组件**,§4.1–4.6 分别定义其接口
- SafetyPlane 对内 core-agnostic(不含任何业务词汇),对外提供两个接入点:**调用方**(Brain/AgentRuntime)和**贡献方**(各 module 声明 DomainRules)
- `PermissionContext` 是 Gate 的唯一入参容器,其不变性由类型系统兜底(§4.2 不变式)
- `Ask Primitive` 的三步是**原子的**,缺一步不成立(§4.4 不变式)
- `Audit` 不区分 allow/deny/ask,三种判决同等可追溯

### 4.1 Decision(`core/safety/decision.py`)

```text
DecisionKind = Literal["allow", "deny", "ask"]

Decision(
  kind: DecisionKind,
  reason: str,                      # 规则 id 或 evidence 链路, 审计用
  rule_id: str | None,              # 命中的规则 id (none = fallback)
  ask_request: AskRequest | None,   # kind == "ask" 时必填
  deny_code: str | None,            # kind == "deny" 时必填 (对外可读代码)
)
# 注: 字段叫 ask_request 而非 ask, 避免与便捷构造器 Decision.ask() 同名冲突.

AskRequest(
  question: str,                # 给用户看的原问题 (HR 的话 / 工具需要的信息)
  draft: str | None,            # Agent 对"如果你同意/回答 X"的建议回复草稿
  context: dict[str, Any],      # 供用户判断的上下文 (HR 名 / 公司 / 岗位 / 历史)
  resume_handle: ResumeHandle,  # 恢复时的路由信息
  timeout_seconds: int,         # 超时自动走 deny 分支
)

ResumeHandle(
  task_id: str,                 # AgentRuntime 的 SuspendedTask id
  module: str,                  # "job_chat" / "mail" / ...
  intent: str,                  # 触发恢复的 IntentSpec 名
  payload_schema: str,          # 用户答案的 schema id, 驱动 payload 校验
)
```

**不变式**:

- `kind == "ask"` ⇒ `ask is not None`;`kind == "deny"` ⇒ `deny_code is not None`。其他组合构造失败(`__post_init__` 断言)。
- `rule_id` 为 `None` 仅当走 **fail-to-ask** 降级路径;此时 `reason` 必须包含异常轨迹摘要。
- `AskRequest.question` 不得为空串;`draft` 可为 `None` 表示由用户自行决定后交由 Agent 续写。
- Decision 是**值对象**,Gate 每次 check 返回新实例,不跨调用复用。

### 4.2 PermissionGate(`core/safety/gate.py`)

```text
class PermissionGate(Protocol):
    def check(
        self,
        *,
        intent: Intent,
        context: PermissionContext,
    ) -> Decision: ...

Intent(
  kind: Literal["tool_call", "module_action"],
  name: str,                    # "job_chat.reply" / "bash.exec" / "mail.send"
  args: Mapping[str, Any],      # 只读视图, 不可改
  evidence_keys: tuple[str, ...],  # 模块声明: 该 intent 必须依据的 profile/memory key
)

PermissionContext(
  module: str,
  task_id: str,
  user_id: str,
  trace_id: str,
  rules: RuleSet,               # 多源合并后的只读规则集
  profile_view: ProfileView,    # 当前可举证的 profile 字段只读视图
  session_approvals: frozenset[str],  # 本会话已授予的 one-time approvals
)
```

**不变式**:

- `PermissionContext` 与其子字段全部 `@dataclass(frozen=True)`;`rules`/`profile_view`/`args` 的容器类型固定为 `Mapping`/`frozenset`/`tuple`,不得暴露 `dict`/`set`/`list`。
- `Gate.check` **纯函数**:同一 `(intent, context)` 必须返回相同 `Decision`(同一实例不要求,值等价即可)。禁止在 `check` 内触发任何 mutating 副作用(包括 logger 之外的 I/O)。
- `Intent.evidence_keys` 由模块声明;Gate 在规则引擎里比对 `profile_view` 是否能覆盖 `evidence_keys`,覆盖不足直接走 `Ask`(白名单逻辑的落点)。
- 默认实现 `WorkspacePermissionGate` 通过 `RuleEngine.evaluate(ctx.rules, intent, profile_view)` 计算;模块可注册特化 `Gate` 覆盖 core 实现,但必须遵守 `PermissionGate` Protocol。

### 4.3 Rules schema(`config/safety/*.yaml`)

```yaml
version: 1
domain: core          # "core" / "job_chat" / "mail" / "game" / ...
rules:
  - id: core.profile_evidence_required
    when:
      intent_kind: tool_call
      intent_name_glob: "job_chat.reply*"
    require:
      # 白名单: intent 涉及的 evidence_keys 必须被 profile_view 全覆盖
      all_evidence_in_profile: true
    otherwise:
      decision: ask
      question_template: "HR 问:{hr_message}\n我需要你确认:{missing_fields}"
      draft_template: null
      timeout_seconds: 7200

  - id: core.monetary_action_always_ask
    when:
      intent_kind: module_action
      intent_name_glob: "*.pay*"
    require:
      session_approval: "monetary:{target}"
    otherwise:
      decision: ask
      question_template: "即将支出 {amount} {currency},是否授权?"
      timeout_seconds: 600
```

**不变式**:

- `version: 1` 强制;未来破坏性变更走 v2,core 同时保留 v1 加载路径一个版本窗口。
- 规则 id 全局唯一,命名 `<domain>.<snake_case>`;审计日志用 `rule_id` 作外键。
- 加载顺序:`core.yaml` → `<domain>.yaml` → session rules;后来者**覆盖**同 id,**合并**不同 id。合并冲突时 `core` 规则不可被 domain 规则下降优先级(防止 domain 规则偷偷放宽核心约束)。
- `when.intent_name_glob` 支持 `*` 通配,不支持正则 —— 规则引擎拒绝加载含正则元字符(`[]/()^$`)的模式,防止 RCE 级规则注入。
- `require` 的子句白名单:`all_evidence_in_profile` / `session_approval` / `budget_under` / `trace_id_not_in_denylist`。新增子句必须先在 `core/safety/rule_predicates.py` 注册类型。
- `otherwise.decision` 枚举值 `ask` / `deny`;**不得**写 `allow`(否则规则失去约束意义)。
- `timeout_seconds` 下限 60,上限 604800(一周);超限加载失败。

### 4.4 SuspendedTask(`core/safety/suspended.py`)

```text
SuspendedTask(
  task_id: str,                   # AgentRuntime 既有 checkpoint id, 不新发
  module: str,
  trace_id: str,
  workspace_id: str,              # 落盘 workspace_facts 的 workspace_id
  suspended_at: datetime,
  ask_request: AskRequest,        # 同 Decision.ask_request
  original_intent: Intent,        # 被拦截的 intent, 用于 resume 后重放
  status: Literal["awaiting_user", "resumed", "timed_out", "denied"],
  resolved_at: datetime | None,
  resolution_payload: Mapping[str, Any] | None,   # 用户的回答
)
```

**不变式**:

- `SuspendedTask` 持久化走既有 `WorkspaceMemory` 的 `workspace_facts` 表(不新建表):key 形如 `safety.suspended.<task_id>`,value 是 JSON 序列化的 SuspendedTask(利用 `WorkspaceMemory` 已有的 JSON 编解码契约,见 `workspace_memory.py` L53–74)。
- 同一 `(module, trace_id, original_intent.name)` 同时最多存在一条 `awaiting_user` 记录;二次提交直接返回已有 task_id,避免反复骚扰用户。
- `resumed` / `timed_out` / `denied` 是终态,不得再转回 `awaiting_user`;超时 task 再次触发等价于 new task。
- 状态每次跃迁发 `EventBus`(`core/events.py::EventBus`)事件:`task.suspended` / `task.resumed` / `task.ask_timeout` / `task.denied`,payload 含 `task_id`/`rule_id`/`decision.reason`;审计持久化由 `JsonlEventSink`(`core/event_sinks.py`)自动承担,不需 SafetyPlane 单独写盘。

### 4.5 IntentSpec `system.task.resume`(新增,路由 Resume)

```text
system.task.resume
  match: (IM 消息里存在 callback_handle ∈ {awaiting SuspendedTask.ask.resume_handle})
  args:
    task_id: str                  # 从 callback_handle 反解
    payload: Mapping[str, Any]    # 用户答复, 先按 ResumeHandle.payload_schema 校验
  effect:
    1. 取出 SuspendedTask, 校验 status == awaiting_user
    2. WorkspaceMemory 读 original_intent + 原 context
    3. Brain.resume(task=suspended, user_answer=payload)
       → 合成最终 output (reply HR / execute module_action)
    4. SuspendedTask.status := resumed
    5. 发 task.resumed 事件
```

**不变式**:

- Resume 只能由 **人类用户在绑定 IM 会话**触发;其他 Agent / 定时任务不得触发(否则破坏 HITL 语义)。
- Resume 失败(schema 校验失败 / 原 task 已终态)不得悄悄失败;必须回 IM 明文"你的回答无法恢复任务 X,原因:Y"。
- Resume 后原 intent 的重放仍走 Gate,允许 `session_approvals` 因本次回答新增一条一次性授权(如"这次面试时间你决定好了,以后类似时间问题 Agent 自己回答"由用户选填)。

### 4.6 Brain 接线(通过 `HookPoint.before_tool_use` 集成)

Pulse `core/brain.py` ReAct loop 在 `tool_registry.invoke` **之前已有 `HookPoint.before_tool_use` 钩子**(`brain.py:555`),`server.py:524` 亦已注册 `policy.before_tool` 同类钩子。SafetyPlane **不改 ReAct loop,而是注册一枚同等优先级的 hook**,与现有 policy 钩子共存、顺序排列。

```text
# core/safety/hooks.py (新增, 约 30 行)
def _safety_before_tool(hctx) -> HookResult:
    intent = Intent(kind="tool_call", name=hctx.args["tool_name"],
                    args=hctx.args["tool_args"], evidence_keys=tool_spec_of(...).evidence_keys)
    decision = gate.check(intent=intent, context=hctx.ctx.permission_context)
    match decision.kind:
      case "allow":
        return HookResult()                                         # 放行
      case "deny":
        return HookResult(block=True, reason=f"deny:{decision.deny_code}",
                          injected={"safety_decision": decision})   # Brain 原路径走 StopReason.tool_blocked
      case "ask":
        task = suspended_store.create(
            ask_request=decision.ask_request, original_intent=intent, ctx=hctx.ctx
        )
        im_channel.notify_ask(ask_request=decision.ask_request, task_id=task.task_id)
        return HookResult(block=True, reason=f"ask:{task.task_id}",
                          injected={"safety_decision": decision, "suspended_task_id": task.task_id})

# core/server.py 注册 (与现有 policy.before_tool 并列)
hooks.register(HookPoint.before_tool_use, _safety_before_tool, name="safety.before_tool", priority=10)
```

**不变式**:

- `gate.check` 走 `HookPoint.before_tool_use`,与 Brain 预算检查(`brain.py:543`)之后、tool 调用之前同一断点;优先级 `10`(早于现有 `policy.before_tool` 的 `20`),语义上"权限优先于策略":无权的动作连策略都不评估。
- 与 `ToolUseContract` 契约 B 串联(`ADR-001` §2):B 层结构信号是"LLM 确实要调此工具",SafetyPlane 是"系统是否允许"。两者职责正交,不得互相替代。
- `HookResult.injected` 用作 Decision 透传通道:`BrainStep` 在接收到 `hook_blocked` 时,额外从 `injected["safety_decision"]` 读取 `Decision` 填入 `step.decision` 字段。
- `BrainStep` 新增 `decision: Decision | None` 字段;`StopReason` 扩展 `tool_denied` / `tool_suspended` 两枚举,`tool_blocked` 保留作为其他 policy hook 拒绝的兜底语义;`ActionReport.status` 同步扩展 `"denied" / "suspended"`(见 ADR-003 §4.1)。
- Brain **不得**直接决定是否 Ask;所有授权判断必须经 Gate。Brain 只消费 `Decision`。

---

## 5. 规则形态

### 5.1 Core Rules(MVP 内含 3 条)

| rule_id | 触发 | 判决 | 理由 |
|---|---|---|---|
| `core.profile_evidence_required` | `tool_call` 且 `evidence_keys` 与 `profile_view` 不全覆盖 | `Ask` | 白名单:Agent 仅回答自己能举证的用户事实 |
| `core.monetary_action_always_ask` | `module_action.*.pay` / `*.charge` / `*.transfer` | `Ask` | 金钱类副作用无条件升级(MVP 范围不区分金额阈值) |
| `core.third_party_first_contact_ask` | `module_action` 向**新的**第三方发信/私信/消息 | `Ask` | 首次联系必须用户知情(二次联系走 session_approvals) |

### 5.2 `job_chat` Domain Rules(MVP 内含 4 条)

| rule_id | 触发 | 判决 | evidence_keys |
|---|---|---|---|
| `job_chat.reply_from_profile` | `job_chat.reply` | fallback `Ask`,`profile_view` 覆盖 `evidence_keys` 时 `Allow` | 依 intent 动态,常见:`salary_expectation` / `base_city` / `tech_stack` / `career_intent` / `education` / `experience_years` |
| `job_chat.time_commitment_always_ask` | `job_chat.reply` 且问题命中时间语义(`"今天"`/`"明天"`/`"下午"`/`"几点"` 等) | `Ask` | 显式列举:时间承诺不存在 profile 举证 |
| `job_chat.interview_decision_always_ask` | `job_chat.accept_card` / `job_chat.reject_card`(任何面试邀约卡片) | `Ask` | 是否接受面试是用户本人决策 |
| `job_chat.send_resume_session_approval` | `job_chat.send_resume` | 首次 `Ask`,用户同意后本会话后续同 HR 自动 `Allow` | 投简历是动作,不是事实陈述;授权可短期复用 |

规则 3、4 覆盖当前 `replier.py` 黑名单三词未包含的**全部新场景**。规则 1 的白名单本质使"遗漏"不可能发生 —— 凡 `evidence_keys` 声明外的,一律 `Ask`。

### 5.3 规则贡献流程(其他模块接入时)

1. 模块在 `config/safety/<domain>.yaml` 声明自己的 domain rules
2. 模块在 tool spec 里声明每个 tool 的 `evidence_keys: tuple[str, ...]`
3. 模块在 `on_startup` 调 `safety.register_domain(domain="<name>")` 触发规则加载
4. SafetyPlane core 无需修改

合规准入:同一 intent 不得被 domain rule 下调至 `Allow`(`core` 规则不可被覆盖放宽)。规则加载器在启动时静态检查此约束。

---

## 6. 可逆性与重评触发

### 6.1 降级开关

| 层 | 环境变量 | 行为 |
|---|---|---|
| 全局 | `PULSE_SAFETY_PLANE=off` | Gate.check 退化为恒 `Allow`,所有 Ask/Deny 失效(紧急回滚) |
| Rules | `PULSE_SAFETY_RULES_DIR=/path/to/override` | 覆盖默认 `config/safety/`,支持灰度实验 |
| Ask 通道 | `PULSE_SAFETY_ASK_CHANNEL=im\|dryrun` | `dryrun` 时 Ask 不发 IM,仅写 JSONL,用于 staging |
| 超时 | `PULSE_SAFETY_ASK_DEFAULT_TIMEOUT=7200` | Rules 里未声明 timeout 时的全局兜底 |

全局 off 是**最后手段**,任何 off 启用必须在 JSONL 审计里标 `safety.disabled=true`,上线后 24 小时内必须 on。

### 6.2 MVP 明确不做

| 项 | 理由 | 后续 ADR |
|---|---|---|
| YOLO Classifier(AI 审 AI) | Claude Code Layer 4,工程成本 > MVP 价值;2 套 Rules 已能覆盖 `chat` + 第二个模块 | v1.1 独立 ADR |
| 手写 Bash AST 解析 | Pulse 不执行 shell,无 attack surface | 不在路线图 |
| 8 来源优先级级联 | MVP 仅 core + domain + session 三源,足够 | v1.2 扩为多源时升级 |
| 编译时消除 | Python 无 Bun `feature()` 机制;`PULSE_SAFETY_*` 环境变量达到运行时控制等效 | 不适用 |
| 连续 Deny 降级为手动模式 | 首版日常业务不会高频 Deny,规则基本以 Ask 为主 | v1.1 视 `task.denied` 事件频次决定 |
| Kill Switch 服务端控制 | 无中央控制面,单用户自部署为主 | 有需求再加 |

### 6.3 重评触发

MVP 第一版落地 + `job_chat` 接入完成后,以下信号任一出现 → 触发重评设计:

1. 第二个模块(`mail` / `intel` 等)接入时,`core.yaml` 需新增 > 3 条规则,或需要修改 `core/safety/` 代码超过 100 行 → 抽象未收敛,重写核心原语。
2. 连续 7 天 EventBus `task.ask_timeout` 比例 > 30% → 用户被问得过多,规则白名单太紧,需调整 evidence_keys 声明粒度。
3. 连续 7 天 `task.denied` 出现 `rule_id IS NULL`(即 fail-to-ask 后又被某默认拒 deny)→ 出错路径语义漏洞,需补异常分支测试。
4. 用户在 IM 手动跳过 Ask(直接绕过 Agent 去 BOSS 回复)次数 > 5 次 → Ask 通道体验差,需改进 question/draft 呈现。

上述指标通过 `JsonlEventSink` 事件可聚合,不引入新的指标管线。

---

## 7. 落地顺序

| 阶段 | 内容 | 完成标记 |
|---|---|---|
| **Step A.1** | `core/safety/decision.py` + `intent.py` + `context.py` 契约定义 + 30+ 条契约单测(frozen/不变式/Protocol 签名) | 所有 SafetyPlane primitive 的 schema 写就且类型安全 |
| **Step A.2** | `core/safety/rule_engine.py` YAML 加载 + predicate 注册 + 多源合并 + 启动时静态校验(`core 不可被 domain 放宽`) | `config/safety/core.yaml` 加载通过,规则冲突检测通过 |
| **Step A.3** | `core/safety/gate.py::WorkspacePermissionGate` 默认实现(Rule Engine 之上)+ fail-to-ask 异常包装 | `PermissionGate.check` 三路径(allow/deny/ask)全单测覆盖 |
| **Step A.4** | `core/safety/suspended.py::SuspendedTaskStore` 基于 `WorkspaceMemory` 实现 + 事件发射 | `task.suspended`/`task.resumed`/`task.ask_timeout` 三事件走通 |
| **Step B.1** | 注册 `HookPoint.before_tool_use` 钩子 `_safety_before_tool`(priority=10,先于现有 `policy.before_tool`);`BrainStep` 加 `decision` 字段;`StopReason` 扩展 `tool_denied`/`tool_suspended`;`ActionReport.status` 扩展 `denied`/`suspended` | hook 单测覆盖三路径 + 向后兼容(无规则时恒 allow) |
| **Step B.2** | IntentSpec `system.task.resume` 新增;IM Channel 把 `AskRequest` 渲染为 IM 消息(主力企业微信,飞书 / CLI 走同套 Adapter 协议);回答路径走 Resume | `trace 2026-04-24 followup` 场景用 fixture 回放:挂起 → IM 问 → 用户答 → 发 HR → 状态归档 |
| **Step B.3** | `job_chat` 迁移:`replier.py` 删 `needs_hitl` 黑名单(漏洞 1),`service.py` 删 `_ensure_reply_text` 的 reason 后缀逃避(漏洞 2),`ChatPolicy.hitl_required` 字段标 deprecated(漏洞 3),`ChatAction.ESCALATE` 改走 Gate → Ask(漏洞 4),`config/safety/job_chat.yaml` 贡献 4 条 domain rules | `job_chat` 模块下 5 个漏洞全部在 PR 一次性关闭,不留 TODO |
| **Step B.4** | 3-5 条真实 HR trace 切片作 fixture,端到端覆盖:profile 能举证直答 / 时间问题 Ask / 用户 IM 回复后发 HR / 用户超时不答自动归档 | `tests/pulse/core/safety/` + `tests/pulse/modules/job/chat/` 全绿 |
| **Step C** | 第二个模块接入(优先 `mail`:读信 Allow / 发信 Ask)验证通用性 | `core/safety/` 不需修改或改动 < 100 行 |
| **Step D** | 基于 `task.ask_timeout` / `task.denied` 真实分布决定是否做 YOLO Classifier / 连续 Deny 降级 | 独立 ADR-007/008 |

Step A 是纯增量,不改任何既有调用链。Step B.1 接入 Brain 时默认规则为空,此时 `Gate.check` 恒返回 `Allow`,语义上等价于当前 Pulse,保证"合入主干后不改变行为,只等 Step B.3 的 Rules 到位才激活约束"。

---

## 8. 风险与取舍

1. **Ask 骚扰率**:白名单过紧会让用户被问得过多。mitigation:`job_chat` 规则 4 用 `session_approval` 机制 —— 同 HR 首次同意后自动放行后续同类动作;超时 Rule 提供"默认 deny 并提示用户"分支,避免悬挂。监控信号见 §6.3 触发条件 2。
2. **规则声明冗长**:YAML 规则写多了会变 config 屎山。mitigation:MVP 限 7 条总规则(core 3 + job_chat 4);第二个模块接入时若需新增 > 3 条 core 规则,直接触发重评(§6.3 触发条件 1),迫使抽象而非堆规则。
3. **冻结数据结构的可用性**:全 `frozen=True` + `Mapping`/`frozenset`/`tuple` 在一些 Python 序列化路径上(老版 pickle / 某些 pydantic 旧版本)不友好。mitigation:`to_dict()`/`from_dict()` 往返作为唯一跨边界传输方式,禁止直接序列化 `frozen` 对象。
4. **Resume 路径的 IM 会话绑定**:若用户同时运行多个 task,IM 会话需识别当前回答对应哪个 `task_id`。mitigation:`AskRequest.resume_handle.task_id` 通过**消息文本中的短标识**(如 `[PS-T-{4 位}]`)或 IM 按钮 callback 携带(企业微信交互卡片 `msgtype=template_card` / 飞书 `interactive` 卡片 / CLI 直接短标识);MVP 优先按钮 callback,短标识作为 fallback(写入 Rules `question_template`)。
5. **规则调试成本**:命中哪条规则、为何 `Ask` 对调试者不直观。mitigation:`Decision.reason` 必须含 `rule_id` + 未满足的 `evidence_key` 列表;`JsonlEventSink` 的 `task.suspended` 事件 payload 完整留存 `Intent` + `profile_view` 快照,便于回放。
