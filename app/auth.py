import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.database import get_supabase_auth_client, get_supabase_client

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
security = HTTPBearer()


class EmailOtpRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class LoginRequest(EmailOtpRequest):
    otp: str = Field(pattern=r"^\d{6}$")


def normalize_email_address(email: str) -> str:
    normalized = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    return normalized


def _profile_role(role: str | None) -> str:
    normalized = (role or "").strip().lower()
    if normalized == "asha worker":
        return "asha"
    if normalized not in {"parent", "asha"}:
        raise HTTPException(status_code=403, detail="Your account has no supported portal role.")
    return normalized


@router.post("/otp/send")
def send_email_otp(data: EmailOtpRequest) -> dict[str, bool]:
    email = normalize_email_address(data.email)
    try:
        client = get_supabase_auth_client()
        client.auth.sign_in_with_otp(
            {
                "email": email,
                "options": {"should_create_user": True},
            }
        )
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="Supabase could not send the verification code. Check Auth email configuration.",
        ) from error

    return {"sent": True}


@router.post("/login")
def login_user(data: LoginRequest) -> dict[str, Any]:
    email = normalize_email_address(data.email)
    try:
        client = get_supabase_auth_client()
        auth_response = client.auth.verify_otp(
            {"email": email, "token": data.otp, "type": "email"}
        )
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=401, detail="The verification code is invalid or expired.") from error

    if not auth_response.session or not auth_response.user:
        raise HTTPException(status_code=401, detail="Supabase did not return a user session.")

    try:
        profile_response = (
            get_supabase_client()
            .table("profiles")
            .select("id, email, full_name, role")
            .eq("id", str(auth_response.user.id))
            .limit(1)
            .execute()
        )
    except Exception as error:
        raise HTTPException(status_code=503, detail="Unable to load your account profile.") from error

    if not profile_response.data:
        raise HTTPException(
            status_code=403,
            detail="Your account profile is missing. Ask an administrator to complete account setup.",
        )

    profile = profile_response.data[0]
    profile["role"] = _profile_role(profile.get("role"))
    return {
        "success": True,
        "access_token": auth_response.session.access_token,
        "refresh_token": auth_response.session.refresh_token,
        "user_profile": profile,
    }


def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> Any:
    """Validate a Supabase user JWT; the service-role key never leaves this API."""
    try:
        user_response = get_supabase_client().auth.get_user(credentials.credentials)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials.") from error

    if not user_response or not user_response.user:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials.")
    return user_response.user
