"""Auto-cleanup of resources created during testing."""

import time
import json
from typing import Optional

import requests

from .api_caller import execute_call, get_cached_token
from .tracker import Tracker


def cleanup_resource(
    url: str,
    tracker: Tracker,
    run_id: str,
    token: Optional[str] = None,
    max_poll_seconds: int = 300,
    poll_interval: int = 10,
) -> dict:
    """Delete a resource and poll for async completion.

    Args:
        url: The resource URL (same URL used for PUT/POST)
        tracker: Tracker instance for logging
        run_id: Current test run ID
        token: Optional pre-acquired token
        max_poll_seconds: Max time to wait for async deletion
        poll_interval: Seconds between async status polls

    Returns:
        dict with status, body, duration_ms
    """
    if token is None:
        token = get_cached_token(url)

    result = execute_call(
        method="DELETE",
        url=url,
        body=None,
        variant_name="cleanup-delete",
        tracker=tracker,
        run_id=run_id,
        is_cleanup=True,
        token=token,
    )

    # Check for async operation (202 Accepted)
    if result["status"] == 202:
        async_url = result["headers"].get(
            "Azure-AsyncOperation",
            result["headers"].get("Location", ""),
        )
        if async_url:
            _poll_async_operation(
                async_url, token, max_poll_seconds, poll_interval
            )

    return result


def _poll_async_operation(
    url: str,
    token: str,
    max_seconds: int = 300,
    interval: int = 10,
) -> Optional[str]:
    """Poll an Azure async operation URL until completion.

    Returns the final status string.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    elapsed = 0
    while elapsed < max_seconds:
        time.sleep(interval)
        elapsed += interval

        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                return f"poll-error-{resp.status_code}"

            data = resp.json()
            status = data.get("status", "").lower()

            if status in ("succeeded", "failed", "canceled", "cancelled"):
                return status

        except (requests.RequestException, json.JSONDecodeError):
            continue

    return "timeout"
