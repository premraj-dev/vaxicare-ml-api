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


def get_supabase_auth_client() -> Client:
    """Create a public-key auth client without sharing mutable auth session state."""
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    anon_key = os.getenv("SUPABASE_ANON_KEY", "").strip()

    if not supabase_url or not anon_key:
        raise RuntimeError(
            "Supabase email OTP is not configured. Add SUPABASE_URL and "
            "SUPABASE_ANON_KEY to the backend environment."
        )

    return create_client(supabase_url, anon_key)


def get_authenticated_supabase_client(access_token: str) -> Client:
    """Use the user JWT for PostgREST so database RLS applies to this request."""
    get_supabase_client()
    client = create_client(
        os.getenv("SUPABASE_URL", "").strip(),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
    )
    client.postgrest.auth(access_token)
    return client
