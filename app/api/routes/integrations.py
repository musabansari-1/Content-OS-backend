import os
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter
from starlette.responses import RedirectResponse


router = APIRouter()


def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())

_load_env_file()

LINKEDIN_CLIENT_ID = os.environ["LINKEDIN_CLIENT_ID"]
LINKEDIN_REDIRECT_URI = os.environ["LINKEDIN_REDIRECT_URI"]
LINKEDIN_CLIENT_SECRET = os.environ["LINKEDIN_CLIENT_SECRET"]
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://localhost:3000")
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_POST_URL = "https://api.linkedin.com/v2/ugcPosts"


@router.get("/auth/linkedin")
def auth_linkedin():
    state = "12345"

    params = {
        "response_type": "code",
        "client_id": LINKEDIN_CLIENT_ID,
        "redirect_uri": LINKEDIN_REDIRECT_URI,
        "state": state,
        "scope": "openid profile email w_member_social",
    }
    linkedin_url = "https://www.linkedin.com/oauth/v2/authorization?" + urlencode(params)
    return RedirectResponse(url=linkedin_url, status_code=302)


@router.get("/auth/linkedin/callback")
async def auth_linkedin_callback(code: str = None, state: str = None, error: str = None):
    if error:
        return RedirectResponse(
            url=f"{FRONTEND_BASE_URL}/integrations?linkedin=error",
            status_code=302,
        )

    if not code:
        return RedirectResponse(
            url=f"{FRONTEND_BASE_URL}/integrations?linkedin=error",
            status_code=302,
        )

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                LINKEDIN_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": LINKEDIN_REDIRECT_URI,
                    "client_id": LINKEDIN_CLIENT_ID,
                    "client_secret": LINKEDIN_CLIENT_SECRET,
                },
            )

        if resp.status_code >= 400:
            return RedirectResponse(
                url=f"{FRONTEND_BASE_URL}/integrations?linkedin=error",
                status_code=302,
            )

        data = resp.json()
        access_token = data.get("access_token")

        if not access_token:
            return RedirectResponse(
                url=f"{FRONTEND_BASE_URL}/integrations?linkedin=error",
                status_code=302,
            )

        print(f"Received LinkedIn access token: {access_token}")

        return RedirectResponse(
            url=f"{FRONTEND_BASE_URL}/integrations?linkedin=connected",
            status_code=302,
        )

    except Exception:
        return RedirectResponse(
            url=f"{FRONTEND_BASE_URL}/integrations?linkedin=error",
            status_code=302,
        )


async def get_linkedin_user_id(access_token: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            LINKEDIN_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    profile = resp.json()
    return profile["sub"]


async def publish_linkedin_post(access_token: str, member_id: str, text: str):
    payload = {
        "author": f"urn:li:person:{member_id}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            LINKEDIN_POST_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
    return resp.status_code, resp.text
