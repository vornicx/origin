# Origin — Manual de Operaciones

> **Just A Rather Very Intelligent System**
> Asistente personal multiagente con reasoning loop, memoria persistente y LLM routing.

---

## 1. Arquitectura

```
┌─────────────────────────────────────────────────┐
│                   FRONTEND                      │
│         React/TS + Vite (puerto 3000)           │
│         WebSocket ←→ ws://localhost:9001        │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│                 API (FastAPI)                    │
│              http://localhost:9001               │
│  /health  /mind/think  /mind/profile  /ws/chat  │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│                  MIND (Core)                     │
│        Reasoning Loop de 6 pasos:                │
│   Intent → Plan → Act → Check → Save → Answer   │
├──────────┬───────────┬───────────┬──────────────┤
│ LLM      │ Memory    │ Skills    │ Profile      │
│ Router   │ Manager   │ Executor  │ Manager      │
└──────────┴───────────┴───────────┴──────────────┘
     │           │           │
     ▼           ▼           ▼
  DeepSeek    PostgreSQL   web_search
  Ollama      + pgvector   datetime
  NVIDIA NIM              system_info
  OpenCode                calculator
  Gemini
```

---

## 2. Requisitos Previos

| Software | Versión | Notas |
|----------|---------|-------|
| Python | 3.11+ | Instalado en sistema |
| Node.js | 18+ | Para frontend React |
| PostgreSQL | 16 | Con extensión pgvector |
| Git | 2.x | Control de versiones |

---

## 3. Encender Origin — Paso a Paso

> **Modo nativo (recomendado).** Desde la versión actual, `start.bat` y `origin` (CLI)
> lanzan el shell nativo Tauri (`origin.exe`) en lugar de abrir una pestaña del navegador.
> Tauri arranca el backend Python como subproceso, espera a `/health`, y muestra el
> overlay transparente + icono de bandeja. Los hotkeys globales (Ctrl+Alt+Space PTT,
> Ctrl+Alt+J modo orb/full, Ctrl+Alt+H mostrar/ocultar) sólo funcionan en este modo,
> no en el modo navegador. El plugin `tauri-plugin-autostart` se activa en el primer
> arranque y registra `origin.exe` en `HKCU\…\Run`; el usuario puede desactivarlo
> desde el menú del tray o con el comando `set_autostart(false)`. Se desinstala
> automáticamente al desinstalar Origin.
>
> **Modo navegador legacy (debug).** Si necesitas inspeccionar la UI con DevTools
> del navegador o probar el backend sin Tauri, usa `origin dev-web`. Arranca uvicorn
> directo y abre `http://localhost:9001` en el navegador por defecto. No registra
> hotkeys globales ni overlay.
>
> **Andamiaje C++ FFI.** El crate `frontend/src-tauri/origin-native/` (workspace
> miembro) compila un módulo C++ vía `cxx-build` y expone funciones a Rust con
> type-safe FFI. Stub actual: `native_hello()` → `"hola desde C++"`. Añadir nuevos
> módulos: declarar en el `#[cxx::bridge]` de `origin-native/src/lib.rs`, implementar
> en `src/cpp/`, registrar build paths en `build.rs`. El comando Tauri `native_hello`
> está expuesto al frontend via `__TAURI__.core.invoke('native_hello')`.



### 3.1 Arrancar PostgreSQL

PostgreSQL corre como servicio de Windows. Verificar:

```bash
pg_isready
# Esperado: :5432 - aceptando conexiones
```

Si no está corriendo:

```powershell
# PowerShell como administrador
Start-Service postgresql-x64-16
```

### 3.2 Arrancar el Backend (API)

```bash
cd <ORIGIN_ROOT>

# Activar el entorno virtual
.\venv\Scripts\activate

# Iniciar el servidor API
python -m uvicorn api.main:app --host 0.0.0.0 --port 9001
```

Salida esperada:
```
Skill registered: web_search
Skill registered: datetime
Skill registered: system_info
Skill registered: calculator
Skill registered: web_scraper
Skill registered: shell
Skill registered: external_apis
Skill registered: memory_compaction
Skill registered: file_manager
Skill registered: code_doctor
Database persistence layer: ACTIVE
Available LLM providers: [DEEPSEEK, OLLAMA, NVIDIA_NIM, OPENCODE, GEMINI]
Uvicorn running on http://0.0.0.0:9001
```

