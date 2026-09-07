"""End-to-end check against running services, using only synthetic credentials."""

import os

import httpx


def main():
    with httpx.Client(base_url=os.environ.get("BANKING_API_URL", "http://127.0.0.1:8000"), timeout=60) as c:
        r = c.post("/auth/login", data={"username": "demo01", "password": "SyntheticDemo!42"})
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = "Bearer " + r.json()["access_token"]
        accounts = c.get("/customers/me/accounts").json()["accounts"]
        aid = accounts[0]["account_id"]
        r = c.post("/chat", json={"message": "Show my balance and last five transactions", "account_id": aid})
        assert r.status_code == 200, r.text
        body = r.json()
        print("Combined query:", body["response"])
        assert "available balance" in body["response"] and len(body["transactions"]) == 5, body
        sid = body["session_id"]
        r = c.post("/chat", json={"message": "Request a checkbook", "session_id": sid})
        assert r.status_code == 200, r.text
        assert r.json()["pending_action"], r.text
        print("Proposal:", r.json()["pending_action"]["summary"])
        r = c.post(f"/chat/{sid}/confirm")
        assert r.status_code == 200, r.text
        r = c.post(f"/chat/{sid}/verify-otp", json={"otp": "654321"})
        assert r.status_code == 200, r.text
        assert "SR-" in r.json()["response"], r.text
        print("Confirmed result:", r.json()["response"])
        r = c.post("/chat", json={"message": "Explain bank charges", "session_id": sid})
        assert r.status_code == 200 and "Source:" in r.json()["response"], r.text
        print("Knowledge citation: passed")
        assert c.delete(f"/chat/{sid}").status_code == 200
        c.post("/auth/logout")
    print("End-to-end smoke passed.")


if __name__ == "__main__":
    main()
