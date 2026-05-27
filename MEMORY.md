# Origin Project Memory

## Phase 1: Architecture & Core Foundation (Completed)

### What We Built

**Core Components Completed:**

1. **Mind (Reasoning Loop)**
   - Implemented 6-step reasoning: Intent → Plan → Act → Check → Save → Answer
   - Location: `core/mind.py`
   - Uses LLM-powered parsing, planning, and self-review
   - Saves learnings to memory after each cycle

2. **Profile Manager**
   - Loads user profile (Vadim Vornic) and Origin identity from JSON
   - Manages LLM provider priorities
   - Generates system prompts with Vadim's style preferences
   - Location: `core/profile_manager.py`

3. **Memory Manager**
   - Embeddings with SentenceTransformer (MiniLM-L6-v2)
   - Memory types: conversation, decision, learning, profile
   - Dynamic relevance search
   - Conversation history tracking
   - Location: `core/memory_manager.py`

4. **LLM Router**
   - Multi-provider support: Ollama, NVIDIA NIM, OpenCode, Gemini, Claude
   - Auto-fallback if primary provider fails
   - Async calls for performance
   - Location: `core/llm_router.py`

5. **FastAPI Backend**
   - Main endpoint: POST `/mind/think` - Executes reasoning loop
   - WebSocket: `/ws/chat` - Real-time chat
   - Helper endpoints: `/mind/profile`, `/mind/memory/search`, `/mind/conversation`
   - Location: `api/main.py`

6. **Configuration System**
   - Profiles: User preferences, Origin identity, LLM priorities
   - Policy: Execution rules, confirmation requirements, safety limits
   - Location: `config/profiles.json`, `config/policy.json`

### Tech Stack Applied

- **Python**: Core, API, LLM integration
- **FastAPI**: Backend framework with async support
- **SQLAlchemy models**: Base structure created (db/models.py pending)
- **PostgreSQL**: Configured but not yet initialized
- **WebSockets**: Real-time communication ready

### User Profile (Vadim Vornic) - Locked In

```
Style: Directo, técnico, sin relleno
Trabajo: Iterativo, testing constante, enfoque práctico
Toma decisiones: Data-driven pero pragmático
Humor: Aprecia ironía ligera, referencias frikis
Languages: Python, TypeScript preferidos
LLM Priority: Ollama → NVIDIA NIM → OpenCode → Gemini → Claude
```

Origin Identity:
- Tono: Respetuoso pero confiado
- Humor: Seco, ligeramente sarcástico, nunca fluff
- Registro: Técnico y claro, sin emojis

## Next Steps (Prioritized)

### Immediate (Phase 2)

1. **Database Schema** (`db/models.py`, migrations)
   - user_profile table
   - memories table (with vector column)
   - conversations table
   - Run migrations with Alembic

2. **Skills System** (First skill: Web Search)
   - Extend BaseSkill for real implementations
   - Create web_search.py skill
   - Integrate skill_executor.py into Mind.execute_plan()

3. **UI Prototype** (React/TypeScript)
   - Minimal chat interface
   - Connect to WebSocket `/ws/chat`
   - Display context tags (mode, profile loaded)
   - Show reasoning steps (debug mode)

4. **Testing & Validation**
   - Test reasoning loop end-to-end
   - Validate memory embedding quality
   - Check LLM routing and fallback

### Medium Term

- Skill marketplace (file operations, code generation, research)
- Real database persistence
- Audit logging
- Performance optimization (token efficiency)

---

## Important Notes

- **No copy-paste**: All code written from scratch using references as guides
- **Reasoning loop is the heart**: Intent → Plan → Act → Check → Save → Answer
- **Memory is critical**: Every interaction updates embeddings for relevance
- **Multi-provider strategy**: Never locked to one LLM
- **Safety first**: Policy enforces confirmations for destructive actions
- **Vadim-first**: All responses tailored to his communication style and preferences

## Key Files Reference

```
<ORIGIN_ROOT>\
├── core/
│   ├── mind.py              # The reasoning loop (6 steps)
│   ├── profile_manager.py   # User & Origin identity
│   ├── memory_manager.py    # Embeddings & memory search
│   ├── llm_router.py        # Multi-provider LLM calls
│   └── config.py            # Configuration management
├── api/main.py              # FastAPI app with /think and /ws/chat
├── config/
│   ├── profiles.json        # Vadim + Origin identity
│   └── policy.json          # Execution rules & safety
└── requirements.txt         # All dependencies
```

## Testing the API

1. Install: `pip install -r requirements.txt`
2. Configure: `.env` with API keys
3. Run: `uvicorn api.main:app --reload`
4. Test: `POST http://localhost:8000/mind/think` with `{"input": "your question"}`

---

Last Updated: 2026-05-10
Phase: Foundation Complete, Ready for Skills & UI
