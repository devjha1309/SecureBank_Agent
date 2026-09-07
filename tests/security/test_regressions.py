from concurrent.futures import ThreadPoolExecutor

import jwt
import pytest
from sqlalchemy import func, select

from core.auth.security import secret
from mock_bank.models.entities import AuthSession, OTPChallenge, ServiceRequest, Transaction, now
from tests.security.test_workflows import prepare


@pytest.mark.parametrize("claim,value", [("iss", "evil"), ("aud", "other"), ("exp", 1)])
def test_invalid_jwt_claims(signed, claim, value):
    c, _, a, _, _, _ = signed
    claims = jwt.decode(a["Authorization"][7:], options={"verify_signature": False})
    claims[claim] = value
    token = jwt.encode(claims, secret(), algorithm="HS256")
    assert c.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_expired_session(signed):
    c, f, a, _, _, _ = signed
    claims = jwt.decode(a["Authorization"][7:], options={"verify_signature": False})
    with f.begin() as db:
        db.get(AuthSession, claims["sid"]).expires_at = 1
    assert c.get("/customers/me/accounts", headers=a).status_code == 401


def test_expired_otp(signed):
    c, f, a, _, aid, _ = signed
    action = prepare(c, a, aid)
    c.post(f"/actions/{action}/confirm", headers=a)
    with f.begin() as db:
        db.scalar(select(OTPChallenge).where(OTPChallenge.action_id == action)).expires_at = now() - 1
    assert (
        c.post("/auth/verify-otp", headers=a, json={"action_id": action, "otp": "654321"}).status_code == 409
    )


def test_parallel_submissions_create_one_request(signed):
    c, f, a, _, aid, _ = signed
    action = prepare(c, a, aid)
    c.post(f"/actions/{action}/confirm", headers=a)
    c.post("/auth/verify-otp", headers=a, json={"action_id": action, "otp": "654321"})

    def submit(_):
        return c.post(
            "/service-requests/checkbook/submit",
            headers=a,
            json={"action_id": action, "idempotency_key": "parallel-test-key-123"},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(submit, range(2)))
    assert 200 in statuses and set(statuses) <= {200, 409}
    with f() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(ServiceRequest).where(ServiceRequest.action_id == action)
            )
            == 1
        )


@pytest.mark.parametrize("kind", ["credit-limit", "report"])
def test_other_sensitive_workflows(signed, kind):
    c, f, a, _, aid, _ = signed
    with f() as db:
        tid = db.scalar(select(Transaction.id).where(Transaction.account_id == aid))
    path = "/service-requests/credit-limit" if kind == "credit-limit" else f"/transactions/{tid}/report"
    body = {"account_id": aid, "requested_limit": 50000} if kind == "credit-limit" else {"account_id": aid}
    r = c.post(path + "/prepare", headers=a, json=body)
    assert r.status_code == 200, r.text
    action = r.json()["action_id"]
    payload = {"action_id": action, "idempotency_key": action}
    assert c.post(path + "/submit", headers=a, json=payload).status_code == 409
    c.post(f"/actions/{action}/confirm", headers=a)
    c.post("/auth/verify-otp", headers=a, json={"action_id": action, "otp": "654321"})
    assert c.post(path + "/submit", headers=a, json=payload).status_code == 200


def test_transaction_and_download_ownership(signed):
    c, f, a, b, aid, bid = signed
    with f() as db:
        tid = db.scalar(select(Transaction.id).where(Transaction.account_id == bid))
    assert c.get("/transactions/" + tid, headers=a).status_code == 404
    r = c.post(
        f"/accounts/{aid}/statements", headers=a, json={"start_date": "2026-01-01", "end_date": "2026-09-07"}
    )
    assert r.status_code == 200, r.text
    url = r.json()["download_url"]
    assert c.get(url, headers=a).status_code == 200
    assert c.get(url, headers=b).status_code == 404
    assert c.get(url).status_code == 401


def test_staff_cannot_access_accounts(bank):
    c, _ = bank
    t = c.post("/auth/login", data={"username": "demo11", "password": "SyntheticDemo!42"}).json()[
        "access_token"
    ]
    assert c.get("/customers/me/accounts", headers={"Authorization": "Bearer " + t}).status_code == 403
