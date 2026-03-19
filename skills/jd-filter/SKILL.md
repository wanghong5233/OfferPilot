---
name: jd-filter
description: JD matching strategy — configurable direction gate, accept/reject rules, and LLM decision prompt for the greeting pipeline.
version: "1.1"
metadata: {"openclaw": {"always": true}}
---

# JD Filter Skill

用户求职意向过滤策略。Agent 在主动打招呼流程中加载此配置，
驱动 **规则硬过滤** 和 **LLM 二元判断** 两层漏斗。

> 修改本文件即可调整 Agent 的岗位筛选行为，无需改代码、无需重启后端。

---

## Intent（求职意图）

我正在寻找 **大模型应用 / AI Agent 方向** 的垂直实习机会，
核心诉求是为秋招积累大厂看重的 Agent 系统工程化落地经验。

---

## Direction Keywords（方向关键词）

规则硬过滤层的工作方式（代码中的实际判断链）：

```
标题含 Title Block 词 且 标题不含 Title Require App 词 → 直接拦截
命中 Reject 词 且 无 Strong Accept 信号              → 拦截
标题含 Title Block 词 且 无 agent/智能体              → 拦截
命中 Strong Accept 信号                               → 放行（可压制 Reject）
命中 agent/智能体 且 无 Reject 信号                   → 放行
命中 Accept 词 且 无 Reject 信号                      → 放行
以上均不满足                                          → 拦截（宁缺毋滥）
```

### Strong Accept（强接受信号 — 可压制 Reject，代表明确的应用/落地方向）

> 这些关键词表示岗位是"应用层"工作，即使同时出现 Reject 词（如"训练"）也放行。
> 例如："大模型应用+训练优化" → 有"应用"强信号，放行。

- 应用
- 落地
- 工作流
- rag
- langgraph
- langchain
- mcp
- copilot
- tool call
- function call
- 产品

### Accept（一般接受信号 — 仅在无 Reject 信号时放行）

> 这些关键词暗示岗位可能相关，但无法压制 Reject。
> 例如："prompt + 预训练" → 有 prompt(Accept) 但也有预训练(Reject)，拦截。

- agent
- 智能体
- 大模型应用
- llm应用
- 应用开发
- 应用工程
- workflow
- prompt
- 对话系统
- 业务落地
- 应用落地

### Reject（拒绝信号 — 命中且无 Strong Accept 信号时拦截）

- 预训练
- pre-train
- post-train
- 底座
- 模型训练
- 训练优化
- 蒸馏
- rlhf
- sft
- dpo
- 算法研究
- 推荐算法
- 搜索算法
- 视觉算法
- 多模态训练
- 基座研发

### Title Block（标题含以下词且无明确应用导向时直接拦截）

- 算法

### Title Require App（标题中需包含以下词才能解除 Title Block）

- 应用
- 开发
- 工程化
- 落地

---

## LLM Decision Rules（LLM 判断规则）

以下规则会注入 LLM prompt，指导其做 should_greet 二元判断。
这是第二层漏斗，输入是**完整 JD 文本**（工作职责+任职资格），不是标题。

### Reject Rules（必须拒绝的情况）

- 岗位核心工作是模型预训练/后训练/RLHF/SFT/蒸馏，而非应用开发
- 岗位核心工作是传统算法（推荐/搜索/CV/NLP基础研究），而非LLM应用
- 岗位核心工作是测试/QA/运维，而非开发
- 岗位要求博士学历（候选人硕士）
- 岗位日薪明确低于200元/天

### Accept Rules（应该接受的情况）

- 岗位涉及 Agent/RAG/LLM应用/工作流/Prompt工程/对话系统/AI应用落地
- 岗位涉及大模型应用层开发，即使标题含"算法"但JD实际是应用开发

### Principle（判断原则）

- 宁缺毋滥：不确定时拒绝
- 地点不作为拒绝理由
- 重点看JD中的工作职责和岗位描述的具体工作内容，不要被标题迷惑
- 候选人的求职核心目标是最高优先级

---

## Parameters（运行参数）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| direction_mode | strict | 方向门控模式：strict / auto / off |
| batch_size | 3 | 每轮打招呼数量 |
| daily_limit | 50 | 每日打招呼上限 |
| search_multiplier | 5 | 搜索量 = batch_size × 此值 |
| min_daily_salary | 200 | 日薪下限（元/天），低于此值直接拒绝 |
