from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class InferenceUnavailable(RuntimeError):
    pass


class ProviderRequestInvalid(RuntimeError):
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
    def __init__(self, region: str, model_id: str):
        import boto3

        self.control_client = boto3.client("bedrock", region_name=region)
        self.runtime_client = boto3.client("bedrock-runtime", region_name=region)
        self.model_id = model_id

    def is_authorized(self) -> bool:
        try:
            response = self.control_client.get_foundation_model_availability(
                modelId=self.model_id
            )
            return bool(
                response.get("authorizationStatus") == "AUTHORIZED"
                and response.get("entitlementAvailability") == "AVAILABLE"
                and response.get("regionAvailability") == "AVAILABLE"
            )
        except Exception as exc:
            raise InferenceUnavailable(_safe_model_error(exc, "Bedrock availability check")) from exc

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
            response = self.runtime_client.converse(**request)
            response["provider"] = "amazon-bedrock"
            response["model"] = self.model_id
            return response
        except Exception as exc:
            message = _safe_model_error(exc, "Bedrock inference")
            if _is_provider_failure(exc):
                raise InferenceUnavailable(message) from exc
            raise ProviderRequestInvalid(message) from exc


def _is_provider_failure(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return isinstance(exc, (TimeoutError, ConnectionError)) or type(exc).__name__ in {
            "ConnectTimeoutError",
            "ConnectionClosedError",
            "EndpointConnectionError",
            "HTTPClientError",
            "ProxyConnectionError",
            "ReadTimeoutError",
        }
    error = response.get("Error", {})
    code = str(error.get("Code", ""))
    message = str(error.get("Message", "")).lower()
    if code == "ValidationException":
        return "operation not allowed" in message or "not authorized" in message
    return code in {
        "AccessDeniedException",
        "InternalServerException",
        "ModelErrorException",
        "ModelNotReadyException",
        "ModelStreamErrorException",
        "ModelTimeoutException",
        "ServiceUnavailableException",
        "ThrottlingException",
    }


def _safe_model_error(exc: Exception, action: str = "Bedrock request") -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error", {})
        code = str(error.get("Code", "BedrockError"))
        message = str(error.get("Message", f"{action} failed."))
        return f"{code}: {message}"[:300]
    return f"{type(exc).__name__}: {action} failed"[:300]


def tool_spec(name: str, description: str) -> dict[str, Any]:
    return {
        "toolSpec": {
            "name": name,
            "description": description,
            "inputSchema": {"json": {"type": "object", "properties": {}}},
        }
    }
