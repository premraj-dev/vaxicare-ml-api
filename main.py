import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from app.asha_routes import router as asha_router
from app.auth import verify_api_key
from app.demo_routes import router as demo_router


# VaxiCare ML API: model-driven risk ranking with server-enforced reminder policy.
# The Supabase service-role key is read only by app.database on the backend.
BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "models" / "final_logistic_model.joblib"
FEATURE_COLUMNS_PATH = BASE_DIR / "models" / "model_feature_columns.json"


try:
    with FEATURE_COLUMNS_PATH.open("r", encoding="utf-8") as feature_file:
        raw_feature_columns = json.load(feature_file)

    if isinstance(raw_feature_columns, dict):
        FEATURE_COLUMNS = [
            raw_feature_columns[key]
            for key in sorted(raw_feature_columns, key=lambda key: int(key))
        ]
    else:
        FEATURE_COLUMNS = raw_feature_columns

    MODEL = joblib.load(MODEL_PATH)
except FileNotFoundError as error:
    raise RuntimeError(
        "Model artifact not found. Confirm both files are inside the models folder."
    ) from error
except Exception as error:
    raise RuntimeError(f"Could not load VaxiCare model artifacts: {error}") from error


LOCAL_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
DEPLOYED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
ALLOWED_ORIGINS = list(dict.fromkeys(LOCAL_ORIGINS + DEPLOYED_ORIGINS))


