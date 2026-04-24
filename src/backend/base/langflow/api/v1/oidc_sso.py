"""OIDC/Keycloak SSO endpoints for Langflow.

Adds four routes under /api/v1/login/oidc/:

  GET /config     — returns {"enabled": bool} so the frontend can decide
                    whether to show the "Sign in with Keycloak" button.

  GET /authorize  — begins the authorization code flow with PKCE; redirects
                    the browser to Keycloak.

  GET /callback   — handles the authorization code returned by Keycloak,
                    exchanges it for tokens, checks realm roles, creates or
                    looks up a Langflow user, sets session cookies, and
                    redirects to the frontend.

  GET /logout     — clears Langflow + SSO cookies and redirects the browser
                    to Keycloak's end-session endpoint.

Configuration (set in .env.langflow):
  LANGFLOW_KEYCLOAK_URL            e.g. http://localhost:8080
  LANGFLOW_KEYCLOAK_REALM          e.g. agents-market
  LANGFLOW_KEYCLOAK_CLIENT_ID      e.g. langflow-builder
  LANGFLOW_KEYCLOAK_CLIENT_SECRET  from Keycloak admin console
  AGENTS_MARKET_FRONTEND_URL       marketplace frontend, default http://localhost:3000

Role-based access:
  Only users with the realm role "agent_producer" or "platform_admin" may
  access the Langflow builder.  Users whose only role is "agent_consumer"
  are redirected to AGENTS_MARKET_FRONTEND_URL.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from langflow.api.utils import DbSession
from langflow.initial_setup.setup import get_or_create_default_folder
from langflow.services.database.models.user.crud import get_user_by_username
from langflow.services.database.models.user.model import User
from langflow.services.deps import get_auth_service, get_settings_service

router = APIRouter(tags=["SSO"], prefix="/login/oidc")

_PLACEHOLDER_SECRET = "REPLACE_WITH_CLIENT_SECRET"

# Realm roles that are allowed to access the Langflow builder
_ALLOWED_ROLES = {"agent_producer", "platform_admin"}


# ── helpers ───────────────────────────────────────────────────────────────────


class OidcConfig(BaseModel):
    enabled: bool
    label: str = "Sign in with Keycloak"


def _get_keycloak_config() -> tuple[str, str, str, str]:
    server_url = os.getenv("LANGFLOW_KEYCLOAK_URL", "").rstrip("/")
    realm = os.getenv("LANGFLOW_KEYCLOAK_REALM", "")
    client_id = os.getenv("LANGFLOW_KEYCLOAK_CLIENT_ID", "")
    client_secret = os.getenv("LANGFLOW_KEYCLOAK_CLIENT_SECRET", "")
    return server_url, realm, client_id, client_secret


def _is_sso_configured() -> bool:
    server_url, realm, client_id, client_secret = _get_keycloak_config()
    return bool(
        server_url
        and realm
        and client_id
        and client_secret
        and client_secret != _PLACEHOLDER_SECRET
    )


def _oidc_base(server_url: str, realm: str) -> str:
    return f"{server_url}/realms/{realm}/protocol/openid-connect"


def _frontend_origin(request: Request) -> str:
    """Return the origin of the frontend (browser-facing URL).

    During local dev the frontend Vite proxy sits at port 3001 while the
    backend is at 7860.  When the browser navigates to /api/v1/login/oidc/authorize
    through the Vite proxy, the Referer header is the frontend page
    (e.g. http://localhost:3001/login).  We extract the origin from that.

    In production the frontend and backend share the same origin (e.g. via
    nginx), so request.base_url is already correct.
    """
    referer = request.headers.get("referer", "")
    if referer:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return str(request.base_url).rstrip("/")


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _realm_roles_from_token(access_token: str) -> set[str]:
    """Decode the JWT payload (no signature verification) and return realm roles."""
    try:
        raw = access_token.split(".")[1]
        raw += "=" * (4 - len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw))
        return set(payload.get("realm_access", {}).get("roles", []))
    except Exception:
        return set()


# ── routes ────────────────────────────────────────────────────────────────────


@router.get("/config", response_model=OidcConfig, include_in_schema=False)
async def oidc_config() -> OidcConfig:
    """Return whether Keycloak SSO is configured on this server."""
    return OidcConfig(enabled=_is_sso_configured())


@router.get("/authorize", include_in_schema=False)
async def oidc_authorize(request: Request) -> RedirectResponse:
    """Redirect the browser to Keycloak to begin the authorization code flow."""
    server_url, realm, client_id, _ = _get_keycloak_config()
    if not _is_sso_configured():
        raise HTTPException(
            status_code=501,
            detail="Keycloak SSO is not configured on this server.",
        )

    # callback_url is always on the backend (Keycloak must be able to reach it)
    backend_base = str(request.base_url).rstrip("/")
    callback_url = f"{backend_base}/api/v1/login/oidc/callback"

    csrf_token = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()

    # State cookie bundles CSRF token, PKCE verifier, and frontend return URL
    state_payload = json.dumps({
        "csrf": csrf_token,
        "verifier": verifier,
        "return_to": _frontend_origin(request),
    })

    params = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "scope": "openid profile email",
            "redirect_uri": callback_url,
            "state": csrf_token,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    auth_url = f"{_oidc_base(server_url, realm)}/auth?{params}"

    resp = RedirectResponse(url=auth_url, status_code=302)
    resp.set_cookie(
        "oidc_state",
        state_payload,
        max_age=300,
        httponly=True,
        samesite="lax",
    )
    return resp


@router.get("/callback", include_in_schema=False)
async def oidc_callback(
    request: Request,
    response: Response,
    db: DbSession,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
) -> RedirectResponse:
    """Handle the Keycloak authorization code, provision/login the user."""

    # Unpack state cookie (contains CSRF, verifier, and return URL)
    raw_cookie = request.cookies.get("oidc_state", "")
    try:
        state_data = json.loads(raw_cookie) if raw_cookie else {}
    except (json.JSONDecodeError, ValueError):
        state_data = {}

    return_to = state_data.get("return_to", str(request.base_url).rstrip("/"))

    # Keycloak error (e.g. user cancelled login, PKCE mismatch)
    if error:
        desc = error_description or error
        return RedirectResponse(
            url=f"{return_to}/login?sso_error={desc}", status_code=302
        )

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter")

    # CSRF validation
    if not state_data or state_data.get("csrf") != state:
        raise HTTPException(
            status_code=400,
            detail="Invalid state parameter — possible CSRF attempt",
        )

    server_url, realm, client_id, client_secret = _get_keycloak_config()
    backend_base = str(request.base_url).rstrip("/")
    callback_url = f"{backend_base}/api/v1/login/oidc/callback"

    # Exchange authorization code for tokens (include PKCE verifier)
    token_url = f"{_oidc_base(server_url, realm)}/token"
    async with httpx.AsyncClient() as hc:
        token_resp = await hc.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": callback_url,
                "code_verifier": state_data.get("verifier", ""),
            },
        )

    if token_resp.status_code != 200:
        raise HTTPException(
            status_code=401,
            detail=f"Token exchange failed: {token_resp.text}",
        )

    kc_tokens = token_resp.json()
    kc_access_token = kc_tokens.get("access_token", "")
    kc_id_token = kc_tokens.get("id_token", "")

    # Role-based access: only agent_producer and platform_admin may use the builder.
    # If the user lacks the required role, log them out of Keycloak and redirect
    # back to the login page with an error message — never create a Langflow session.
    realm_roles = _realm_roles_from_token(kc_access_token)
    if not realm_roles.intersection(_ALLOWED_ROLES):
        error_msg = "Access denied: your account does not have permission to use the Langflow builder."
        # Pass the error via a cookie so the post_logout_redirect_uri stays clean
        # (Keycloak rejects redirect URIs that contain query parameters unless they
        # are explicitly registered; a plain /login URL is safe).
        kc_logout_params: dict[str, str] = {
            "client_id": client_id,
            "post_logout_redirect_uri": f"{return_to}/login",
        }
        if kc_id_token:
            kc_logout_params["id_token_hint"] = kc_id_token
        kc_logout_url = f"{_oidc_base(server_url, realm)}/logout?{urlencode(kc_logout_params)}"
        resp = RedirectResponse(url=kc_logout_url, status_code=302)
        resp.set_cookie("sso_error", error_msg, httponly=False, samesite="lax", max_age=60)
        return resp

    # Fetch user info from Keycloak
    userinfo_url = f"{_oidc_base(server_url, realm)}/userinfo"
    async with httpx.AsyncClient() as hc:
        userinfo_resp = await hc.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {kc_access_token}"},
        )

    if userinfo_resp.status_code != 200:
        raise HTTPException(
            status_code=401,
            detail="Failed to fetch user info from Keycloak",
        )

    userinfo = userinfo_resp.json()
    username: str = userinfo.get("preferred_username") or userinfo.get("sub") or ""
    if not username:
        raise HTTPException(
            status_code=400,
            detail="Keycloak did not return a usable username",
        )

    # Sync is_superuser: platform_admin Keycloak role maps to Langflow superuser.
    is_platform_admin = "platform_admin" in realm_roles

    # JIT-provision or look up the Langflow user
    auth = get_auth_service()
    user = await get_user_by_username(db, username)
    if user is None:
        user = User(
            username=username,
            # Random password — SSO users never use it directly
            password=auth.get_password_hash(secrets.token_urlsafe(32)),
            is_active=True,
            is_superuser=is_platform_admin,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        await get_or_create_default_folder(db, user.id)
    elif user.is_superuser != is_platform_admin:
        # Sync the flag on every login so role changes in Keycloak take effect.
        user.is_superuser = is_platform_admin
        db.add(user)
        await db.commit()
        await db.refresh(user)

    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail="Your account is inactive. Contact a platform administrator.",
        )

    # Issue Langflow session tokens and set cookies
    lf_tokens = await auth.create_user_tokens(
        user_id=user.id, db=db, update_last_login=True
    )
    auth_settings = get_settings_service().auth_settings

    # Redirect to the frontend home page (not the backend root)
    final = RedirectResponse(url=f"{return_to}/", status_code=302)
    final.delete_cookie("oidc_state")
    final.set_cookie(
        "access_token_lf",
        lf_tokens["access_token"],
        httponly=auth_settings.ACCESS_HTTPONLY,
        samesite=auth_settings.ACCESS_SAME_SITE,
        secure=auth_settings.ACCESS_SECURE,
        expires=auth_settings.ACCESS_TOKEN_EXPIRE_SECONDS,
        domain=auth_settings.COOKIE_DOMAIN,
    )
    if lf_tokens.get("refresh_token"):
        final.set_cookie(
            "refresh_token_lf",
            lf_tokens["refresh_token"],
            httponly=auth_settings.REFRESH_HTTPONLY,
            samesite=auth_settings.REFRESH_SAME_SITE,
            secure=auth_settings.REFRESH_SECURE,
            expires=auth_settings.REFRESH_TOKEN_EXPIRE_SECONDS,
            domain=auth_settings.COOKIE_DOMAIN,
        )
    # Non-HttpOnly marker cookies so the frontend can detect the SSO session
    # and trigger Keycloak logout when the user logs out.
    final.set_cookie(
        "sso_provider",
        "keycloak",
        httponly=False,
        samesite="lax",
        secure=auth_settings.ACCESS_SECURE,
        expires=auth_settings.ACCESS_TOKEN_EXPIRE_SECONDS,
    )
    if kc_id_token:
        final.set_cookie(
            "kc_id_token",
            kc_id_token,
            httponly=False,
            samesite="lax",
            secure=auth_settings.ACCESS_SECURE,
            expires=auth_settings.ACCESS_TOKEN_EXPIRE_SECONDS,
        )
    return final


@router.get("/logout", include_in_schema=False)
async def oidc_logout(request: Request) -> RedirectResponse:
    """Clear Langflow auth cookies and end the Keycloak SSO session."""
    if not _is_sso_configured():
        raise HTTPException(
            status_code=501,
            detail="Keycloak SSO is not configured on this server.",
        )

    server_url, realm, client_id, _ = _get_keycloak_config()
    frontend_origin = _frontend_origin(request)
    id_token = request.cookies.get("kc_id_token", "")

    params: dict[str, str] = {
        "client_id": client_id,
        "post_logout_redirect_uri": f"{frontend_origin}/login",
    }
    if id_token:
        params["id_token_hint"] = id_token

    logout_url = f"{_oidc_base(server_url, realm)}/logout?{urlencode(params)}"

    resp = RedirectResponse(url=logout_url, status_code=302)
    # Clear SSO marker cookies
    resp.delete_cookie("sso_provider")
    resp.delete_cookie("kc_id_token")
    # Clear Langflow auth cookies
    resp.delete_cookie("access_token_lf")
    resp.delete_cookie("refresh_token_lf")
    return resp
