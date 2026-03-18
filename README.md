<h1 align="center">🚀 OfferPilot</h1>

<p align="center">
  <strong>AI Agent 驱动的智能求职系统 — 让 Agent 处理重复劳动，你只管准备面试</strong>
</p>

<p align="center">
  <a href="#-why-offerpilot">Why</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#-核心特性">特性</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#%EF%B8%8F-系统架构">架构</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#-快速开始">快速开始</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#-技术栈">技术栈</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#-skills-生态">Skills</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#-roadmap">Roadmap</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12+-blue?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/LangGraph-Stateful_Workflow-purple?logo=langchain" alt="LangGraph" />
  <img src="https://img.shields.io/badge/OpenClaw-Agent_Runtime-green" alt="OpenClaw" />
  <img src="https://img.shields.io/badge/Patchright-Anti_Detection-orange" alt="Patchright" />
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License" />
</p>

<p align="center">
  <strong>中文</strong> | <a href="./README_EN.md">English</a>
</p>

---

## 💡 Why OfferPilot

在 BOSS 直聘上求职，每天需要：

- 浏览上百条 JD，逐个判断是否匹配自己的方向
- 给几十个岗位发打招呼消息，等待回复
- 回答"期望薪资多少""什么时候到岗"等高度重复的 HR 提问
- 追踪面试邀请邮件、安排日程、整理投递状态

**这些机械劳动完全可以交给 Agent。** OfferPilot 构建了 `搜索 → 匹配 → 打招呼 → 对话 → 审批 → 追踪` 的完整闭环，让求职者把精力集中在真正重要的事情上——准备面试和打磨技术。

> **核心原则：** Agent 负责重复劳动，人类保留关键决策。所有自动化操作受多层安全门控约束，可控、可审计、可回滚。

---

## ✨ 核心特性

### 🎯 JD 智能匹配 — 两层漏斗架构

传统方案依赖 LLM 评分（0-100）+ 阈值，但 LLM 数值校准天然不可靠——同一 JD 跑两次可能得到 72 和 81，阈值设多少都不对。OfferPilot 采用 **规则硬过滤 + LLM 二元判断** 的漏斗架构，消除阈值困境：

```
搜索结果 (~15 条)
    │
    ├── [Layer 1] 规则硬过滤 (成本=0, 延迟=0)
    │     ├─ 薪资/岗位类型过滤
    │     ├─ 三层方向信号 (Strong Accept / Accept / Reject)
    │     └─ 关键词从 SKILL.md 热加载，无需改代码
    │
    └── [Layer 2] 详情页完整 JD + LLM 二元判断
          ├─ 导航到详情页，提取完整 JD (工作职责 + 任职资格)
          ├─ LLM 只做 should_greet: true/false，不评分
          ├─ 通过 → 同页点击「立即沟通」
          └─ 拒绝 → 跳过
```

**为什么不用评分？** LLM 评分天然校准差——同一个 JD 跑两次可能得到 72 和 81，而阈值设多少都不对。二元判断消除了阈值困境，让 LLM 做它擅长的事（分类推理），而非它不擅长的事（数值预测）。

### 🤖 主动打招呼 + 对话 Copilot

| 能力 | 说明 |
|------|------|
| **主动打招呼** | 搜索 → 规则过滤 → 详情页 JD 提取 → LLM 判断 → 自动发起沟通 |
| **对话自动回复** | 拉取未读消息 → 意图分类 → 多分支决策（发简历 / 画像回复 / 通知介入 / 忽略） |
| **HR 主动联系门禁** | HR 主动找你时，自动构造伪 JD → 匹配评分 → 不达标则忽略 |
| **预览/自动 双模式** | 渐进式信任：先预览确认 → 建立信心后开启自动模式 |

### 🛡️ ProductionGuard — 7×24 自治守护

```
ProductionGuard
├── 内置调度器 — 替代外部 cron，自包含驱动 greet / chat 任务
├── 时段感知   — 工作日高峰自动加密、夜间休眠、早晨唤醒
├── 资源治理   — 定期清理多余标签页、孤儿 Chrome 进程
└── 健康守护   — 周期性探测浏览器存活，异常自动重建
```

一个 `PRODUCTION_GUARD_ENABLED=true` 就能启动无人值守运行，自动感知时段、调度任务、治理资源。