### 3.3 Arrancar el Frontend

```bash
cd <ORIGIN_ROOT>\frontend

# Modo desarrollo (con hot reload)
npm run dev

# O servir el build de producción
npm run build
npx vite preview --port 3000
```

### 3.4 Verificar que todo funciona

```bash
# Health check del API
curl http://localhost:9001/health
# → {"status":"ok","service":"origin-api"}

# Test del reasoning loop
curl -X POST http://localhost:9001/mind/think \
  -H "Content-Type: application/json" \
  -d '{"input": "Hola Origin"}'
```

Abrir navegador en `http://localhost:3000` — la interfaz debe mostrar "online" con punto verde.

---

## 4. Variables de Entorno (.env)

Archivo: `<ORIGIN_ROOT>\.env`

```env
# LLM API Keys (orden de prioridad: DeepSeek > Ollama > NVIDIA > OpenCode > Gemini)
DEEPSEEK_API_KEY=sk-...
GEMINI_API_KEY=...
OPENCODE_API_KEY=sk-...
NVIDIA_NIM_API_KEY=nvapi-...

# Ollama (local, sin API key)
OLLAMA_BASE_URL=http://localhost:11434

# Base de datos
DATABASE_URL=postgresql://postgres:password@localhost/origin_db

# Entorno
DEBUG=false
LOG_LEVEL=INFO
```

---

## 5. Cómo Funciona el Reasoning Loop

Cada vez que envías un mensaje, Origin ejecuta 6 pasos secuenciales:

### Paso 1 — Intent (Parseo de intención)
El LLM analiza qué quieres. Retorna tipo de tarea, skills necesarias y confianza.

### Paso 2 — Plan (Generación de plan)
Genera 1-5 pasos concretos para cumplir tu intención. Cada paso puede invocar una skill.

### Paso 3 — Act (Ejecución)
El `SkillExecutor` ejecuta el plan. Si un paso requiere `web_search`, lo llama. Si es `calculator`, evalúa. Si no necesita skill, responde directo.

### Paso 4 — Check (Auto-revisión)
El LLM revisa críticamente si el resultado cumple tu intención. Identifica gaps y mejoras.

### Paso 5 — Save (Guardar aprendizajes)
Memorias relevantes (decisiones, patrones) se guardan en RAM (caché) y PostgreSQL (persistente) con embeddings vectoriales de 384 dimensiones.

### Paso 6 — Answer (Respuesta final)
Genera la respuesta personalizada usando tu perfil (estilo de comunicación, humor, contexto técnico).

---

## 6. LLM Router — Cadena de Fallback

```
DeepSeek V4 Pro (principal)
    ↓ si falla
Ollama (local, requiere VRAM)
    ↓ si falla
NVIDIA NIM (Mixtral 8x7B)
    ↓ si falla
OpenCode
    ↓ si falla
Gemini Pro
```

Cada intento se registra en logs con tiempos y razón de fallback.

---

## 7. Skills Disponibles (10 skills)

### Skills Base

| Skill | Nombre | Descripción |
|-------|--------|-------------|
| `web_search` | Búsqueda Web | DuckDuckGo Instant Answer API, sin API key |
| `datetime` | Fecha/Hora | Hora actual, conversión de zonas horarias, cálculos temporales |
| `system_info` | Info del Sistema | OS, CPU, RAM, disco, procesos (requiere `psutil`) |
| `calculator` | Calculadora | Evaluación matemática segura via AST (sin `eval`) |

### Skills Avanzadas