app = FastAPI(
    title="VaxiCare ML API",
    version="1.1.0",
    description=(
        "Vaccination dropout-risk prediction, server-enforced reminder planning, "
        "and Supabase-backed demo reminder records."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)

# Includes demo and ASHA capacity routes
app.include_router(demo_router)
app.include_router(asha_router)



class PredictionRequest(BaseModel):
    """Use the same raw feature scale that was used in the training dataset."""

    model_config = ConfigDict(str_strip_whitespace=True)

    gender: Literal["Female", "Male"]
    vaccine_name: Literal[
        "MR-1",
        "MR-2",
        "OPV-1",
        "OPV-2",
        "OPV-3",
        "Penta-1",
        "Penta-2",
        "Penta-3",
    ]
    dose_number: int = Field(ge=1)
    age_months: float = Field(ge=0)
    previous_doses_received: int = Field(ge=0)
    missed_dose_count: int = Field(ge=0)
    previous_delay_days: int = Field(ge=0)
    average_delay_days: float = Field(ge=0)
    days_since_last_dose: int = Field(ge=0)
    days_until_next_dose: int
    vaccination_completion_rate: float = Field(
        ge=0.0, le=1.0, description="Past adherence rate must be between 0.0 and 1.0"
    )
    dose_sequence_completion_rate: float = Field(
        ge=0.0, le=1.0, description="Sequence completion rate must be between 0.0 and 1.0"
    )
    district_vaccination_coverage: float = Field(
        ge=0.0, le=1.0, description="District coverage must be between 0.0 and 1.0"
    )
    district_full_immunisation_rate: float = Field(
        ge=0.0, le=1.0, description="District full immunisation rate must be between 0.0 and 1.0"
    )
    district_dpt_coverage: float = Field(
        ge=0.0, le=1.0, description="District DPT coverage must be between 0.0 and 1.0"
    )
    district_polio_coverage: float = Field(
        ge=0.0, le=1.0, description="District polio coverage must be between 0.0 and 1.0"
    )
    district_bcg_coverage: float = Field(
        ge=0.0, le=1.0, description="District BCG coverage must be between 0.0 and 1.0"
    )
    due_month: int = Field(
        ge=1, le=12, default=1, description="Due date month (1-12)"
    )
    monsoon_flag: int = Field(
        ge=0, le=1, default=0, description="Monsoon season indicator (0 or 1)"
    )
    harvest_flag: int = Field(
        ge=0, le=1, default=0, description="Harvest season indicator (0 or 1)"
    )
    migration_flag: int = Field(
        ge=0, le=1, default=0, description="Migratory family indicator (0 or 1)"
    )
    distance_to_health_center: float = Field(
        ge=0.0, default=2.0, description="Distance to health facility in km"
    )
    days_overdue: int = Field(default=0, ge=0)


class ReminderPlanRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    child_id: str | None = None
    child_name: str | None = None
    next_vaccine: str = Field(min_length=1)
    missed_dose_count: int = Field(ge=0)
    dropout_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    next_dose_due_date: date
    preferred_language: Literal["Marathi", "Hindi", "English"] = "English"


def missed_dose_floor(missed_dose_count: int) -> float:
    """Missed-dose count acts only as a minimum risk floor."""
    if missed_dose_count == 0:
        return 0.0
    if missed_dose_count == 1:
        return 0.25
    if missed_dose_count == 2:
        return 0.50
    return 0.75


def get_risk_level(effective_prob: float) -> str:
    """Determine risk tier using primary ML probability with missed-dose floor."""
    if effective_prob >= 0.75:
        return "High"
    if effective_prob >= 0.50:
        return "Medium"
    if effective_prob >= 0.25:
        return "Low"
    return "Normal"


def get_reminder_days(risk_level: str) -> list[int]:
    if risk_level == "High":
        return [4, 3, 2, 1]
    if risk_level == "Medium":
        return [3, 2, 1]
    if risk_level == "Low":
        return [2, 1]
    return [1]


def calculate_priority_score(
    dropout_probability: float, missed_dose_count: int, days_overdue: int = 0
) -> float:
    """ML probability is the primary sorting key; missed-dose count is a floor."""
    floor = missed_dose_floor(missed_dose_count)
    effective_prob = max(dropout_probability, floor)
    overdue_component = min(max(days_overdue, 0), 60) * 0.1
    return round(effective_prob * 100.0 + overdue_component, 2)


def generate_risk_reasons(request: PredictionRequest) -> list[str]:
    """Generate top 2-3 feature contribution explanations for ASHA workers."""
    reasons = []

    if request.missed_dose_count > 0:
        reasons.append(
            f"History of {request.missed_dose_count} missed vaccine dose(s)"
        )
    if request.days_since_last_dose >= 45:
        reasons.append(
            f"Extended interval of {request.days_since_last_dose} days since last dose"
        )
    if request.distance_to_health_center >= 5.0:
        reasons.append(
            f"High physical distance ({request.distance_to_health_center} km) to health facility"
        )
    if request.average_delay_days >= 14:
        reasons.append(
            f"High average delay ({round(request.average_delay_days, 1)} days) across past doses"
        )
    if request.monsoon_flag == 1:
        reasons.append("Monsoon season mobility and access disruptions")
    if request.migration_flag == 1:
        reasons.append("Seasonal family migration risk factor")
    if request.harvest_flag == 1:
        reasons.append("Harvest season agricultural workload disruption")
    if request.vaccination_completion_rate < 0.7:
        reasons.append(
            f"Low past vaccination adherence rate ({round(request.vaccination_completion_rate * 100)}%)"
        )
    if request.district_vaccination_coverage < 0.75:
        reasons.append(
            f"Low district coverage ({round(request.district_vaccination_coverage * 100)}%)"
        )

    if not reasons:
        reasons.append(
            "Routine vaccination schedule tracking; no elevated risk factors identified."
        )

    return reasons[:3]


def recommended_action(risk_level: str, days_overdue: int) -> str:
    if days_overdue > 0:
        return "Immediate ASHA Follow-up: Home Visit + Call"
    if risk_level == "High":
        return "ASHA Home Visit + Calling Task"
    if risk_level == "Medium":
        return "Calling-Agent Follow-up + 3 Reminders"
    if risk_level == "Low":
        return "ASHA Monitoring + 2 Reminders"
    return "Routine Reminder"


@app.get("/")
def root() -> dict:
    return {
        "message": "VaxiCare ML API is running.",
        "docs": "/docs",
        "health": "/health",
        "supabase_demo": "/api/v1/demo/connection",
    }


@app.get("/health")
def health_check() -> dict:
    """Health route stays available even if Supabase credentials are absent."""
    return {
        "status": "ok",
        "service": "VaxiCare ML API",
        "version": "1.1.0",
        "supabase_configured": bool(
            os.getenv("SUPABASE_URL", "").strip()
            and os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        ),
    }


@app.post("/api/v1/predict")
def predict_dropout_risk(request: PredictionRequest) -> dict:
    """Predict probability; ML signal is primary for risk level and priority ranking."""
    model_input = request.model_dump(exclude={"days_overdue"})

    try:
        input_frame = pd.DataFrame([model_input])
        encoded_frame = pd.get_dummies(
            input_frame,
            columns=["gender", "vaccine_name"],
            dtype=int,
        )
        encoded_frame = encoded_frame.reindex(columns=FEATURE_COLUMNS, fill_value=0)
        encoded_frame = encoded_frame.astype(float)

        dropout_probability = float(MODEL.predict_proba(encoded_frame)[0][1])
        predicted_miss_next_dose = int(MODEL.predict(encoded_frame)[0])
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Prediction failed. Check the exported model and feature-column files.",
        ) from error

    floor = missed_dose_floor(request.missed_dose_count)
    effective_prob = max(dropout_probability, floor)
    risk_level = get_risk_level(effective_prob)

    priority_score = calculate_priority_score(
        dropout_probability=dropout_probability,
        missed_dose_count=request.missed_dose_count,
        days_overdue=request.days_overdue,
    )

    risk_reasons = generate_risk_reasons(request)

    return {
        "dropout_probability": round(dropout_probability, 4),
        "dropout_probability_percent": round(dropout_probability * 100, 2),
        "predicted_miss_next_dose": predicted_miss_next_dose,
        "risk_level": risk_level,
        "priority_score": priority_score,
        "recommended_action": recommended_action(
            risk_level=risk_level,
            days_overdue=request.days_overdue,
        ),
        "risk_reasons": risk_reasons,
    }


