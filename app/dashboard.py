from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from app.auth import security, verify_api_key
from app.database import get_authenticated_supabase_client

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


def _user_id(user: Any) -> str:
    user_id = getattr(user, "id", None)
    if not user_id and isinstance(user, dict):
        user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="The authenticated user has no profile id.")
    return str(user_id)


def _embedded(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return value[0] if value else {}
    return value if isinstance(value, dict) else {}


def _query_rows(client: Any, table: str, select: str, child_ids: list[str]) -> list[dict[str, Any]]:
    if not child_ids:
        return []
    response = (
        client.table(table)
        .select(select)
        .in_("child_id", child_ids)
        .execute()
    )
    return response.data or []


def _profile(client: Any, user_id: str) -> dict[str, Any]:
    response = (
        client.table("profiles")
        .select("id, full_name, email, role")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=403, detail="No VaxiCare profile is linked to this account.")
    row = response.data[0]
    role = (row.get("role") or "").strip().lower()
    if role == "asha worker":
        role = "asha"
    if role not in {"parent", "asha"}:
        raise HTTPException(status_code=403, detail="Your account does not have a dashboard role.")
    row["role"] = role
    return row


def _fetch_dashboard(client: Any, profile: dict[str, Any], scope: str) -> dict[str, Any]:
    user_id = profile["id"]
    asha_id: str | None = None
    asha_village: str | None = None
    if scope == "asha":
        worker_response = (
            client.table("asha_workers")
            .select("id, village_id, villages(name)")
            .eq("profile_id", user_id)
            .limit(1)
            .execute()
        )
        if not worker_response.data:
            raise HTTPException(status_code=403, detail="No ASHA worker assignment is linked to this account.")
        worker = worker_response.data[0]
        asha_id = worker["id"]
        asha_village = _embedded(worker.get("villages")).get("name")

    child_query = (
        client.table("children")
        .select(
            "id, guardian_id, asha_id, village_id, name, surname, dob, "
            "guardian_name, guardian_phone, gender, is_demo, villages(name)"
        )
        .order("name")
    )
    if scope == "parent":
        child_query = child_query.eq("guardian_id", user_id)
    else:
        child_query = child_query.eq("asha_id", asha_id)
    child_response = child_query.execute()
    child_rows = child_response.data or []
    child_ids = [row["id"] for row in child_rows]

    vaccination_rows = _query_rows(
        client,
        "vaccination_records",
        "id, child_id, vaccine, due_date, given_date, status, is_demo",
        child_ids,
    )
    prediction_rows = _query_rows(
        client,
        "risk_predictions",
        "id, child_id, dropout_probability, risk_level, priority_score, reasons, scored_at, is_demo",
        child_ids,
    )
    intervention_rows = _query_rows(
        client,
        "interventions",
        "id, child_id, asha_id, type, note, status, created_at, resolved_at, is_demo, children(name, surname)",
        child_ids,
    )
    reminder_rows = _query_rows(
        client,
        "reminder_plans",
        "id, child_id, plan, generated_at, is_demo",
        child_ids,
    )

    latest_predictions: dict[str, dict[str, Any]] = {}
    for prediction in sorted(
        prediction_rows,
        key=lambda row: row.get("scored_at") or "",
        reverse=True,
    ):
        latest_predictions.setdefault(prediction["child_id"], prediction)

    today = date.today()
    current_month = today.strftime("%Y-%m")
    records_by_child: dict[str, list[dict[str, Any]]] = {}
    completed_this_month = 0
    pending_count = 0
    overdue_count = 0
    status_counts: Counter[str] = Counter()
    for record in vaccination_rows:
        records_by_child.setdefault(record["child_id"], []).append(record)
        due_date = date.fromisoformat(record["due_date"])
        given_date = date.fromisoformat(record["given_date"]) if record.get("given_date") else None
        if given_date:
            status_counts["completed"] += 1
            if record["given_date"][:7] == current_month:
                completed_this_month += 1
        elif due_date > today:
            status_counts["pending"] += 1
            pending_count += 1
        elif due_date < today:
            status_counts["overdue"] += 1
            overdue_count += 1

    dashboard_children: list[dict[str, Any]] = []
    for child in child_rows:
        child_records = sorted(
            records_by_child.get(child["id"], []),
            key=lambda record: record.get("due_date") or "",
        )
        due_records = [
            record for record in child_records
            if not record.get("given_date")
        ]
        next_record = due_records[0] if due_records else None
        last_completed = max(
            (record for record in child_records if record.get("given_date")),
            key=lambda record: record["given_date"],
            default=None,
        )
        prediction = latest_predictions.get(child["id"])
        eligible_records = [
            record for record in child_records
            if date.fromisoformat(record["due_date"]) <= today
        ]
        completed = sum(bool(record.get("given_date")) for record in eligible_records)
        overdue = [
            record for record in due_records
            if date.fromisoformat(record["due_date"]) < today
        ]
        village = _embedded(child.get("villages")).get("name")
        dashboard_children.append(
            {
                "id": child["id"],
                "childId": child["id"],
                "childName": " ".join(
                    part for part in (child.get("name"), child.get("surname")) if part
                ),
                "parentName": child.get("guardian_name") or profile.get("full_name") or "",
                "parentEmail": profile.get("email") if scope == "parent" else "",
                "guardianPhone": child.get("guardian_phone") or "",
                "dob": child.get("dob"),
                "gender": child.get("gender") or "Female",
                "village": village or asha_village or "",
                "lastVaccine": last_completed.get("vaccine", "—") if last_completed else "—",
                "nextVaccine": next_record.get("vaccine", "—") if next_record else "—",
                "nextDueDate": next_record.get("due_date") if next_record else None,
                "missedDoses": len(overdue),
                "daysOverdue": max(
                    ((today - date.fromisoformat(record["due_date"])).days for record in overdue),
                    default=0,
                ),
                "dropoutProbability": prediction.get("dropout_probability", 0.0) if prediction else 0.0,
                "riskLevel": prediction.get("risk_level") if prediction else None,
                "priorityScore": prediction.get("priority_score", 0.0) if prediction else 0.0,
                "risk_reasons": prediction.get("reasons", []) if prediction else [],
                "isDemo": bool(child.get("is_demo")),
                "completionRate": round((completed / len(eligible_records)) * 100) if eligible_records else 0,
            }
        )

    dashboard_children.sort(key=lambda child: child["priorityScore"], reverse=True)
    high_risk_count = sum(
        child["riskLevel"] == "High" for child in dashboard_children
    )

    for records in records_by_child.values():
        records.sort(key=lambda row: row.get("due_date") or "")

    dashboard_interventions = []
    for row in sorted(
        intervention_rows,
        key=lambda item: item.get("created_at") or "",
        reverse=True,
    ):
        child = _embedded(row.get("children"))
        dashboard_interventions.append(
            {
                "id": row["id"],
                "childId": row["child_id"],
                "childName": " ".join(
                    part for part in (child.get("name"), child.get("surname")) if part
                ),
                "type": row["type"],
                "date": row.get("created_at"),
                "note": row.get("note") or "",
                "status": row.get("status") or "scheduled",
                "resolvedAt": row.get("resolved_at"),
                "isDemo": bool(row.get("is_demo")),
            }
        )

    latest_reminders: dict[str, dict[str, Any]] = {}
    for reminder in sorted(
        reminder_rows,
        key=lambda row: row.get("generated_at") or "",
        reverse=True,
    ):
        latest_reminders.setdefault(reminder["child_id"], reminder)

    month_labels: list[dict[str, Any]] = []
    year, month = today.year, today.month
    for _ in range(6):
        month_labels.append({"month": f"{year:04d}-{month:02d}", "vaccinations": 0, "followUps": 0})
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    monthly = {entry["month"]: entry for entry in month_labels}
    intervention_counts: Counter[str] = Counter()
    for record in vaccination_rows:
        if record.get("given_date", "")[:7] in monthly:
            monthly[record["given_date"][:7]]["vaccinations"] += 1
    for row in intervention_rows:
        month_key = (row.get("created_at") or "")[:7]
        if month_key in monthly:
            monthly[month_key]["followUps"] += 1
        intervention_counts[row.get("type", "other")] += 1

    child_names = {child["id"]: child["childName"] for child in dashboard_children}
    frontend_records = [
        {
            "id": record["id"],
            "childId": record["child_id"],
            "childName": child_names.get(record["child_id"], ""),
            "vaccine": record["vaccine"],
            "dose": record["vaccine"],
            "date": record.get("given_date") or record.get("due_date"),
            "dueDate": record.get("due_date"),
            "givenDate": record.get("given_date"),
            "status": (
                "Completed" if record.get("given_date")
                else "Pending" if date.fromisoformat(record["due_date"]) > today
                else "Due today" if date.fromisoformat(record["due_date"]) == today
                else "Overdue"
            ),
            "isDemo": bool(record.get("is_demo")),
        }
        for record in vaccination_rows
    ]

    demo_data_present = any(
        row.get("is_demo")
        for row in child_rows + vaccination_rows + prediction_rows + intervention_rows + reminder_rows
    )
    return {
        "profile": profile,
        "summary": {
            "totalChildren": len(child_rows),
            "vaccinatedThisMonth": completed_this_month,
            "pending": pending_count,
            "overdue": overdue_count,
            "highRisk": high_risk_count,
            "completed": status_counts["completed"],
            "resolvedInterventions": sum(
                row.get("status", "").lower() in {"resolved", "completed"}
                for row in intervention_rows
            ),
            "totalDoses": len(vaccination_rows),
        },
        "children": dashboard_children,
        "vaccinationRecords": frontend_records,
        "interventions": dashboard_interventions,
        "reminderPlans": [
            {"childId": child_id, "plan": row.get("plan"), "generatedAt": row.get("generated_at")}
            for child_id, row in latest_reminders.items()
        ],
        "analytics": {
            "riskDistribution": [
                {"label": level, "value": sum(child["riskLevel"] == level for child in dashboard_children)}
                for level in ("High", "Medium", "Low", "Normal")
            ],
            "vaccinationDistribution": [
                {"label": label, "value": status_counts[key]}
                for key, label in (("completed", "Completed"), ("pending", "Pending"), ("overdue", "Overdue"))
            ],
            "monthlyTrend": list(reversed(month_labels)),
            "interventionDistribution": [
                {"label": label.replace("_", " ").title(), "value": intervention_counts[key]}
                for key, label in (
                    ("call", "Calls"),
                    ("reminder", "Reminders"),
                    ("home_visit", "Home visits"),
                    ("followup", "Follow-ups"),
                )
            ],
        },
        "demoDataPresent": demo_data_present,
    }


def _dashboard(
    scope: str,
    credentials: HTTPAuthorizationCredentials,
    user: Any,
) -> dict[str, Any]:
    client = get_authenticated_supabase_client(credentials.credentials)
    profile = _profile(client, _user_id(user))
    if profile["role"] != scope:
        raise HTTPException(status_code=403, detail=f"This endpoint is for {scope} accounts.")
    try:
        return _fetch_dashboard(client, profile, scope)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=503, detail="Unable to load dashboard data from Supabase.") from error


