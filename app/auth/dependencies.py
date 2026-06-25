from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.domain import AuthUser
from app.auth.service import AuthService


auth_service = AuthService()
bearer_scheme = HTTPBearer(auto_error=False)


def require_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> AuthUser:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Authentication required.")

    return auth_service.get_current_user(credentials.credentials)


def require_verified_user(current_user: AuthUser = Depends(require_current_user)) -> AuthUser:
    if not current_user.email_verified_at:
        raise HTTPException(
            status_code=403,
            detail="Verify your email before accessing this feature.",
        )
    return current_user
