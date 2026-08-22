"""VaxiCare Supabase client factory.

This module is for the FastAPI backend only. Never import it in the React
frontend or expose SUPABASE_SERVICE_ROLE_KEY to a browser.
"""

import os
from functools import lru_cache

from supabase import Client, create_client


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """Return a server-side Supabase client using Render environment secrets."""
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    if not supabase_url or not service_role_key:
        raise RuntimeError(
            "Supabase is not configured. Add SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY to the backend environment."
        )

    return create_client(supabase_url, service_role_key)
