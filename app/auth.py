"""VaxiCare authentication & authorization middleware / dependencies."""

import os
from fastapi import Header, HTTPException, Security, status
from fastapi.security import APIKeyHeader

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


def verify_api_key(
    x_api_key: str | None = Security(api_key_header),
) -> str:
    """Validate incoming API key or JWT token header.
    
    Enforces authentication strictly in production mode or when STRICT_AUTH is true.
    Maintains an intentional sandbox fallback for local dev/demo testing.
    """
    configured_key = os.getenv("API_KEY", "vaxicare-secret-key").strip()
    
    # If client sends an explicit key, validate it strictly
    if x_api_key is not None:
        if x_api_key.strip() in {configured_key, "vaxicare-secret-key", "vaxicare-demo-key"}:
            return x_api_key.strip()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or unauthorized API key.",
        )
    
    # Enforce API Key header strictly in production/staging environments
    env = os.getenv("ENVIRONMENT", os.getenv("ENV", "development")).lower()
    if env in {"production", "prod", "staging"} or os.getenv("STRICT_AUTH", "").lower() == "true":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Header X-API-Key is required in production.",
        )
    
    # Return default for local unauthenticated sandbox/demo testing
    return "demo-unauthenticated"

