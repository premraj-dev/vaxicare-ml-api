import logging
import os
from typing import Any

import resend
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from resend.exceptions import ResendError

from app.auth import normalize_email_address, verify_api_key

router = APIRouter(prefix="/api", tags=["Email"])
logger = logging.getLogger(__name__)


class RegistrationEmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


@router.post("/send-email")
def send_registration_email(
    data: RegistrationEmailRequest,
    user: Any = Depends(verify_api_key),
) -> dict[str, bool]:
    recipient = normalize_email_address(data.email)
    authenticated_email = getattr(user, "email", None)
    if not authenticated_email or normalize_email_address(authenticated_email) != recipient:
        raise HTTPException(
            status_code=403,
            detail="You can only send a registration email to your authenticated address.",
        )

    api_key = os.getenv("RESEND_API_KEY", "").strip()
    from_email = os.getenv("FROM_EMAIL", "").strip()
    if not api_key or not from_email:
        raise HTTPException(
            status_code=503,
            detail="Email delivery is not configured on the backend.",
        )

    resend.api_key = api_key
    try:
        resend.Emails.send(
            {
                "from": from_email,
                "to": [recipient],
                "subject": "Vaxicare Registration Successful",
                "html": "<p>Your Vaxicare registration was successful.</p>",
                "text": "Your Vaxicare registration was successful.",
            }
        )
    except ResendError as error:
        logger.exception("Resend failed to send a registration email.")
        raise HTTPException(
            status_code=502,
            detail="The registration email could not be sent.",
        ) from error

    return {"sent": True}
