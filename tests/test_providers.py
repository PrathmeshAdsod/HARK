from __future__ import annotations

import pytest

from hark.bedrock import InferenceUnavailable, ProviderRequestInvalid, _is_provider_failure
from hark.providers import GeminiGateway, ProviderRouter

from test_service import settings


def response(provider: str, model: str) -> dict:
    return {
        "provider": provider,
        "model": model,
        "usage": {"inputTokens": 1, "outputTokens": 1},
        "output": {"message": {"role": "assistant", "content": [{"text": "verified"}]}},
    }


class FakeBedrock:
    def __init__(self, authorized: bool, fail: bool = False):
        self.authorized = authorized
        self.fail = fail
        self.availability_calls = 0
        self.converse_calls = 0

    def is_authorized(self):
        self.availability_calls += 1
        return self.authorized

    def converse(self, **kwargs):
        self.converse_calls += 1
        if self.fail:
            raise InferenceUnavailable("Bedrock unavailable")
        return response("amazon-bedrock", "amazon.nova-micro-v1:0")


class FakeGemini:
    def __init__(self, fail_models=()):
        self.fail_models = set(fail_models)
        self.reasoning_models = []
        self.embedding_calls = []

    def converse(self, *, model, **kwargs):
        self.reasoning_models.append(model)
        if model in self.fail_models:
            raise InferenceUnavailable(f"{model} unavailable")
        return response("google-gemini", model)

    def embed(self, *, model, dimensions, task_type, **kwargs):
        self.embedding_calls.append((model, dimensions, task_type))
        return [1.0] + [0.0] * (dimensions - 1), 0


def call_router(router: ProviderRouter):
    return router.converse(
        run_id="run-1",
        system_text="system",
        messages=[{"role": "user", "content": [{"text": "diagnose"}]}],
    )


def test_known_bedrock_unavailable_skips_inference_and_uses_primary_gemini():
    bedrock = FakeBedrock(authorized=False)
    gemini = FakeGemini()
    router = ProviderRouter(settings(), lambda *args: True, bedrock=bedrock, gemini=gemini)

    result = call_router(router)
    second = call_router(router)

    assert result["model"] == "gemini-3.5-flash-lite"
    assert second["model"] == "gemini-3.5-flash-lite"
    assert bedrock.converse_calls == 0
    assert bedrock.availability_calls == 1


def test_bedrock_remains_preferred_when_authorized():
    bedrock = FakeBedrock(authorized=True)
    gemini = FakeGemini()
    router = ProviderRouter(settings(), lambda *args: True, bedrock=bedrock, gemini=gemini)

    assert call_router(router)["provider"] == "amazon-bedrock"
    assert gemini.reasoning_models == []


def test_provider_failure_falls_back_primary_then_tertiary():
    bedrock = FakeBedrock(authorized=True, fail=True)
    gemini = FakeGemini(fail_models={"gemini-3.5-flash-lite"})
    router = ProviderRouter(settings(), lambda *args: True, bedrock=bedrock, gemini=gemini)

    result = call_router(router)

    assert result["model"] == "gemini-3.1-flash-lite"
    assert gemini.reasoning_models == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]


def test_all_provider_failures_surface_without_fabricated_success():
    gemini = FakeGemini(
        fail_models={"gemini-3.5-flash-lite", "gemini-3.1-flash-lite"}
    )
    router = ProviderRouter(
        settings(),
        lambda *args: True,
        bedrock=FakeBedrock(authorized=False),
        gemini=gemini,
    )

    with pytest.raises(InferenceUnavailable):
        call_router(router)


def test_canonical_embedding_uses_one_model_and_exact_dimensions():
    gemini = FakeGemini()
    router = ProviderRouter(
        settings(),
        lambda *args: True,
        bedrock=FakeBedrock(authorized=False),
        gemini=gemini,
    )

    query, _ = router.embed_query("run-1", "query")
    document, _ = router.embed_document("run-1", "document")

    assert len(query) == len(document) == 256
    assert gemini.embedding_calls == [
        ("gemini-embedding-2", 256, "RETRIEVAL_QUERY"),
        ("gemini-embedding-2", 256, "RETRIEVAL_DOCUMENT"),
    ]


def test_server_budget_denial_prevents_provider_request():
    gateway = GeminiGateway("configured", lambda *args: False)

    with pytest.raises(InferenceUnavailable, match="budget"):
        gateway.embed(
            run_id="run-1",
            text="query",
            model="gemini-embedding-2",
            dimensions=256,
            task_type="RETRIEVAL_QUERY",
            daily_budget=150,
            per_minute_limit=90,
        )


def test_invalid_embedding_shape_is_not_silently_mixed(monkeypatch):
    gateway = GeminiGateway("configured", lambda *args: True)
    monkeypatch.setattr(gateway, "_request", lambda **kwargs: {"embedding": {"values": [1.0]}})

    with pytest.raises(ProviderRequestInvalid, match="shape"):
        gateway.embed(
            run_id="run-1",
            text="query",
            model="gemini-embedding-2",
            dimensions=256,
            task_type="RETRIEVAL_QUERY",
            daily_budget=150,
            per_minute_limit=90,
        )


def test_bedrock_provider_failures_are_distinct_from_invalid_requests():
    class BedrockError(Exception):
        def __init__(self, code, message=""):
            self.response = {"Error": {"Code": code, "Message": message}}

    assert _is_provider_failure(BedrockError("ModelTimeoutException"))
    assert _is_provider_failure(
        BedrockError("ValidationException", "Operation not allowed for this account")
    )
    assert not _is_provider_failure(
        BedrockError("ValidationException", "Malformed tool input schema")
    )

    EndpointConnectionError = type("EndpointConnectionError", (Exception,), {})
    assert _is_provider_failure(EndpointConnectionError())
