import pytest


@pytest.mark.parametrize("suffix", ["", "/balance", "/transactions"])
def test_other_customer_account_denied(signed, suffix):
    client, _, a, _, _, bid = signed
    assert client.get(f"/accounts/{bid}{suffix}", headers=a).status_code == 404


def test_other_customer_statement_denied(signed):
    client, _, a, _, _, bid = signed
    assert (
        client.post(
            f"/accounts/{bid}/statements",
            headers=a,
            json={"start_date": "2026-01-01", "end_date": "2026-02-01"},
        ).status_code
        == 404
    )


def test_unauthenticated_rejected(bank):
    client, _ = bank
    assert client.get("/customers/me/accounts").status_code == 401


def test_refresh_rotation_and_logout(bank):
    client, _ = bank
    tokens = client.post("/auth/login", data={"username": "demo01", "password": "SyntheticDemo!42"}).json()
    new = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert new.status_code == 200
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    headers = {"Authorization": "Bearer " + new.json()["access_token"]}
    assert client.post("/auth/logout", headers=headers).status_code == 200
    assert client.get("/auth/me", headers=headers).status_code == 401
