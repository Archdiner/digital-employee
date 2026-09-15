"""Linked accounts. Plain OAuth 2.0 authorization-code flow for Google Workspace and Microsoft 365.
Tokens live in the connections table and are refreshed on use. No SDKs: two token URLs and one table."""
import hashlib
import hmac
import os
import secrets
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

from . import db

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/")
STATE_KEY = (os.environ.get("ADMIN_PASSWORD") or "dev-state-key").encode()

PROVIDERS = {
    "google": {
        "label": "Google Workspace",
        "auth": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scopes": "openid email https://www.googleapis.com/auth/drive https://www.googleapis.com/auth/documents "
                  "https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/presentations",
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "extra": {"access_type": "offline", "prompt": "consent"},
        "whoami": ("https://openidconnect.googleapis.com/v1/userinfo", "email"),
    },
    "microsoft": {
        "label": "Microsoft 365",
        "auth": "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/organizations/oauth2/v2.0/token",
        "scopes": "openid email offline_access User.Read Files.ReadWrite.All Sites.ReadWrite.All",
        "client_id": os.environ.get("MS_CLIENT_ID", "cd3458ee-bdae-4f32-92e0-fe3057be0759"),
        "client_secret": os.environ.get("MS_CLIENT_SECRET", ""),
        "extra": {"response_mode": "query"},
        "whoami": ("https://graph.microsoft.com/v1.0/me", "userPrincipalName"),
    },
}


def configured(provider):
    p = PROVIDERS[provider]
    return bool(p["client_id"] and p["client_secret"] and "unset" not in (p["client_id"], p["client_secret"]))


def redirect_uri(provider):
    return f"{BASE_URL}/connect/{provider}/callback"


def _sign(payload: str) -> str:
    return hmac.new(STATE_KEY, payload.encode(), hashlib.sha256).hexdigest()[:32]


def make_state(employee_id, provider):
    payload = f"{employee_id}:{provider}:{secrets.token_urlsafe(8)}"
    return f"{payload}:{_sign(payload)}"


def parse_state(state):
    payload, _, sig = state.rpartition(":")
    if not hmac.compare_digest(sig, _sign(payload)):
        raise ValueError("bad state")
    eid, provider, _ = payload.split(":", 2)
    return int(eid), provider


def auth_url(employee_id, provider):
    p = PROVIDERS[provider]
    params = {"client_id": p["client_id"], "redirect_uri": redirect_uri(provider), "response_type": "code",
              "scope": p["scopes"], "state": make_state(employee_id, provider), **p["extra"]}
    return f"{p['auth']}?{urllib.parse.urlencode(params)}"


def exchange(provider, code):
    p = PROVIDERS[provider]
    r = requests.post(p["token"], data={"client_id": p["client_id"], "client_secret": p["client_secret"], "code": code,
                                        "grant_type": "authorization_code", "redirect_uri": redirect_uri(provider)}, timeout=30)
    r.raise_for_status()
    return r.json()


def save(employee_id, provider, tok):
    p = PROVIDERS[provider]
    url, field = p["whoami"]
    who = requests.get(url, headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=30).json().get(field)
    expires = datetime.now(timezone.utc) + timedelta(seconds=int(tok.get("expires_in", 3600)))
    db.q(
        """insert into connections (employee_id, provider, account, access_token, refresh_token, expires_at, scopes)
           values (%s, %s, %s, %s, %s, %s, %s)
           on conflict (employee_id, provider) do update set account = excluded.account, access_token = excluded.access_token,
             refresh_token = coalesce(excluded.refresh_token, connections.refresh_token), expires_at = excluded.expires_at, scopes = excluded.scopes""",
        (employee_id, provider, who, tok["access_token"], tok.get("refresh_token"), expires, tok.get("scope")),
    )
    return who


def get(employee_id, provider):
    return db.q("select * from connections where employee_id = %s and provider = %s", (employee_id, provider), one=True)


def list_for(employee_id):
    rows = db.q("select provider, account, created_at from connections where employee_id = %s", (employee_id,))
    return {r["provider"]: r for r in rows}


def token(employee_id, provider):
    """A valid access token, refreshed if it expires within a minute."""
    c = get(employee_id, provider)
    if not c:
        raise LookupError(f"no {provider} account linked to this employee")
    if c["expires_at"] and c["expires_at"] > datetime.now(timezone.utc) + timedelta(seconds=60):
        return c["access_token"]
    p = PROVIDERS[provider]
    r = requests.post(p["token"], data={"client_id": p["client_id"], "client_secret": p["client_secret"],
                                        "refresh_token": c["refresh_token"], "grant_type": "refresh_token",
                                        **({"scope": p["scopes"]} if provider == "microsoft" else {})}, timeout=30)
    r.raise_for_status()
    tok = r.json()
    expires = datetime.now(timezone.utc) + timedelta(seconds=int(tok.get("expires_in", 3600)))
    db.q("update connections set access_token = %s, refresh_token = coalesce(%s, refresh_token), expires_at = %s where id = %s",
         (tok["access_token"], tok.get("refresh_token"), expires, c["id"]))
    return tok["access_token"]


def delete(employee_id, provider):
    db.q("delete from connections where employee_id = %s and provider = %s", (employee_id, provider))
