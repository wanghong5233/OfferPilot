<h1 align="center">Pulse</h1>

<p align="center">
  <strong>A modular, MCP-first personal AI agent framework that can evolve over time.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/FastAPI-async-teal?logo=fastapi" alt="FastAPI" />
  <img src="https://img.shields.io/badge/MCP-Compatible-green" alt="MCP" />
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License" />
</p>

<p align="center">
  <a href="./README.md">中文</a> | <strong>English</strong>
</p>

---

## What Pulse Is

Pulse is a general-purpose personal AI assistant platform, designed as a **real framework** rather than a demo:

- **Brain**: ReAct loop (`think -> act -> observe -> respond`)
- **Tools**: three-ring model (`ring1 builtin`, `ring2 module`, `ring3 mcp`)
- **Memory**: core / recall / archival
- **Evolution**: preference learning + governance + rollback
- **Skills**: generate and activate tools dynamically

The architecture is built to support incremental extension, so new capabilities can be integrated without rewriting the core.

---

## Current Architecture Focus

- MCP-first service integration (`mcp_servers.yaml` + transport selection)
- Channel -> Router -> Policy -> Brain unified execution path
- Real observability baseline:
  - Event bus and in-memory event timeline
  - trace ID and latency for core execution APIs
  - event query endpoints for debugging and operations

---

## Quick Start

### 1) Install

```bash
pip install -e .
```

### 2) Run API

```bash
pulse start
# or
uvicorn pulse.core.server:create_app --factory --host 0.0.0.0 --port 8010
```

API docs: `http://127.0.0.1:8010/docs`

### 3) Optional helper scripts

```bash
./scripts/setup.sh
./scripts/start.sh
./scripts/pulsectl.sh status
```

---

## Docker

Use root compose for the default API stack:

```bash
docker compose up --build
```

If you need a compose file with PostgreSQL bootstrap, use:

```bash
docker compose -f infra/docker-compose.yml up --build
```

---

## Important API Endpoints

| Category | Endpoint | Description |
|----------|----------|-------------|
| Health | `GET /health` | Service health |
| Brain | `POST /api/brain/run` | Run reasoning execution |
| Brain | `GET /api/brain/tools` | List registered tools |
| MCP | `GET /api/mcp/tools` | List local/external MCP tools |
| MCP | `POST /api/mcp/call` | Call MCP tool |
| Memory | `POST /api/memory/search` | Semantic recall search |
| Evolution | `GET /api/evolution/dashboard` | Evolution and governance dashboard |
| Events | `GET /api/system/events/recent` | Recent system events |
| Events | `GET /api/system/events/stats` | Event stats in time window |

---

## Configuration

Primary runtime settings use `PULSE_` prefix.

Examples:

- `PULSE_PORT`
- `PULSE_BRAIN_MAX_STEPS`
- `PULSE_CORE_MEMORY_PATH`
- `PULSE_MCP_SERVERS_CONFIG_PATH`
- `PULSE_MCP_PREFERRED_SERVER`

See `src/pulse/core/config.py` for full settings.

---

## Repository Notes

- Pulse is the active product framework.
- Some historical OpenClaw/OfferPilot related materials may still exist as references.
- Default entrypoints (`README.md`, this file, root compose, main scripts) are aligned with Pulse.

---

## License

[MIT License](./LICENSE)
