-- Kairós database schema — run once in Supabase SQL Editor
-- https://supabase.com/dashboard/project/{your-project-ref}/sql

-- Profiles
CREATE TABLE IF NOT EXISTS public.profiles (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT UNIQUE NOT NULL,
    name        TEXT,
    last_login  TIMESTAMPTZ DEFAULT now(),
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- Check-ins
CREATE TABLE IF NOT EXISTS public.check_ins (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id   TEXT,
    status       TEXT DEFAULT 'in_progress',
    transcript   JSONB,
    core_summary TEXT,
    core_insight TEXT,
    date         DATE DEFAULT CURRENT_DATE,
    completed_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT now()
);

-- Agent signals (specialist readings)
CREATE TABLE IF NOT EXISTS public.agent_signals (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    check_in_id  UUID REFERENCES public.check_ins(id) ON DELETE CASCADE,
    profile_id   TEXT,
    agent        TEXT NOT NULL,
    score        FLOAT,
    insight      TEXT,
    signals      JSONB,
    raw_context  TEXT,
    model        TEXT,
    created_at   TIMESTAMPTZ DEFAULT now()
);

-- Check-in Q&A turns
CREATE TABLE IF NOT EXISTS public.check_in_turns (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    check_in_id  UUID REFERENCES public.check_ins(id) ON DELETE CASCADE,
    profile_id   TEXT,
    agent        TEXT,
    position     INT,
    question     TEXT,
    chips        JSONB,
    answer       TEXT,
    answer_value JSONB,
    asked_at     TIMESTAMPTZ DEFAULT now(),
    answered_at  TIMESTAMPTZ
);

-- Habits
CREATE TABLE IF NOT EXISTS public.habits (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title       TEXT NOT NULL,
    description TEXT,
    position    INT DEFAULT 0,
    archived_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- Habit logs (one row per habit per day)
CREATE TABLE IF NOT EXISTS public.habit_logs (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    habit_id   UUID REFERENCES public.habits(id) ON DELETE CASCADE,
    date       DATE DEFAULT CURRENT_DATE,
    completed  BOOLEAN DEFAULT true,
    note       TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (habit_id, date)
);

-- Assessment results
CREATE TABLE IF NOT EXISTS public.assessment_results (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id TEXT NOT NULL,
    kind       TEXT NOT NULL,
    score      INT,
    max_score  INT,
    severity   TEXT,
    answers    JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Latest assessment per profile/kind
CREATE OR REPLACE VIEW public.latest_assessment AS
SELECT DISTINCT ON (profile_id, kind)
    id, profile_id, kind, score, max_score, severity, answers, created_at
FROM public.assessment_results
ORDER BY profile_id, kind, created_at DESC;

-- Enable Row Level Security (open policies for demo — restrict in prod)
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.check_ins ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.assessment_results ENABLE ROW LEVEL SECURITY;

CREATE POLICY IF NOT EXISTS "service_role_all" ON public.profiles FOR ALL USING (true);
CREATE POLICY IF NOT EXISTS "service_role_all" ON public.check_ins FOR ALL USING (true);
CREATE POLICY IF NOT EXISTS "service_role_all" ON public.agent_signals FOR ALL USING (true);
CREATE POLICY IF NOT EXISTS "service_role_all" ON public.assessment_results FOR ALL USING (true);
