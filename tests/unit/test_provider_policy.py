from types import SimpleNamespace

import pytest

from core.config.settings import get_settings
from core.model_router import router


@pytest.mark.parametrize(
    "sensitive,long_text,expected",
    [(True, True, "ollama"), (False, False, "ollama"), (False, True, "anthropic")],
)
async def test_provider_policy(monkeypatch, sensitive, long_text, expected):
    settings = get_settings()
    for key, value in {
        "model_provider": "openai",
        "allow_external_llm": True,
        "local_model_provider": "ollama",
        "external_model_provider": "anthropic",
        "external_model_name": "configured-deployment",
    }.items():
        monkeypatch.setattr(settings, key, value)
    seen = []

    class FakeModel:
        def with_structured_output(self, *args, **kwargs):
            return self

        async def ainvoke(self, messages):
            assert "person@example.test" not in messages[-1][1]
            return {
                "raw": SimpleNamespace(usage_metadata={"total_tokens": 10}),
                "parsed": router.IntentPlan(intents=["balance"], confidence=1),
            }

    def build(provider, name):
        seen.append(provider)
        return FakeModel()

    monkeypatch.setattr(router, "build_model", build)
    await router.classify(
        "balance for person@example.test "
        + ("Please explain this complex request. " * 15 if long_text else ""),
        sensitive=sensitive,
    )
    assert seen == [expected]
