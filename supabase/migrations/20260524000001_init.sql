-- ============================================================
-- Kairós — esquema inicial
-- Sin auth todavía: tablas globales (un solo usuario implícito).
-- Cuando se agregue auth, se añadirá `user_id uuid` + RLS.
-- ============================================================

-- ─── Enums ──────────────────────────────────────────────────
create type agent_kind as enum (
  'animo',
  'sueno',
  'foco',
  'energia',
  'pantalla',
  'rachas'
);

create type check_in_status as enum (
  'pending',
  'in_progress',
  'completed',
  'skipped'
);

create type habit_frequency as enum (
  'daily',
  'weekly',
  'custom'
);

-- ─── Check-ins ──────────────────────────────────────────────
-- Una sesión diaria de ~4 min con Kairós Core.
create table check_ins (
  id               uuid primary key default gen_random_uuid(),
  date             date not null default current_date,
  status           check_in_status not null default 'pending',
  started_at       timestamptz,
  completed_at     timestamptz,
  duration_seconds integer,
  core_summary     text,                 -- síntesis del Core
  core_insight     text,                 -- titular del día
  transcript       jsonb,                -- conversación completa
  created_at       timestamptz not null default now()
);

create index check_ins_date_idx on check_ins(date desc);

-- ─── Señales de los agentes ─────────────────────────────────
-- Cada fila = una lectura estructurada de un especialista
-- durante (o fuera de) un check-in.
create table agent_signals (
  id           uuid primary key default gen_random_uuid(),
  check_in_id  uuid references check_ins(id) on delete cascade,
  agent        agent_kind not null,
  score        numeric(5,2),             -- 0–100 normalizado, opcional
  insight      text,                     -- lectura del agente en prosa
  signals      jsonb not null default '[]'::jsonb,
                                         -- array de {key, value, weight}
  raw_context  text,                     -- lo que recibió el agente
  model        text,                     -- ej. 'openrouter/llama-3.3-70b-instruct:free'
  created_at   timestamptz not null default now()
);

create index agent_signals_check_in_idx on agent_signals(check_in_id);
create index agent_signals_agent_date_idx on agent_signals(agent, created_at desc);

-- ─── Hábitos ────────────────────────────────────────────────
create table habits (
  id              uuid primary key default gen_random_uuid(),
  name            text not null,
  description     text,
  emoji           text,
  frequency       habit_frequency not null default 'daily',
  target_per_week integer,                -- ej. 3 = 3×/semana (null si daily)
  color           text,                   -- hex, ej. '#8b5cf6'
  position        integer not null default 0,
  archived_at     timestamptz,
  created_at      timestamptz not null default now()
);

create index habits_active_position_idx
  on habits(position)
  where archived_at is null;

-- ─── Registro diario de hábitos ─────────────────────────────
create table habit_logs (
  id         uuid primary key default gen_random_uuid(),
  habit_id   uuid not null references habits(id) on delete cascade,
  date       date not null default current_date,
  completed  boolean not null default true,
  note       text,
  created_at timestamptz not null default now(),
  unique (habit_id, date)
);

create index habit_logs_date_idx     on habit_logs(date desc);
create index habit_logs_habit_idx    on habit_logs(habit_id, date desc);

-- ─── Vista útil: última lectura por agente ──────────────────
create view latest_agent_signal as
select distinct on (agent)
  agent, score, insight, signals, created_at, check_in_id
from agent_signals
order by agent, created_at desc;