| Skill | Nombre | Descripción |
|-------|--------|-------------|
| `web_scraper` | Web Scraper | Extrae contenido estructurado de páginas web: texto, enlaces, tablas, metadatos. Acciones: `extract`, `text`, `links`, `tables`, `select` (CSS), `meta`. Bloquea URLs internas por seguridad. |
| `shell` | Shell/Sistema | Ejecuta comandos del sistema, abre apps, gestiona procesos. Acciones: `run`, `open`, `kill`, `list`, `which`, `env`. Bloquea comandos destructivos (rm -rf, format, etc). |
| `external_apis` | APIs Externas | Gateway a APIs públicas gratuitas (sin API key). Acciones: `weather` (Open-Meteo), `translate` (MyMemory), `crypto` (CoinGecko), `exchange` (ExchangeRate), `news` (Wikinews), `ip_info`, `define`, `random_fact`. |
| `memory_compaction` | Compactación de Memoria | Analiza y optimiza las memorias de Origin. Acciones: `stats`, `duplicates`, `cluster`, `compact` (progressive/key_facts/merge), `plan`, `prune`. Detecta duplicados por similitud coseno. |
| `file_manager` | Gestor de Archivos | Lee, escribe, busca y gestiona archivos locales. Acciones: `read`, `write`, `append`, `list`, `search`, `grep`, `info`, `move`, `copy`, `mkdir`, `delete`, `tree`. Protege rutas del sistema. |
| `code_doctor` | Code Doctor | Diagnóstico de salud de proyectos React via react-doctor. Acciones: `scan`, `score`, `diagnostics`, `diff`, `staged`, `compare`. Score 0-100, categorías: Security, Correctness, Performance, Accessibility, Architecture, Dead Code. Requiere Node >= 22. |

### Crear una nueva skill

1. Crear archivo en `skills/mi_skill.py`
2. Heredar de `BaseSkill`
3. Implementar `validate_inputs()` y `execute()`
4. Registrar en `skill_executor.py` → `_register_defaults()`
5. Exportar en `skills/__init__.py`

```python
from .base_skill import BaseSkill

class MiSkill(BaseSkill):
    def __init__(self):
        super().__init__(name="mi_skill", description="...")

    def validate_inputs(self, inputs):
        # Validar inputs
        return True, ""

    async def execute(self, inputs):
        # Lógica de la skill
        return {"success": True, "result": {...}, "error": None, "execution_time": 0.1}
```

---

## 8. Base de Datos

### Esquema

| Tabla | Propósito |
|-------|-----------|
| `user_profiles` | Perfil del usuario (estilos, preferencias, humor) |
| `memories` | Memorias con embeddings `vector(384)` para búsqueda semántica |
| `conversations` | Historial completo de conversaciones por `cycle_id` |
| `audit_log` | Log inmutable de acciones ejecutadas |

### Migraciones

```bash
# Ejecutar migraciones pendientes
python -m alembic upgrade head

# Ver estado actual
python -m alembic current

# Crear nueva migración
python -m alembic revision --autogenerate -m "descripcion"
```

### Búsqueda vectorial

Las memorias se almacenan con embeddings generados por `all-MiniLM-L6-v2` (384 dims). PostgreSQL + pgvector permite búsqueda por similitud coseno:

```sql
SELECT content, 1 - (embedding <=> query_vector) AS similarity
FROM memories
WHERE user_id = 'vadim_vornic'
ORDER BY embedding <=> query_vector
LIMIT 5;
```

---

## 9. API Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/mind/think` | Reasoning loop completo. Body: `{"input": "..."}` |
| `GET` | `/mind/profile` | Perfil del usuario + identidad de Origin |
| `GET` | `/mind/memory/search?query=...&top_k=5` | Búsqueda semántica en memorias |
| `GET` | `/mind/conversation?n=20` | Últimas N conversaciones |
| `WS` | `/ws/chat` | WebSocket para chat en tiempo real |

### Formato WebSocket

**Enviar:**
```json
{"type": "message", "content": "Tu pregunta aquí"}
```

**Recibir:**
```json
{"type": "status", "message": "Procesando...", "stage": "thinking"}
{"type": "answer", "content": "Respuesta...", "cycle_id": "uuid", "timestamp": "iso"}
{"type": "error", "message": "Descripción del error"}
```

---

## 10. Estructura de Archivos

