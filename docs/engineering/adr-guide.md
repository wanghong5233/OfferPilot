# ADR 写作规范

> 定位：Pulse 的 **Architecture Decision Record** 写作指南与模板。
> 目标读者：任何准备在 `docs/adr/` 下新建一份 `ADR-NNN-<title>.md` 的人。
> 关联：`../code-review-checklist.md`（工程宪法）、`./agent-concepts.md`（概念速查）。

---

## 1. ADR 是什么

**Architecture Decision Record（架构决策记录）**：记录一次**有架构意义的决定**，以及**为什么这么决**。

概念源于 Michael Nygard 2011 年 Cognitect 博客《Documenting Architecture Decisions》，已被 IEEE Software、AWS Prescriptive Guidance、Azure Well-Architected、GitHub `adr/` 组织列为标准实践。

核心价值只有一句：

> **让未来的人（包括未来的你）能追溯"为什么这么写"，而不只是看到"写成这样"。**

## 2. ADR 不是什么

| 常见误解 | 真相 |
|---|---|
| ADR 是实施计划 / Gantt | 不是。ADR 是**决策**；实施由 tickets / PR 承担。Pulse-B 模板在 §7 给落地顺序，但那是决策的**约束**，不是排期。 |
| 写好 ADR 就要按它执行 | 半对。ADR 通过后代码**必须**兑现它。违背要么 `amend`（修同一份），要么 `supersede`（写 ADR-NNN 取代）。不能悄悄改。 |
| ADR 可以堆调研、对比、学习笔记 | 绝对不行。ADR 只承担"决策"。调研/产品对比/学习辨析请放 `engineering/`（如 `agent-concepts.md`）。 |
| ADR 越详细越好 | 错。Nygard 原版 20 行就能结账。过度扩写说明没想透，或混进了非 ADR 内容。 |
| 写完 ADR 就能不改 | 错。状态可流转：`Proposed → Accepted → Deprecated / Superseded`。ADR 是**不可变的历史记录**，但可以被后来的 ADR 取代。 |

## 3. 业界标准三档

| 档位 | 行数 | 固定章节 | 适用 |
|---|---|---|---|
| **Nygard 原版**（2011） | 20–50 | Title / Status / Context / Decision / Consequences | 任何决策的最小集 |
| **MADR v4**（2024, 主流） | 80–150 | 上 + Decision Drivers / Considered Options / Pros & Cons / Confirmation | 需要选项对比、需正式评审 |
| **Y-Statement**（Zdun et al.） | 1 句 | "In the context of X, facing Y, we decided for A and against B, to achieve Q, accepting D." | 库选型、极小决策、Slack 上快速对齐 |

参考：<https://adr.github.io/>、<https://adr.github.io/madr/>。

## 4. Pulse 采用的两档

Pulse 的 `ADR-001/003/004/005/006` 当前都是**重量级**（300–400 行），把 ADR + 接口契约 + 落地顺序三件事合并。这是刻意的：Pulse 规模不大，合并让同一份文档作为 single source of truth 更省成本。但**不是所有决策都需要这么重**。

因此规范两档并行：

| 档 | 别名 | 行数 | 适用 |
|---|---|---|---|
| **Pulse-A** | 轻量 / MADR-style | 50–120 | 库选型、框架切换、单点策略（示例：ADR-002 patchright） |
| **Pulse-B** | 重量 / RFC-style | 200–400 | 新增核心子系统、跨模块契约、授权/内核语义（示例：ADR-001、ADR-006） |

决策标准：

- 涉及 **≥ 2 个模块 / 贯穿 Core** → Pulse-B
- 只改 **1 个模块 / 单点依赖** → Pulse-A
- 不确定 → 先写 Pulse-A，写到一半发现溢出再升 Pulse-B

## 5. Pulse-A 模板（轻量）

