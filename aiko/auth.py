"""Nova JWT auth for the aiko CLI (ADR-009).

Login: POST https://login.eramos.us/auth/login {identifier, password}
  -> {token (HS256 JWT, 7d), refresh_token, user}
The token is stored in ~/.aiko/credentials.yaml (0600) and attached as
Bearer to every aikod request.
"""
import time
from pathlib import Path

import httpx
import yaml

CRED_PATH = Path.home() / ".aiko" / "credentials.yaml"
NOVA_BASE = "https://login.eramos.us"


def load_token() -> str | None:
    if not CRED_PATH.exists():
        return None
    creds = yaml.safe_load(CRED_PATH.read_text()) or {}
    return creds.get("token")


def login(identifier: str, password: str, base: str = NOVA_BASE) -> dict:
    r = httpx.post(f"{base}/auth/login",
                   json={"identifier": identifier, "password": password},
                   timeout=15)
    r.raise_for_status()
    data = r.json()
    CRED_PATH.parent.mkdir(parents=True, exist_ok=True)
    CRED_PATH.write_text(yaml.safe_dump({
        "token": data["token"],
        "refresh_token": data["refresh_token"],
        "user": data["user"],
        "login_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, sort_keys=False))
    CRED_PATH.chmod(0o600)
    return data["user"]


def auth_headers() -> dict:
    tok = load_token()
    if not tok:
        raise SystemExit("Not logged in. Run: aiko login")
    return {"Authorization": f"Bearer {tok}"}
