"""OIDC SSO endpoints for Langflow.

Adds four routes under /api/v1/login/oidc/:

  GET /config     — returns {"enabled": bool} so the frontend can decide
                    whether to show the SSO login button.

  GET /authorize  — begins the authorization code flow with PKCE; redirects
                    the browser to the OIDC provider.

  GET /callback   — handles the authorization code, exchanges it for tokens,
                    checks realm roles, creates or looks up a Langflow user,
                    sets session cookies, and redirects to the frontend.

  GET /logout     — clears Langflow + SSO cookies and redirects the browser
                    to the provider's end-session endpoint.

Configuration (set in .env.langflow).  Variable names align with SSOConfig
columns so the env-var source and the DB record use the same vocabulary:

  LANGFLOW_INITIAL_SSO_PROVIDER_NAME    e.g. keycloak       (SSOConfig.provider_name)
  LANGFLOW_INITIAL_SSO_DISCOVERY_URL    OIDC discovery URL  (SSOConfig.discovery_url)
  LANGFLOW_INITIAL_SSO_CLIENT_ID        client id           (SSOConfig.client_id)
  LANGFLOW_INITIAL_SSO_CLIENT_SECRET    client secret       (SSOConfig.client_secret_encrypted)
  LANGFLOW_INITIAL_SSO_SCOPES           space-separated     (SSOConfig.scopes, default: openid profile email)
  AGENTS_MARKET_FRONTEND_URL            marketplace frontend, default http://localhost:3000

On the first SSO request the env vars (plus endpoints fetched from the
discovery document) are written into the sso_config table so the
configuration is visible to platform admins without inspecting env vars.

Role-based access:
  Only users with the realm role "agent_producer" or "platform_admin" may
  access the Langflow builder.  Users whose only role is "agent_consumer"
  are redirected to AGENTS_MARKET_FRONTEND_URL.

User identity tracking:
  A SSOUserProfile row is created / updated on every successful login,
  linking the Langflow user.id to the stable provider sub claim.  On
  subsequent logins the user is resolved via the SSOUserProfile first,
  so provider-side username changes do not create duplicate Langflow accounts.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import select

from langflow.api.utils import DbSession
from langflow.initial_setup.setup import get_or_create_default_folder
from langflow.services.database.models.auth.sso import SSOConfig, SSOUserProfile
from langflow.services.database.models.user.crud import get_user_by_username
from langflow.services.database.models.user.model import User
from langflow.services.deps import get_auth_service, get_settings_service

router = APIRouter(tags=["SSO"], prefix="/login/oidc")

_PLACEHOLDER_SECRET = "REPLACE_WITH_CLIENT_SECRET"

# Realm roles that are allowed to access the Langflow builder
_ALLOWED_ROLES = {"agent_producer", "platform_admin"}

# Module-level caches: seeded lazily on the first SSO request.
_sso_config_seeded: bool = False
_oidc_discovery_cache: dict | None = None


# ── env-var helpers ───────────────────────────────────────────────────────────


def _get_initial_sso_env() -> tuple[str, str, str, str, str]:
    """Return (provider_name, discovery_url, client_id, client_secret, scopes)."""
    return (
        os.getenv("LANGFLOW_INITIAL_SSO_PROVIDER_NAME", "keycloak"),
        os.getenv("LANGFLOW_INITIAL_SSO_DISCOVERY_URL", "").rstrip("/"),
        os.getenv("LANGFLOW_INITIAL_SSO_CLIENT_ID", ""),
        os.getenv("LANGFLOW_INITIAL_SSO_CLIENT_SECRET", ""),
        os.getenv("LANGFLOW_INITIAL_SSO_SCOPES", "openid profile email"),
    )


def _is_sso_configured() -> bool:
    _, discovery_url, client_id, client_secret, _ = _get_initial_sso_env()
    return bool(
        discovery_url
        and client_id
        and client_secret
        and client_secret != _PLACEHOLDER_SECRET
    )


# ── OIDC discovery ────────────────────────────────────────────────────────────


async def _fetch_discovery() -> dict:
    """Fetch and cache the OIDC provider discovery document."""
    global _oidc_discovery_cache
    if _oidc_discovery_cache is not None:
        return _oidc_discovery_cache

    _, discovery_url, _, _, _ = _get_initial_sso_env()
    async with httpx.AsyncClient() as hc:
        resp = await hc.get(discovery_url, timeout=10)
        resp.raise_for_status()
        _oidc_discovery_cache = resp.json()
    return _oidc_discovery_cache


# ── helpers ───────────────────────────────────────────────────────────────────


class OidcConfig(BaseModel):
    enabled: bool
    label: str = "Sign in with SSO"


def _frontend_origin(request: Request) -> str:
    """Return the origin of the frontend (browser-facing URL)."""
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


async def _ensure_sso_config(db) -> None:
    """Lazily seed one SSOConfig row from env vars on the first SSO request.

    Fetches the OIDC discovery document to populate all endpoint columns so the
    DB record is a complete snapshot of the provider config (no manual URL assembly).
    Env vars remain authoritative; we only write if no row exists yet.
    """
    global _sso_config_seeded
    if _sso_config_seeded:
        return

    provider_name, discovery_url, client_id, client_secret, scopes = _get_initial_sso_env()
    if not all([discovery_url, client_id, client_secret]):
        _sso_config_seeded = True
        return

    result = await db.exec(select(SSOConfig).where(SSOConfig.provider_name == provider_name))
    existing = result.first()
    if existing is None:
        discovery = await _fetch_discovery()
        config = SSOConfig(
            provider="oidc",
            provider_name=provider_name,
            enabled=True,
            enforce_sso=True,
            client_id=client_id,
            client_secret_encrypted=client_secret,
            discovery_url=discovery_url,
            scopes=scopes,
            user_id_claim="sub",
            username_claim="preferred_username",
            email_claim="email",
            issuer=discovery.get("issuer"),
            authorization_endpoint=discovery.get("authorization_endpoint"),
            token_endpoint=discovery.get("token_endpoint"),
            jwks_uri=discovery.get("jwks_uri"),
        )
        db.add(config)
        await db.commit()

    _sso_config_seeded = True


async def _upsert_sso_profile(
    db,
    user_id: str,
    provider_name: str,
    sub_claim: str,
    email: str | None,
) -> None:
    """Create or update the SSOUserProfile for a successful login."""
    result = await db.exec(
        select(SSOUserProfile).where(
            SSOUserProfile.sso_provider == provider_name,
            SSOUserProfile.sso_user_id == sub_claim,
        )
    )
    profile = result.first()
    now = datetime.now(timezone.utc)
    if profile is None:
        profile = SSOUserProfile(
            user_id=user_id,
            sso_provider=provider_name,
            sso_user_id=sub_claim,
            email=email,
            sso_last_login_at=now,
        )
        db.add(profile)
    else:
        profile.sso_last_login_at = now
        if email and profile.email != email:
            profile.email = email
        db.add(profile)
    await db.commit()


# ── routes ────────────────────────────────────────────────────────────────────


@router.get("/config", response_model=OidcConfig, include_in_schema=False)
async def oidc_config() -> OidcConfig:
    """Return whether OIDC SSO is configured on this server."""
    return OidcConfig(enabled=_is_sso_configured())


@router.get("/authorize", include_in_schema=False)
async def oidc_authorize(request: Request, db: DbSession) -> RedirectResponse:
    """Redirect the browser to the OIDC provider to begin the authorization code flow."""
    _, _, client_id, _, scopes = _get_initial_sso_env()
    if not _is_sso_configured():
        raise HTTPException(status_code=501, detail="SSO is not configured on this server.")

    await _ensure_sso_config(db)
    discovery = await _fetch_discovery()

    backend_base = str(request.base_url).rstrip("/")
    callback_url = f"{backend_base}/api/v1/login/oidc/callback"

    csrf_token = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()

    state_payload = json.dumps({
        "csrf": csrf_token,
        "verifier": verifier,
        "return_to": _frontend_origin(request),
    })

    params = urlencode({
        "client_id": client_id,
        "response_type": "code",
        "scope": scopes,
        "redirect_uri": callback_url,
        "state": csrf_token,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    auth_url = f"{discovery['authorization_endpoint']}?{params}"

    resp = RedirectResponse(url=auth_url, status_code=302)
    resp.set_cookie("oidc_state", state_payload, max_age=300, httponly=True, samesite="lax")
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
    """Handle the OIDC authorization code, provision/login the user."""

    raw_cookie = request.cookies.get("oidc_state", "")
    try:
        state_data = json.loads(raw_cookie) if raw_cookie else {}
    except (json.JSONDecodeError, ValueError):
        state_data = {}

    return_to = state_data.get("return_to", str(request.base_url).rstrip("/"))

    if error:
        desc = error_description or error
        return RedirectResponse(url=f"{return_to}/login?sso_error={desc}", status_code=302)

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter")

    if not state_data or state_data.get("csrf") != state:
        raise HTTPException(status_code=400, detail="Invalid state parameter — possible CSRF attempt")

    provider_name, _, client_id, client_secret, _ = _get_initial_sso_env()
    discovery = await _fetch_discovery()

    backend_base = str(request.base_url).rstrip("/")
    callback_url = f"{backend_base}/api/v1/login/oidc/callback"

    async with httpx.AsyncClient() as hc:
        token_resp = await hc.post(
            discovery["token_endpoint"],
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
        raise HTTPException(status_code=401, detail=f"Token exchange failed: {token_resp.text}")

    kc_tokens = token_resp.json()
    access_token = kc_tokens.get("access_token", "")
    id_token = kc_tokens.get("id_token", "")

    # Role-based access: only agent_producer and platform_admin may use the builder.
    realm_roles = _realm_roles_from_token(access_token)
    if not realm_roles.intersection(_ALLOWED_ROLES):
        error_msg = "Access denied: your account does not have permission to use the Langflow builder."
        logout_params: dict[str, str] = {
            "client_id": client_id,
            "post_logout_redirect_uri": f"{return_to}/login",
        }
        if id_token:
            logout_params["id_token_hint"] = id_token
        end_session_endpoint = discovery.get("end_session_endpoint", "")
        logout_url = f"{end_session_endpoint}?{urlencode(logout_params)}"
        resp = RedirectResponse(url=logout_url, status_code=302)
        resp.set_cookie("sso_error", error_msg, httponly=False, samesite="lax", max_age=60)
        return resp

    async with httpx.AsyncClient() as hc:
        userinfo_resp = await hc.get(
            discovery["userinfo_endpoint"],
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if userinfo_resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Failed to fetch user info from SSO provider")

    userinfo = userinfo_resp.json()

    sub_claim: str = userinfo.get("sub", "")
    username: str = userinfo.get("preferred_username") or sub_claim
    email: str | None = userinfo.get("email") or None

    if not sub_claim:
        raise HTTPException(status_code=400, detail="SSO provider did not return a sub claim")

    is_platform_admin = "platform_admin" in realm_roles
    auth = get_auth_service()

    # Primary: resolve via SSOUserProfile (stable sub → Langflow user.id link).
    result = await db.exec(
        select(SSOUserProfile).where(
            SSOUserProfile.sso_provider == provider_name,
            SSOUserProfile.sso_user_id == sub_claim,
        )
    )
    sso_profile = result.first()

    user: User | None = None
    if sso_profile is not None:
        user = await db.get(User, sso_profile.user_id)
        if user is None:
            sso_profile = None

    if user is None:
        user = await get_user_by_username(db, username)

    if user is None:
        user = User(
            username=username,
            # Random password — SSO users never authenticate with it directly.
            password=auth.get_password_hash(secrets.token_urlsafe(32)),
            is_active=True,
            is_superuser=is_platform_admin,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        await get_or_create_default_folder(db, user.id)
    elif user.is_superuser != is_platform_admin:
        user.is_superuser = is_platform_admin
        db.add(user)
        await db.commit()
        await db.refresh(user)

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Your account is inactive. Contact a platform administrator.")

    await _upsert_sso_profile(db, user_id=str(user.id), provider_name=provider_name, sub_claim=sub_claim, email=email)

    lf_tokens = await auth.create_user_tokens(user_id=user.id, db=db, update_last_login=True)
    auth_settings = get_settings_service().auth_settings

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
    final.set_cookie(
        "sso_provider",
        provider_name,
        httponly=False,
        samesite="lax",
        secure=auth_settings.ACCESS_SECURE,
        expires=auth_settings.ACCESS_TOKEN_EXPIRE_SECONDS,
    )
    if id_token:
        final.set_cookie(
            "kc_id_token",
            id_token,
            httponly=False,
            samesite="lax",
            secure=auth_settings.ACCESS_SECURE,
            expires=auth_settings.ACCESS_TOKEN_EXPIRE_SECONDS,
        )
    return final


@router.get("/logout", include_in_schema=False)
async def oidc_logout(request: Request) -> RedirectResponse:
    """Clear Langflow auth cookies and end the SSO session."""
    if not _is_sso_configured():
        raise HTTPException(status_code=501, detail="SSO is not configured on this server.")

    _, _, client_id, _, _ = _get_initial_sso_env()
    discovery = await _fetch_discovery()
    frontend_origin = _frontend_origin(request)
    id_token = request.cookies.get("kc_id_token", "")

    params: dict[str, str] = {
        "client_id": client_id,
        "post_logout_redirect_uri": f"{frontend_origin}/login",
    }
    if id_token:
        params["id_token_hint"] = id_token

    end_session_endpoint = discovery.get("end_session_endpoint", "")
    logout_url = f"{end_session_endpoint}?{urlencode(params)}"

    resp = RedirectResponse(url=logout_url, status_code=302)
    resp.delete_cookie("sso_provider")
    resp.delete_cookie("kc_id_token")
    resp.delete_cookie("access_token_lf")
    resp.delete_cookie("refresh_token_lf")
    return resp
