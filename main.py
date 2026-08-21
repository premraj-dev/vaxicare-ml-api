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


# VaxiCare ML API: model-driven risk ranking with server-enforced reminder policy.
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
    origin.strip( )
    for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
ALLOWED_ORIGINS = list(dict.fromkeys(LOCAL_ORIGINS + DEPLOYED_ORIGINS))


app = FastAPI(
    title="VaxiCare ML API",
    version="1.0.0",
    description="Vaccination dropout-risk prediction, reminder planning, and ASHA prioritisation.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


class PredictionRequest(BaseModel):
    """Use the same raw feature scale that was used in the Colab training dataset."""

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
    vaccination_completion_rate: float = Field(ge=0)
    dose_sequence_completion_rate: float = Field(ge=0)
    district_vaccination_coverage: float = Field(ge=0)
    district_full_immunisation_rate: float = Field(ge=0)
    district_dpt_coverage: float = Field(ge=0)
    district_polio_coverage: float = Field(ge=0)
    district_bcg_coverage: float = Field(ge=0)
    days_overdue: int = Field(default=0, ge=0)


class ReminderPlanRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    child_id: str | None = None
    child_name: str | None = None
    next_vaccine: str = Field(min_length=1)
    missed_dose_count: int = Field(ge=0)
    next_dose_due_date: date
    preferred_language: Literal["Marathi", "Hindi", "English"] = "English"


def risk_from_missed_doses(missed_dose_count: int) -> tuple[str, list[int]]:
    """The reminder policy is fixed by missed-dose count, not ML probability."""

    if missed_dose_count == 0:
        return "Normal", [1]
    if missed_dose_count == 1:
        return "Low", [2, 1]
    if missed_dose_count == 2:
        return "Medium", [3, 2, 1]
    return "High", [4, 3, 2, 1]


def calculate_priority_score(
    risk_level: str, days_overdue: int, dropout_probability: float
) -> float:
    """Risk level is primary; overdue duration and ML probability are secondary signals."""

    risk_weight = {
        "Normal": 0,
        "Low": 100,
        "Medium": 200,
        "High": 300,
    }[risk_level]
    overdue_component = min(max(days_overdue, 0), 60) * 2
    probability_component = dropout_probability * 25
    return round(risk_weight + overdue_component + probability_component, 2)


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
    }


@app.get("/health")
def health_check() -> dict:
    return {
        "status": "ok",
        "service": "VaxiCare ML API",
        "version": "1.0.0",
    }


@app.post("/api/v1/predict")
def predict_dropout_risk(request: PredictionRequest) -> dict:
    risk_level, _ = risk_from_missed_doses(request.missed_dose_count)
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

    priority_score = calculate_priority_score(
        risk_level=risk_level,
        days_overdue=request.days_overdue,
        dropout_probability=dropout_probability,
    )

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
    }


@app.post("/api/v1/reminder-plan")
def create_reminder_plan(request: ReminderPlanRequest) -> dict:
    risk_level, days_before = risk_from_missed_doses(request.missed_dose_count)
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
                "reminder_date": (due_date - timedelta(days=days_before_vaccine)).isoformat(),
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
