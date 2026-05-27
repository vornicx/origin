# Origin - Just A Rather Very Intelligent System

Un asistente de IA personal técnico, multimodal y adaptativo. Construcción desde cero con arquitectura de Mente-Cuerpo-Interfaz.

## 📐 Arquitectura

```
┌─────────────────────────────────────┐
│  INTERFAZ (TypeScript/React)        │
│  Chat + Control Panel               │
└──────────────┬──────────────────────┘
               │ WebSocket / REST
┌──────────────▼──────────────────────┐
│  API (Python/FastAPI)               │
│  Orquestación de rutas              │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  MENTE (Python Core)                │
│  Reasoning Loop + Memoria           │
│  int → plan → act → check → save    │
└──────────────┬──────────────────────┘
               │
    ┌──────────┴──────────┐
    │                     │
┌───▼────┐          ┌────▼────┐
│ Skills │          │ LLM      │
│ Cuerpo │          │ Router   │
└────────┘          └──────────┘
```

## 🗂 Estructura del Proyecto

- `core/` - Mente de Origin (Python)
- `skills/` - Habilidades/Agentes del Cuerpo
- `api/` - Backend FastAPI
- `db/` - Modelos y migrations PostgreSQL
- `frontend/` - UI React/TypeScript
- `config/` - Configuración (perfiles, políticas)
- `tests/` - Tests unitarios

## 🚀 Quick Start

```bash
# Setup
python -m venv venv
source venv/bin/activate  # o venv\Scripts\activate en Windows
pip install -r requirements.txt

# Database
psql -U postgres -c "CREATE DATABASE origin_db;"
alembic upgrade head

# Run API
uvicorn api.main:app --reload

# Run Frontend (otra terminal)
cd frontend && npm install && npm run dev
```

## ⚙️ Configuración

### Variables de entorno (`.env`)

Copia `.env.example` a `.env` y rellena tus claves:

```
DEEPSEEK_API_KEY=...
ORIGIN_JWT_SECRET=<genera con: python -c "import secrets; print(secrets.token_hex(32))">
DATABASE_URL=postgresql://user:password@localhost/origin_db
LOG_FORMAT=json   # activa logging estructurado en producción
```

### Despliegue con Docker

```bash
docker compose up -d
```

Levanta: API (9001) + PostgreSQL/pgvector + Crucix (3117) + Osiris (3000).

### Perfil de Usuario
`config/profiles.json` - Datos, estilos, preferencias de Vadim

### Política de Ejecución
`config/policy.json` - Límites, confirmaciones, reglas de seguridad

## 🧠 Reasoning Loop

1. **Intent** → Parsear intención + tipo de skill
2. **Plan** → Generar 1-5 pasos
3. **Act** → Ejecutar skills/subagentes
4. **Check** → Auto-revisar resultado
5. **Save** → Guardar aprendizajes en memoria
6. **Answer** → Responder a usuario

## 🔒 Seguridad

Ver [SECURITY.md](SECURITY.md) para el proceso de reporte de vulnerabilidades y la checklist de hardening antes del despliegue.

---

**Status**: Beta — arquitectura completa, hardening de seguridad aplicado, tests pasando.
