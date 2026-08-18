from __future__ import annotations

import json
import socket
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .bedrock import BedrockGateway, InferenceUnavailable, ProviderRequestInvalid
from .config import Settings


BudgetConsumer = Callable[[str, str, int, int], bool]


class GeminiGateway:
    api_base = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str, budget_consumer: BudgetConsumer):
        self.api_key = api_key
        self.budget_consumer = budget_consumer

    def embed(
        self,
        *,
        run_id: str,
        text: str,
        model: str,
        dimensions: int,
        task_type: str,
        daily_budget: int,
        per_minute_limit: int,
    ) -> tuple[list[float], int]:
        payload = {
            "model": f"models/{model}",
            "content": {"parts": [{"text": text[:30000]}]},
            "taskType": task_type,
            "outputDimensionality": dimensions,
        }
        response = self._request(
            run_id=run_id,
            model=model,
            method="embedContent",
            payload=payload,
            budget_key=f"gemini:{model}:embedding",
            daily_budget=daily_budget,
            per_minute_limit=per_minute_limit,
        )
        values = response.get("embedding", {}).get("values")
        if not isinstance(values, list) or len(values) != dimensions:
            raise ProviderRequestInvalid("Gemini embedding response had an unexpected shape.")
        return [float(value) for value in values], 0

    def converse(
        self,
        *,
        run_id: str,
        model: str,
        system_text: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        daily_budget: int,
        per_minute_limit: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_text}]},
            "contents": _gemini_contents(messages),
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if tools:
            declarations = []
            names = []
            for item in tools:
                spec = item["toolSpec"]
                names.append(str(spec["name"]))
                declarations.append(
                    {
                        "name": spec["name"],
                        "description": spec["description"],
                        "parameters": spec["inputSchema"]["json"],
                    }
                )
            payload["tools"] = [{"functionDeclarations": declarations}]
            payload["toolConfig"] = {
                "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": names}
            }

        response = self._request(
            run_id=run_id,
            model=model,
            method="generateContent",
            payload=payload,
            budget_key=f"gemini:{model}:reasoning",
            daily_budget=daily_budget,
            per_minute_limit=per_minute_limit,
        )
        candidates = response.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ProviderRequestInvalid("Gemini returned no candidate response.")
        parts = candidates[0].get("content", {}).get("parts", [])
        content: list[dict[str, Any]] = []
        for part in parts:
            call = part.get("functionCall")
            if isinstance(call, dict):
                content.append(
                    {
                        "toolUse": {
                            "toolUseId": str(call.get("id") or "gemini-call"),
                            "name": str(call.get("name") or ""),
                            "input": call.get("args") or {},
                        }
                    }
                )
            elif part.get("text"):
                content.append({"text": str(part["text"])})
        usage = response.get("usageMetadata", {})
        return {
            "provider": "google-gemini",
            "model": model,
            "usage": {
                "inputTokens": int(usage.get("promptTokenCount", 0) or 0),
                "outputTokens": int(usage.get("candidatesTokenCount", 0) or 0),
            },
            "output": {"message": {"role": "assistant", "content": content}},
        }

    def _request(
        self,
        *,
        run_id: str,
        model: str,
        method: str,
        payload: dict[str, Any],
        budget_key: str,
        daily_budget: int,
        per_minute_limit: int,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise InferenceUnavailable("Gemini credentials are not configured.")
        if not self.budget_consumer(run_id, budget_key, daily_budget, per_minute_limit):
            raise InferenceUnavailable(f"Hark's request budget for {model} is exhausted.")
        url = f"{self.api_base}/{quote(model, safe='.-_')}:{method}"
        request = Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=25) as result:
                return json.loads(result.read().decode("utf-8"))
        except HTTPError as exc:
            reason = _google_error_reason(exc)
            message = f"Gemini provider request failed with HTTP {exc.code} ({reason})."
            if exc.code in {401, 403, 408, 409, 429, 500, 502, 503, 504}:
                raise InferenceUnavailable(message) from exc
            raise ProviderRequestInvalid(message) from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise InferenceUnavailable("Gemini provider request timed out or was unreachable.") from exc


class ProviderRouter:
    def __init__(
        self,
        settings: Settings,
        budget_consumer: BudgetConsumer,
        *,
        bedrock: BedrockGateway | None = None,
        gemini: GeminiGateway | None = None,
    ):
        self.settings = settings
        self.bedrock = bedrock or BedrockGateway(settings.aws_region, settings.bedrock_model_id)
        self.gemini = gemini or GeminiGateway(settings.gemini_api_key, budget_consumer)
        self._bedrock_authorized = False
        self._bedrock_status_expires_at = 0.0

    def embed_query(self, run_id: str, text: str) -> tuple[list[float], int]:
        return self._embed(run_id, text, "RETRIEVAL_QUERY")

    def embed_document(self, run_id: str, text: str) -> tuple[list[float], int]:
        return self._embed(run_id, text, "RETRIEVAL_DOCUMENT")

    def _embed(self, run_id: str, text: str, task_type: str) -> tuple[list[float], int]:
        return self.gemini.embed(
            run_id=run_id,
            text=text,
            model=self.settings.embedding_model_id,
            dimensions=self.settings.embedding_dimensions,
            task_type=task_type,
            daily_budget=self.settings.gemini_embedding_request_budget_per_day,
            per_minute_limit=self.settings.gemini_embedding_request_limit_per_minute,
        )

    def converse(
        self,
        *,
        run_id: str,
        system_text: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 420,
    ) -> dict[str, Any]:
        if self._bedrock_is_authorized():
            try:
                return self.bedrock.converse(
                    system_text=system_text,
                    messages=messages,
                    tools=tools,
                    max_tokens=max_tokens,
                )
            except InferenceUnavailable:
                self._mark_bedrock_unhealthy()

        try:
            return self.gemini.converse(
                run_id=run_id,
                model=self.settings.gemini_primary_model_id,
                system_text=system_text,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                daily_budget=self.settings.gemini_primary_request_budget_per_day,
                per_minute_limit=self.settings.gemini_primary_request_limit_per_minute,
            )
        except InferenceUnavailable:
            return self.gemini.converse(
                run_id=run_id,
                model=self.settings.gemini_tertiary_model_id,
                system_text=system_text,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                daily_budget=self.settings.gemini_tertiary_request_budget_per_day,
                per_minute_limit=self.settings.gemini_tertiary_request_limit_per_minute,
            )

    def _bedrock_is_authorized(self) -> bool:
        now = time.monotonic()
        if now < self._bedrock_status_expires_at:
            return self._bedrock_authorized
        try:
            self._bedrock_authorized = self.bedrock.is_authorized()
        except InferenceUnavailable:
            self._bedrock_authorized = False
        self._bedrock_status_expires_at = now + self.settings.bedrock_health_ttl_seconds
        return self._bedrock_authorized

    def _mark_bedrock_unhealthy(self) -> None:
        self._bedrock_authorized = False
        self._bedrock_status_expires_at = (
            time.monotonic() + self.settings.bedrock_health_ttl_seconds
        )


def _gemini_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contents = []
    for message in messages:
        parts = []
        for item in message.get("content", []):
            if item.get("text"):
                parts.append({"text": str(item["text"])})
        if parts:
            role = "model" if message.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": parts})
    if not contents:
        raise ProviderRequestInvalid("The reasoning request contained no text content.")
    return contents


def _google_error_reason(exc: HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        status = payload.get("error", {}).get("status")
        if status:
            return str(status)[:80]
    except Exception:
        pass
    return "PROVIDER_ERROR"
