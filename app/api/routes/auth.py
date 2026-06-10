from fastapi import APIRouter, Depends

from app.auth.dependencies import auth_service, require_current_user
from app.auth.types import AuthResponse, LoginRequest, RegisterRequest, UserResponse


router = APIRouter()


@router.get("/me", response_model=UserResponse)
def get_me(current_user: UserResponse = Depends(require_current_user)):
    return current_user


@router.post("/auth/register", response_model=AuthResponse)
def register(request: RegisterRequest):
    return auth_service.register(request)


@router.post("/auth/login", response_model=AuthResponse)
def login(request: LoginRequest):
    return auth_service.login(request)
