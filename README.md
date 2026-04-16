<h1 align="center">Pulse</h1>

<p align="center">
  <strong>A self-evolving personal AI agent platform — modular, extensible, and built to grow with you.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/FastAPI-async-teal?logo=fastapi" alt="FastAPI" />
  <img src="https://img.shields.io/badge/MCP-Compatible-green" alt="MCP" />
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License" />
</p>

---

## What is Pulse

Pulse is a **general-purpose personal AI agent** — think JARVIS, but open-source and self-hostable. It starts as a capable assistant and *evolves* over time by learning your preferences, accumulating memories, and even generating new skills on demand.

Unlike single-purpose chatbots, Pulse is designed around a **capability-driven architecture** where every feature is a pluggable module:

| Layer | What it does |
|-------|-------------|
| **Brain** | ReAct reasoning loop — plans, calls tools, observes results, responds |
| **Modules** | Domain-specific pipelines (job hunting, intelligence gathering, etc.) |
| **Memory** | Core memory (personality/preferences), recall memory (conversations), archival memory (facts) |
| **Skills** | Dynamically generated tools from natural language — "monitor BTC price" becomes a live tool |
| **Evolution** | Reflection pipeline, governance, preference learning — Pulse gets better with every interaction |

---

## Architecture Overview

```
                    ┌──────────────────────────┐
                    │       User Channels       │
                    │   CLI · Feishu · Web API   │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │      Intent Router        │
                    │  exact → prefix → LLM     │
                    └────────────┬─────────────┘
                                 │
              ┌──────────────────▼──────────────────┐
              │              Brain (ReAct)           │
              │  think → act → observe → respond     │
              │                                      │
              │  ┌──────────────────────────────┐   │
              │  │       Tool Registry           │   │
              │  │  Ring 1: Built-in tools       │   │
              │  │  Ring 2: Module-as-tools      │   │
              │  │  Ring 3: MCP external tools   │   │
              │  └──────────────────────────────┘   │
              └──────────┬─────────────┬────────────┘
                         │             │
          ┌──────────────▼──┐   ┌──────▼──────────────┐
          │   Memory System  │   │  Evolution Engine   │
          │  Core · Recall   │   │  Reflect · Govern   │
          │  Archival        │   │  Learn · Evolve     │
          └─────────────────┘   └─────────────────────┘
```

---

## Key Features

**Brain & Tools**
- ReAct multi-step reasoning with cost control (daily budget)
- Three-ring tool model: built-in → module → MCP external
- MCP client/server for ecosystem integration

**Memory**
- Core Memory: persistent personality (SOUL), user profile, preferences
- Recall Memory: conversation history with semantic search
- Archival Memory: append-only factual store for long-horizon retrieval

**Evolution**
- Automatic preference extraction from conversations
- Soul governance with audit trail, rollback, and risk-based modes (autonomous / supervised / gated)
- DPO pair collection for future fine-tuning
- Governance rules versioning, diffing, and hot-reload

**Skill Generation**
- Natural language → Python tool (AST-validated, sandboxed)
- Hot-loading into the tool registry without restart

**Modules**
- Pluggable domain modules with automatic discovery
- Each module registers as a Brain-callable tool
- Built-in: `boss_greet`, `boss_chat`, `email_tracker`, `intel_interview`, `intel_techradar`, `intel_query`

**Policy & Safety**
- Policy engine: keyword blocking, intent-based confirmation, custom rules
- Configurable via JSON, no code changes needed

---

## Project Structure

