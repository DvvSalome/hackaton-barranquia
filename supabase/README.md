# Supabase

Migraciones del esquema de Kairós. Sin auth todavía — un solo usuario implícito.

## Aplicar

Con Supabase CLI vinculado al proyecto:

```bash
supabase link --project-ref <ref>
supabase db push
```

O contra una instancia local:

```bash
supabase start
supabase db reset       # corre todas las migraciones desde cero
```

## Esquema actual

- `check_ins` — sesiones diarias de ~4 min con Kairós Core
- `agent_signals` — lecturas estructuradas de los 6 especialistas
- `habits` + `habit_logs` — hábitos y check-marks diarios
- `latest_agent_signal` — view: última lectura por agente

## Siguiente migración (cuando agreguemos auth)

Añadirá `user_id uuid references auth.users` a las 4 tablas + políticas RLS.
