"""Add missing local demo configuration without overwriting existing API keys."""

import secrets
from pathlib import Path

from dotenv import dotenv_values

path = Path(".env")
if not path.exists():
    path.write_text(Path(".env.example").read_text())
current = dotenv_values(path)
lines = path.read_text().splitlines()
for key, value in {
    "JWT_SECRET": secrets.token_urlsafe(48),
    "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
    "GRAFANA_PASSWORD": secrets.token_urlsafe(24),
}.items():
    if not current.get(key):
        lines = [line for line in lines if not line.startswith(key + "=")]
        lines.append(key + "=" + value)
path.write_text("\n".join(lines) + "\n")
path.chmod(0o600)
print("Local configuration ready. Existing API keys preserved; generated values were not displayed.")
