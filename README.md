# Kairós — Copiloto de bienestar digital (versión lite)

Sistema multimodal de bienestar que combina:
- **6 consultores LLM especializados** (LangChain) — ánimo, sueño, foco, energía, pantalla, rachas
- **Computer Vision** (YOLOv8 + MediaPipe) — postura, somnolencia, distracciones
- **Extensión Chrome** — monitoreo de apps y tiempo de navegación
- **Modelo ML** (GradientBoosting) — predicción de riesgo de bienestar
- **Orquestador LangChain** — sintetiza todo y da recomendaciones

## Arquitectura

```
Chrome Extension → POST /extension/ingest
     ↓
API Service (port 8001)
  ├── Core Chat          (LangChain)
  ├── 6 Especialistas    (LangChain — paralelo)
  ├── ML Predictor       (scikit-learn Pipeline)
  └── Orquestador        (LangChain synthesis)
     ↑
CV Service (port 8002)
  ├── YOLOv8n            (objetos + personas)
  └── MediaPipe          (gestos + postura + cara)
```

## Setup rápido

### 1. Variables de entorno

```bash
cp .env.example api-service/.env
# Edita api-service/.env con tu OPENROUTER_API_KEY
# Obtén una clave gratuita en https://openrouter.ai
```

### 2. Entrenar el modelo ML

```bash
cd api-service
pip install -r requirements.txt
python ml_model/train.py
```

### 3. Iniciar CV Service

```bash
cd cv-service
pip install -r requirements.txt
uvicorn main:app --port 8002 --reload
```

### 4. Iniciar API Service

```bash
cd api-service
uvicorn main:app --port 8001 --reload
```

### 5. Instalar extensión Chrome

1. Abre Chrome → `chrome://extensions`
2. Activa "Modo desarrollador"
3. "Cargar sin empaquetar" → selecciona la carpeta `extension/`
4. Configura API Base: `http://localhost:8001`

## Endpoints principales

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/health` | GET | Liveness check |
| `/agents` | GET | Lista de especialistas |
| `/checkin/synthesize` | POST | Síntesis completa (6 especialistas + ML) |
| `/chat` | POST | Chat con Kairós Core |
| `/extension/ingest` | POST | Datos de la extensión Chrome |
| `/digital/summary` | GET | Resumen de comportamiento digital |
| `/cv/analyze-proxy` | POST | Proxy al CV service |
| `/onboarding/tests` | GET | Tests PHQ-9, GAD-7, etc. |
| `/onboarding/submit` | POST | Enviar respuestas de test |
| `/recommendations` | POST | Recomendaciones LLM |
| `/ml/predict` | POST | Predicción directa del modelo ML |

## Flujo de datos

```
1. Usuario hace check-in (chat)
2. Kairós Core recopila contexto del día
3. /checkin/synthesize ejecuta:
   a. 6 especialistas en paralelo (LangChain)
   b. ML predictor → nivel de riesgo bajo/moderado/alto
   c. Orquestador sintetiza → respuesta final
4. CV Service analiza imagen → señales de postura/somnolencia
5. Extensión Chrome → datos de apps y tiempo de pantalla
6. Todo se combina en /recommendations
```

## Modelos LangChain

Cada especialista es una cadena:
```python
ChatPromptTemplate (system_prompt con foco específico)
  | ChatOpenAI (OpenRouter — modelos gratuitos disponibles)
  | StrOutputParser
  | json.loads
```

El modelo ML está expuesto como LangChain Tool via `@tool` decorator.