@app.post("/api/v1/reminder-plan")
def create_reminder_plan(request: ReminderPlanRequest) -> dict:
    """Create reminder plan driven primarily by ML risk tier with missed-dose floor."""
    prob = request.dropout_probability if request.dropout_probability is not None else 0.0
    floor = missed_dose_floor(request.missed_dose_count)
    effective_prob = max(prob, floor)
    risk_level = get_risk_level(effective_prob)
    days_before = get_reminder_days(risk_level)

    today = date.today()
    due_date = request.next_dose_due_date
    days_until_due = (due_date - today).days

    child_details = {
        "child_id": request.child_id,
        "child_name": request.child_name,
        "next_vaccine": request.next_vaccine,
        "preferred_language": request.preferred_language,
    }

    if days_until_due <= 0:
        return {
            "risk_level": risk_level,
            "number_of_reminders": 1,
            "immediate_action": True,
            "asha_action": "Immediate ASHA Urgent Follow-up",
            "reminders": [
                {
                    **child_details,
                    "reminder_number": 1,
                    "days_before_vaccine": 0,
                    "reminder_date": today.isoformat(),
                    "vaccine_due_date": due_date.isoformat(),
                    "channel": "SMS + Calling Agent",
                    "status": "Immediate Overdue Trigger",
                    "asha_action": "Immediate ASHA Follow-up",
                }
            ],
        }

    asha_action = {
        "Normal": "Routine Monitoring",
        "Low": "Show in ASHA Monitoring List",
        "Medium": "Calling-Agent Follow-up",
        "High": "ASHA Home Visit + Calling Task",
    }[risk_level]

    reminders = []
    for reminder_number, days_before_vaccine in enumerate(days_before, start=1):
        reminders.append(
            {
                **child_details,
                "reminder_number": reminder_number,
                "days_before_vaccine": days_before_vaccine,
                "reminder_date": (
                    due_date - timedelta(days=days_before_vaccine)
                ).isoformat(),
                "vaccine_due_date": due_date.isoformat(),
                "channel": "SMS + Calling Agent",
                "status": "Pending",
                "asha_action": asha_action,
            }
        )

    return {
        "risk_level": risk_level,
        "number_of_reminders": len(reminders),
        "immediate_action": False,
        "asha_action": asha_action,
        "reminders": reminders,
    }

