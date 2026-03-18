<h1 align="center">🚀 OfferPilot</h1>

<p align="center">
  <strong>AI Agent-Powered Intelligent Job Hunting System — Let the Agent Handle the Grind, You Focus on Interviews</strong>
</p>

<p align="center">
  <a href="#-why-offerpilot">Why</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#-key-features">Features</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#%EF%B8%8F-architecture">Architecture</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#-getting-started">Get Started</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#-tech-stack">Tech Stack</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#-skills-ecosystem">Skills</a>&nbsp;&nbsp;·&nbsp;&nbsp;
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
  <a href="./README.md">中文</a> | <strong>English</strong>
</p>

---

## 💡 Why OfferPilot

Job hunting on BOSS Zhipin (China's largest recruitment platform) involves a daily grind of:

- Browsing hundreds of job descriptions, manually deciding which ones match your direction
- Sending dozens of greeting messages and waiting for replies
- Answering highly repetitive HR questions like "What's your expected salary?" or "When can you start?"
- Tracking interview invitation emails, scheduling, and maintaining application status

**All of this mechanical labor can be delegated to an Agent.** OfferPilot builds a complete closed-loop pipeline: `Search → Match → Greet → Chat → Approve → Track`, freeing job seekers to focus on what truly matters — preparing for interviews and sharpening skills.

> **Core Principle:** The Agent handles repetitive labor; humans retain critical decisions. All automated actions are governed by multi-layer safety gates — controllable, auditable, and reversible.

---

## ✨ Key Features

### 🎯 Intelligent JD Matching — Two-Layer Funnel Architecture

Conventional approaches rely on LLM scoring (0–100) with a threshold, but LLM numerical calibration is inherently unreliable — the same JD may score 72 on one run and 81 on the next, and no threshold is ever right. OfferPilot adopts a **rule-based hard filtering + LLM binary decision** funnel architecture that eliminates the threshold dilemma:

```
Search Results (~15 items)
    │
    ├── [Layer 1] Rule-Based Hard Filtering (cost=0, latency=0)
    │     ├─ Salary / job type filter
    │     ├─ Three-tier direction signals (Strong Accept / Accept / Reject)
    │     └─ Keywords hot-loaded from SKILL.md — no code changes needed
    │
    └── [Layer 2] Full JD from Detail Page + LLM Binary Decision
          ├─ Navigate to detail page, extract full JD (responsibilities + requirements)
          ├─ LLM outputs should_greet: true/false only — no scoring
          ├─ Pass → click "Start Chat" on the same page
          └─ Reject → skip
```

**Why not scoring?** LLM scoring has inherent calibration drift — the same JD may yield 72 or 81 across runs, and any threshold will either miss good jobs or let bad ones through. Binary decisions eliminate this dilemma, letting the LLM do what it excels at (classification & reasoning) rather than what it struggles with (numerical prediction). See the [design doc](./docs/JD匹配偏差分析与方案设计.md) for the full analysis.

### 🤖 Proactive Greeting + Chat Copilot

| Capability | Description |
|------------|-------------|
| **Proactive Greeting** | Search → rule filter → detail page JD extraction → LLM decision → auto-initiate chat |
| **Auto Reply** | Pull unread messages → intent classification → branching (send resume / profile reply / escalate / ignore) |
| **HR Inbound Gating** | When HR reaches out first, auto-construct pseudo-JD → match scoring → ignore if below threshold |
| **Preview / Auto Dual Mode** | Progressive trust: preview first → enable auto mode once confident |

### 🛡️ ProductionGuard — 24/7 Autonomous Operation

```
ProductionGuard
├── Built-in Scheduler — replaces external cron, self-contained greet / chat task dispatch
├── Time-Aware       — auto-intensify during peak hours, sleep at night, wake in the morning
├── Resource Governance — periodic cleanup of excess tabs and orphan Chrome processes
└── Health Guardian   — periodic browser liveness probes, auto-rebuild on failure
```

A single `PRODUCTION_GUARD_ENABLED=true` enables unattended operation with automatic time-awareness, task scheduling, and resource governance.

### 🔒 Four-Layer Safety Guards

| Layer | Mechanism |
|-------|-----------|
| **L1 Structured Output** | Pydantic Schema constrains LLM output + confidence threshold gating |
| **L2 Deterministic Hard Rules** | Topic whitelist · per-HR reply cap · auto-escalation notifications |
| **L3 Execution Guards** | Preview mode (generate but don't send) · dual-switch activation · approval tokens + tool budgets |
| **L4 Audit & Traceability** | Full action logs (input + decision + output + screenshots) · Timeline replay |

### 📧 Intelligent Email Secretary

- IMAP strict read-only access, auto-classifies interview invitations / rejections / document requests
- Structured schedule extraction (time / location / format) + Feishu scheduled reminders
- Application status auto-synced to dashboard

### 📋 Material Generation & HITL Approval

- LangGraph `interrupt_before` for Human-in-the-Loop interruption
- PostgreSQL checkpoint persistence — approval flows survive service restarts
- `approve / reject / regenerate` three-way approval decisions
- Export customized resume PDF / TXT after approval

### 🔔 Feishu Notifications & Alerts

- Cookie expiration auto-detection → Feishu emergency alert (with screenshot)
- Daily summary: scan count / chats processed / auto-replies / exceptions
- Tiered alerts: info (blue) / warning (orange) / critical (red)

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Entry Points                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ Feishu/WeChat │  │ Web Dashboard│  │  ProductionGuard      │  │
│  │ (Channel)     │  │ (Next.js)    │  │  (24/7 Autonomous)    │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬────────────┘  │
│         │                 │                      │               │
│         ▼                 ▼                      ▼               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │             OpenClaw Agent Runtime (WSL)                  │   │
│  │  Brain Intent Router → Skill Bridge → HTTP Backend Calls  │   │
│  │  Skills: job-monitor / boss-chat-copilot / jd-filter ...  │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                          │ HTTP                                  │
│                          ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │             FastAPI + LangGraph (Business Engine)         │   │
│  │                                                           │   │
│  │  ┌───────────┐ ┌───────────┐ ┌───────────┐              │   │
│  │  │ JD Match   │ │ Chat      │ │ Email     │              │   │
│  │  │ Graph      │ │ Decision  │ │ Classify  │              │   │
│  │  │ (Rule →   │ │ (Pull →   │ │ (Fetch →  │              │   │
│  │  │  Detail → │ │  Intent → │ │  Classify │              │   │
│  │  │  LLM)     │ │  Gate →   │ │  → Sync)  │              │   │
│  │  └───────────┘ │  Reply)   │ └───────────┘              │   │
│  │                └───────────┘                              │   │
│  │  ┌───────────────────────────────────────────────┐       │   │
│  │  │ Patchright Browser Automation Layer            │       │   │
│  │  │ CDP Fingerprint Elimination · Cookie Persist   │       │   │
│  │  │ Rate Limiting · Screenshot Audit               │       │   │
│  │  │ MutationObserver Anti-Detection · Stealth      │       │   │
│  │  └───────────────────────────────────────────────┘       │   │
│  │  ┌───────────────────────────────────────────────┐       │   │
│  │  │ Observability Layer                            │       │   │
│  │  │ EventBus → SSE → Frontend Monitor Panel        │       │   │
│  │  │ LangSmith Tracing · Actions Audit · Screenshots│       │   │
│  │  └───────────────────────────────────────────────┘       │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                          │                                       │
│              ┌───────────┴───────────┐                           │
│              ▼                       ▼                           │
│  ┌──────────────────┐   ┌──────────────────┐                    │
│  │ PostgreSQL       │   │ ChromaDB         │                    │
│  │ (WSL Native)     │   │ (Embedded)       │                    │
│  │ · jobs           │   │ · jd_history     │                    │
│  │ · applications   │   │   (similarity)   │                    │
│  │ · actions (+img) │   └──────────────────┘                    │
│  │ · user_profiles  │                                            │
│  │ · greet_records  │                                            │
│  └──────────────────┘                                            │
└──────────────────────────────────────────────────────────────────┘
```

### Division of Responsibilities: OpenClaw vs LangGraph

| Layer | Component | Responsibility |
|-------|-----------|----------------|
| Scheduling + Routing | OpenClaw + ProductionGuard | Message entry, intent routing, Skill bridging, 24/7 autonomous scheduling |
| Business Orchestration | LangGraph | Multi-step workflows, conditional branching, state persistence, HITL interrupts |
| Strategy Configuration | Skills (`SKILL.md`) | JD matching rules, direction gate keywords, LLM prompt injection |
| Tool Execution | Patchright + MCP Server | Browser automation, email access, search engines, data persistence |

> Skills serve as thin bridges + strategy declarations. Complex business logic lives in LangGraph state graphs. LangGraph workflows can run and be tested independently of OpenClaw.

---

## 📂 Project Structure

```
OfferPilot/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI entry + API routes + SSE + Guard lifecycle
│   │   ├── boss_scan.py            # Patchright BOSS automation (search/greet/chat pull)
│   │   ├── boss_chat_service.py    # BOSS Chat Copilot business logic
│   │   ├── boss_chat_workflow.py   # BOSS Chat LangGraph workflow
│   │   ├── workflow.py             # JD analysis + LLM binary decision (run_greet_decision)
│   │   ├── production_guard.py     # ProductionGuard 24/7 autonomous daemon
│   │   ├── skill_loader.py         # SKILL.md hot-reload parser
│   │   ├── agent_events.py         # Thread-safe EventBus (SSE push)
│   │   ├── schemas.py              # Pydantic models (GreetDecision, etc.)
│   │   ├── storage.py              # PostgreSQL persistence
│   │   ├── email_workflow.py       # Email classification + schedule extraction
│   │   ├── material_workflow.py    # Material generation + HITL approval
│   │   ├── tz.py                   # Unified Beijing time (solves WSL timezone issues)
│   │   └── ...                     # Email/notification/intel/interview/form modules
│   ├── tests/                      # Integration tests (pipeline/gate/JD extraction/chat)
│   ├── sql/init_db.sql             # Database DDL
│   └── smoke_check.py              # API smoke test
├── frontend/
│   ├── src/app/page.tsx            # Next.js dashboard + HITL approval + Agent monitor
│   └── src/components/             # ProfileForm / ResumeUpload components
├── skills/                         # OpenClaw Skills (8 total)
│   ├── jd-filter/SKILL.md          # ★ JD matching strategy config (keywords/LLM rules/params)
│   ├── job-monitor/
│   ├── boss-chat-copilot/
│   ├── resume-tailor/
│   ├── application-tracker/
│   ├── email-reader/
│   ├── company-intel/
│   └── interview-prep/
├── scripts/                        # Operations scripts
│   ├── setup.sh                    # One-click initialization
│   ├── start.sh                    # Start all services
│   ├── start_backend.sh            # Start backend (with Guard)
│   ├── start_frontend.sh           # Start frontend
│   ├── boss-login.sh               # BOSS Zhipin first-time login
│   └── ...                         # Heartbeat/logging/ClawHub sync
├── docs/                           # Design documents
│   ├── JD匹配偏差分析与方案设计.md
│   ├── boss-chat-automation-v2.md
│   └── browser-agent-architecture-decision.md
├── infra/docker-compose.yml        # Docker Compose (optional)
├── Makefile
└── .env.example
```

---

## 🛠️ Tech Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Agent Runtime | **OpenClaw** | Skill scheduling, Heartbeat autonomy, multi-channel access |
| Business Orchestration | **LangGraph** | State machine workflows, conditional branching, checkpoint persistence, interrupt approval |
| LLM | **Qwen3-Max / Qwen-Plus** | Structured Output, primary-backup failover |
| Tool Protocol | **MCP** | Standardized tool interface, zero modification when switching models |
| Browser Automation | **Patchright** | Playwright fork with CDP fingerprint elimination, MutationObserver anti-detection |
| Backend | **FastAPI** | Async API + SSE event streaming |
| Frontend | **Next.js + Tailwind CSS** | Dashboard + HITL approval + Agent monitoring panel |
| Database | **PostgreSQL** | Business data + LangGraph checkpoints + greeting deduplication |
| Vector Search | **ChromaDB** | JD history similarity queries (embedded mode, zero extra services) |
| Observability | **EventBus + SSE + LangSmith** | Real-time event stream + audit logs + screenshot replay + LLM tracing |
| Deployment | **Native WSL** | Zero-container dev/debug, browser visually accessible |

---

## 🧩 Skills Ecosystem

OfferPilot ships **8 OpenClaw Skills**:

| Skill | Function | Trigger |
|-------|----------|---------|
| `jd-filter` | **JD matching strategy config** — direction keywords / LLM rules / runtime params | Backend `skill_loader.py` hot-reload |
| `job-monitor` | JD analysis + BOSS job scanning | "Analyze this JD" / Heartbeat |
| `boss-chat-copilot` | BOSS message patrol + smart replies | "Process unread messages" / Heartbeat |
| `resume-tailor` | Resume customization + approval + export | "Tailor my resume for this job" |
| `application-tracker` | Form recognition + HITL autofill approval | "Preview this application form" |
| `email-reader` | Email classification + schedule extraction + status sync | Heartbeat auto-trigger |
| `company-intel` | Company intelligence research | "Research ByteDance AI team" |
| `interview-prep` | Interview question bank + answer strategies | "Generate interview questions" |

### jd-filter: Strategy as Configuration

`jd-filter` is one of OfferPilot's core innovations — **matching strategy is declared in a Markdown file, not hardcoded in Python**:

```markdown
# skills/jd-filter/SKILL.md

## Direction Keywords
### Strong Accept — signals that override Reject
- application, deployment, rag, langgraph, mcp ...

### Accept — pass only when no Reject signal present
- agent, LLM application, prompt, dialogue system ...

### Reject — block when matched
- pre-training, rlhf, distillation, recommendation algorithm ...

## LLM Decision Rules
### Reject Rules
- Core work is model training, not application development
- Core work is testing/QA, not development
```

The backend hot-loads this file at runtime via `skill_loader.py`, automatically compiling keywords into regex patterns and LLM prompt fragments. **Edit the Markdown to change matching behavior — no code changes, no server restart.**

---

## 🚀 Getting Started

**Prerequisites:** WSL2 + Ubuntu · Python 3.12+ · Node.js 22+

### 1. Configure Environment Variables

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env.local
# Edit .env, fill in DASHSCOPE_API_KEY, etc.
```

### 2. One-Click Setup

```bash
./scripts/setup.sh    # PostgreSQL + Python deps + Playwright + frontend deps
```

### 3. First-Time BOSS Zhipin Login

```bash
./scripts/boss-login.sh   # Browser opens → scan QR with phone → Cookie auto-saved
```

### 4. Daily Startup

```bash
# Terminal 1: Backend + Frontend + PG
./scripts/start.sh

# Terminal 2: OpenClaw Agent Runtime
source /root/.nvm/nvm.sh && nvm use 22
openclaw-gateway
```

Backend API docs: http://127.0.0.1:8010/docs | Frontend dashboard: http://127.0.0.1:3000

### 5. Enable ProductionGuard (Optional)

Set `PRODUCTION_GUARD_ENABLED=true` in `.env` to enter 24/7 autonomous mode on backend startup:

```bash
curl http://localhost:8010/api/guard/status   # Check guard status
curl http://localhost:8010/health             # Health check
```

### Makefile Shortcuts

| Command | Description |
|---------|-------------|
| `make setup` | One-click initialization |
| `make boss-login` | First-time BOSS login |
| `make start` | Start all services |
| `make ps` | Check service status |
| `make health` | Health check |

---

## 📡 API Overview

| Module | Endpoint | Description |
|--------|----------|-------------|
| Health | `GET /health` | Health check (Guard status + browser status) |
| Guard | `GET /api/guard/status` `POST /api/guard/start` `POST /api/guard/stop` | ProductionGuard control |
| JD Analysis | `POST /api/jd/analyze` | LangGraph structured analysis + matching |
| Resume | `POST /api/resume/upload` | Upload + text extraction + persistence |
| Material Approval | `POST /api/material/generate` `POST /api/material/review` | HITL approval + export |
| BOSS Scan | `POST /api/boss/scan` | Job search + greeting |
| BOSS Chat | `POST /api/boss/chat/process` | Message processing + decisions + auto-reply |
| BOSS Patrol | `POST /api/boss/chat/heartbeat/trigger` | Scheduled trigger + summary notification |
| Profile | `GET/PUT /api/profile` | Job seeker profile configuration |
| Email | `POST /api/email/ingest` `POST /api/email/fetch` | Classification + status sync |
| Schedule | `GET /api/schedules/upcoming` | Interview schedule |
| Intel | `POST /api/company/intel` `POST /api/interview/prep` | Company research + question bank |
| Agent | `GET /api/agent/events` (SSE) | Real-time event stream + history query |
| Notification | `POST /api/notify/daily-summary` | Feishu daily summary |

---

## 🔍 Agent Observability

```
Backend Modules ─emit()─→ EventBus ─SSE─→ Frontend Monitor Panel
                                              │
  Event Types:                                ├─ Browser ops (launch/navigate/click/screenshot)
  browser_launch / browser_navigate           ├─ LLM calls (prompt → response)
  llm_call / intent_classified                ├─ Intent classification + safety blocks
  greet_decision / safety_blocked             ├─ Greeting decisions + reply generation
  workflow_start / workflow_end               └─ Workflow lifecycle
```

- **Real-time Stream**: `EventBus → SSE → dark-themed terminal-style monitor panel` with event filtering + auto-scroll
- **Persistent Audit**: `actions` table records all operations (input + decision + output + screenshot path)
- **LLM Tracing**: Set `LANGCHAIN_TRACING_V2=true` to enable LangSmith node-level tracing

---

## 📐 Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| JD Matching | **Rules + LLM binary decision** over LLM scoring | Scoring thresholds can never be calibrated correctly ([design doc](./docs/JD匹配偏差分析与方案设计.md)) |
| Browser Engine | **Patchright** over Playwright | CDP fingerprint elimination, higher anti-detection pass rate on BOSS |
| Scheduling | **Built-in ProductionGuard** over external cron | Unified time-awareness + resource governance + health guardian |
| Strategy Config | **SKILL.md** over JSON/YAML | Declarative, human-readable, aligned with OpenClaw Skills ecosystem |
| Timezone Handling | **Unified `now_beijing()`** | WSL defaults to UTC, causing scheduling/logging/notification errors |

---

## 🗺️ Roadmap

- [x] LangGraph multi-node workflows (JD analysis / chat decisions / email classification)
- [x] Patchright browser automation (anti-detection + Cookie persistence)
- [x] BOSS Chat Copilot (intent recognition + profile-based auto-reply)
- [x] Agent observability (EventBus + SSE + audit logs + LangSmith)
- [x] BOSS proactive greeting + quota management
- [x] Two-layer funnel JD matching architecture (rule gates + LLM binary decision)
- [x] Full JD extraction from detail pages + multi-section concatenation + JS fallback
- [x] OpenClaw Skills-driven strategy configuration (`jd-filter` hot-reload)
- [x] ProductionGuard 24/7 autonomous operation
- [x] Feishu tiered alerts + Cookie expiration detection
- [ ] Multi-platform support (Lagou, Liepin)
- [ ] Mobile-responsive frontend
- [ ] Ollama local model offline mode
- [ ] Agent evaluation framework (automated A/B testing)

---

## 📄 License

[MIT License](./LICENSE)

---

<p align="center">
  <strong>If this project helps you, give it a ⭐ Star!</strong>
  <br/>
  <sub>Built with LangGraph, OpenClaw, and a lot of job-hunting frustration.</sub>
</p>
