"""Supabase-backed VaxiCare demo endpoints.

The send endpoint is deliberately a simulation: it records a demonstrable
delivery status and ASHA follow-up row, but does not claim to contact a phone.
"""

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.database import get_supabase_client


router = APIRouter(prefix="/api/v1/demo", tags=["Supabase Demo"])


class DemoSendReminderRequest(BaseModel):
    """Delivery channel used for a clearly labelled hackathon simulation."""

    delivery_mode: Literal["sms", "voice"] = "sms"


def _embedded_record(value: Any) -> dict[str, Any]:
    """Normalise Supabase embedded relationship data to one mapping."""
    if isinstance(value, list):
        return value[0] if value else {}
    return value if isinstance(value, dict) else {}


def _database_unavailable(action: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=(
            f"Supabase is unavailable while trying to {action}. "
            "Check the backend-only Render secrets and Supabase project status."
        ),
    )


def _reminder_view(row: dict[str, Any]) -> dict[str, Any]:
    """Return a frontend-friendly reminder record without database internals."""
    child = _embedded_record(row.get("children"))
    schedule = _embedded_record(row.get("dose_schedules"))

    return {
        "id": row.get("id"),
        "child_id": row.get("child_id"),
        "child_name": child.get("full_name"),
        "asha_id": child.get("assigned_asha_id"),
        "vaccine_name": schedule.get("vaccine_name"),
        "dose_number": schedule.get("dose_number"),
        "vaccine_due_date": schedule.get("due_date"),
        "schedule_status": schedule.get("status"),
        "reminder_number": row.get("reminder_number"),
        "risk_level": row.get("risk_level"),
        "planned_at": row.get("planned_at"),
        "channel": row.get("channel"),
        "language": row.get("language"),
        "message_text": row.get("message_text"),
        "delivery_status": row.get("delivery_status"),
        "provider_reference": row.get("provider_reference"),
        "sent_at": row.get("sent_at"),
    }


@router.get("/connection")
def get_demo_connection_status() -> dict[str, Any]:
    """Safe proof that FastAPI can read the Supabase demo tables."""
    try:
        response = (
            get_supabase_client()
            .table("children")
            .select("id", count="exact")
            .limit(1)
            .execute()
        )
    except Exception:
        raise _database_unavailable("verify the database connection")

    return {
        "status": "ok",
        "database": "Supabase",
        "seeded_children_available": response.count or 0,
        "demo_mode": True,
        "note": "The backend can read the persistent VaxiCare demo database.",
    }


@router.get("/reminders")
def list_demo_reminders(
    child_id: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """List seeded persistent reminder events, optionally for one child."""
    try:
        query = (
            get_supabase_client()
            .table("reminder_events")
            .select(
                "id, child_id, reminder_number, risk_level, planned_at, channel, "
                "language, message_text, delivery_status, provider_reference, sent_at, "
                "children!inner(id, full_name, assigned_asha_id), "
                "dose_schedules(vaccine_name, dose_number, due_date, status)"
            )
            .order("planned_at")
            .limit(limit)
        )
        if child_id:
            query = query.eq("child_id", child_id)
        response = query.execute()
    except Exception:
        raise _database_unavailable("read demo reminder events")

    reminders = [_reminder_view(row) for row in (response.data or [])]
    return {"demo_mode": True, "count": len(reminders), "records": reminders}


@router.get("/risk-queue")
def list_demo_risk_queue(
    asha_id: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    """Return ASHA priorities ordered primarily by ML dropout probability."""
    try:
        query = (
            get_supabase_client()
            .table("risk_assessments")
            .select(
                "id, child_id, dropout_probability, risk_level, priority_score, "
                "model_version, assessed_at, "
                "children!inner(id, full_name, assigned_asha_id, area_id)"
            )
            .order("dropout_probability", desc=True)
            .order("priority_score", desc=True)
            .limit(limit)
        )
        if asha_id:
            query = query.eq("children.assigned_asha_id", asha_id)
        response = query.execute()
    except Exception:
        raise _database_unavailable("read the ASHA risk queue")

    records = []
    for row in response.data or []:
        child = _embedded_record(row.get("children"))
        records.append(
            {
                "id": row.get("id"),
                "child_id": row.get("child_id"),
                "child_name": child.get("full_name"),
                "asha_id": child.get("assigned_asha_id"),
                "area_id": child.get("area_id"),
                "dropout_probability": row.get("dropout_probability"),
                "risk_level": row.get("risk_level"),
                "priority_score": row.get("priority_score"),
                "model_version": row.get("model_version"),
                "assessed_at": row.get("assessed_at"),
            }
        )

    return {"demo_mode": True, "count": len(records), "records": records}


@router.post("/reminders/{reminder_id}/send")
def send_demo_reminder(
    reminder_id: UUID,
    payload: DemoSendReminderRequest,
) -> dict[str, Any]:
    """Record an honest, non-provider demo SMS or voice-delivery result."""
    try:
        database = get_supabase_client()
        lookup = (
            database.table("reminder_events")
            .select(
                "id, child_id, reminder_number, risk_level, planned_at, channel, "
                "language, message_text, delivery_status, provider_reference, sent_at, "
                "children!inner(id, full_name, assigned_asha_id), "
                "dose_schedules(vaccine_name, dose_number, due_date, status)"
            )
            .eq("id", str(reminder_id))
            .limit(1)
            .execute()
        )
    except Exception:
        raise _database_unavailable("find the demo reminder")

    if not lookup.data:
        raise HTTPException(status_code=404, detail="Reminder event was not found.")

    existing = lookup.data[0]
    if existing.get("delivery_status") in {"Demo SMS Sent", "Demo Voice Sent"}:
        return {
            "demo_mode": True,
            "external_delivery": False,
            "message": "This reminder has already been recorded as a demo delivery.",
            "reminder": _reminder_view(existing),
        }

    child = _embedded_record(existing.get("children"))
    timestamp = datetime.now(timezone.utc).isoformat()
    delivery_status = "Demo SMS Sent" if payload.delivery_mode == "sms" else "Demo Voice Sent"
    provider_reference = (
        f"DEMO-{payload.delivery_mode.upper()}-"
        f"{str(reminder_id)[:8]}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    )

    try:
        update = (
            database.table("reminder_events")
            .update(
                {
                    "delivery_status": delivery_status,
                    "provider_reference": provider_reference,
                    "sent_at": timestamp,
                }
            )
            .eq("id", str(reminder_id))
            .execute()
        )

        intervention_type = "Reminder" if payload.delivery_mode == "sms" else "Call"
        intervention = (
            database.table("interventions")
            .insert(
                {
                    "child_id": existing["child_id"],
                    "asha_id": child["assigned_asha_id"],
                    "intervention_type": intervention_type,
                    "status": "Completed",
                    "notes": (
                        "HACKATHON DEMO ONLY: Recorded a simulated "
                        f"{payload.delivery_mode.upper()} reminder. No external SMS or "
                        "voice provider was contacted."
                    ),
                    "completed_at": timestamp,
                }
            )
            .execute()
        )
    except Exception:
        raise _database_unavailable("record the demo delivery")

    updated_row = (update.data or [existing])[0]
    updated_row["children"] = existing.get("children")
    updated_row["dose_schedules"] = existing.get("dose_schedules")

    return {
        "demo_mode": True,
        "external_delivery": False,
        "message": (
            f"{delivery_status} recorded in Supabase. This is a simulated demo action; "
            "no real message or call was sent."
        ),
        "reminder": _reminder_view(updated_row),
        "asha_follow_up": (intervention.data or [None])[0],
    }
