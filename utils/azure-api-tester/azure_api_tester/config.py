"""Configuration management for Azure API Tester."""

import os
import random
import string
import json
import subprocess
from pathlib import Path
from typing import Optional

import yaml


CONFIG_PATH = Path.home() / ".azure-api-tester/azure-config.yaml"
DATA_DIR = Path.home() / ".azure-api-tester"


def ensure_data_dir() -> Path:
    """Ensure the data directory exists."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "logs").mkdir(exist_ok=True)
    return DATA_DIR


def load_config() -> dict:
    """Load configuration from ~/.azure-api-tester/azure-config.yaml."""
    if not CONFIG_PATH.exists():
        return {"defaults": {}, "overrides": {}}

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f) or {}

    return {
        "defaults": config.get("defaults", {}),
        "overrides": config.get("overrides", {}),
        "settings": config.get("settings", {}),
    }


def _generate_random_suffix(length: int = 6) -> str:
    """Generate a random alphanumeric suffix."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _get_subscription_id() -> Optional[str]:
    """Get the current subscription ID from az CLI."""
    try:
        result = subprocess.run(
            ["az", "account", "show", "--query", "id", "--output", "tsv"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _get_az_account_info() -> dict:
    """Get full Azure account info."""
    try:
        result = subprocess.run(
            ["az", "account", "show", "--output", "json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def resolve_value(param_name: str, config: dict, overrides: dict[str, str]) -> tuple[Optional[str], str]:
    """Resolve a parameter value from CLI overrides, config, or auto-detection.

    Priority: CLI overrides > config overrides > config defaults > auto-detection
    Returns (value, source) where source describes where the value came from.
    """
    # CLI overrides (--param key=value)
    if param_name in overrides:
        value = overrides[param_name]
        if "{random}" in value:
            value = value.replace("{random}", _generate_random_suffix())
        return value, "--param"

    # Config overrides section
    config_overrides = config.get("overrides", {})
    if param_name in config_overrides:
        value = str(config_overrides[param_name])
        if "{random}" in value:
            value = value.replace("{random}", _generate_random_suffix())
        return value, "config (overrides)"

    # Config defaults section
    defaults = config.get("defaults", {})
    if param_name in defaults:
        value = str(defaults[param_name])
        if value == "auto" and param_name == "subscriptionId":
            sub_id = _get_subscription_id()
            return sub_id, "auto (az account)" if sub_id else (None, "")
        if "{random}" in value:
            value = value.replace("{random}", _generate_random_suffix())
        return value, "config (defaults)"

    # Auto-detect subscriptionId
    if param_name == "subscriptionId":
        sub_id = _get_subscription_id()
        return (sub_id, "auto (az account)") if sub_id else (None, "")

    # Auto-detect common Azure params
    if param_name == "api-version":
        return None, ""  # Usually already in the URL

    return None, ""


def prompt_for_value(param_name: str, description: str = "", default: str = "") -> str:
    """Interactively prompt the user for a parameter value."""
    prompt = f"  Enter value for '{param_name}'"
    if description:
        prompt += f" ({description})"
    if default:
        prompt += f" [{default}]"
    prompt += ": "

    value = input(prompt).strip()
    return value if value else default


def resolve_all_uri_params(
    uri_params: list,
    config: dict,
    cli_overrides: dict[str, str],
    interactive: bool = True,
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Resolve all URI parameters, prompting for any that are missing.

    Returns:
        (resolved, sources, missing) where:
        - resolved: dict of param_name -> resolved_value
        - sources: dict of param_name -> source description
        - missing: list of param names that could not be resolved
    """
    resolved = {}
    sources = {}
    missing = []

    for param in uri_params:
        name = param.name if hasattr(param, "name") else param
        desc = param.description if hasattr(param, "description") else ""

        # Skip api-version — it's in the URL template already
        if name == "api-version":
            continue

        value, source = resolve_value(name, config, cli_overrides)

        if value is None and interactive:
            value = prompt_for_value(name, desc)
            if value:
                source = "interactive"

        if value:
            resolved[name] = value
            sources[name] = source
        else:
            missing.append(name)

    return resolved, sources, missing


def substitute_url(url_template: str, params: dict[str, str]) -> str:
    """Substitute URI parameters into the URL template."""
    url = url_template
    for name, value in params.items():
        url = url.replace(f"{{{name}}}", value)
    return url
