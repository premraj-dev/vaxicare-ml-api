"""Seed or clear isolated VaxiCare demo rows using the backend service key."""

from __future__ import annotations

import json
import os
import random
import secrets
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from app.database import get_supabase_client  # noqa: E402

VILLAGE_ID = str(uuid5(NAMESPACE_URL, "vaxicare-demo-village-jategaon-bk"))
VILLAGE_NAME = "Jategaon Bk"
ASHA_NAMES = ("सविता पाटील", "मंगला शिंदे", "वैशाली जाधव", "सुनीता मोरे")
FIRST_NAMES = (
    "आरव", "अनया", "वेदांत", "गौरी", "अथर्व", "सई", "ओम", "सान्वी",
    "विहान", "ईशा", "शौर्य", "मुक्ता", "समर्थ", "जान्हवी", "यश", "प्राजक्ता",
    "तन्वी", "रोहन", "काव्या", "आदित्य", "मृणाल", "स्वरा", "नील", "राधिका",
    "अर्णव", "श्रुती", "अभिजीत", "दीपाली", "ओजस", "माधुरी", "सिद्धार्थ",
    "आर्या", "निखिल", "सायली", "प्रणव", "रेवती", "चैतन्य", "मेघा",
)
CHILD_AGE_MONTHS = (
    60, 48, 42, 0, 0, 0, 0,
    0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 15, 16, 17, 18, 19, 20, 0, 1, 2, 3,
    4, 5, 6, 7, 9, 10, 11, 12,
)
SURNAMES = (
    "Umap", "Pawar", "Ingawale", "Khaldakar", "More", "Jadhav",
    "Shinde", "Gaikwad", "Kale", "Bhosale",
)
VACCINE_SCHEDULE = (
    ("BCG", 0),
    ("OPV-0", 0),
    ("HepB-0", 0),
    ("OPV-1", 42),
    ("Penta-1", 42),
    ("PCV-1", 42),
    ("Rota-1", 42),
    ("fIPV-1", 42),
    ("OPV-2", 70),
    ("Penta-2", 70),
    ("Rota-2", 70),
    ("OPV-3", 98),
    ("Penta-3", 98),
    ("PCV-2", 98),
    ("Rota-3", 98),
    ("fIPV-2", 98),
    ("MR-1", 270),
    ("PCV-Booster", 270),
    ("MR-2", 480),
    ("DPT-Booster-1", 480),
    ("OPV-Booster", 480),
    ("DPT-Booster-2", 1825),
)
PREDICTION_VACCINES = ("MR-1", "MR-2", "OPV-1", "OPV-2", "OPV-3", "Penta-1", "Penta-2", "Penta-3")
DEMO_TABLES = (
    "reminder_plans",
    "interventions",
    "risk_predictions",
    "vaccination_records",
    "children",
    "asha_workers",
    "vaccine_schedule",
    "villages",
)


def _stable_id(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"vaxicare-demo:{value}"))


def _emails() -> list[str]:
    values = [
        item.strip().lower()
        for item in os.getenv("DEMO_ASHA_EMAILS", "").split(",")
        if item.strip()
    ]
    if len(values) != 4 or len(set(values)) != 4:
        raise RuntimeError(
            "Set DEMO_ASHA_EMAILS in vaxicare-ml-api/.env to four distinct email addresses you control."
        )
    if any("@" not in email for email in values):
        raise RuntimeError("Every DEMO_ASHA_EMAILS entry must be a valid email address.")
    return values


def _demo_rows(client: Any, table: str, columns: str = "id") -> list[dict[str, Any]]:
    response = client.table(table).select(columns).eq("is_demo", True).limit(10000).execute()
    return response.data or []


def clear_demo() -> int:
    client = get_supabase_client()
    demo_profiles = _demo_rows(client, "profiles", "id, email")
    deleted = 0
    for table in DEMO_TABLES:
        rows = _demo_rows(client, table)
        if rows:
            client.table(table).delete().eq("is_demo", True).execute()
            deleted += len(rows)

    for profile in demo_profiles:
        client.auth.admin.delete_user(profile["id"])
        deleted += 1
    print(f"Cleared {deleted} demo rows and demo auth accounts; non-demo rows were not selected.")
    return deleted


