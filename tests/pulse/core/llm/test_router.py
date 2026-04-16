from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from pulse.core.llm.router import LLMRouter


class _StructuredOutput(BaseModel):
    value: str


class _FakeClient:
    def __init__(self, model: str, responses: dict[str, Any]) -> None:
        self._model = model
        self._responses = responses

    def _resolve(self) -> Any:
        value = self._responses[self._model]
        if isinstance(value, Exception):
            raise value
        return value

    def invoke(self, _: Any) -> Any:
        return self._resolve()

    def with_structured_output(self, schema: type[BaseModel]) -> Any:
        parent = self

        class _StructuredInvoker:
            def invoke(self, _: Any) -> BaseModel:
                value = parent._resolve()
                if isinstance(value, schema):
                    return value
                if isinstance(value, dict):
                    return schema(**value)
                return schema(value=str(value))

        return _StructuredInvoker()


def _fake_factory(responses: dict[str, Any]):
    def _factory(model: str, base_url: str, api_key: str) -> _FakeClient:
        assert base_url
        assert api_key
        return _FakeClient(model, responses)

    return _factory


def test_candidate_models_uses_route_defaults_without_env() -> None:
    router = LLMRouter(
        route_defaults={
            "default": ("d1", "d2"),
            "classification": ("c1", "c2"),
        }
    )
    assert router.candidate_models("classification") == ["c1", "c2", "d1", "d2"]


def test_candidate_models_route_env_overrides_and_dedupes(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PRIMARY", "g1")
    monkeypatch.setenv("MODEL_FALLBACK", "g2")
    monkeypatch.setenv("MODEL_ROUTE_CLASSIFICATION_PRIMARY", "r1")
    monkeypatch.setenv("MODEL_ROUTE_CLASSIFICATION_FALLBACK", "g2")

    router = LLMRouter(
        route_defaults={
            "default": ("d1", "d2"),
            "classification": ("c1", "c2"),
        }
    )
    assert router.candidate_models("classification") == ["r1", "g2", "g1", "d1", "d2"]


def test_resolve_api_config_prefers_pulse_env(monkeypatch) -> None:
    monkeypatch.setenv("PULSE_MODEL_API_KEY", "sk-pulse")
    monkeypatch.setenv("PULSE_MODEL_BASE_URL", "https://example.invalid/v1")
    router = LLMRouter()
    base_url, api_key = router.resolve_api_config()
    assert base_url == "https://example.invalid/v1"
    assert api_key == "sk-pulse"


def test_coerce_text_supports_multimodal_list() -> None:
    value = LLMRouter.coerce_text(
        [
            {"type": "text", "text": "line1"},
            {"type": "image", "url": "x"},
            "line3",
        ]
    )
    assert value == "line1\n{'type': 'image', 'url': 'x'}\nline3"


def test_invoke_text_fallback_to_second_model(monkeypatch) -> None:
    monkeypatch.setenv("PULSE_MODEL_API_KEY", "sk-test")
    responses = {
        "m1": RuntimeError("first failed"),
        "m2": "ok-from-second",
    }
    router = LLMRouter(
        route_defaults={"default": ("m1", "m2")},
        client_factory=_fake_factory(responses),
    )
    assert router.invoke_text("hello") == "ok-from-second"


def test_invoke_structured_fallback_and_schema_parse(monkeypatch) -> None:
    monkeypatch.setenv("PULSE_MODEL_API_KEY", "sk-test")
    responses = {
        "m1": RuntimeError("first failed"),
        "m2": {"value": "ok-structured"},
    }
    router = LLMRouter(
        route_defaults={"default": ("m1", "m2")},
        client_factory=_fake_factory(responses),
    )
    output = router.invoke_structured("hello", _StructuredOutput)
    assert output.value == "ok-structured"
