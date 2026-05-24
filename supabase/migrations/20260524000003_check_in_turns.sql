-- ============================================================
-- Kairós — turnos del check-in
-- Cada turno = una pregunta dinámica generada por el agente
-- + la respuesta del usuario. Permite reconstruir la sesión
-- y darles a los agentes contexto histórico real.
-- ============================================================

create table check_in_turns (
  id            uuid primary key default gen_random_uuid(),
  check_in_id   uuid not null references check_ins(id) on delete cascade,
  profile_id    uuid references profiles(id) on delete cascade,
  agent         agent_kind,                -- null = pregunta del Core
  position      integer not null default 0,
  question      text not null,
  answer        text,                      -- texto / label que el usuario eligió
  answer_value  jsonb,                     -- estructurado: {key, value, weight?}
  chips         jsonb,                     -- opciones presentadas al usuario
  asked_at      timestamptz not null default now(),
  answered_at   timestamptz
);

create index check_in_turns_checkin_idx
  on check_in_turns (check_in_id, position);

create index check_in_turns_profile_idx
  on check_in_turns (profile_id, asked_at desc);

create index check_in_turns_agent_idx
  on check_in_turns (profile_id, agent, asked_at desc);

-- ─── Vista: últimos turnos respondidos por agente ────────────
create view recent_agent_turns as
select
  profile_id, agent, question, answer, answer_value, asked_at, check_in_id
from check_in_turns
where answered_at is not null
order by asked_at desc;
