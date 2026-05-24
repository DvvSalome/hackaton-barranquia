# api-service

Backend de los agentes de Kairós. FastAPI + OpenAI (Core) + OpenRouter (6 especialistas).

## Arquitectura

```
                ┌─────────────────────────────┐
   browser ───▶ │ /chat       (OpenAI Core)   │
                │ /checkin/*                  │
                │ /agents/{kind} (OpenRouter) │
                │ /habits                     │
                └──────────┬──────────────────┘
                           ▼
                       Supabase
```

- **Core** (OpenAI, default `gpt-4o-mini`) — copiloto que conversa con el usuario y sintetiza al final.
- **6 especialistas** (OpenRouter, default `openai/gpt-oss-120b:free`) — `animo`, `sueno`, `foco`, `energia`, `pantalla`, `rachas`. Cada uno devuelve JSON con `score`, `insight`, `signals[]`.

Fallback automático en OpenRouter: si el modelo primario está rate-limited, intenta `gpt-oss-20b:free`, `gemma-4-31b-it:free`, `minimax-m2.5:free`. Más 2 reintentos con backoff.

## Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| GET  | `/health` | ping |
| GET  | `/agents` | metadata de los 6 agentes (nombre, color, foco) |
| POST | `/agents/{kind}` | corre un especialista sobre `context`. Opcional `save:true` |
| POST | `/chat` | turno del Core en la conversación (`messages: [{role, content}]`) |
| POST | `/checkin/synthesize` | ejecuta los 6 agentes en paralelo sobre `transcript` y devuelve `summary` del Core |
| POST | `/checkin/start` | crea fila `check_ins` (requiere DB) |
| GET  | `/habits` | lista hábitos activos |
| POST | `/habits/log` | marca un hábito como hecho hoy |

Docs interactivos: <http://127.0.0.1:8787/docs>

## Dev local

Desde la raíz del repo:

```bash
cd api-service
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn main:app --port 8787 --reload
```

Lee `../.env`. Variables necesarias:
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`
- `OPENAI_API_KEY`, `OPENAI_MODEL` (opcional)
- `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` (opcional)

## Migraciones de Supabase

Antes de usar `save:true` o cualquier endpoint con DB, aplica el SQL de
`supabase/migrations/20260524000001_init.sql` en el SQL Editor del proyecto
Supabase (Dashboard → SQL Editor → New query → paste → Run).
