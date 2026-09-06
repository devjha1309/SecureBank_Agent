import os

os.environ["MODEL_PROVIDER"] = "mock"
os.environ["ALLOW_EXTERNAL_LLM"] = "false"
os.environ["MOUNT_UI"] = "false"
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from apps.banking_api.app import app
from mock_bank.database.session import get_db
from mock_bank.models.entities import Base, CustomerAccount
from mock_bank.seed_data import seed as seed_module


@pytest.fixture
def bank(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(seed_module, "SessionLocal", factory)
    seed_module.seed()

    def db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = db
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, factory
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def signed(bank):
    client, factory = bank

    def login(name):
        response = client.post("/auth/login", data={"username": name, "password": "SyntheticDemo!42"})
        assert response.status_code == 200, response.text
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    a, b = login("demo01"), login("demo02")
    with factory() as db:
        aid = db.scalar(select(CustomerAccount.account_id).where(CustomerAccount.customer_id == "CUST-001"))
        bid = db.scalar(select(CustomerAccount.account_id).where(CustomerAccount.customer_id == "CUST-002"))
    return client, factory, a, b, aid, bid
