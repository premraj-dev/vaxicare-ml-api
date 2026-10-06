# VaxiCare ML API

VaxiCare ML API is a FastAPI service that predicts child-vaccination dropout risk and drives ASHA worker prioritization, reminders, and village-level capacity planning.

## What this service does

A Logistic Regression model (chosen over XGBoost after time-based evaluation) predicts dropout probability from historical dose adherence, seasonal factors, and geography — with strict leakage controls so only past-known data is used.

**ML probability is the primary ranking signal.** Missed-dose count acts only as a minimum risk floor, so a child with zero missed doses but a high predicted risk still surfaces near the top of the ASHA queue — this is a deliberate change from early-stage reminder-count-only ranking.

## Model Performance

Evaluated on a time-based train/test split with realistic class balance (11.25% positive dropout rate):

| Metric | Logistic Regression | XGBoost |
|---|---|---|
| PR-AUC | 99.82% | 98.17% |
| Precision | 91.84% | 92.08% |
| Recall | 100.0% | 93.13% |
| Recall@Top-10% | 88.89% | 85.56% |
| Recall@Top-20% | 100.0% | 98.89% |

Logistic Regression is deployed as the production model.

## Endpoints

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/health` | GET | — | Confirms the service is running |
| `/api/v1/predict` | POST | X-API-Key* | Returns dropout probability, risk level, priority score, recommended ASHA action, and top 2–3 explainable `risk_reasons` |
| `/api/v1/reminder-plan` | POST | X-API-Key* | Generates the deterministic missed-dose-based reminder schedule |
| `/api/v1/asha/capacity-queue` | GET / POST | X-API-Key | Groups children by village, ranks by ML probability, caps the list to ASHA `daily_capacity` (1–50) |
| `/api/v1/batch/daily-scoring` | POST | X-API-Key | Re-scores all active children, rebuilds the ASHA queue, flags reminder triggers — idempotent per day |

\* Open fallback for `/predict` and `/reminder-plan` applies only when `ENVIRONMENT=development`. In production (`ENVIRONMENT=production` or `STRICT_AUTH=true`), all endpoints strictly require `X-API-Key`.

## Explainable Predictions

Every `/predict` response includes `risk_reasons`, e.g.:

```json
{
  "dropout_probability": 0.9995,
  "risk_level": "High",
  "priority_score": 100.25,
  "recommended_action": "Immediate ASHA Follow-up: Home Visit + Call",
  "risk_reasons": [
    "History of 1 missed vaccine dose(s)",
    "Extended interval of 60 days since last dose",
    "High physical distance (6.5 km) to health facility"
  ]
}
```

## Features Used

Historical adherence (leakage-checked, past-only), missed-dose count, delay-related features, district-level coverage, plus seasonal and geographic signals:

- `due_month`, `monsoon_flag`, `harvest_flag`, `migration_flag`
- `distance_to_health_center` (km)

All coverage/completion-rate fields are strictly validated to `[0.0, 1.0]` and rejected with `422` otherwise.

## Village Capacity Queue

`/api/v1/asha/capacity-queue` groups high-risk children by village, sorts each cluster by raw ML probability, and caps the visit list to the worker's `daily_capacity` (validated 1–50).

## Daily Automated Scoring

`/api/v1/batch/daily-scoring` re-evaluates all active children each morning, updates risk assessments, rebuilds the priority queue, and flags children for reminders. Re-running it on the same date returns `"idempotent_execution": true` without duplicating entries.

## Project Structure

```
vaxicare-ml-api/
├── app/
│   ├── __init__.py
│   ├── auth.py            # X-API-Key / Bearer auth, production enforcement
│   ├── asha_routes.py      # capacity-queue, batch daily-scoring
│   └── demo_routes.py      # demo risk-queue
├── models/
│   ├── final_logistic_model.joblib
│   ├── model_feature_columns.json
│   └── model_metadata.joblib
├── main.py
├── requirements.txt
├── .env.example
└── .gitignore
```

## Local Setup

```bash
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --no-cache-dir -r requirements.txt
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Interactive docs: `http://127.0.0.1:8000/docs`

`/health` returns:

```json
{ "status": "ok", "service": "VaxiCare ML API", "version": "1.0.0" }
```

## Environment Variables

```
ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
ENVIRONMENT=development        # set to "production" to enforce strict auth
STRICT_AUTH=false              # or true to force auth regardless of ENVIRONMENT
API_KEY=your-key-here
```

For Render, add the deployed frontend URL to `ALLOWED_ORIGINS`.

## Render Deployment

| Setting | Value |
|---|---|
| Runtime | Python |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/health` |
| Env Vars | `ALLOWED_ORIGINS`, `ENVIRONMENT`, `STRICT_AUTH`, `API_KEY` |

The `models/` folder must stay in the repo — `main.py` loads both artifacts at startup.

## Prototype Note

Production rollout should add a persistent database for child/reminder events, secrets management, audit logging, verified messaging provider integration, and clinical/public-health review before real-world use.
