# Kairós Sensor — extensión de navegador

Mide tu vida digital y la inyecta como una métrica más al copiloto Kairós.

## Qué hace

- **Tiempo por dominio** — usa `chrome.tabs` + `chrome.idle` para medir, solo
  cuando estás activo, cuántos minutos pasas en cada sitio.
- **Búsquedas** — captura tus queries en Google, Bing, DuckDuckGo, Perplexity
  y Kagi desde el URL (sin tocar el contenido de la página).
- **Categorización local** — clasifica cada dominio en
  `social | entertainment | news | work | education | shopping | search | ai | other`.
  La lista canónica vive en el backend (`api-service/digital.py`).
- **Buffer local + flush periódico** — guarda todo en `chrome.storage.local`
  y lo envía al endpoint `POST /extension/ingest` del API Kairós cada 5 min
  (configurable). Si la red falla, NO se pierde nada.
- **Sin tocar el DOM** — no hay content scripts ni telemetría externa.
  Solo APIs nativas de Chrome y tu propio backend.

Lo que la extensión NO hace (y por qué):
- No usa `chrome.history` masivamente: respeta tu privacidad y solo
  contabiliza la actividad mientras el navegador está en primer plano.
- No exporta texto de las páginas que visitas, solo `url`, `domain` y `title`.
- No envía nada hasta que configures `apiBase` y `profileId` en el popup.

## Instalación local (modo desarrollador)

1. Abre `chrome://extensions` y activa **Developer mode** (arriba a la derecha).
2. **Load unpacked** → selecciona la carpeta `kairos-dashboard/extension/`.
3. Haz click en el icono de Kairós Sensor:
   - **Profile ID**: el UUID que ves en el dashboard tras hacer login (o tu email).
   - **API base**: por defecto `http://localhost:8000` (tu FastAPI local).
   - Activa el toggle.
   - Pulsa **Guardar**.
4. Navega normalmente. Cada 5 min, o pulsando **Enviar ahora**, los datos
   se envían a `POST /extension/ingest`.

## Flujo en el backend

Cada envío:
1. Inserta las `browsing_sessions` y `search_queries` crudas.
2. Recalcula `digital_metrics` del día (minutos por categoría + scores 0–100).
3. Esa métrica queda disponible en:
   - `GET /digital/{profile_id}/today` — última métrica
   - `GET /digital/{profile_id}/history?days=14` — serie diaria
4. El recomendador (`POST /recommendations/generate`) toma la métrica digital
   + lecturas de los 6 agentes + resultados del onboarding + hábitos actuales,
   y le pide al LLM 3–5 recomendaciones, donde al menos 1 es un hábito
   accionable que el usuario puede aceptar con
   `POST /recommendations/{id}/accept-habit`.

## Scoring (resumen)

- `social`: óptimo ~25 min/día. >120 min penaliza fuerte.
- `entertainment`: óptimo ~45 min/día. >240 min cae a 0.
- `focus` = trabajo + educación, crece hasta 180 min y luego plateau.
- `balance`: penaliza si >30 % del tiempo está concentrado en
  social + entretenimiento + shopping.
- `digital_overall` = 0.35·social + 0.35·focus + 0.30·balance.

Ver implementación en `api-service/digital.py`.

## Próximos pasos

- Whitelist/blacklist de dominios desde el popup.
- Modo "deep focus" temporal que pause el tracking de social.
- Push de la métrica al dashboard en tiempo real (WebSocket o polling).
