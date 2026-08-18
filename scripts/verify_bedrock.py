from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from hark.bedrock import BedrockGateway, InferenceUnavailable  # noqa: E402
from hark.config import load_settings  # noqa: E402


def main() -> None:
    settings = load_settings()
    gateway = BedrockGateway(settings.aws_region, settings.bedrock_model_id)
    result = {"authorized": False, "converse": False}
    try:
        result["authorized"] = gateway.is_authorized()
        if result["authorized"]:
            response = gateway.converse(
                system_text="Reply with the single word ready.",
                messages=[{"role": "user", "content": [{"text": "Status check"}]}],
                max_tokens=8,
            )
            result["converse"] = bool(response.get("output", {}).get("message"))
    except InferenceUnavailable as exc:
        result["provider_error"] = str(exc)
    passed = result["authorized"] and result["converse"]
    print(json.dumps({"passed": passed, "checks": result}, indent=2, sort_keys=True))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
