from __future__ import annotations

import json

from hark.web import lambda_handler


def event(method: str, path: str, body: str | None = None):
    return {
        "requestContext": {"http": {"method": method}},
        "rawPath": path,
        "body": body,
        "isBase64Encoded": False,
    }


def test_landing_is_served_with_security_headers():
    response = lambda_handler(event("GET", "/"), None)
    assert response["statusCode"] == 200
    assert "Execution memory for Agent Skills" in response["body"]
    assert response["headers"]["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response["headers"]["content-security-policy"]


def test_unknown_route_is_json_404():
    response = lambda_handler(event("GET", "/does-not-exist"), None)
    assert response["statusCode"] == 404
    assert json.loads(response["body"])["error"] == "NOT_FOUND"
