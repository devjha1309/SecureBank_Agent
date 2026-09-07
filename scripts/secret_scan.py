"""Scan tracked source without displaying suspected secrets."""

import re
import subprocess
from pathlib import Path

patterns = [
    re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{24,}"),
    re.compile(rb"AKIA[A-Z0-9]{16}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
files = subprocess.check_output(["git", "ls-files", "-z"]).split(b"\0")
failed = []
for item in files:
    if not item:
        continue
    path = Path(item.decode())
    if path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example":
        failed.append(str(path))
        continue
    if path.is_file() and any(p.search(path.read_bytes()) for p in patterns):
        failed.append(str(path))
if failed:
    print("Potential secrets found in: " + ", ".join(failed))
    raise SystemExit(1)
print("Tracked-file secret scan passed.")
