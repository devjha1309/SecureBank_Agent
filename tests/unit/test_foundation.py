from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from core.config.settings import Settings
from mock_bank.models.entities import Account, Base


def test_schema_roundtrip():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Account(last_four="1234", kind="savings", current_paise=100, available_paise=100))
        db.commit()
        assert db.scalar(select(func.count()).select_from(Account)) == 1
        assert db.scalar(select(Account)).currency == "INR"


def test_mock_is_default():
    assert Settings(_env_file=None).model_provider == "mock"
    assert not Settings(_env_file=None).allow_external_llm