@router.get("/asha")
def asha_dashboard(
    credentials: HTTPAuthorizationCredentials = Security(security),
    user: Any = Depends(verify_api_key),
) -> dict[str, Any]:
    return _dashboard("asha", credentials, user)


@router.get("/parent")
def parent_dashboard(
    credentials: HTTPAuthorizationCredentials = Security(security),
    user: Any = Depends(verify_api_key),
) -> dict[str, Any]:
    return _dashboard("parent", credentials, user)


class InterventionRequest(BaseModel):
    child_id: UUID
    type: Literal["call", "reminder", "home_visit", "followup"]
    note: str = Field(default="", max_length=2000)


@router.post("/interventions")
def create_intervention(
    payload: InterventionRequest,
    credentials: HTTPAuthorizationCredentials = Security(security),
    user: Any = Depends(verify_api_key),
) -> dict[str, Any]:
    client = get_authenticated_supabase_client(credentials.credentials)
    profile = _profile(client, _user_id(user))
    if profile["role"] != "asha":
        raise HTTPException(status_code=403, detail="Only ASHA workers can record interventions.")
    try:
        worker_response = (
            client.table("asha_workers")
            .select("id")
            .eq("profile_id", profile["id"])
            .limit(1)
            .execute()
        )
        if not worker_response.data:
            raise HTTPException(status_code=403, detail="No ASHA worker assignment is linked to this account.")
        result = (
            client.table("interventions")
            .insert(
                {
                    "child_id": str(payload.child_id),
                    "asha_id": worker_response.data[0]["id"],
                    "type": payload.type,
                    "note": payload.note,
                    "status": "scheduled",
                }
            )
            .select("id, child_id, type, note, status, created_at, is_demo")
            .single()
            .execute()
        )
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=403, detail="The child is not assigned to your ASHA account.") from error
    return result.data


@router.patch("/interventions/{intervention_id}/resolve")
def resolve_intervention(
    intervention_id: UUID,
    credentials: HTTPAuthorizationCredentials = Security(security),
    user: Any = Depends(verify_api_key),
) -> dict[str, Any]:
    client = get_authenticated_supabase_client(credentials.credentials)
    profile = _profile(client, _user_id(user))
    if profile["role"] != "asha":
        raise HTTPException(status_code=403, detail="Only ASHA workers can resolve interventions.")
    try:
        result = (
            client.table("interventions")
            .update(
                {
                    "status": "resolved",
                    "resolved_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", str(intervention_id))
            .select("id, status, resolved_at")
            .limit(1)
            .execute()
        )
    except Exception as error:
        raise HTTPException(status_code=503, detail="Unable to update the intervention.") from error
    if not result.data:
        raise HTTPException(status_code=404, detail="Intervention not found or not assigned to your account.")
    return result.data[0]