```markdown
# ADR-NNN: <简短标题, 动词开头, 如 "Adopt X" / "Replace Y with Z">

| 字段 | 值 |
|---|---|
| 状态 | Proposed / Accepted / Deprecated / Superseded by ADR-MMM |
| 日期 | YYYY-MM-DD |
| 作用域 | 被约束的代码/目录/配置 |
| 关联 | 前置 ADR / 相关宪法条款 / 关键 issue |

## 1. 现状
<!-- 两三句说清楚当前状态。不是"问题背景"长篇大论, 是"我们现在在哪里"。 -->

## 2. 决策
<!-- 一句话可以说清楚的决定。必要时附 1 个代码片段或 1 个表格。 -->

## 3. 理由
<!-- 3-5 条 bullet, 每条不超过一行。引用前置 ADR / 宪法条款。 -->

## 4. 取舍
<!-- 选它放弃了什么; 反对方案各一行。 -->

## 5. 合规兜底
<!-- 这个决策被违反时, 谁拦? 一般是某个 regression test 或 lint 规则。 -->
```

典型样例：ADR-002（patchright 替代 playwright，~100 行）。

## 6. Pulse-B 模板（重量）

```markdown
# ADR-NNN: <主题> — <子标题/口号>

| 字段 | 值 |
|---|---|
| 状态 | Proposed / Accepted / ... |
| 日期 | YYYY-MM-DD |
| 作用域 | 被约束的代码/目录/配置 |
| 关联 | 前置 ADR / 设计文档 / 核心原语 |

## 1. 现状
<!-- 当前状态 + 具体触发事件(场景/bug trace). 可列表枚举模块级缺陷, 必要时给宪法判例标注。 -->

## 2. 分层职责
<!-- 表格: 每层负责什么 / 不负责什么。这是决策的"骨架图", 防止后续争议。 -->

## 3. 第一性原理
<!-- 表格: 维度 / 分析 / 结论。这是 Pulse 特有的章节, 逼作者对每个关键维度给出推理链, 不留"感觉这样比较好"的黑箱。 -->

## 4. 接口契约
<!-- 关键数据结构 + Protocol 签名 + **不变式**. 所有跨模块约定必须在这里冻结。 -->

## 5. 核心形态（可选）
<!-- 若决策本身是"某种规则/策略/协议", 在这里列 MVP 实例; 否则省略。 -->

## 6. 可逆性与重评触发
<!-- 6.1 降级开关(紧急回滚路径)
     6.2 MVP 明确不做的(防止 scope creep)
     6.3 重评触发条件(什么信号出现时回来重写本 ADR) -->

## 7. 落地顺序
<!-- 表格: Step A/B/C/D, 每步 "完成标记"。一批改动做完再跑一次, 不与调试宪法冲突。 -->

## 8. 风险与取舍
<!-- 列 3-6 条真实风险 + mitigation. 承认局限, 不做"零风险"虚假陈述。 -->
```

典型样例：ADR-001（ToolUseContract）、ADR-006（SafetyPlane）。

## 7. 何时写 ADR

触发条件满足**任一项**就写：

1. 新增一个 Core 子系统 / 跨模块契约（例：SafetyPlane、Observability、ActionReport）
2. 替换一项关键依赖（例：playwright → patchright）
3. 修订一条被代码广泛引用的**不变式**（例：ToolUseContract 的 A/B/C 三环）
4. 发生一次**根因级**缺陷复盘，结论需要长期约束后续所有模块（例：auto-reply 决策契约）
5. 某个讨论涉及"以后第二个、第三个模块也会遇到"的问题

## 8. 何时**不**写 ADR

- 单 PR 可以说清楚的 bugfix / 重构
- 纯实现细节（某函数用哪个算法、某变量命名）
- 某次一次性调试的过程记录 → 放 `handoff/YYYY-MM-DD-<slug>.md`
- 产品调研 / 学习笔记 / 技术博客草稿 → 放 `engineering/` 或外部博客
- 只影响某一模块内部、不对外暴露契约的设计 → 放 `modules/<mod>/architecture.md`

## 9. 状态生命周期

```text
               amend(同一份修订)
                 ╲
Proposed ──────► Accepted ──────► Deprecated
                     │                │
                     └─► Superseded ◄─┘
                         by ADR-MMM
```

| 状态 | 含义 | 谁能改 |
|---|---|---|
| **Proposed** | 写就、待评审 | 作者 |
| **Accepted** | 评审通过，代码必须兑现 | 只能 amend（小修）或 supersede（写 ADR-MMM 取代） |
| **Deprecated** | 不再适用但未有取代方案 | 不改原文，只改 Status 行 |
| **Superseded by ADR-MMM** | 被 ADR-MMM 取代 | 不改原文，只改 Status 行 |