### 🔒 四层安全守卫

| 层级 | 机制 |
|------|------|
| **L1 Structured Output** | Pydantic Schema 约束 LLM 输出 + confidence 阈值拦截 |
| **L2 确定性硬规则** | 话题白名单 · 同一 HR 回复上限 · 升级话题自动通知 |
| **L3 执行层守卫** | 预览模式（生成不发送）· 双开关激活 · 审批令牌 + 工具预算 |
| **L4 审计与追溯** | 全量 Action 日志（输入 + 决策 + 输出 + 截图）· Timeline 回放 |

### 📧 邮件智能秘书

- IMAP 严格只读接入，自动分类面试邀请 / 拒信 / 补材料
- 结构化日程提取（时间 / 地点 / 面试形式）+ 飞书定时提醒
- 投递状态自动同步看板

### 📋 材料生成与 HITL 审批

- LangGraph `interrupt_before` 实现 Human-in-the-Loop 中断
- PostgreSQL checkpoint 持久化，服务重启后审批流可恢复
- `approve / reject / regenerate` 三种审批决策
- 审批通过后导出定制简历 PDF / TXT

### 🔔 飞书通知与告警

- Cookie 过期自动检测 → 飞书紧急告警（附截图）
- 每日任务摘要：扫描数 / 聊天处理数 / 自动回复数 / 异常记录
- 分级告警：info（蓝色）/ warning（橙色）/ critical（红色）

---

## 🏗️ 系统架构

```
┌──────────────────────────────────────────────────────────────────┐
│  用户入口                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │  飞书 / 微信  │  │  Web 看板    │  │  ProductionGuard      │  │
│  │  (Channel)   │  │  (Next.js)   │  │  (7×24 自治调度)       │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬────────────┘  │
│         │                 │                      │               │
│         ▼                 ▼                      ▼               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │             OpenClaw Agent Runtime (WSL)                  │   │
│  │  Brain 意图路由 → Skill 桥接 → HTTP 调用后端              │   │
│  │  Skills: job-monitor / boss-chat-copilot / jd-filter ...  │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                          │ HTTP                                  │
│                          ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │             FastAPI + LangGraph (业务引擎)                │   │
│  │                                                           │   │
│  │  ┌───────────┐ ┌───────────┐ ┌───────────┐              │   │
│  │  │ JD 匹配图  │ │ 对话决策图 │ │ 邮件分类图 │              │   │
│  │  │ (Rule →   │ │ (Pull →   │ │ (Fetch →  │              │   │
│  │  │  Detail → │ │  Intent → │ │  Classify │              │   │
│  │  │  LLM)     │ │  Gate →   │ │  → Sync)  │              │   │
│  │  └───────────┘ │  Reply)   │ └───────────┘              │   │
│  │                └───────────┘                              │   │
│  │  ┌───────────────────────────────────────────────┐       │   │
│  │  │ Patchright 浏览器自动化层                       │       │   │
│  │  │ CDP 指纹消除 · Cookie 持久化 · 限速 · 截图审计  │       │   │
│  │  │ MutationObserver 反检测 · Stealth 注入         │       │   │
│  │  └───────────────────────────────────────────────┘       │   │
│  │  ┌───────────────────────────────────────────────┐       │   │
│  │  │ 可观测性层                                      │       │   │
│  │  │ EventBus → SSE → 前端监控面板                   │       │   │
│  │  │ LangSmith 追踪 · actions 审计表 · 截图回放      │       │   │
│  │  └───────────────────────────────────────────────┘       │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                          │                                       │
│              ┌───────────┴───────────┐                           │
│              ▼                       ▼                           │
│  ┌──────────────────┐   ┌──────────────────┐                    │
│  │ PostgreSQL       │   │ ChromaDB         │                    │
│  │ (WSL 原生)       │   │ (嵌入式)          │                    │
│  │ · jobs           │   │ · jd_history     │                    │
│  │ · applications   │   │   (辅助相似查询)  │                    │
│  │ · actions (+截图) │   └──────────────────┘                    │
│  │ · user_profiles  │                                            │
│  │ · greet_records  │                                            │
│  └──────────────────┘                                            │
└──────────────────────────────────────────────────────────────────┘
```

### OpenClaw 与 LangGraph 的分工