```
Pulse/
├── src/pulse/
│   ├── core/
│   │   ├── brain.py            # ReAct reasoning loop
│   │   ├── tool.py             # ToolRegistry + @tool decorator
│   │   ├── cost.py             # LLM budget controller
│   │   ├── config.py           # Pydantic settings
│   │   ├── server.py           # FastAPI application
│   │   ├── module.py           # BaseModule + ModuleRegistry
│   │   ├── events.py           # In-process EventBus
│   │   ├── sandbox.py          # Code safety checker
│   │   ├── skill_generator.py  # NL → tool pipeline
│   │   ├── mcp_client.py       # MCP consumer
│   │   ├── mcp_server.py       # MCP provider
│   │   ├── channel/            # CLI, Feishu adapters
│   │   ├── router/             # Intent routing
│   │   ├── policy/             # Safety policy engine
│   │   ├── llm/                # LLM router + failover
│   │   ├── storage/            # DB engine + vector store
│   │   ├── notify/             # Webhook notifications
│   │   ├── scheduler/          # Background task runner
│   │   ├── browser/            # Browser pool + auth
│   │   ├── memory/             # Core, Recall, Archival memory
│   │   ├── learning/           # Preference extractor, DPO collector
│   │   └── soul/               # Governance, Evolution engine
│   ├── modules/                # Domain modules (auto-discovered)
│   └── tools/                  # Built-in tool definitions
├── config/                     # Runtime configuration (JSON/YAML)
├── tests/pulse/                # Unit & integration tests
├── generated/                  # Dynamically created skills
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

---

## Quick Start

**Prerequisites:** Python 3.11+

### 1. Install

```bash
pip install -e .
```

### 2. Run

```bash
pulse start
# or
uvicorn pulse.core.server:create_app --factory --host 0.0.0.0 --port 8010
```

### 3. Docker

```bash
docker compose up --build
```

API docs: http://localhost:8010/docs

---

## Configuration

All settings are managed via environment variables with `PULSE_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `PULSE_PORT` | `8010` | API server port |
| `PULSE_LLM_DEFAULT_MODEL` | — | Default LLM model name |
| `PULSE_BRAIN_DAILY_BUDGET_USD` | `5.0` | Daily LLM spend limit |
| `PULSE_CORE_MEMORY_PATH` | `~/.pulse/core_memory.json` | Core memory persistence |
| `PULSE_EVOLUTION_DEFAULT_MODE` | `autonomous` | Default governance mode |
| `PULSE_GENERATED_SKILLS_DIR` | `generated/skills` | Skill output directory |

See `src/pulse/core/config.py` for the full list.

---

## API Highlights

| Category | Endpoint | Description |
|----------|----------|-------------|
| Brain | `POST /api/brain/run` | Execute a reasoning query |
| Brain | `GET /api/brain/tools` | List registered tools |
| Brain | `GET /api/brain/cost/status` | LLM budget status |
| Memory | `GET /api/memory/core` | Read core memory |
| Memory | `POST /api/memory/search` | Semantic search recall memory |
| Memory | `GET /api/memory/archival/recent` | Recent archival facts |
| Skills | `POST /api/skills/generate` | Create a new skill from NL |
| Skills | `POST /api/skills/activate` | Activate a generated skill |
| Evolution | `GET /api/evolution/dashboard` | Monitoring dashboard |
| Evolution | `POST /api/evolution/reflect` | Trigger reflection |
| Governance | `GET /api/evolution/governance/mode` | Current governance mode |
| Governance | `POST /api/evolution/governance/reload` | Hot-reload rules |
| Governance | `GET /api/evolution/governance/versions` | Rule version history |
| Modules | `GET /api/modules/{name}/health` | Module health check |
| Channel | `POST /api/channel/cli/ingest` | CLI message ingestion |
| System | `GET /health` | Service health |

---

## Development

```bash
# Run tests
pytest tests/pulse -q

# Run a specific test
pytest tests/pulse/core/test_brain.py -v
```

---

## Implementation Phases

Pulse was built incrementally across 8 milestones:

| Milestone | Focus | Status |
|-----------|-------|--------|
| M0 | Project skeleton, module system, EventBus | Done |
| M1 | Capability extraction (LLM, Storage, Notify, Scheduler, Browser) | Done |
| M2 | Module migration, legacy cleanup | Done |
| M3 | Channels, Intent Router, Policy Engine, Docker | Done |
| M4 | Brain (ReAct), Tool Registry, MCP, Cost Control | Done |
| M5 | Memory system (Core, Recall, Memory Tools) | Done |
| M6 | Skill Generator (sandbox + hot-load) | Done |
| M7 | Evolution Engine (governance, DPO, reflection) | Done |

See `docs/Pulse实施计划.md` for detailed progress, `docs/Pulse架构方案.md` for architecture design,
`docs/Pulse-MCP优先实施方案.md` for the MCP-first rollout, and `docs/README.md`
for the current-vs-historical docs index.

---

## License

[MIT License](./LICENSE)