**关键规则**：Accepted 后禁止 silent rewrite。任何改动要么在文首加一行 `## Amended YYYY-MM-DD: <摘要>`，要么另开新 ADR 取代。这是 ADR 可追溯性的底线。

## 10. 命名 / 目录 / 索引

- 文件名：`docs/adr/ADR-NNN-<PascalCaseTitle>.md`，`NNN` 三位零填充（`001` 而不是 `1`）
- 编号：全局递增，**不重用**（即使 ADR-004 被 superseded，编号也不回收）
- 标题：动词开头、点明主题，避免"重构 X"这种空话
- 索引：新增 ADR 后同步更新 `docs/README.md` §D 表格
- 关联：ADR 内的 `前置 ADR` / `关联` 字段用相对路径，如 `ADR-001-ToolUseContract.md`

## 11. ADR vs RFC vs 设计文档 vs 实施计划

| 文档类型 | 回答 | Pulse 位置 |
|---|---|---|
| **ADR** | 为什么这么决 + 约束是什么 | `docs/adr/` |
| **RFC** | 详细设计 + 开放给讨论 | Pulse 当前把 RFC 内容合并进 Pulse-B ADR（§4 接口契约 / §7 落地顺序） |
| **设计文档** | 系统/子系统当前长什么样（只读快照） | `docs/Pulse-内核架构总览.md` 等根级长期文档 |
| **实施计划** | 什么时候、谁、怎么做 | `docs/Pulse实施计划.md` + tickets / PR |

一句话区分：**ADR 冻结约束，设计文档描述结果，实施计划分派动作**。

## 12. 反模式（ADR 里不该出现什么）

| 反模式 | 为什么不行 | 正确去处 |
|---|---|---|
| "参考 LangChain 也是这么做的" | ADR 不是产品对比文章 | `engineering/agent-concepts.md` §五方案对比 |
| "这是 2024 年 MCP 最新趋势" | ADR 不是趋势综述 | 博客 / `engineering/` 笔记 |
| "第一性原理告诉我们……" 长篇论述 | §3 第一性原理表是**结论**, 不是推导过程 | 推导过程放 chat 记录或 issue |
| "M0-M3 我们做了什么" | ADR 不是进度汇报 | `Pulse实施计划.md` |
| 多条未解决的 TODO | ADR 是**已决定**的决策 | 未决定就保持 Proposed 状态别 merge |
| 和其他 ADR 重复的定义 | ADR 是单一真源 | 用 `关联` 字段引用，不复制 |

## 13. 写 ADR 的最小动作清单

1. 新建 `docs/adr/ADR-NNN-<Title>.md`，套 Pulse-A 或 Pulse-B 模板
2. 填 `状态: Proposed`、日期、作用域、关联
3. 按模板填内容，保持每段可验证（"这一句 6 个月后别人还能核对吗？"）
4. 本地 `markdownlint` / `zh-punct` 检查
5. 更新 `docs/README.md` §D 索引表
6. PR 标题 `docs(adr): ADR-NNN <title>`，reviewer 至少 1 人
7. Accepted 后**不再修改正文**；需要更新走 amend / supersede

## 14. 参考阅读

- Michael Nygard, [*Documenting Architecture Decisions*](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions.html) — 原版，必读
- MADR, [*Markdown Architectural Decision Records*](https://adr.github.io/madr/) — 业界主流模板
- GitHub `adr/` 组织, <https://adr.github.io/> — 综合索引
- Zdun et al., [*Sustainable Architectural Decisions*](https://www.infoq.com/articles/sustainable-architectural-design-decisions) — Y-Statement 出处
- AWS Prescriptive Guidance, [*Using architectural decision records*](https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/welcome.html)
- Michael Keeling, [*Love Unrequited: Architecture, Agile, and ADRs*](https://ieeexplore.ieee.org/document/9801811), IEEE Software 39(4)

---

**底线**：ADR 的唯一评判标准 —— **两年后的新同事翻到这份 ADR，能不能在 5 分钟内理解"为什么当初这么决"，并据此判断眼前的代码是否还合规**。做不到就不叫 ADR。