| 层级 | 组件 | 职责 |
|------|------|------|
| 调度 + 路由 | OpenClaw + ProductionGuard | 消息入口、意图路由、Skill 桥接、7×24 自治调度 |
| 业务编排 | LangGraph | 多步工作流、条件分支、状态持久化、HITL 中断 |
| 策略配置 | Skills (`SKILL.md`) | JD 匹配规则、方向门控关键词、LLM prompt 注入 |
| 工具执行 | Patchright + MCP Server | 浏览器操作、邮件读取、搜索引擎、数据持久化 |

> Skill 做薄桥接 + 策略声明，复杂业务逻辑在 LangGraph 状态图中执行。即使不通过 OpenClaw，LangGraph 工作流也可独立运行和测试。

---

## 📂 项目结构

```
OfferPilot/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI 入口 + API 路由 + SSE + Guard 生命周期
│   │   ├── boss_scan.py            # Patchright BOSS 自动化 (搜索/打招呼/对话拉取)
│   │   ├── boss_chat_service.py    # BOSS 对话 Copilot 业务逻辑
│   │   ├── boss_chat_workflow.py   # BOSS 对话 LangGraph 工作流
│   │   ├── workflow.py             # JD 分析 + LLM 二元判断 (run_greet_decision)
│   │   ├── production_guard.py     # ProductionGuard 7×24 自治守护
│   │   ├── skill_loader.py         # SKILL.md 热加载解析器
│   │   ├── agent_events.py         # 线程安全 EventBus (SSE 实时推送)
│   │   ├── schemas.py              # Pydantic 数据模型 (GreetDecision 等)
│   │   ├── storage.py              # PostgreSQL 持久化
│   │   ├── email_workflow.py       # 邮件分类 + 日程提取
│   │   ├── material_workflow.py    # 材料生成 + HITL 审批
│   │   ├── tz.py                   # 统一北京时间 (解决 WSL 时区问题)
│   │   └── ...                     # 邮件/通知/情报/面试/表单等服务模块
│   ├── tests/                      # 集成测试 (管道/门控/JD提取/对话等)
│   ├── sql/init_db.sql             # 数据库 DDL
│   └── smoke_check.py              # API 烟雾测试
├── frontend/
│   ├── src/app/page.tsx            # Next.js 看板 + HITL 审批 + Agent 监控面板
│   └── src/components/             # ProfileForm / ResumeUpload 等组件
├── skills/                         # OpenClaw Skills (8 个)
│   ├── jd-filter/SKILL.md          # ★ JD 匹配策略配置 (方向关键词/LLM规则/参数)
│   ├── job-monitor/
│   ├── boss-chat-copilot/
│   ├── resume-tailor/
│   ├── application-tracker/
│   ├── email-reader/
│   ├── company-intel/
│   └── interview-prep/
├── scripts/                        # 运维脚本
│   ├── setup.sh                    # 一键初始化
│   ├── start.sh                    # 启动全部服务
│   ├── start_backend.sh            # 启动后端 (含 Guard)
│   ├── start_frontend.sh           # 启动前端
│   ├── boss-login.sh               # BOSS 直聘首次登录
│   └── ...                         # Heartbeat/日志/ClawHub 同步等
├── docs/                           # 设计文档
│   ├── JD匹配偏差分析与方案设计.md
│   ├── boss-chat-automation-v2.md
│   └── browser-agent-architecture-decision.md
├── infra/docker-compose.yml        # Docker 编排 (可选)
├── Makefile
└── .env.example
```

---

## 🛠️ 技术栈

| 层级 | 选型 | 说明 |
|------|------|------|
| Agent Runtime | **OpenClaw** | Skill 调度、Heartbeat 自治、Channel 多端接入 |
| 业务编排 | **LangGraph** | 状态机工作流、条件分支、checkpoint 持久化、interrupt 审批 |
| LLM | **Qwen3-Max / Qwen-Plus** | Structured Output、主备降级（Failover） |
| 工具协议 | **MCP** | 标准化工具接口，换模型零修改 |
| 浏览器自动化 | **Patchright** | Playwright 分支，CDP 指纹消除，MutationObserver 反检测 |
| 后端 | **FastAPI** | 异步 API + SSE 流式事件 |
| 前端 | **Next.js + Tailwind CSS** | 看板 + HITL 审批 + Agent 监控面板 |
| 数据库 | **PostgreSQL** | 业务数据 + LangGraph checkpoint + 打招呼去重 |
| 向量检索 | **ChromaDB** | JD 历史相似度辅助查询（嵌入式，零额外服务） |
| 可观测性 | **EventBus + SSE + LangSmith** | 实时事件流 + 审计日志 + 截图回放 + LLM 追踪 |
| 部署 | **全 WSL 原生** | 开发调试零容器，浏览器可直接可视化 |

