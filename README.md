# VaxiCare ML API

VaxiCare ML API is a FastAPI service that predicts child-vaccination dropout risk, generates reminder plans, and serves Supabase-backed parent and ASHA dashboards.

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
| `/health` | GET | None | Confirms the service is running |
| `/api/auth/otp/send` | POST | None | Sends an email verification code through Supabase Auth |
| `/api/auth/login` | POST | Email OTP | Verifies the code and returns the Supabase access token and profile |
| `/api/send-email` | POST | Supabase bearer token | Sends a registration-success email to the authenticated user's own email address |
| `/api/dashboard/asha` | GET | Supabase bearer token | Returns assigned children, live counts, predictions, records, and interventions |
| `/api/dashboard/parent` | GET | Supabase bearer token | Returns only the authenticated parent's children and related records |
| `/api/dashboard/interventions` | POST | Supabase bearer token | Records an ASHA action for an assigned child |
| `/api/dashboard/interventions/{id}/resolve` | PATCH | Supabase bearer token | Resolves an assigned intervention |
| `/api/v1/predict` | POST | None | Returns dropout probability, risk level, priority score, action, and risk reasons |
| `/api/v1/reminder-plan` | POST | None | Generates the existing missed-dose/risk-based reminder schedule |

Dashboard requests use the user JWT for PostgREST so Supabase RLS applies. The service-role key is only used inside this backend and the demo seeder.

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

## Supabase Setup and Demo Data

1. In Supabase Dashboard, open **SQL Editor** and run [`migrations/20261022_demo_dashboard.sql`](./migrations/20261022_demo_dashboard.sql), then [`migrations/20261023_auth_signup_role.sql`](./migrations/20261023_auth_signup_role.sql) and [`migrations/20261023_newborn_immunization.sql`](./migrations/20261023_newborn_immunization.sql). These migrations create the profile infrastructure, persist the selected signup role, and create the newborn/vaccine/immunization tables and policies. The profile migration must be applied first.
2. Copy `.env.example` to `.env` in this backend folder. Set `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and `SUPABASE_ANON_KEY`; set `DEMO_ASHA_EMAILS` to four unused email addresses you control.
3. Start the API from this folder:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

4. In another terminal, from `vaxicare-frontend-main`, run `pnpm run seed:demo`. It calls the running model and reminder-plan endpoints, then inserts only demo-marked rows. Run `pnpm run seed:clear` to remove rows and auth accounts marked as demo.

## Project Structure

```
vaxicare-ml-api/
|-- app/
|   |-- auth.py             # Supabase email OTP and bearer-token verification
|   |-- dashboard.py        # RLS-scoped dashboard data and intervention APIs
|   |-- database.py         # Server-side Supabase clients
|-- migrations/
|   |-- 20261022_demo_dashboard.sql
|-- scripts/
|   |-- seed_demo.py
|-- models/
|-- main.py
|-- requirements.txt
|-- .env.example
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
{ "status": "ok", "service": "VaxiCare ML API", "version": "1.1.0" }
```

## Environment Variables

Backend `.env` values (copy `.env.example`):

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` - backend only; never expose it to the browser
- `SUPABASE_ANON_KEY` - used by backend Supabase Auth for email OTP
- `RESEND_API_KEY` - Resend API key; backend only
- `FROM_EMAIL` - verified sender address configured in Resend
- `ALLOWED_ORIGINS` - include the deployed frontend origin
- `DEMO_ASHA_EMAILS` - four unused ASHA demo emails you control
- `VAXI_API_BASE_URL` - defaults to `http://127.0.0.1:8000` for local seeding

## Render Deployment

| Setting | Value |
|---|---|
| Runtime | Python |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/health` |
| Env Vars | `ALLOWED_ORIGINS`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `RESEND_API_KEY`, `FROM_EMAIL` |

The `models/` folder must stay in the repo — `main.py` loads both artifacts at startup.

## Prototype Note

Production rollout should add a persistent database for child/reminder events, secrets management, audit logging, verified messaging provider integration, and clinical/public-health review before real-world use.
