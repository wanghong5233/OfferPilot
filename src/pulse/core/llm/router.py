from __future__ import annotations

import os
from typing import Any, Callable

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

RouteDefaults = dict[str, tuple[str, str]]
ClientFactory = Callable[[str, str, str], Any]

DEFAULT_ROUTE_MODELS: RouteDefaults = {
    "default": ("qwen-plus", "qwen3-max"),
    "classification": ("qwen-plus", "qwen3-max"),
    "planning": ("qwen-plus", "qwen3-max"),
    "generation": ("qwen-plus", "qwen3-max"),
    "cheap": ("qwen-turbo", "qwen-plus"),
}


def _route_env_prefix(route: str) -> str:
    key = "".join(ch if ch.isalnum() else "_" for ch in str(route or "default").upper())
    return f"MODEL_ROUTE_{key}"


def _dedupe_models(models: list[str]) -> list[str]:
    return list(dict.fromkeys([m.strip() for m in models if isinstance(m, str) and m.strip()]))


def _read_env(*keys: str) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class LLMRouter:
    """Route-aware model router with fallback and structured output support."""

    def __init__(
        self,
        *,
        route_defaults: RouteDefaults | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        defaults = dict(DEFAULT_ROUTE_MODELS)
        if route_defaults:
            defaults.update(route_defaults)
        if "default" not in defaults:
            raise ValueError("route_defaults must include 'default'")
        self._route_defaults = defaults
        self._client_factory = client_factory

    def route_default_pair(self, route: str) -> tuple[str, str]:
        return self._route_defaults.get(route, self._route_defaults["default"])

    def candidate_models(self, route: str = "default") -> list[str]:
        normalized = str(route or "default").strip() or "default"
        default_primary, default_fallback = self.route_default_pair(normalized)
        global_primary = _read_env("MODEL_PRIMARY", "PULSE_MODEL_PRIMARY") or self._route_defaults["default"][0]
        global_fallback = _read_env("MODEL_FALLBACK", "PULSE_MODEL_FALLBACK") or self._route_defaults["default"][1]
        prefix = _route_env_prefix(normalized)
        route_primary = _read_env(f"{prefix}_PRIMARY", f"PULSE_{prefix}_PRIMARY")
        route_fallback = _read_env(f"{prefix}_FALLBACK", f"PULSE_{prefix}_FALLBACK")

        return _dedupe_models(
            [
                route_primary or global_primary,
                route_fallback or global_fallback,
                global_primary,
                global_fallback,
                default_primary,
                default_fallback,
            ]
        )

    def resolve_api_config(self, model: str = "") -> tuple[str, str]:
        """Resolve (base_url, api_key) with auto-detection by model name prefix."""
        model_lower = model.strip().lower()

        if model_lower.startswith(("gpt-", "gpt4", "o1-", "o3-", "o4-", "chatgpt")):
            key = _read_env("OPENAI_API_KEY")
            if key:
                return _read_env("OPENAI_BASE_URL") or "https://api.openai.com/v1", key

        if model_lower.startswith("qwen"):
            key = _read_env("DASHSCOPE_API_KEY", "QWEN_API_KEY")
            if key:
                url = _read_env("OPENAI_COMPAT_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
                return url, key

        if model_lower.startswith("deepseek"):
            key = _read_env("DEEPSEEK_API_KEY")
            if key:
                return _read_env("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1", key

        pulse_key = _read_env("PULSE_MODEL_API_KEY")
        if pulse_key:
            base_url = _read_env("PULSE_MODEL_BASE_URL", "OPENAI_COMPAT_BASE_URL")
            return base_url or "https://api.openai.com/v1", pulse_key

        openai_key = _read_env("OPENAI_API_KEY")
        if openai_key:
            return _read_env("OPENAI_BASE_URL") or "https://api.openai.com/v1", openai_key

        dashscope_key = _read_env("DASHSCOPE_API_KEY", "QWEN_API_KEY")
        if dashscope_key:
            url = _read_env("OPENAI_COMPAT_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            return url, dashscope_key

        deepseek_key = _read_env("DEEPSEEK_API_KEY")
        if deepseek_key:
            return _read_env("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1", deepseek_key

        raise RuntimeError(
            "No model API key found. Set OPENAI_API_KEY, "
            "DASHSCOPE_API_KEY/QWEN_API_KEY, or DEEPSEEK_API_KEY."
        )

    def build_client(self, model: str) -> Any:
        base_url, api_key = self.resolve_api_config(model)
        if self._client_factory is not None:
            return self._client_factory(model, base_url, api_key)
        return ChatOpenAI(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=0.1,
            timeout=60,
            max_retries=1,
        )

    @staticmethod
    def coerce_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            return "\n".join(parts).strip()
        return str(content)

    def invoke_structured(self, prompt_value: Any, schema: type[Any], *, route: str = "default") -> Any:
        errors: list[str] = []
        for model in self.candidate_models(route):
            try:
                llm = self.build_client(model).with_structured_output(schema)
                return llm.invoke(prompt_value)
            except Exception as exc:  # pragma: no cover - environment dependent
                errors.append(f"{model}: {exc}")
        raise RuntimeError(
            f"All models failed for structured output (route={route}): " + " | ".join(errors)
        )

    def invoke_text(self, prompt_value: Any, *, route: str = "default") -> str:
        errors: list[str] = []
        for model in self.candidate_models(route):
            try:
                message = self.build_client(model).invoke(prompt_value)
                if isinstance(message, AIMessage):
                    return self.coerce_text(message.content)
                return self.coerce_text(message)
            except Exception as exc:  # pragma: no cover - environment dependent
                errors.append(f"{model}: {exc}")
        raise RuntimeError(f"All models failed for text output (route={route}): " + " | ".join(errors))

    def invoke_chat(
        self,
        messages: list[Any],
        *,
        tools: list[dict[str, Any]] | None = None,
        route: str = "default",
    ) -> AIMessage:
        """Chat-style invocation with optional tool calling for ReAct loops."""
        errors: list[str] = []
        for model in self.candidate_models(route):
            try:
                client = self.build_client(model)
                if tools:
                    client = client.bind_tools(tools)
                result = client.invoke(messages)
                if isinstance(result, AIMessage):
                    return result
                return AIMessage(content=self.coerce_text(result))
            except Exception as exc:
                errors.append(f"{model}: {exc}")
        raise RuntimeError(f"All models failed for chat (route={route}): " + " | ".join(errors))
