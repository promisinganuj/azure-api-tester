"""Fetch and parse OpenAPI specs from Azure/azure-rest-api-specs to enrich dry-run output."""

import json
import os
import re
import time
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

SPECS_REPO = "Azure/azure-rest-api-specs"
GITHUB_API = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com"
CACHE_DIR = Path.home() / ".azure-api-tester" / "specs"
CACHE_TTL = 86400  # 24 hours


@dataclass
class OpenApiEnrichment:
    """Additional metadata extracted from the OpenAPI spec for a single operation."""
    spec_url: str = ""
    arm_id_fields: list[dict] = field(default_factory=list)  # [{name, resource_type, description}]
    confirmed_required: list[str] = field(default_factory=list)
    confirmed_readonly: list[str] = field(default_factory=list)
    enum_values: dict[str, list[str]] = field(default_factory=dict)  # field -> values
    format_annotations: dict[str, str] = field(default_factory=dict)  # field -> format
    default_values: dict[str, object] = field(default_factory=dict)
    pattern_constraints: dict[str, str] = field(default_factory=dict)
    mutability: dict[str, list[str]] = field(default_factory=dict)  # field -> ["create","read","update"]
    enriched: bool = False
    error: str = ""


def _github_headers() -> dict:
    """Build GitHub API headers, optionally using a token."""
    headers = {"Accept": "application/vnd.github.v3+json",
               "User-Agent": "azure-api-tester/0.1.0"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _cache_path(url: str) -> Path:
    """Deterministic cache file path for a URL."""
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{h}.json"


def _get_cached(url: str) -> Optional[dict]:
    """Return cached JSON if fresh, else None."""
    p = _cache_path(url)
    if p.exists():
        age = time.time() - p.stat().st_mtime
        if age < CACHE_TTL:
            return json.loads(p.read_text())
    return None


def _put_cache(url: str, data: dict) -> None:
    """Write JSON to the cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(url).write_text(json.dumps(data))


def _extract_provider_info(url_template: str) -> tuple[str, str]:
    """Extract (provider_namespace, service_folder) from an ARM URL template.

    E.g. '.../providers/Microsoft.CognitiveServices/accounts/...'
    -> ('Microsoft.CognitiveServices', 'cognitiveservices')
    """
    m = re.search(r"/providers/(Microsoft\.[^/]+)/", url_template, re.IGNORECASE)
    if not m:
        return "", ""
    ns = m.group(1)  # e.g. Microsoft.CognitiveServices
    # Service folder: strip "Microsoft.", lowercase
    folder = re.sub(r"^Microsoft\.", "", ns, flags=re.IGNORECASE).lower()
    return ns, folder


def _list_spec_files(service_folder: str, ns: str, api_version: str) -> list[str]:
    """List JSON spec files for a service+version via GitHub API.

    Tries stable/ first, then falls back to preview/.
    Returns raw.githubusercontent.com URLs for each JSON file.
    """
    for track in ("stable", "preview"):
        dir_path = f"specification/{service_folder}/resource-manager/{ns}/{track}/{api_version}"
        api_url = f"{GITHUB_API}/repos/{SPECS_REPO}/contents/{dir_path}"

        cached = _get_cached(api_url)
        if cached is not None:
            entries = cached
        else:
            try:
                resp = requests.get(api_url, headers=_github_headers(), timeout=15)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                entries = resp.json()
                _put_cache(api_url, entries)
            except requests.RequestException:
                continue

        # Filter to .json files (skip examples/ dir etc.)
        urls = []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("type") == "file" and entry["name"].endswith(".json"):
                raw_url = f"{RAW_BASE}/{SPECS_REPO}/main/{dir_path}/{entry['name']}"
                urls.append(raw_url)
        if urls:
            return urls
    return []


def _fetch_spec_json(url: str) -> Optional[dict]:
    """Fetch a JSON spec file, using cache."""
    cached = _get_cached(url)
    if cached is not None:
        return cached
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "azure-api-tester/0.1.0"})
        resp.raise_for_status()
        data = resp.json()
        _put_cache(url, data)
        return data
    except (requests.RequestException, json.JSONDecodeError):
        return None


def _normalize_path(path: str) -> str:
    """Normalize an ARM path for comparison: lowercase, strip query string."""
    path = re.sub(r"\?.*$", "", path)
    path = re.sub(r"^https?://[^/]+", "", path)
    return path.lower().rstrip("/")


def _find_operation(spec_json: dict, http_method: str, url_template: str) -> Optional[dict]:
    """Find the operation in the spec matching the method + path.

    Returns the operation object (e.g. spec_json['paths'][path]['put']).
    """
    target = _normalize_path(url_template)
    method_lower = http_method.lower()

    for path, methods in spec_json.get("paths", {}).items():
        norm = _normalize_path(path)
        if norm == target and method_lower in methods:
            return methods[method_lower]
    return None


def _resolve_ref(spec_json: dict, ref: str) -> Optional[dict]:
    """Resolve a $ref like '#/definitions/Foo' within the spec."""
    if not ref.startswith("#/"):
        return None
    parts = ref.lstrip("#/").split("/")
    node = spec_json
    for p in parts:
        if isinstance(node, dict) and p in node:
            node = node[p]
        else:
            return None
    return node


def _collect_fields(
    spec_json: dict,
    schema: dict,
    prefix: str = "",
    visited: set = None,
) -> tuple[list[dict], list[str], list[str]]:
    """Recursively walk a schema and collect field metadata.

    Returns (arm_id_fields, required_fields, readonly_fields) with dotted paths.
    """
    if visited is None:
        visited = set()

    arm_id_fields = []
    required_fields = []
    readonly_fields = []

    # Resolve $ref
    if "$ref" in schema:
        ref_key = schema["$ref"]
        if ref_key in visited:
            return arm_id_fields, required_fields, readonly_fields
        visited.add(ref_key)
        resolved = _resolve_ref(spec_json, ref_key)
        if resolved:
            schema = resolved
        else:
            return arm_id_fields, required_fields, readonly_fields

    # Handle allOf — process all sub-schemas, then fall through to properties
    if "allOf" in schema:
        for sub in schema["allOf"]:
            a, r, ro = _collect_fields(spec_json, sub, prefix, visited)
            arm_id_fields.extend(a)
            required_fields.extend(r)
            readonly_fields.extend(ro)
        # Don't return — fall through to process direct properties too

    required_set = set(schema.get("required", []))

    for prop_name, prop_schema in schema.get("properties", {}).items():
        full_name = f"{prefix}.{prop_name}" if prefix else prop_name

        # Resolve inline $ref
        actual = prop_schema
        if "$ref" in prop_schema:
            ref_key = prop_schema["$ref"]
            if ref_key not in visited:
                visited.add(ref_key)
                resolved = _resolve_ref(spec_json, ref_key)
                if resolved:
                    actual = resolved

        if prop_name in required_set:
            required_fields.append(full_name)

        if actual.get("readOnly"):
            readonly_fields.append(full_name)

        fmt = actual.get("format", "")
        if fmt == "arm-id" or actual.get("x-ms-arm-id-details"):
            # Try to determine what resource type it points to
            arm_type = ""
            arm_details = actual.get("x-ms-arm-id-details", {})
            if isinstance(arm_details, dict):
                allowed = arm_details.get("allowedResources", [])
                if allowed and isinstance(allowed[0], dict):
                    arm_type = allowed[0].get("type", "")
            desc = actual.get("description", "")
            arm_id_fields.append({
                "name": full_name,
                "resource_type": arm_type,
                "description": desc,
            })

        # Recurse into nested object properties
        if actual.get("properties"):
            a, r, ro = _collect_fields(spec_json, actual, full_name, visited)
            arm_id_fields.extend(a)
            required_fields.extend(r)
            readonly_fields.extend(ro)

    return arm_id_fields, required_fields, readonly_fields


def _collect_metadata(
    spec_json: dict,
    schema: dict,
    prefix: str = "",
    visited: set = None,
) -> tuple[dict, dict, dict, dict]:
    """Collect enum_values, format_annotations, default_values, mutability."""
    if visited is None:
        visited = set()

    enums = {}
    formats = {}
    defaults = {}
    muts = {}

    if "$ref" in schema:
        ref_key = schema["$ref"]
        if ref_key in visited:
            return enums, formats, defaults, muts
        visited.add(ref_key)
        resolved = _resolve_ref(spec_json, ref_key)
        if resolved:
            schema = resolved
        else:
            return enums, formats, defaults, muts

    if "allOf" in schema:
        for sub in schema["allOf"]:
            e, f, d, m = _collect_metadata(spec_json, sub, prefix, visited)
            enums.update(e); formats.update(f); defaults.update(d); muts.update(m)
        # Don't return — fall through to process direct properties too

    for prop_name, prop_schema in schema.get("properties", {}).items():
        full_name = f"{prefix}.{prop_name}" if prefix else prop_name

        actual = prop_schema
        if "$ref" in prop_schema:
            ref_key = prop_schema["$ref"]
            if ref_key not in visited:
                visited.add(ref_key)
                resolved = _resolve_ref(spec_json, ref_key)
                if resolved:
                    actual = resolved

        if "enum" in actual:
            enums[full_name] = actual["enum"]

        fmt = actual.get("format")
        if fmt:
            formats[full_name] = fmt

        if "default" in actual:
            defaults[full_name] = actual["default"]

        mut = actual.get("x-ms-mutability")
        if mut:
            muts[full_name] = mut

        pattern = actual.get("pattern")
        if pattern:
            pass  # captured separately

        if actual.get("properties"):
            e, f, d, m = _collect_metadata(spec_json, actual, full_name, visited)
            enums.update(e); formats.update(f); defaults.update(d); muts.update(m)

    return enums, formats, defaults, muts


def _collect_patterns(
    spec_json: dict,
    schema: dict,
    prefix: str = "",
    visited: set = None,
) -> dict[str, str]:
    """Collect regex pattern constraints from schema properties."""
    if visited is None:
        visited = set()

    patterns = {}

    if "$ref" in schema:
        ref_key = schema["$ref"]
        if ref_key in visited:
            return patterns
        visited.add(ref_key)
        resolved = _resolve_ref(spec_json, ref_key)
        if resolved:
            schema = resolved
        else:
            return patterns

    if "allOf" in schema:
        for sub in schema["allOf"]:
            patterns.update(_collect_patterns(spec_json, sub, prefix, visited))
        # Don't return — fall through to process direct properties too

    for prop_name, prop_schema in schema.get("properties", {}).items():
        full_name = f"{prefix}.{prop_name}" if prefix else prop_name

        actual = prop_schema
        if "$ref" in prop_schema:
            ref_key = prop_schema["$ref"]
            if ref_key not in visited:
                visited.add(ref_key)
                resolved = _resolve_ref(spec_json, ref_key)
                if resolved:
                    actual = resolved

        if "pattern" in actual:
            patterns[full_name] = actual["pattern"]

        if actual.get("properties"):
            patterns.update(_collect_patterns(spec_json, actual, full_name, visited))

    return patterns


def enrich_from_openapi(
    url_template: str,
    http_method: str,
    api_version: str,
) -> OpenApiEnrichment:
    """Main entry point: fetch the OpenAPI spec and extract enrichment data.

    This does NOT modify the ApiSpec in place — it returns a separate
    OpenApiEnrichment object that the CLI can merge/display.
    """
    result = OpenApiEnrichment()

    ns, service_folder = _extract_provider_info(url_template)
    if not ns or not service_folder:
        result.error = f"Could not extract provider namespace from URL template"
        return result

    # List spec files for this service + version
    spec_urls = _list_spec_files(service_folder, ns, api_version)
    if not spec_urls:
        result.error = f"No OpenAPI spec found for {ns} api-version={api_version}"
        return result

    # Find the spec file containing the matching operation
    operation = None
    matched_url = ""
    for url in spec_urls:
        spec_json = _fetch_spec_json(url)
        if not spec_json:
            continue
        op = _find_operation(spec_json, http_method, url_template)
        if op:
            operation = op
            matched_url = url
            break

    if not operation or not spec_json:
        result.error = f"Operation {http_method} not found in any spec file for {ns}"
        return result

    result.spec_url = matched_url
    result.enriched = True

    # Find the request body schema
    body_schema = None
    for param in operation.get("parameters", []):
        if param.get("in") == "body" and "schema" in param:
            body_schema = param["schema"]
            break

    if body_schema:
        arm_ids, req, ro = _collect_fields(spec_json, body_schema)
        result.arm_id_fields = arm_ids
        result.confirmed_required = req
        result.confirmed_readonly = ro

        enums, formats, defaults, muts = _collect_metadata(spec_json, body_schema)
        result.enum_values = enums
        result.format_annotations = formats
        result.default_values = defaults
        result.mutability = muts

        result.pattern_constraints = _collect_patterns(spec_json, body_schema)

    return result