```
<ORIGIN_ROOT>\
├── .env                    # API keys y configuración
├── alembic.ini             # Config de migraciones
├── MANUAL.md               # Este archivo
│
├── api/
│   └── main.py             # FastAPI app, endpoints HTTP + WebSocket
│
├── core/
│   ├── config.py           # Settings (pydantic) + ConfigManager
│   ├── llm_router.py       # Multi-provider LLM routing con fallback
│   ├── memory_manager.py   # Memoria en RAM con embeddings (caché)
│   ├── mind.py             # Reasoning loop de 6 pasos + persistencia DB
│   ├── profile_manager.py  # Perfil de usuario + identidad Origin
│   └── utils.py            # Helpers (JSON extraction, logging)
│
├── config/
│   ├── profiles.json       # Perfil de Vadim + personalidad Origin
│   └── policy.json         # Política de ejecución (auto/confirmación)
│
├── db/
│   ├── connection.py       # SQLAlchemy engine + session factory
│   ├── models.py           # ORM: UserProfile, Memory, Conversation, AuditLog
│   ├── repository.py       # Repositories (CRUD por tabla)
│   └── migrations/
│       ├── env.py           # Config Alembic
│       └── versions/
│           └── 001_initial_schema.py
│
├── skills/
│   ├── base_skill.py       # Clase abstracta BaseSkill
│   ├── web_search.py       # DuckDuckGo search
│   ├── datetime_skill.py   # Fecha, hora, zonas horarias
│   ├── system_info.py      # Info del sistema (OS, CPU, RAM)
│   ├── calculator.py       # Evaluación matemática segura
│   ├── web_scraper.py      # Extracción web estructurada (BS4 + lxml)
│   ├── shell_skill.py      # Ejecución de comandos con seguridad
│   ├── external_apis.py    # Gateway a APIs públicas gratuitas
│   ├── memory_compaction.py # Compactación inteligente de memorias
│   ├── file_manager.py     # Gestión de archivos locales
│   ├── code_doctor.py      # Diagnóstico React via react-doctor
│   └── skill_executor.py   # Registry + dispatcher de skills
│
├── frontend/
│   ├── src/
│   │   ├── main.tsx                 # Entry point React
│   │   ├── App.tsx                  # Root component
│   │   ├── types.ts                 # TypeScript interfaces
│   │   ├── hooks/useWebSocket.ts    # WebSocket hook (puerto 9001)
│   │   ├── components/
│   │   │   ├── ChatInterface.tsx    # UI principal del chat
│   │   │   └── ContextTags.tsx      # Tags de status/modo/perfil
│   │   └── styles/app.css           # Dark terminal theme
│   ├── vite.config.ts               # Vite + proxy config
│   └── dist/                        # Build de producción
│
└── venv/                   # Entorno virtual Python
```

---

## 11. Troubleshooting

### El API no arranca
```bash
# Verificar que no hay otro proceso en el puerto
netstat -ano | findstr :9001
# Si hay proceso zombie:
taskkill /PID <pid> /F
```

### "Database persistence layer: RAM-only"
```bash
# Verificar PostgreSQL
pg_isready
# Verificar credenciales en .env vs las de PostgreSQL
psql -U postgres -d origin_db -c "SELECT 1"
```

### DeepSeek no responde
- Verificar `DEEPSEEK_API_KEY` en `.env`
- El sistema hará fallback automático al siguiente proveedor
- Los logs muestran qué proveedor se usó y por qué

### Frontend muestra "offline"
- Verificar que el API corre en puerto 9001
- Verificar que el WebSocket endpoint responde:
  ```bash
  curl -H "Connection: Upgrade" -H "Upgrade: websocket" http://localhost:9001/ws/chat
  # Esperado: HTTP 101
  ```

### MemoryError al arrancar
- Matar procesos Python zombie: `taskkill /IM python.exe /F`
- El modelo de embeddings (MiniLM) se carga lazy (solo cuando se necesita)

---

## 12. Apagar Origin

```bash
# 1. Detener el frontend (Ctrl+C en la terminal de Vite)

# 2. Detener el API (Ctrl+C en la terminal de uvicorn)

# 3. PostgreSQL sigue corriendo como servicio (no necesita apagarse)
#    Si quieres detenerlo:
#    PowerShell admin: Stop-Service postgresql-x64-16
```

---

## 13. Stack Tecnológico

| Capa | Tecnología |
|------|-----------|
| LLM Principal | DeepSeek V4 Pro |
| LLM Fallback | Ollama, NVIDIA NIM, OpenCode, Gemini |
| Backend | Python 3.11, FastAPI, Uvicorn |
| Base de datos | PostgreSQL 16 + pgvector 0.8.0 |
| ORM | SQLAlchemy 2.x |
| Migraciones | Alembic |
| Embeddings | SentenceTransformers (all-MiniLM-L6-v2, 384 dims) |
| Frontend | React 18, TypeScript, Vite 5 |
| Comunicación | WebSocket (tiempo real) + REST (HTTP) |

---

*Generado: 2026-05-12 | Versión: 0.2.1 — 10 skills activas*
