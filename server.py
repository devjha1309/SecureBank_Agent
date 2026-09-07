"""Authenticated banking API entrypoint; use agent.py for all five services."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("apps.banking_api.main:app", host="127.0.0.1", port=8000, access_log=False)
