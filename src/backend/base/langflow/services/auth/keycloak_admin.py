"""Keycloak Admin REST API client used for self-service registration.

Authenticates with the confidential `agents-market-backend` service-account
client (client_credentials grant), which is granted the realm-management
`manage-users`, `view-users` and `query-users` roles.

If the signup behaviour here changes, update docs/user-registration.md.

Configuration (env vars):

  KEYCLOAK_CLUSTER_INTERNAL_URL         cluster-internal Keycloak base URL
  KEYCLOAK_REALM                        realm name (default: agents-market)
  AGENTS_MARKET_BACKEND_CLIENT_ID       service-account client id
  AGENTS_MARKET_BACKEND_CLIENT_SECRET   service-account client secret
"""

from __future__ import annotations

import os
import time
from http import HTTPStatus

import httpx
from loguru import logger

# Realm roles a user may pick for themselves during registration. Never accept a
# raw realm role name from the client — platform_admin must stay unreachable.
SIGNUP_ROLE_BY_ALIAS = {
    "producer": "agent_producer",
    "consumer": "agent_consumer",
}

_TIMEOUT = 10.0

_token_cache: dict[str, object] = {"access_token": None, "expires_at": 0.0}


class KeycloakAdminError(Exception):
    """Admin API call failed; carries an HTTP status to surface to the caller."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _base_url() -> str:
    return os.getenv("KEYCLOAK_CLUSTER_INTERNAL_URL", "http://keycloak:8080").rstrip("/")


def _realm() -> str:
    return os.getenv("KEYCLOAK_REALM", "agents-market")


def _client_id() -> str:
    return os.getenv("AGENTS_MARKET_BACKEND_CLIENT_ID", "agents-market-backend")


def _client_secret() -> str:
    return os.getenv("AGENTS_MARKET_BACKEND_CLIENT_SECRET", "")


def is_configured() -> bool:
    """Whether the service-account client credentials are available."""
    return bool(_client_secret())


async def _admin_token(client: httpx.AsyncClient) -> str:
    now = time.time()
    cached = _token_cache["access_token"]
    if cached and now < float(_token_cache["expires_at"]):
        return str(cached)

    resp = await client.post(
        f"{_base_url()}/realms/{_realm()}/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": _client_id(),
            "client_secret": _client_secret(),
        },
        timeout=_TIMEOUT,
    )
    if resp.status_code != HTTPStatus.OK:
        logger.error(f"Keycloak service-account token request failed: {resp.status_code} {resp.text}")
        raise KeycloakAdminError(503, "Registration is temporarily unavailable.")

    data = resp.json()
    token = data["access_token"]
    _token_cache["access_token"] = token
    # Renew slightly early so a token never expires mid-request.
    _token_cache["expires_at"] = now + max(0, int(data.get("expires_in", 60)) - 15)
    return token


def _keycloak_error_detail(resp: httpx.Response, fallback: str) -> str:
    """Extract Keycloak's human-readable error (password policy violations, ...)."""
    try:
        body = resp.json()
    except ValueError:
        return fallback
    return body.get("errorMessage") or body.get("error_description") or body.get("error") or fallback


async def register_user(
    *,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    role_alias: str,
) -> bool:
    """Create a Keycloak user, assign their realm role, and email a verification link.

    Returns whether the verification email was sent. Raises KeycloakAdminError on
    failure; the user is rolled back if role assignment fails so a producer is
    never left with only the realm's default role.
    """
    realm_role = SIGNUP_ROLE_BY_ALIAS[role_alias]
    if not is_configured():
        raise KeycloakAdminError(501, "Self-service registration is not configured on this server.")

    admin_base = f"{_base_url()}/admin/realms/{_realm()}"

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        headers = {"Authorization": f"Bearer {await _admin_token(client)}"}

        create_resp = await client.post(
            f"{admin_base}/users",
            headers=headers,
            json={
                # registrationEmailAsUsername is on for this realm: username == email.
                "username": email,
                "email": email,
                "firstName": first_name,
                "lastName": last_name,
                "enabled": True,
                "emailVerified": False,
                "requiredActions": ["VERIFY_EMAIL"],
                "credentials": [{"type": "password", "value": password, "temporary": False}],
            },
        )
        if create_resp.status_code == HTTPStatus.CONFLICT:
            raise KeycloakAdminError(409, "An account with this email address already exists.")
        if create_resp.status_code == HTTPStatus.BAD_REQUEST:
            raise KeycloakAdminError(400, _keycloak_error_detail(create_resp, "Invalid registration details."))
        if create_resp.status_code != HTTPStatus.CREATED:
            logger.error(f"Keycloak user creation failed: {create_resp.status_code} {create_resp.text}")
            raise KeycloakAdminError(502, "Could not create the account. Please try again later.")

        user_id = create_resp.headers.get("location", "").rstrip("/").rsplit("/", 1)[-1]
        if not user_id:
            logger.error("Keycloak user creation returned no Location header")
            raise KeycloakAdminError(502, "Could not create the account. Please try again later.")

        try:
            role_resp = await client.get(f"{admin_base}/roles/{realm_role}", headers=headers)
            role_resp.raise_for_status()
            assign_resp = await client.post(
                f"{admin_base}/users/{user_id}/role-mappings/realm",
                headers=headers,
                json=[role_resp.json()],
            )
            assign_resp.raise_for_status()
        except httpx.HTTPError:
            logger.exception(f"Failed to assign realm role {realm_role}; rolling back user {user_id}")
            await client.delete(f"{admin_base}/users/{user_id}", headers=headers)
            raise KeycloakAdminError(502, "Could not create the account. Please try again later.") from None

        # Best effort: the account exists and is usable even if SMTP is down, and
        # Keycloak still forces VERIFY_EMAIL on first login.
        try:
            verify_resp = await client.put(f"{admin_base}/users/{user_id}/send-verify-email", headers=headers)
            email_sent = verify_resp.status_code < HTTPStatus.BAD_REQUEST
            if not email_sent:
                logger.warning(f"Verification email not sent for {email}: {verify_resp.status_code} {verify_resp.text}")
        except httpx.HTTPError:
            logger.exception(f"Verification email not sent for {email}")
            email_sent = False

    return email_sent