---

## 🧩 Skills 生态

OfferPilot 开发了 **8 个 OpenClaw Skills**：

| Skill | 功能 | 触发方式 |
|-------|------|---------|
| `jd-filter` | **JD 匹配策略配置** — 方向关键词/LLM 规则/运行参数 | 后端 `skill_loader.py` 热加载 |
| `job-monitor` | JD 分析 + BOSS 岗位扫描 | "分析一下这个 JD" / Heartbeat |
| `boss-chat-copilot` | BOSS 消息巡检 + 智能回复 | "处理未读消息" / Heartbeat |
| `resume-tailor` | 简历定制 + 审批 + 导出 | "帮我针对这个岗位改简历" |
| `application-tracker` | 表单识别 + HITL 填充审批 | "帮我预览这个网申表单" |
| `email-reader` | 邮件分类 + 日程提取 + 状态同步 | Heartbeat 自动触发 |
| `company-intel` | 公司情报自动调研 | "调研字节跳动 AI 团队" |
| `interview-prep` | 面试题库 + 答法建议 | "生成面试题" |

### jd-filter：策略即配置

`jd-filter` 是 OfferPilot 的核心创新点之一——**匹配策略声明在 Markdown 文件中，而非硬编码在 Python 里**：

```markdown
# skills/jd-filter/SKILL.md

## Direction Keywords
### Strong Accept — 可压制 Reject 的强信号
- 应用、落地、rag、langgraph、mcp ...

### Accept — 仅在无 Reject 时放行
- agent、智能体、prompt、对话系统 ...

### Reject — 命中则拦截
- 预训练、rlhf、蒸馏、推荐算法 ...

## LLM Decision Rules
### Reject Rules
- 岗位核心是模型训练，而非应用开发
- 岗位核心是测试/QA，而非开发
```

后端通过 `skill_loader.py` 在运行时热加载该文件，自动编译为正则和 LLM prompt 片段。修改 Markdown 即可调整匹配行为，**无需改代码、无需重启后端**。

---

## 🚀 快速开始

**前置条件：** WSL2 + Ubuntu · Python 3.12+ · Node.js 22+

### 1. 配置环境变量

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env.local
# 编辑 .env，填入 DASHSCOPE_API_KEY 等
```

### 2. 一键初始化

```bash
./scripts/setup.sh    # PostgreSQL + Python 依赖 + Playwright + 前端依赖
```

### 3. 首次登录 BOSS 直聘

```bash
./scripts/boss-login.sh   # 浏览器打开 → 手机扫码 → Cookie 自动保存
```

### 4. 日常启动

```bash
# 终端 1: 后端 + 前端 + PG
./scripts/start.sh

