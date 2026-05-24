-- ============================================================
-- Kairós — profiles (mock auth) + onboarding assessments
-- Aún sin Supabase Auth: el email es la PK natural.
-- Cuando llegue auth real, se añadirá auth_user_id + RLS.
-- ============================================================

create type assessment_kind as enum ('phq9', 'gad7', 'habits', 'screen');

create type severity_level as enum (
  'minimal',
  'mild',
  'moderate',
  'moderately_severe',
  'severe',
  'unknown'
);

-- ─── Perfil de usuario (mock) ────────────────────────────────
create table profiles (
  id         uuid primary key default gen_random_uuid(),
  email      text not null unique,
  name       text,
  created_at timestamptz not null default now(),
  last_login timestamptz
);

create index profiles_email_idx on profiles (lower(email));

-- ─── Resultados de tests del onboarding ──────────────────────
create table assessment_results (
  id           uuid primary key default gen_random_uuid(),
  profile_id   uuid not null references profiles(id) on delete cascade,
  kind         assessment_kind not null,
  score        integer,                   -- suma cruda
  max_score    integer,
  severity     severity_level not null default 'unknown',
  answers      jsonb not null default '[]'::jsonb,
                                          -- array [{key, value, label}]
  notes        text,
  completed_at timestamptz not null default now(),
  unique (profile_id, kind, completed_at)
);

create index assessment_results_profile_idx
  on assessment_results (profile_id, kind, completed_at desc);

-- ─── Vista: último resultado por test ────────────────────────
create view latest_assessment as
select distinct on (profile_id, kind)
  profile_id, kind, score, max_score, severity, answers, completed_at
from assessment_results
order by profile_id, kind, completed_at desc;

-- ─── Enlace opcional: check-in pertenece a un profile ────────
alter table check_ins
  add column if not exists profile_id uuid references profiles(id) on delete cascade;

create index if not exists check_ins_profile_idx on check_ins(profile_id, date desc);

alter table agent_signals
  add column if not exists profile_id uuid references profiles(id) on delete cascade;

create index if not exists agent_signals_profile_idx on agent_signals(profile_id, agent, created_at desc);

alter table habits
  add column if not exists profile_id uuid references profiles(id) on delete cascade;

alter table habit_logs
  add column if not exists profile_id uuid references profiles(id) on delete cascade;
