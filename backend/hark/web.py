from __future__ import annotations

import base64
import json
import mimetypes
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import load_settings
from .service import ExecutionDisabled, InvalidTask, RunService
from .store import CapacityError, NotFoundError, Store


API_DEMO_RE = re.compile(r"^/api/demos/([A-Za-z0-9_-]{32})$")
API_RUN_RE = re.compile(r"^/api/demos/([A-Za-z0-9_-]{32})/runs$")
API_INVALIDATE_RE = re.compile(
    r"^/api/demos/([A-Za-z0-9_-]{32})/experiences/([0-9a-f-]{36})/invalidate$"
)
ASSET_RE = re.compile(r"^/assets/(styles\.css|app\.js)$")
_MODULE = Path(__file__).resolve()
FRONTEND = next(
    candidate
    for candidate in (_MODULE.parents[1] / "frontend", _MODULE.parents[2] / "frontend")
    if candidate.is_dir()
)


@lru_cache(maxsize=1)
def service() -> RunService:
    return RunService(load_settings())


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET").upper()
    path = event.get("rawPath") or "/"

    try:
        if method == "GET" and path == "/health":
            store = Store(load_settings())
            database_ok = store.ping()
            return _json(200, {"status": "ok" if database_ok else "degraded", "database": database_ok})

        if method == "POST" and path == "/api/demos":
            demo_id = service().create_demo()
            return _json(201, {"demo_id": demo_id, "url": f"/demo/{demo_id}"})

        match = API_DEMO_RE.fullmatch(path)
        if method == "GET" and match:
            return _json(200, service().store.get_demo(match.group(1)))

        match = API_RUN_RE.fullmatch(path)
        if method == "POST" and match:
            payload = _body(event)
            task = payload.get("task")
            if not isinstance(task, str):
                raise InvalidTask("A task string is required.")
            return _json(200, service().execute(match.group(1), task))

        match = API_INVALIDATE_RE.fullmatch(path)
        if method == "POST" and match:
            changed = service().store.invalidate_experience(match.group(1), match.group(2))
            if not changed:
                raise NotFoundError("That active experience was not found.")
            return _json(200, service().store.get_demo(match.group(1)))

        match = ASSET_RE.fullmatch(path)
        if method == "GET" and match:
            return _static(FRONTEND / match.group(1))

        if method == "GET" and (path == "/" or path.startswith("/demo/")):
            return _static(FRONTEND / "index.html", no_cache=True)

        return _json(404, {"error": "NOT_FOUND", "message": "The requested Hark route does not exist."})
    except InvalidTask as exc:
        return _json(422, {"error": "UNSUPPORTED_TASK", "message": str(exc)})
    except NotFoundError as exc:
        return _json(404, {"error": "DEMO_NOT_FOUND", "message": str(exc)})
    except CapacityError as exc:
        return _json(429, {"error": "CAPACITY_REACHED", "message": str(exc)})
    except ExecutionDisabled as exc:
        return _json(503, {"error": "EXECUTION_PAUSED", "message": str(exc)})
    except json.JSONDecodeError:
        return _json(400, {"error": "INVALID_JSON", "message": "Request body must be valid JSON."})
    except Exception:
        return _json(500, {"error": "INTERNAL_ERROR", "message": "Hark could not complete this request."})


def _body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise json.JSONDecodeError("object required", raw, 0)
    return value


def _json(status: int, value: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
            **_security_headers(),
        },
        "body": json.dumps(value, separators=(",", ":"), default=str),
    }


def _static(path: Path, no_cache: bool = False) -> dict[str, Any]:
    if not path.is_file() or path.parent != FRONTEND:
        return _json(404, {"error": "NOT_FOUND", "message": "Asset not found."})
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "statusCode": 200,
        "headers": {
            "content-type": f"{content_type}; charset=utf-8",
            "cache-control": "no-store" if no_cache else "public, max-age=3600",
            **_security_headers(),
        },
        "body": path.read_text(encoding="utf-8"),
    }


def _security_headers() -> dict[str, str]:
    return {
        "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        "referrer-policy": "no-referrer",
        "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "strict-transport-security": "max-age=31536000; includeSubDomains",
    }