def _get_or_create_asha(client: Any, email: str, index: int) -> dict[str, Any]:
    existing_profile = (
        client.table("profiles")
        .select("id, is_demo")
        .eq("email", email)
        .limit(1)
        .execute()
    )
    if existing_profile.data:
        raise RuntimeError(
            f"Email {email} is already associated with a profile and was not modified. "
            "Choose an unused demo email."
        )

    full_name = ASHA_NAMES[index]
    created = client.auth.admin.create_user(
        {
            "email": email,
            "password": secrets.token_urlsafe(32),
            "email_confirm": True,
            "user_metadata": {"full_name": full_name},
        }
    )
    user = created.user
    if not user:
        raise RuntimeError(f"Supabase did not create a demo auth user for {email}.")
    profile_id = str(user.id)
    client.table("profiles").upsert(
        {
            "id": profile_id,
            "email": email,
            "full_name": full_name,
            "mobile_number": f"90000000{index + 1:02d}",
            "role": "asha",
            "is_demo": True,
        },
        on_conflict="id",
    ).execute()
    return {"id": profile_id, "name": full_name, "email": email, "phone": f"90000000{index + 1:02d}"}


def _api_post(base_url: str, route: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{route}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ML API request {route} failed ({error.code}): {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Cannot reach ML API at {base_url}. Start the API or set VAXI_API_BASE_URL."
        ) from error


def _child_age_months(index: int) -> int:
    return CHILD_AGE_MONTHS[index]


def _missed_target(index: int) -> int:
    if index < 3:
        return 3
    if index < 7:
        return 2
    if index < 30:
        return 1
    return 0


def _make_doses(child_id: str, dob: date, missed_target: int, index: int) -> list[dict[str, Any]]:
    today = date.today()
    past_schedule = [
        (vaccine, due_age)
        for vaccine, due_age in VACCINE_SCHEDULE
        if dob + timedelta(days=due_age) <= today
    ]
    missed_indices = set(range(max(0, len(past_schedule) - missed_target), len(past_schedule)))
    rows: list[dict[str, Any]] = []

    for dose_index, (vaccine, due_age) in enumerate(VACCINE_SCHEDULE):
        due_date = dob + timedelta(days=due_age)
        if dose_index in missed_indices and due_date < today:
            status = "overdue"
            given_date = None
        elif due_date > today:
            status = "pending"
            given_date = None
        else:
            status = "completed"
            delay = (index + dose_index) % 8
            given_date = min(due_date + timedelta(days=delay), today)
        rows.append(
            {
                "id": _stable_id(f"dose:{child_id}:{vaccine}:{due_age}"),
                "child_id": child_id,
                "vaccine": vaccine,
                "due_date": due_date.isoformat(),
                "given_date": given_date.isoformat() if given_date else None,
                "status": status,
                "is_demo": True,
            }
        )
    return rows


def _prediction_input(
    index: int,
    age_months: int,
    missed_doses: int,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    today = date.today()
    outstanding = [record for record in records if not record["given_date"]]
    next_record = min(outstanding, key=lambda row: row["due_date"], default=None)
    eligible = [record for record in records if date.fromisoformat(record["due_date"]) <= today]
    completed = sum(bool(record["given_date"]) for record in eligible)
    completion_rate = completed / max(len(eligible), 1)
    overdue_days = max(
        (
            (today - date.fromisoformat(record["due_date"])).days
            for record in outstanding
            if date.fromisoformat(record["due_date"]) < today
        ),
        default=0,
    )
    last_given = max(
        (
            date.fromisoformat(record["given_date"])
            for record in records
            if record["given_date"]
        ),
        default=today,
    )
    supported = [record["vaccine"] for record in records if record["vaccine"] in PREDICTION_VACCINES]
    vaccine = (
        next_record["vaccine"]
        if next_record and next_record["vaccine"] in PREDICTION_VACCINES
        else supported[-1] if supported else "Penta-1"
    )
    due_date = date.fromisoformat(next_record["due_date"]) if next_record else today
    return {
        "gender": "Female" if index % 2 else "Male",
        "vaccine_name": vaccine,
        "dose_number": (index % 3) + 1,
        "age_months": age_months,
        "previous_doses_received": completed,
        "missed_dose_count": missed_doses,
        "previous_delay_days": min(overdue_days, 90),
        "average_delay_days": float(min(overdue_days, 45)),
        "days_since_last_dose": max(0, (today - last_given).days),
        "days_until_next_dose": (due_date - today).days,
        "vaccination_completion_rate": max(0.0, min(1.0, completion_rate)),
        "dose_sequence_completion_rate": max(0.0, min(1.0, completion_rate)),
        "district_vaccination_coverage": 0.82,
        "district_full_immunisation_rate": 0.78,
        "district_dpt_coverage": 0.81,
        "district_polio_coverage": 0.84,
        "district_bcg_coverage": 0.9,
        "days_overdue": overdue_days,
    }


def seed_demo() -> None:
    emails = _emails()
    client = get_supabase_client()
    api_base = os.getenv("VAXI_API_BASE_URL", "http://127.0.0.1:8000").strip()
    _api_post(api_base, "/api/v1/predict", {
        "gender": "Female",
        "vaccine_name": "Penta-1",
        "dose_number": 1,
        "age_months": 2,
        "previous_doses_received": 0,
        "missed_dose_count": 0,
        "previous_delay_days": 0,
        "average_delay_days": 0,
        "days_since_last_dose": 0,
        "days_until_next_dose": 1,
        "vaccination_completion_rate": 1,
        "dose_sequence_completion_rate": 1,
        "district_vaccination_coverage": 0.8,
        "district_full_immunisation_rate": 0.8,
        "district_dpt_coverage": 0.8,
        "district_polio_coverage": 0.8,
        "district_bcg_coverage": 0.8,
        "days_overdue": 0,
    })
    clear_demo()

    villages = (
        client.table("villages")
        .select("id, name, is_demo")
        .ilike("name", VILLAGE_NAME)
        .limit(1)
        .execute()
    ).data or []
    if villages:
        village_id = villages[0]["id"]
    else:
        client.table("villages").insert(
            {
                "id": VILLAGE_ID,
                "name": VILLAGE_NAME,
                "district": "Pune",
                "taluka": "Shirur",
                "is_demo": True,
            }
        ).execute()
        village_id = VILLAGE_ID

    asha_accounts = [_get_or_create_asha(client, email, index) for index, email in enumerate(emails)]
    asha_rows = []
    for index, account in enumerate(asha_accounts):
        asha_rows.append(
            {
                "id": _stable_id(f"asha-worker:{account['id']}"),
                "profile_id": account["id"],
                "village_id": village_id,
                "worker_code": f"DEMO-ASHA-{index + 1:02d}",
                "is_demo": True,
            }
        )
    client.table("asha_workers").insert(asha_rows).execute()

    existing_schedule = (
        client.table("vaccine_schedule")
        .select("vaccine, due_age_days, is_demo")
        .limit(1000)
        .execute()
    ).data or []
    existing_pairs = {(row["vaccine"], row["due_age_days"]) for row in existing_schedule}
    schedule_rows = [
        {
            "id": _stable_id(f"schedule:{vaccine}:{due_age_days}"),
            "vaccine": vaccine,
            "due_age_days": due_age_days,
            "is_demo": True,
        }
        for vaccine, due_age_days in VACCINE_SCHEDULE
        if (vaccine, due_age_days) not in existing_pairs
    ]
    if schedule_rows:
        client.table("vaccine_schedule").insert(schedule_rows).execute()

    today = date.today()
    children_rows: list[dict[str, Any]] = []
    doses_by_child: dict[str, list[dict[str, Any]]] = {}
    child_asha_ids: dict[str, str] = {}
    child_missed_counts: dict[str, int] = {}
    for index, first_name in enumerate(FIRST_NAMES):
        child_id = _stable_id(f"child:{index + 1:02d}")
        age_months = _child_age_months(index)
        dob = today - timedelta(days=age_months * 30 + index % 19)
        missed_target = _missed_target(index)
        record_rows = _make_doses(child_id, dob, missed_target, index)
        asha_index = index % 4
        asha_id = asha_rows[asha_index]["id"]
        surname = SURNAMES[index % len(SURNAMES)]
        children_rows.append(
            {
                "id": child_id,
                "guardian_id": None,
                "asha_id": asha_id,
                "village_id": village_id,
                "name": first_name,
                "surname": surname,
                "dob": dob.isoformat(),
                "guardian_name": f"{FIRST_NAMES[(index + 7) % len(FIRST_NAMES)]} {surname}",
                "guardian_phone": f"90000000{index + 11:02d}",
                "gender": "Female" if index % 2 else "Male",
                "is_demo": True,
            }
        )
        doses_by_child[child_id] = record_rows
        child_asha_ids[child_id] = asha_id
        child_missed_counts[child_id] = missed_target

    client.table("children").insert(children_rows).execute()
    vaccination_rows = [
        record for records in doses_by_child.values() for record in records
    ]
    for offset in range(0, len(vaccination_rows), 500):
        client.table("vaccination_records").insert(vaccination_rows[offset : offset + 500]).execute()

    prediction_rows: list[dict[str, Any]] = []
    child_by_id = {row["id"]: row for row in children_rows}
    for index, child_id in enumerate(child_by_id):
        child = child_by_id[child_id]
        age_months = max(0, (today - date.fromisoformat(child["dob"])).days // 30)
        records = doses_by_child[child_id]
        request = _prediction_input(index, age_months, child_missed_counts[child_id], records)
        prediction = _api_post(api_base, "/api/v1/predict", request)
        prediction_rows.append(
            {
                "id": _stable_id(f"prediction:{child_id}"),
                "child_id": child_id,
                "asha_id": child_asha_ids[child_id],
                "dropout_probability": prediction["dropout_probability"],
                "risk_level": prediction["risk_level"],
                "priority_score": prediction["priority_score"],
                "reasons": prediction.get("risk_reasons", []),
                "scored_at": datetime.now(timezone.utc).isoformat(),
                "is_demo": True,
            }
        )
        outstanding = [row for row in records if not row["given_date"]]
        next_record = min(outstanding, key=lambda row: row["due_date"], default=None)
        plan = _api_post(
            api_base,
            "/api/v1/reminder-plan",
            {
                "child_id": child_id,
                "child_name": f"{child['name']} {child['surname']}",
                "next_vaccine": next_record["vaccine"] if next_record else "Penta-1",
                "missed_dose_count": child_missed_counts[child_id],
                "dropout_probability": prediction["dropout_probability"],
                "next_dose_due_date": next_record["due_date"] if next_record else today.isoformat(),
                "preferred_language": "Marathi",
            },
        )
        client.table("reminder_plans").insert(
            {
                "id": _stable_id(f"reminder-plan:{child_id}"),
                "child_id": child_id,
                "plan": plan,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "is_demo": True,
            }
        ).execute()

    risk_mix = Counter(row["risk_level"] for row in prediction_rows)
    if (
        risk_mix["Low"] < 18
        or not 3 <= risk_mix["Medium"] <= 8
        or not 1 <= risk_mix["High"] <= 6
    ):
        raise RuntimeError(
            "The ML predictions did not produce the requested mostly-low, several-medium, "
            f"few-high mix (returned {dict(risk_mix)}). No risk levels are hardcoded; "
            "adjust the child feature profiles for this model and rerun."
        )
    client.table("risk_predictions").insert(prediction_rows).execute()

    rng = random.Random(20261022)
    action_types = ("call", "reminder", "home_visit", "followup")
    intervention_rows = []
    for index in range(20):
        child = children_rows[(index * 7 + 2) % len(children_rows)]
        created_at = datetime.now(timezone.utc) - timedelta(
            days=rng.randint(0, 29),
            hours=rng.randint(0, 23),
        )
        resolved = index % 3 != 0
        intervention_rows.append(
            {
                "id": _stable_id(f"intervention:{index + 1:02d}"),
                "child_id": child["id"],
                "asha_id": child["asha_id"],
                "type": action_types[index % len(action_types)],
                "note": (
                    "Demo follow-up recorded after checking the child's vaccination schedule."
                    if index % 2
                    else "Demo caregiver contact; next dose and visit timing discussed."
                ),
                "status": "resolved" if resolved else "scheduled",
                "created_at": created_at.isoformat(),
                "resolved_at": (created_at + timedelta(hours=2)).isoformat() if resolved else None,
                "is_demo": True,
            }
        )
    client.table("interventions").insert(intervention_rows).execute()
    print(
        f"Seeded {len(asha_rows)} ASHA workers, {len(children_rows)} children, "
        f"{len(vaccination_rows)} vaccination records, {len(prediction_rows)} ML predictions, "
        f"and {len(intervention_rows)} interventions in {VILLAGE_NAME}."
    )
    print(f"ML risk mix: {dict(risk_mix)}. Reminder plans were generated through the ML API.")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"seed:demo", "seed:clear"}:
        raise SystemExit("Usage: python scripts\\seed_demo.py seed:demo|seed:clear")
    try:
        if sys.argv[1] == "seed:demo":
            seed_demo()
        else:
            clear_demo()
    except Exception as error:
        raise SystemExit(f"Demo seed operation failed: {error}") from error


if __name__ == "__main__":
    main()
