"""Execute Azure REST API calls with token acquisition."""

import json
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from .tracker import Tracker, ApiCallRecord


def _get_access_token(resource: str) -> str:
    """Acquire an access token via az CLI."""
    result = subprocess.run(
        ["az", "account", "get-access-token", "--resource", resource,
         "--query", "accessToken", "--output", "tsv"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to acquire token for {resource}: {result.stderr.strip()}"
        )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError(f"Empty token returned for resource: {resource}")
    return token


def _detect_resource(url: str) -> str:
    """Detect the Azure resource scope from the URL."""
    resource_map = {
        "management.azure.com": "https://management.azure.com",
        "graph.microsoft.com": "https://graph.microsoft.com",
        "vault.azure.net": "https://vault.azure.net",
        "cognitiveservices.azure.com": "https://cognitiveservices.azure.com",
        "openai.azure.com": "https://cognitiveservices.azure.com",
        "search.windows.net": "https://search.azure.com",
        "ml.azure.com": "https://management.azure.com",
    }
    for domain, resource in resource_map.items():
        if domain in url:
            return resource
    return "https://management.azure.com"


def execute_call(
    method: str,
    url: str,
    body: Optional[dict],
    variant_name: str,
    tracker: Tracker,
    run_id: str,
    is_cleanup: bool = False,
    token: Optional[str] = None,
) -> dict:
    """Execute a single API call and log it.

    Returns a dict with: status, body, headers, duration_ms
    """
    # Acquire token if not provided
    if token is None:
        resource = _detect_resource(url)
        token = _get_access_token(resource)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Mask the token for logging
    logged_headers = {**headers, "Authorization": "Bearer <REDACTED>"}

    start = time.monotonic()
    try:
        resp = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=body if body else None,
            timeout=120,
        )
        duration_ms = (time.monotonic() - start) * 1000

        # Parse response body
        try:
            resp_body = resp.json()
            resp_body_str = json.dumps(resp_body, indent=2)
        except (json.JSONDecodeError, ValueError):
            resp_body = resp.text
            resp_body_str = resp.text

        resp_headers = dict(resp.headers)

    except requests.RequestException as e:
        duration_ms = (time.monotonic() - start) * 1000
        resp_body_str = json.dumps({"error": str(e)})
        resp_headers = {}
        resp = type("FakeResp", (), {"status_code": 0})()

    # Log the call
    record = ApiCallRecord(
        run_id=run_id,
        variant_name=variant_name,
        timestamp=datetime.now(timezone.utc).isoformat(),
        method=method,
        url=url,
        request_headers=logged_headers,
        request_body=body,
        response_status=resp.status_code,
        response_headers=resp_headers,
        response_body=resp_body_str,
        duration_ms=round(duration_ms, 2),
        is_cleanup=is_cleanup,
    )
    tracker.log_call(record)

    return {
        "status": resp.status_code,
        "body": resp_body_str,
        "headers": resp_headers,
        "duration_ms": round(duration_ms, 2),
        "variant_name": variant_name,
    }


def get_cached_token(url: str) -> str:
    """Get a token for the URL's resource scope. Can be reused across calls."""
    resource = _detect_resource(url)
    return _get_access_token(resource)
