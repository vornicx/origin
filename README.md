# Origin — AI Desktop Assistant

A personal, multimodal, and adaptive AI assistant for Windows. Built from scratch with a Mind-Body-Interface architecture.

## Architecture

```
┌─────────────────────────────────────┐
│  INTERFACE (TypeScript/React/Tauri) │
│  Chat + Control Panel               │
└──────────────┬──────────────────────┘
               │ WebSocket / REST
┌──────────────▼──────────────────────┐
│  API (Python/FastAPI)               │
│  Route orchestration · Port 9001    │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  MIND (Python Core)                 │
│  Reasoning Loop + Memory            │
│  intent → plan → act → check → save │
└──────────────┬──────────────────────┘
               │
    ┌──────────┴──────────┐
    │                     │
┌───▼────┐          ┌─────▼────┐
│ Skills │          │  LLM     │
│  (43+) │          │  Router  │
└────────┘          └──────────┘
```

## Project Structure

| Directory | Description |
|---|---|
| `core/` | Mind: reasoning loop, memory, auth, cache |
| `skills/` | 43+ skills (OS control, vision, voice, web…) |
| `api/` | FastAPI backend with versioned `/v1/` router |
| `db/` | SQLAlchemy models + Alembic migrations (PostgreSQL/pgvector) |
| `frontend/` | React 18 + TypeScript + Tauri 2 desktop app |
| `tests/` | 118+ unit & integration tests |
| `scripts/` | DB backup, quality metrics, smoke tests |
| `.github/` | CI/CD + weekly security audit + Dependabot |

## Quick Start

```bash
# Clone and set up Python environment
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Fill in your keys (see Configuration below)

# Database
psql -U postgres -c "CREATE DATABASE origin;"
alembic upgrade head

# Start API
uvicorn api.main:app --reload --port 9001

# Start frontend (separate terminal)
cd frontend && npm install && npm run dev
```

Or with Docker (recommended):

```bash
docker compose up -d
```

Starts: API (9001) · PostgreSQL/pgvector · Redis · Crucix (3117) · Osiris (3000)

## Configuration

Copy `.env.example` to `.env` and fill in your keys:

```env
# LLM providers (at least one required)
DEEPSEEK_API_KEY=
GROQ_API_KEY=
GEMINI_API_KEY=
ANTHROPIC_API_KEY=

# Security (required)
ORIGIN_JWT_SECRET=        # python -c "import secrets; print(secrets.token_hex(32))"

# Database
DATABASE_URL=postgresql://postgres:postgres@localhost/origin

# Optional
REDIS_URL=redis://localhost:6379
LOG_FORMAT=json           # structured JSON logging for production
TELEGRAM_BOT_TOKEN=
```

## LLM Router

Origin routes requests across multiple providers automatically:

| Provider | Models | Use case |
|---|---|---|
| DeepSeek | deepseek-chat | Default reasoning |
| Groq | llama3, mixtral | Fast responses |
| Gemini | gemini-pro | Multimodal |
| Claude | claude-3-* | Complex tasks |
| Ollama | any local model | Offline / privacy |

## Reasoning Loop

Every request goes through a 6-step loop:

1. **Intent** — parse intent and skill type
2. **Plan** — generate 1–5 execution steps
3. **Act** — execute skills / sub-agents
4. **Check** — auto-review result quality
5. **Save** — persist learnings to memory
6. **Answer** — respond to the user

## Skills

43+ built-in skills including:

- **OS Control** — window management, processes, shell commands
- **Vision** — screenshot capture and analysis
- **Voice** — wake word detection, speech synthesis
- **Web** — search, scraping, browser automation
- **Self-Improvement** — background loop that scans logs, detects bugs, and applies LLM-generated fixes every 5 minutes
- **Telegram**, **Camera**, **Clipboard**, **Scheduler**, **Reminders**, and more

## Security

- HMAC-SHA256 token auth + bcrypt password hashing
- Path traversal prevention on all file operations
- Content Security Policy headers
- Prompt injection guard
- GDPR endpoints: `GET /auth/export`, `DELETE /auth/account`
- Weekly automated CVE scan (pip-audit + bandit) via GitHub Actions
- Dependabot for automatic dependency security updates

See [SECURITY.md](SECURITY.md) for the vulnerability reporting process and hardening checklist.

## Continuous Quality

```
In-process   → SelfImprovement skill runs every 5 min
On push      → CI: lint + type check + 118 tests + pip-audit
Weekly       → Scheduled audit: CVE scan + bandit + coverage + metrics snapshot
Automated    → Dependabot opens PRs for security patches (pip, npm, Actions)
```

Quality metrics are tracked over time in `metrics/history.jsonl` via `scripts/quality_metrics.py`.

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI 0.104, Uvicorn |
| Database | PostgreSQL 16 + pgvector, SQLAlchemy 2.0, Alembic |
| Cache | Redis 7 (in-process TTL dict fallback) |
| Frontend | React 18, TypeScript 5.2, Vite, Tauri 2.1 |
| Auth | HMAC-SHA256 tokens, bcrypt |
| Tests | pytest, pytest-asyncio, Vitest, k6 |
| Infra | Docker Compose, GitHub Actions |

---

**Status:** Beta — full architecture implemented, security hardening applied, 118 tests passing.
