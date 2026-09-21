"""OIDC SSO endpoints for Langflow.

Adds four routes under /api/v1/login/oidc/:

  GET /authorize  — begins the authorization code flow with PKCE; redirects
                    the browser to the OIDC provider.

  GET /callback   — handles the authorization code, exchanges it for tokens,
                    checks realm roles, creates or looks up a Langflow user,
                    sets session cookies, and redirects to the frontend.

  GET /logout     — clears Langflow + SSO cookies and redirects the browser
                    to the provider's end-session endpoint.

  GET /marketplace?path=login|signup
                  — hands off to the marketplace, which owns consumer sign-in
                    and all signup.

Signup lives in the marketplace, not here — see docs/user-registration.md.

Configuration (set in .env.langflow).  Variable names align with SSOConfig
columns so the env-var source and the DB record use the same vocabulary:

  LANGFLOW_INITIAL_SSO_PROVIDER_NAME    e.g. keycloak       (SSOConfig.provider_name)
  LANGFLOW_INITIAL_SSO_DISCOVERY_URL    OIDC discovery URL  (SSOConfig.discovery_url)
  LANGFLOW_INITIAL_SSO_CLIENT_ID        client id           (SSOConfig.client_id)
  LANGFLOW_INITIAL_SSO_CLIENT_SECRET    client secret       (SSOConfig.client_secret_encrypted)
  LANGFLOW_INITIAL_SSO_SCOPES           space-separated     (SSOConfig.scopes, default: openid profile email)
  AGENTS_MARKET_FRONTEND_URL            marketplace frontend (signup redirect),
                                        default http://localhost:3001

On the first SSO request the env vars (plus endpoints fetched from the
discovery document) are written into the sso_config table so the
configuration is visible to platform admins without inspecting env vars.

Role-based access:
  Only users with the realm role "agent_producer", "platform_developer" or
  "platform_admin" — or the org role admin/producer in some organization — may
  access the Langflow builder.  Everyone else is signed back out of Keycloak
  and returned to /login with an access-denied message.  An account that has
  picked no role at all yet is instead sent to the marketplace's /onboarding
  screen, which is the place that can fix it.

Organization profiles:
  A user acts either personally or inside one organization, and each profile is
  its own Langflow user row ("alice@x.com#acme").  Flows, folders and the
  LITELLM_KEY variable are already scoped by user_id, so this keeps the two
  apart without touching a single query, and gives each profile its own LiteLLM
  key.  Switching profile re-runs /authorize with ?org_id=, so membership is
  re-checked against a freshly minted token every time rather than trusted from
  anything the browser holds.
  See 0to1-agents-market/docs/organizations-and-roles.md.

User identity tracking:
  A SSOUserProfile row is created / updated on every successful login,
  linking the Langflow user.id to the stable provider sub claim (per profile).  On
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
from urllib.parse import urlencode, urlparse, urlunparse

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
from loguru import logger

router = APIRouter(tags=["SSO"], prefix="/login/oidc")

_PLACEHOLDER_SECRET = "REPLACE_WITH_CLIENT_SECRET"

# Realm roles that are allowed to access the Langflow builder personally.
_ALLOWED_ROLES = {"agent_producer", "platform_admin", "platform_developer"}

# Every realm role this platform assigns. A token carrying none of them belongs
# to an account that hasn't picked a role yet, which is a different problem from
# being denied — see oidc_callback.
_KNOWN_REALM_ROLES = _ALLOWED_ROLES | {
    "agent_consumer",
    "platform_reviewer",
}

# Org roles that are allowed to access the builder under that org's profile.
_ORG_BUILDER_ROLES = {"admin", "producer"}

# Organization membership arrives as Keycloak group paths /orgs/<org-id>/<role>.
_ORG_GROUP_ROOT = "orgs"
_ORG_ROLES = ("admin", "producer", "consumer", "billing_manager")

# Separates the email from the org in a scoped Langflow username. Chosen because
# Keycloak forbids it in a username, so it can never occur in the email half.
SCOPE_SEPARATOR = "#"

# Marketplace pages the builder is allowed to hand off to.
_MARKETPLACE_PATHS = {"login", "signup"}


def parse_org_groups(groups: list[str] | None) -> dict[str, set[str]]:
    """Map Keycloak group paths to {org_id: {org_role, ...}}."""
    out: dict[str, set[str]] = {}
    for path in groups or []:
        parts = [p for p in str(path).split("/") if p]
        if len(parts) < 2 or parts[0] != _ORG_GROUP_ROOT:
            continue
        roles = out.setdefault(parts[1], set())
        if len(parts) >= 3 and parts[2] in _ORG_ROLES:
            roles.add(parts[2])
    return out


def scoped_username(username: str, org_id: str | None) -> str:
    """The Langflow username for `username` acting under `org_id`."""
    return f"{username}{SCOPE_SEPARATOR}{org_id}" if org_id else username


def split_scoped_username(scoped: str) -> tuple[str, str | None]:
    """Inverse of scoped_username: (username, org_id)."""
    if SCOPE_SEPARATOR not in scoped:
        return scoped, None
    username, _, org_id = scoped.rpartition(SCOPE_SEPARATOR)
    return username, org_id or None


def builder_profiles(realm_roles: set[str], orgs: dict[str, set[str]]) -> list[str | None]:
    """Profiles this identity may open the builder under, personal first.

    None is the personal profile. An empty result means "no builder access".
    """
    profiles: list[str | None] = []
    if realm_roles & _ALLOWED_ROLES:
        profiles.append(None)
    profiles.extend(
        org_id
        for org_id, roles in sorted(orgs.items())
        if roles & _ORG_BUILDER_ROLES
    )
    return profiles

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
        logger.info(f"Fetched OIDC discovery document from {discovery_url}")
        logger.info(f"Fetched OIDC discovery document:\n{json.dumps(_oidc_discovery_cache, indent=2)}")
    return _oidc_discovery_cache


# ── helpers ───────────────────────────────────────────────────────────────────


class IdTokenClaims(BaseModel):
    """Claims extracted from an OIDC id_token JWT payload."""

    sub: str
    email: str | None = None
    email_verified: bool | None = None
    name: str | None = None
    preferred_username: str | None = None
    given_name: str | None = None
    family_name: str | None = None


def _rewrite_url_origin(url: str) -> str:
    """Replace the scheme and netloc of *url* with LANGFLOW_KEYCLOAK_PUBLIC_URL.

    When the OIDC provider is behind a reverse proxy the discovery document
    returns internal URLs.  Set LANGFLOW_KEYCLOAK_PUBLIC_URL to the
    browser-facing origin so redirects point to the right place.
    """
    public_url = os.getenv("LANGFLOW_KEYCLOAK_PUBLIC_URL", "").rstrip("/")
    if not public_url:
        return url
    parsed_public = urlparse(public_url)
    return urlunparse(urlparse(url)._replace(scheme=parsed_public.scheme, netloc=parsed_public.netloc))


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


def _decode_jwt_payload(token: str) -> dict:
    """Decode the payload portion of a JWT without verifying signature."""
    try:
        raw = token.split(".")[1]
        raw += "=" * (4 - len(raw) % 4)
        return json.loads(base64.urlsafe_b64decode(raw))
    except Exception:
        return {}


def _realm_roles_from_token(access_token: str) -> set[str]:
    """Decode the JWT payload (no signature verification) and return realm roles."""
    payload = _decode_jwt_payload(access_token)
    return set(payload.get("realm_access", {}).get("roles", []))


def _org_groups_from_token(access_token: str) -> dict[str, set[str]]:
    """Organization membership from the token's `groups` claim."""
    payload = _decode_jwt_payload(access_token)
    return parse_org_groups(payload.get("groups"))



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
    scope: str = "",
) -> None:
    """Create or update the SSOUserProfile for a successful login.

    `scope` is the org id the login is scoped to ("" for personal): one provider
    identity has one profile row per profile it can act under.
    """
    result = await db.exec(
        select(SSOUserProfile).where(
            SSOUserProfile.sso_provider == provider_name,
            SSOUserProfile.sso_user_id == sub_claim,
            SSOUserProfile.sso_scope == scope,
        )
    )
    profile = result.first()

    if profile is None:
        # user_id is UNIQUE here (ix_sso_user_profile_user_id): a user has at
        # most one profile. A caller that asserts a different sub for a user who
        # already has one (e.g. the executor's internal/local paths, which don't
        # always know the real sub) must not insert a second row. Reuse the
        # existing profile instead, and keep its sso_user_id — never overwrite a
        # real SSO identity with an asserted one.
        result = await db.exec(select(SSOUserProfile).where(SSOUserProfile.user_id == user_id))
        profile = result.first()

    now = datetime.now(timezone.utc)
    if profile is None:
        profile = SSOUserProfile(
            user_id=user_id,
            sso_provider=provider_name,
            sso_user_id=sub_claim,
            sso_scope=scope,
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


# ── LiteLLM user sync ─────────────────────────────────────────────────────────


def _litellm_url() -> str:
    return os.getenv("LITELLM_URL", "http://localhost:4000").rstrip("/")


def _litellm_master_key() -> str:
    return os.getenv("LITELLM_MASTER_KEY", "")


async def _sync_user_to_litellm(db, user: User, username: str) -> None:
    """Create the user in LiteLLM, generate a key, and store it as LITELLM_KEY variable.

    Skipped for the 'admin' superuser — admin uses the master key directly.
    """
    email = username if "@" in username else f"{username}@agents-market.com"
    base_url = _litellm_url()
    master_key = _litellm_master_key()
    auth_headers = {"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"}
    list_headers = {"x-litellm-api-key": master_key}

    try:
        async with httpx.AsyncClient(timeout=15) as hc:
            # Check whether the user already exists in LiteLLM.
            list_resp = await hc.get(
                f"{base_url}/user/list",
                params={"page": 1, "page_size": 25, "user_email": email},
                headers=list_headers,
            )
            litellm_user_id: str | None = None
            if list_resp.status_code == 200:
                users = list_resp.json().get("users", [])
                existing = next((u for u in users if u.get("user_email") == email), None)
                if existing:
                    litellm_user_id = existing.get("user_id")

            if litellm_user_id is None:
                # Create the user in LiteLLM.
                create_resp = await hc.post(
                    f"{base_url}/user/new",
                    json={"auto_create_key": False, "models": ["no-default-models"], "user_email": email, "user_id": str(user.id), "user_role": "internal_user_viewer"},
                    headers=auth_headers,
                )
                create_resp.raise_for_status()
                litellm_user_id = create_resp.json().get("user_id")

            if not litellm_user_id:
                logger.warning("LiteLLM did not return a user_id for %s — skipping key generation", username)
                return

            # Ensure the "default" team exists and add the user to it.
            team_id: str | None = None
            team_list_resp = await hc.get(
                f"{base_url}/v2/team/list",
                params={"team_alias": "default", "page": 1, "page_size": 10, "sort_by": "created_at", "sort_order": "desc"},
                headers=auth_headers,
            )
            if team_list_resp.status_code == 200:
                teams = team_list_resp.json().get("teams", [])
                default_team = next((t for t in teams if t.get("team_alias") == "default"), None)
                if default_team:
                    team_id = default_team.get("team_id")

            if team_id is None:
                create_team_resp = await hc.post(
                    f"{base_url}/team/new",
                    json={
                        "team_alias": "default",
                        "organization_id": None,
                        "models": ["all-proxy-models"],
                        "router_settings": {
                            "routing_strategy": None,
                            "allowed_fails": None,
                            "cooldown_time": None,
                            "num_retries": None,
                            "timeout": None,
                            "retry_after": None,
                            "fallbacks": None,
                            "context_window_fallbacks": None,
                            "retry_policy": None,
                            "model_group_alias": None,
                            "enable_tag_filtering": False,
                            "routing_strategy_args": None,
                        },
                    },
                    headers=auth_headers,
                )
                create_team_resp.raise_for_status()
                team_id = create_team_resp.json().get("team_id")
                logger.info("Created LiteLLM default team: %s", team_id)

            if team_id:
                member_resp = await hc.post(
                    f"{base_url}/team/member_add",
                    json={
                        "team_id": team_id,
                        "member": {
                            "user_email": email,
                            "user_id": litellm_user_id,
                            "role": "user",
                        },
                    },
                    headers=auth_headers,
                )
                if member_resp.status_code not in (200, 409):
                    logger.warning("Unexpected status %s adding %s to default team", member_resp.status_code, username)
            else:
                logger.warning("Could not resolve default team id — skipping team membership for %s", username)

            # Generate an API key for the user.
            key_resp = await hc.post(
                f"{base_url}/key/generate",
                json={
                    "user_id": litellm_user_id,
                    "team_id": team_id,
                    "key_alias": email,
                    "models": ["all-team-models"],
                    "key_type": "llm_api",
                    "duration": None,
                    "metadata": {},
                },
                headers=auth_headers,
            )
            key_resp.raise_for_status()
            litellm_key = key_resp.json().get("key", "")

        if not litellm_key:
            logger.warning("LiteLLM returned empty key for user %s", username)
            return

        # Store the key as a private Langflow variable called LITELLM_KEY.
        from langflow.services.deps import get_variable_service
        from langflow.services.variable.constants import CREDENTIAL_TYPE
        from langflow.services.variable.service import DatabaseVariableService

        variable_service = get_variable_service()
        if isinstance(variable_service, DatabaseVariableService):
            var_name = "LITELLM_KEY"
            try:
                existing_var = await variable_service.get_variable_object(
                    user_id=user.id, name=var_name, session=db
                )
                from langflow.services.database.models.variable.model import VariableUpdate

                await variable_service.update_variable_fields(
                    user_id=user.id,
                    variable_id=existing_var.id,
                    variable=VariableUpdate(
                        id=existing_var.id,
                        name=var_name,
                        value=litellm_key,
                        type=CREDENTIAL_TYPE,
                        var_visibility="private",
                    ),
                    session=db,
                )
            except ValueError:
                db_var = await variable_service.create_variable(
                    user_id=user.id,
                    name=var_name,
                    value=litellm_key,
                    type_=CREDENTIAL_TYPE,
                    session=db,
                )
                db_var.var_visibility = "private"
                db.add(db_var)
                await db.flush()

        # Mark user as synced.
        user.synced_llm = True
        db.add(user)
        await db.commit()
        logger.info("LiteLLM sync complete for user %s", username)

    except Exception:
        logger.exception("Failed to sync user %s to LiteLLM — continuing login", username)


async def find_or_create_sso_user(
    db,
    *,
    sub_claim: str,
    username: str,
    email: str | None,
    is_platform_admin: bool,
    provider_name: str,
    org_id: str | None = None,
    sync_litellm: bool = True,
) -> User:
    """Find or create a Langflow User for an external SSO identity.

    Shared by the browser OIDC callback (oidc_callback below) and the
    internal POST /variables/internal/ensure-user endpoint, which the
    executor calls to lazily provision consumer users who are blocked
    from ever logging into the builder (see _ALLOWED_ROLES) and so would
    otherwise never get a Langflow User row or a LITELLM_KEY.

    `org_id` selects the profile. Each profile is a separate User row, so the
    caller's flows, folders and LITELLM_KEY are isolated per profile; the
    caller is responsible for having checked membership first.
    """
    auth = get_auth_service()
    scope = org_id or ""
    lf_username = scoped_username(username, org_id)

    # Primary: resolve via SSOUserProfile (stable sub + profile → user.id link).
    result = await db.exec(
        select(SSOUserProfile).where(
            SSOUserProfile.sso_provider == provider_name,
            SSOUserProfile.sso_user_id == sub_claim,
            SSOUserProfile.sso_scope == scope,
        )
    )
    sso_profile = result.first()

    user: User | None = None
    if sso_profile is not None:
        user = await db.get(User, sso_profile.user_id)
        if user is None:
            sso_profile = None

    if user is None:
        user = await get_user_by_username(db, lf_username)

    if user is None:
        user = User(
            username=lf_username,
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

    await _upsert_sso_profile(
        db,
        user_id=str(user.id),
        provider_name=provider_name,
        sub_claim=sub_claim,
        email=email,
        scope=scope,
    )

    # Sync non-admin users to LiteLLM on first login so they get a personal API key.
    # Keyed on the scoped username, so an org profile gets its own key and its
    # spend never lands on the personal one.
    if sync_litellm and username != "admin" and not getattr(user, "synced_llm", False):
        await _sync_user_to_litellm(db, user, lf_username)

    return user


# ── routes ────────────────────────────────────────────────────────────────────


@router.get("/marketplace", include_in_schema=False)
async def oidc_marketplace(path: str = "login") -> RedirectResponse:
    """Hand off to the marketplace, which owns consumer sign-in and all signup.

    `path` is allowlisted rather than passed through, so this cannot be used as
    an open redirect.
    """
    if path not in _MARKETPLACE_PATHS:
        raise HTTPException(status_code=400, detail="Unsupported marketplace path.")
    base = os.getenv("AGENTS_MARKET_FRONTEND_URL", "http://localhost:3001").rstrip("/")
    return RedirectResponse(url=f"{base}/{path}", status_code=302)


@router.get("/authorize", include_in_schema=False)
async def oidc_authorize(
    request: Request, db: DbSession, org_id: Optional[str] = None
) -> RedirectResponse:
    """Redirect the browser to the OIDC provider to begin the authorization code flow.

    `org_id` asks to land in that organization's profile. It is only a request:
    the callback checks it against the token Keycloak actually issues. Switching
    profile re-enters here, which is a silent redirect while the Keycloak session
    is alive.
    """
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
        "org_id": org_id or "",
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
    auth_url = _rewrite_url_origin(f"{discovery['authorization_endpoint']}?{params}")

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

    # Role-based access: personal access needs a builder realm role, an org
    # profile needs admin/producer in that org. No usable profile ends the
    # Keycloak session rather than leaving a half-logged-in browser.
    realm_roles = _realm_roles_from_token(access_token)
    orgs = _org_groups_from_token(access_token)
    profiles = builder_profiles(realm_roles, orgs)
    requested_org = (state_data.get("org_id") or "") or None
    denied = not profiles or (requested_org is not None and requested_org not in profiles)

    # An account that has picked no role yet isn't denied, it's unfinished —
    # send it to the marketplace onboarding screen, keeping the Keycloak
    # session so the user doesn't have to sign in twice.
    if denied and not realm_roles.intersection(_KNOWN_REALM_ROLES):
        base = os.getenv("AGENTS_MARKET_FRONTEND_URL", "http://localhost:3001").rstrip("/")
        return RedirectResponse(url=f"{base}/onboarding", status_code=302)

    if denied:
        error_msg = (
            f"Access denied: your account has no producer role in '{requested_org}'."
            if profiles and requested_org
            else "Access denied: your account does not have permission to use the Langflow builder."
        )
        logout_params: dict[str, str] = {
            "client_id": client_id,
            "post_logout_redirect_uri": f"{return_to}/login",
        }
        if id_token:
            logout_params["id_token_hint"] = id_token
        end_session_endpoint = discovery.get("end_session_endpoint", "")
        
        logout_url = _rewrite_url_origin(f"{end_session_endpoint}?{urlencode(logout_params)}")

        resp = RedirectResponse(url=logout_url, status_code=302)
        resp.set_cookie("sso_error", error_msg, httponly=False, samesite="lax", max_age=60)
        return resp

    # Requested profile when allowed, else the first one they do have.
    active_org = requested_org if requested_org is not None else profiles[0]

    # Try to fetch userinfo from the provider; fall back to id_token claims.
    userinfo: dict = {}
    try:
        async with httpx.AsyncClient() as hc:
            userinfo_resp = await hc.get(
                discovery["userinfo_endpoint"],
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if userinfo_resp.status_code == 200:
            userinfo = userinfo_resp.json()
        else:
            logger.warning(
                "Userinfo endpoint returned %s, falling back to id_token claims",
                userinfo_resp.status_code,
            )
    except Exception:
        logger.warning("Failed to fetch userinfo from provider, falling back to id_token claims")

    if not userinfo and id_token:
        userinfo = _decode_jwt_payload(id_token)

    if not userinfo:
        raise HTTPException(status_code=401, detail="Failed to fetch user info from SSO provider")

    sub_claim: str = userinfo.get("sub", "")
    username: str = userinfo.get("preferred_username") or sub_claim
    email: str | None = userinfo.get("email") or None

    if not sub_claim:
        raise HTTPException(status_code=400, detail="SSO provider did not return a sub claim")

    is_platform_admin = "platform_admin" in realm_roles
    auth = get_auth_service()

    user = await find_or_create_sso_user(
        db,
        sub_claim=sub_claim,
        username=username,
        email=email,
        is_platform_admin=is_platform_admin,
        provider_name=provider_name,
        org_id=active_org,
    )

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
    # These two outlive the access token on purpose: the session survives for the
    # refresh-token lifetime, and logout needs them to spot an SSO session and to
    # pass id_token_hint. Expiring them sooner leaves the Keycloak session open.
    final.set_cookie(
        "sso_provider",
        provider_name,
        httponly=False,
        samesite="lax",
        secure=auth_settings.ACCESS_SECURE,
        expires=auth_settings.REFRESH_TOKEN_EXPIRE_SECONDS,
    )
    if id_token:
        final.set_cookie(
            "kc_id_token",
            id_token,
            httponly=False,
            samesite="lax",
            secure=auth_settings.ACCESS_SECURE,
            expires=auth_settings.REFRESH_TOKEN_EXPIRE_SECONDS,
        )
    # Readable by the frontend so the profile switcher can render without an
    # extra round trip. Display only — tampering with it buys nothing, because
    # /authorize re-derives the allowed profiles from a fresh Keycloak token.
    # base64 so the JSON survives cookie quoting rules intact.
    profiles_payload = json.dumps(
        {"active": active_org or "", "available": [p or "" for p in profiles]}
    )
    final.set_cookie(
        "sso_profiles",
        base64.urlsafe_b64encode(profiles_payload.encode()).decode(),
        httponly=False,
        samesite="lax",
        secure=auth_settings.ACCESS_SECURE,
        expires=auth_settings.REFRESH_TOKEN_EXPIRE_SECONDS,
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
    logout_url = _rewrite_url_origin(f"{end_session_endpoint}?{urlencode(params)}")

    resp = RedirectResponse(url=logout_url, status_code=302)
    auth_settings = get_settings_service().auth_settings
    resp.delete_cookie("sso_provider")
    resp.delete_cookie("kc_id_token")
    resp.delete_cookie("sso_profiles")
    # Attributes must match the ones used to set these, or the browser keeps them.
    resp.delete_cookie(
        "access_token_lf",
        httponly=auth_settings.ACCESS_HTTPONLY,
        samesite=auth_settings.ACCESS_SAME_SITE,
        secure=auth_settings.ACCESS_SECURE,
        domain=auth_settings.COOKIE_DOMAIN,
    )
    resp.delete_cookie(
        "refresh_token_lf",
        httponly=auth_settings.REFRESH_HTTPONLY,
        samesite=auth_settings.REFRESH_SAME_SITE,
        secure=auth_settings.REFRESH_SECURE,
        domain=auth_settings.COOKIE_DOMAIN,
    )
    resp.delete_cookie(
        "apikey_tkn_lflw",
        httponly=auth_settings.ACCESS_HTTPONLY,
        samesite=auth_settings.ACCESS_SAME_SITE,
        secure=auth_settings.ACCESS_SECURE,
        domain=auth_settings.COOKIE_DOMAIN,
    )
    return resp
