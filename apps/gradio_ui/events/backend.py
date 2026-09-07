"""Backend-for-frontend: encrypted, expiring server-side authentication sessions."""

import secrets
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

from core.config.settings import get_settings
from core.errors import BankError
from core.memory.store import store

COOKIE = "securebank_ui"


def cipher() -> Fernet:
    path = Path(".local/ui-key")
    path.parent.mkdir(mode=0o700, exist_ok=True)
    try:
        with path.open("xb") as file:
            path.chmod(0o600)
            file.write(Fernet.generate_key())
    except FileExistsError:
        pass
    return Fernet(path.read_bytes())


def get_session(cookies) -> tuple[str, dict]:
    key = cookies.get(COOKIE, "")
    value = store.get("ui:" + key) if key else None
    if not value:
        raise BankError("AUTH_REQUIRED", "Please sign in again.", 401)
    return key, value


def save(key: str, value: dict) -> None:
    store.put("ui:" + key, value, 900)


async def api(cookies, method: str, path: str, **kwargs):
    key, session = get_session(cookies)
    access = cipher().decrypt(session["sealed_access"].encode()).decode()
    async with httpx.AsyncClient(base_url=get_settings().banking_api_url, timeout=40) as c:
        response = await c.request(method, path, headers={"Authorization": "Bearer " + access}, **kwargs)
    if response.status_code >= 400:
        try:
            message = response.json()["safe_message"]
        except Exception:
            message = "The banking service is unavailable. Please try again."
        raise BankError("BANKING_REQUEST_FAILED", message, response.status_code)
    return response


async def login(username: str, password: str) -> str:
    async with httpx.AsyncClient(base_url=get_settings().banking_api_url, timeout=10) as c:
        response = await c.post("/auth/login", data={"username": username, "password": password})
    if response.status_code != 200:
        raise BankError("INVALID_CREDENTIALS", "Sign-in failed. Check your demo credentials.", 401)
    token = response.json()["access_token"]
    key = secrets.token_urlsafe(32)
    save(
        key,
        {
            "sealed_access": cipher().encrypt(token.encode()).decode(),
            "chat_id": None,
            "account_map": {},
            "download_reference": None,
        },
    )
    return key
