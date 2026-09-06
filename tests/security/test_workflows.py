from sqlalchemy import func, select

from mock_bank.models.entities import ServiceRequest


def prepare(client, headers, aid):
    response = client.post("/service-requests/checkbook/prepare", headers=headers, json={"account_id": aid})
    assert response.status_code == 200, response.text
    return response.json()["action_id"]


def test_confirmation_otp_and_idempotency(signed):
    c, f, a, b, aid, _ = signed
    action = prepare(c, a, aid)
    payload = {"action_id": action, "idempotency_key": "unique-test-key-0001"}
    assert c.post("/service-requests/checkbook/submit", headers=a, json=payload).status_code == 409
    assert c.post(f"/actions/{action}/confirm", headers=b).status_code == 404
    assert c.post(f"/actions/{action}/confirm", headers=a).status_code == 200
    assert c.post("/service-requests/checkbook/submit", headers=a, json=payload).status_code == 409
    assert (
        c.post("/auth/verify-otp", headers=a, json={"action_id": action, "otp": "654321"}).status_code == 200
    )
    first = c.post("/service-requests/checkbook/submit", headers=a, json=payload)
    assert first.status_code == 200, first.text
    second = c.post("/service-requests/checkbook/submit", headers=a, json=payload)
    assert second.json() == first.json()
    assert c.get("/service-requests/" + first.json()["reference"], headers=b).status_code == 404
    with f() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(ServiceRequest).where(ServiceRequest.action_id == action)
            )
            == 1
        )


def test_wrong_otp_locks(signed):
    c, _, a, _, aid, _ = signed
    action = prepare(c, a, aid)
    c.post(f"/actions/{action}/confirm", headers=a)
    for _ in range(3):
        assert (
            c.post("/auth/verify-otp", headers=a, json={"action_id": action, "otp": "000000"}).status_code
            == 400
        )
    assert (
        c.post("/auth/verify-otp", headers=a, json={"action_id": action, "otp": "654321"}).status_code == 429
    )


def test_cancel_prevents_submission(signed):
    c, _, a, _, aid, _ = signed
    action = prepare(c, a, aid)
    c.post(f"/actions/{action}/cancel", headers=a)
    assert c.post(f"/actions/{action}/confirm", headers=a).status_code == 409
