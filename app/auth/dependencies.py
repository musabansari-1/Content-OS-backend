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