# 终端 2: OpenClaw Agent Runtime
source /root/.nvm/nvm.sh && nvm use 22
openclaw-gateway
```

后端 API 文档：http://127.0.0.1:8010/docs | 前端看板：http://127.0.0.1:3000

### 5. 开启 ProductionGuard (可选)

在 `.env` 中设置 `PRODUCTION_GUARD_ENABLED=true`，后端启动时自动进入 7×24 自治模式：

```bash
curl http://localhost:8010/api/guard/status   # 查看守护状态
curl http://localhost:8010/health             # 健康检查
```

### Makefile 快捷命令

| 命令 | 说明 |
|------|------|
| `make setup` | 一键初始化 |
| `make boss-login` | 首次登录 BOSS |
| `make start` | 启动全部服务 |
| `make ps` | 查看服务状态 |
| `make health` | 健康检查 |

---

## 📡 API 概览

| 模块 | 端点 | 说明 |
|------|------|------|
| 基础 | `GET /health` | 健康检查（含 Guard 状态 + 浏览器状态） |
| Guard | `GET /api/guard/status` `POST /api/guard/start` `POST /api/guard/stop` | ProductionGuard 控制 |
| JD 分析 | `POST /api/jd/analyze` | LangGraph 结构化分析 + 匹配 |
| 简历 | `POST /api/resume/upload` | 上传 + 文本提取 + 持久化 |
| 材料审批 | `POST /api/material/generate` `POST /api/material/review` | HITL 审批 + 导出 |
| BOSS 扫描 | `POST /api/boss/scan` | 岗位搜索 + 打招呼 |
| BOSS 对话 | `POST /api/boss/chat/process` | 消息处理 + 决策 + 自动回复 |
| BOSS 巡检 | `POST /api/boss/chat/heartbeat/trigger` | 定时触发 + 摘要通知 |
| 画像 | `GET/PUT /api/profile` | 求职画像配置 |
| 邮件 | `POST /api/email/ingest` `POST /api/email/fetch` | 分类 + 状态同步 |
| 日程 | `GET /api/schedules/upcoming` | 面试日程 |
| 情报 | `POST /api/company/intel` `POST /api/interview/prep` | 公司调研 + 题库 |
| Agent | `GET /api/agent/events` (SSE) | 实时事件流 + 历史查询 |
| 通知 | `POST /api/notify/daily-summary` | 飞书每日摘要 |

---

## 🔍 Agent 可观测性

```
Backend 各模块 ─emit()─→ EventBus ─SSE─→ 前端监控面板
                                             │
  事件类型：                                  ├─ 浏览器操作 (启动/导航/点击/截图)
  browser_launch / browser_navigate           ├─ LLM 调用 (prompt → response)
  llm_call / intent_classified                ├─ 意图分类 + 安全拦截
  greet_decision / safety_blocked             ├─ 打招呼决策 + 回复生成
  workflow_start / workflow_end               └─ 工作流生命周期
```

- **实时流**：`EventBus → SSE → 前端暗色终端风格监控面板`，支持事件过滤 + 自动滚动
- **持久化审计**：`actions` 表记录全量操作（输入 + 决策 + 输出 + 截图路径）
- **LLM 追踪**：配置 `LANGCHAIN_TRACING_V2=true` 即可启用 LangSmith 节点级追踪

---

## 📐 设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| JD 匹配方式 | **规则 + LLM 二元判断** 而非 LLM 评分 | 评分阈值永远调不好（详见[设计文档](./docs/JD匹配偏差分析与方案设计.md)） |
| 浏览器引擎 | **Patchright** 而非 Playwright | CDP 指纹消除，BOSS 反爬检测通过率更高 |
| 调度方式 | **ProductionGuard** 内置 而非外部 cron | 时段感知 + 资源治理 + 健康守护一体化 |
| 策略配置 | **SKILL.md** 而非 JSON/YAML | 声明式、可读性强、与 OpenClaw Skills 生态对齐 |
| 时区处理 | **统一 `now_beijing()`** | WSL 默认 UTC 会导致调度/日志/通知全部错乱 |

---

## 🗺️ Roadmap

- [x] LangGraph 多节点工作流（JD 分析 / 对话决策 / 邮件分类）
- [x] Patchright 浏览器自动化（反检测 + Cookie 持久化）
- [x] BOSS 对话 Copilot（意图识别 + 画像自动回复）
- [x] Agent 可观测性（EventBus + SSE + 审计日志 + LangSmith）
- [x] BOSS 主动打招呼 + 配额管理
- [x] JD 两层漏斗匹配架构（规则门控 + LLM 二元判断）
- [x] 详情页完整 JD 提取 + 多段拼接 + JS fallback
- [x] OpenClaw Skills 策略配置化（`jd-filter` 热加载）
- [x] ProductionGuard 7×24 自治守护
- [x] 飞书分级告警 + Cookie 过期检测
- [ ] 多平台支持（拉勾、猎聘）
- [ ] 前端移动端适配
- [ ] Ollama 本地模型离线模式
- [ ] Agent 评测框架（自动化 A/B 测试）

---

## 📄 许可证

[MIT License](./LICENSE)

---

<p align="center">
  <strong>如果这个项目对你有帮助，欢迎 ⭐ Star 支持！</strong>
  <br/>
  <sub>Built with LangGraph, OpenClaw, and a lot of job-hunting frustration.</sub>
</p>
