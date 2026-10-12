import os
from fastapi import APIRouter, HTTPException, Depends, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from supabase import create_client, Client

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

# Initialize Supabase Client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    supabase: Client = None
else:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

security = HTTPBearer()

class RegisterRequest(BaseModel):
    full_name: str
    mobile_number: str = Field(..., pattern=r"^\d{10}$")
    password: str = "Vaxicare@123"  # Default fallback password for UI simplicity
    role: str # "Parent" or "ASHA worker"

class LoginRequest(BaseModel):
    mobile_number: str = Field(..., pattern=r"^\d{10}$")
    otp: str = Field(..., pattern=r"^\d{6}$")  # Accepts 6-digit OTP from UI

@router.post("/register")
def register_user(data: RegisterRequest):
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase client not initialized.")
    
    normalized_phone = f"+91{data.mobile_number}"
    internal_email = f"{data.mobile_number}@vaxicare.local"

    try:
        # Create user in Supabase Auth
        auth_response = supabase.auth.admin.create_user({
            "email": internal_email,
            "password": data.password,
            "email_confirm": True,
            "user_metadata": {
                "full_name": data.full_name,
                "mobile_number": normalized_phone,
                "role": data.role
            }
        })

        user_id = auth_response.user.id if hasattr(auth_response, 'user') else auth_response.get("user", {}).get("id")

        # Insert record into public.profiles table
        profile_data = {
            "id": user_id,
            "full_name": data.full_name,
            "mobile_number": normalized_phone,
            "role": data.role
        }
        
        supabase.table("profiles").insert(profile_data).execute()

        return {
            "success": True,
            "message": "User registered successfully!",
            "user": profile_data
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/login")
def login_user(data: LoginRequest):
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase client not initialized.")

    # For demo convenience, accept '123456' as valid OTP
    if data.otp != "123456":
        raise HTTPException(status_code=401, detail="Invalid OTP. Use 123456 for demo login.")

    internal_email = f"{data.mobile_number}@vaxicare.local"
    normalized_phone = f"+91{data.mobile_number}"

    try:
        # Sign in using the internal email and default demo password
        auth_response = supabase.auth.sign_in_with_password({
            "email": internal_email,
            "password": "Vaxicare@123"
        })

        if not auth_response.session:
            raise HTTPException(status_code=401, detail="User not found. Please register first.")

        # Fetch user profile data
        profile_query = supabase.table("profiles").select("*").eq("mobile_number", normalized_phone).execute()
        profile = profile_query.data[0] if profile_query.data else {}

        return {
            "success": True,
            "message": "Login successful",
            "access_token": auth_response.session.access_token,
            "refresh_token": auth_response.session.refresh_token,
            "user": profile
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

def verify_api_key(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Validates the Supabase bearer token passed from the frontend."""
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase client not initialized.")
    
    token = credentials.credentials
    try:
        user_response = supabase.auth.get_user(token)
        if not user_response or not user_response.user:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials.")
        return user_response.user
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")