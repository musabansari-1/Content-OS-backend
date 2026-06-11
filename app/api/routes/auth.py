from fastapi import APIRouter, Depends

from app.auth.dependencies import require_current_user
from app.auth.types import AuthResponse, LoginRequest, RegisterRequest, UserResponse
from app.services.auth_workflows import (
    get_current_user_profile,
    login_user,
    register_user,
)


router = APIRouter()


@router.get("/me", response_model=UserResponse)
def get_me(current_user: UserResponse = Depends(require_current_user)):
    return get_current_user_profile(current_user)


@router.post("/auth/register", response_model=AuthResponse)
def register(request: RegisterRequest):
    return register_user(request)


@router.post("/auth/login", response_model=AuthResponse)
def login(request: LoginRequest):
    return login_user(request)
