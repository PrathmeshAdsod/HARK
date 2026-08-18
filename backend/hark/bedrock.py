from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class InferenceUnavailable(RuntimeError):
    pass


@dataclass
class AgentResult:
    diagnosis: str
    tool_calls: int
    failures: int
    actions: list[str]
    evidence: dict[str, Any]
    input_tokens: int
    output_tokens: int


class BedrockGateway:
    def __init__(self, region: str, model_id: str, embedding_model_id: str, dimensions: int):
        import boto3

        self.client = boto3.client("bedrock-runtime", region_name=region)
        self.model_id = model_id
        self.embedding_model_id = embedding_model_id
        self.dimensions = dimensions

    def embed(self, text: str) -> tuple[list[float], int]:
        try:
            response = self.client.invoke_model(
                modelId=self.embedding_model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(
                    {
                        "inputText": text[:50000],
                        "dimensions": self.dimensions,
                        "normalize": True,
                    }
                ),
            )
            payload = json.loads(response["body"].read())
            embedding = payload.get("embedding")
            if not isinstance(embedding, list) or len(embedding) != self.dimensions:
                raise InferenceUnavailable("Embedding response had an unexpected shape.")
            return [float(value) for value in embedding], int(payload.get("inputTextTokenCount", 0))
        except InferenceUnavailable:
            raise
        except Exception as exc:
            raise InferenceUnavailable(_safe_model_error(exc)) from exc

    def converse(
        self,
        *,
        system_text: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 420,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "modelId": self.model_id,
            "system": [{"text": system_text}],
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": 0,
                "topP": 0.9,
            },
        }
        if tools:
            request["toolConfig"] = {"tools": tools, "toolChoice": {"any": {}}}
        try:
            return self.client.converse(**request)
        except Exception as exc:
            raise InferenceUnavailable(_safe_model_error(exc)) from exc


def _safe_model_error(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error", {})
        code = str(error.get("Code", "BedrockError"))
        message = str(error.get("Message", "Inference request failed."))
        return f"{code}: {message}"[:300]
    return f"{type(exc).__name__}: inference request failed"[:300]


def tool_spec(name: str, description: str) -> dict[str, Any]:
    return {
        "toolSpec": {
            "name": name,
            "description": description,
            "inputSchema": {"json": {"type": "object", "properties": {}}},
        }
    }
