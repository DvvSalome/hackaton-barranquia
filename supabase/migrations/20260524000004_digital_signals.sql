-- ============================================================
-- Kairós — señales digitales (extensión de navegador)
--
-- Captura tiempo en sitios web, búsquedas, uso en redes sociales.
-- Se agregan en métricas por categoría y alimentan al recomendador
-- LLM, que propone hábitos y tips concretos.
-- ============================================================

-- ─── Categorías canónicas ──────────────────────────────────
create type site_category as enum (
  'social',          -- IG, TikTok, X, Facebook, Reddit, BeReal, …
  'entertainment',   -- YouTube, Netflix, Twitch, Spotify web, juegos
  'news',            -- medios, agregadores
  'work',            -- gmail, slack, notion, docs, jira, github
  'education',       -- cursos, wikipedia, papers, docs técnicas
  'shopping',        -- amazon, mercadolibre, etc
  'search',          -- google, bing, ddg, perplexity
  'ai',              -- chat.openai, claude, gemini, copilot
  'other'
);

-- ─── Sesiones de navegación (raw, vienen de la extensión) ──
create table browsing_sessions (
  id           uuid primary key default gen_random_uuid(),
  profile_id   uuid references profiles(id) on delete cascade,
  domain       text not null,
  url          text,
  title        text,
  category     site_category not null default 'other',
  started_at   timestamptz not null,
  ended_at     timestamptz,
  duration_sec integer not null default 0,
  active       boolean not null default true,   -- false si la pestaña estuvo idle
  source       text not null default 'extension',
  created_at   timestamptz not null default now()
);

create index browsing_sessions_profile_time_idx
  on browsing_sessions (profile_id, started_at desc);

create index browsing_sessions_domain_idx
  on browsing_sessions (profile_id, domain, started_at desc);

create index browsing_sessions_category_idx
  on browsing_sessions (profile_id, category, started_at desc);

-- ─── Búsquedas (lo que el usuario escribe en Google/Bing/DDG/Perplexity) ─
create table search_queries (
  id          uuid primary key default gen_random_uuid(),
  profile_id  uuid references profiles(id) on delete cascade,
  engine      text not null,        -- google | bing | duckduckgo | perplexity | other
  query       text not null,
  ts          timestamptz not null,
  source      text not null default 'extension',
  created_at  timestamptz not null default now()
);

create index search_queries_profile_time_idx
  on search_queries (profile_id, ts desc);

-- ─── Métricas digitales por día ─────────────────────────────
-- Una fila por (profile_id, date). Se sobrescribe (upsert) cuando
-- el backend recalcula con datos nuevos de la extensión.
create table digital_metrics (
  id             uuid primary key default gen_random_uuid(),
  profile_id     uuid not null references profiles(id) on delete cascade,
  date           date not null default current_date,
  -- minutos por categoría
  minutes_social        integer not null default 0,
  minutes_entertainment integer not null default 0,
  minutes_news          integer not null default 0,
  minutes_work          integer not null default 0,
  minutes_education     integer not null default 0,
  minutes_shopping      integer not null default 0,
  minutes_search        integer not null default 0,
  minutes_ai            integer not null default 0,
  minutes_other         integer not null default 0,
  -- scores 0-100 (más alto = mejor para tu bienestar)
  score_social          numeric(5,2),
  score_focus           numeric(5,2),
  score_balance         numeric(5,2),
  score_digital_overall numeric(5,2),
  top_domains           jsonb not null default '[]'::jsonb,
                         -- [{domain, minutes, category}]
  search_themes         jsonb not null default '[]'::jsonb,
                         -- [{theme, n, sample_queries}]
  context_snapshot      jsonb,   -- contexto crudo usado por el recomendador
  computed_at           timestamptz not null default now(),
  unique (profile_id, date)
);

create index digital_metrics_profile_date_idx
  on digital_metrics (profile_id, date desc);

-- ─── Recomendaciones generadas por el LLM ──────────────────
create type recommendation_kind as enum (
  'habit',           -- propuesta de hábito accionable
  'tip',             -- consejo concreto, no recurrente
  'warning',         -- alerta (ej. exceso de social)
  'reflection'      -- pregunta para reflexionar
);

create type recommendation_status as enum (
  'pending',
  'accepted',
  'dismissed',
  'snoozed'
);

create table recommendations (
  id              uuid primary key default gen_random_uuid(),
  profile_id      uuid not null references profiles(id) on delete cascade,
  kind            recommendation_kind not null,
  status          recommendation_status not null default 'pending',
  title           text not null,
  body            text not null,
  rationale       text,           -- por qué (1-2 frases citando métricas)
  source_metrics  jsonb,          -- {digital: {...}, agents: [...], assessments: [...]}
  habit_proposal  jsonb,          -- {name, emoji, frequency, target_per_week, trigger}
  score_impact    numeric(5,2),   -- 0-100, cuánto puede mover el digital overall
  model           text,
  created_at      timestamptz not null default now(),
  acted_at        timestamptz,
  linked_habit_id uuid references habits(id) on delete set null
);

create index recommendations_profile_status_idx
  on recommendations (profile_id, status, created_at desc);

-- ─── Vista útil: ultima métrica digital por usuario ────────
create view latest_digital_metric as
select distinct on (profile_id)
  profile_id, date, minutes_social, minutes_entertainment, minutes_news,
  minutes_work, minutes_education, minutes_shopping, minutes_search,
  minutes_ai, minutes_other,
  score_social, score_focus, score_balance, score_digital_overall,
  top_domains, search_themes, computed_at
from digital_metrics
order by profile_id, date desc;

-- ─── Permisos para service_role (sigue la convención previa) ─
grant usage on schema public to service_role;
grant all on all tables in schema public to service_role;
grant all on all sequences in schema public to service_role;
