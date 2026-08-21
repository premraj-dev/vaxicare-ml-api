VaxiCare ML API

VaxiCare ML API is a FastAPI service that loads the trained child-vaccination dropout model and provides two server-side capabilities: dropout-risk prediction and fixed reminder-plan generation.

What this service does

The API uses the Logistic Regression model exported from Google Colab to calculate a dropout probability. The model probability is a secondary ASHA ranking signal. The number and timing of reminder events are determined only by the child’s missed-dose count.

Endpoint
Purpose
GET /health
Confirms the service is running.
POST /api/v1/predict
Returns dropout probability, risk level, priority score, and recommended ASHA action.
POST /api/v1/reminder-plan
Applies the fixed Normal, Low, Medium, High, and overdue reminder policy.




Project structure

Plain Text


vaxicare-ml-api/
├── app/
│   └── __init__.py
├── models/
│   ├── final_logistic_model.joblib
│   └── model_feature_columns.json
├── main.py
├── requirements.txt
├── .env.example
└── .gitignore



Local setup

Use Python 3.12 and a virtual environment.

Plain Text


py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --no-cache-dir -r requirements.txt
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000



Open the interactive API documentation at:

Plain Text


http://127.0.0.1:8000/docs



The health endpoint returns:

JSON


{
  "status": "ok",
  "service": "VaxiCare ML API",
  "version": "1.0.0"
}



Reminder policy

Child condition
Risk level
Fixed reminder schedule
Server-side ASHA action
No missed doses
Normal
D−1
Routine monitoring
1 missed dose
Low
D−2, D−1
Show in ASHA monitoring list
2 missed doses
Medium
D−3, D−2, D−1
Calling-agent follow-up
3 or more missed doses
High
D−4, D−3, D−2, D−1
ASHA home visit and calling task
Due today or overdue
Any
Immediate event
Immediate ASHA urgent follow-up




CORS configuration

Copy .env.example to .env for local development if needed. The ALLOWED_ORIGINS variable accepts a comma-separated list.

Plain Text


ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000



For Render deployment, add the published frontend URL to ALLOWED_ORIGINS, for example:

Plain Text


ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,https://your-vaxicare-frontend.example



Render deployment settings

Create a Python Web Service from this repository and use:

Render setting
Value
Runtime
Python
Build Command
pip install -r requirements.txt
Start Command
uvicorn main:app --host 0.0.0.0 --port $PORT
Health Check Path
/health
Environment Variable
ALLOWED_ORIGINS with the frontend URL added




The models/ folder must remain in the repository because main.py loads both exported model artifacts at startup.

Important prototype note

This repository is suitable for the VaxiCare prototype. Production rollout should add authenticated access, a database for child and reminder events, secure secrets management, audit logging, verified messaging provider templates, and clinical/public-health review before real-world use.

