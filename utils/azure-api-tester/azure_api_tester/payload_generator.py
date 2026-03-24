"""Generate test payload variants from parsed API schema."""

import copy
import random
import string
from typing import Optional

from .doc_parser import ApiSpec, SchemaField, EnumDefinition
from .identity_resolver import IdentityContext, build_identity_block


# Maps field name patterns to realistic default values
_NAME_VALUE_MAP = {
    "location": "eastus",
    "name": "test-{random}",
    "description": "Auto-generated test resource",
    "displayname": "Test Resource",
    "deploymentname": "test-deployment",
}

# Maps type strings to default values
_TYPE_VALUE_MAP = {
    "string": "test-value",
    "integer": 1,
    "int32": 1,
    "int64": 1,
    "number": 1.0,
    "boolean": True,
    "object": {},
}

# Fields that are server-populated (read-only) and should NOT be in request payloads
_READ_ONLY_FIELDS = {
    "provisioningstate", "scoringuri", "swaggeruri",
    "principalid", "tenantid", "clientid",
    "createdat", "createdby", "createdbytype",
    "lastmodifiedat", "lastmodifiedby", "lastmodifiedbytype",
}

# Enums that are read-only / server-set
_READ_ONLY_ENUMS = {
    "EndpointProvisioningState", "createdByType",
}


def _random_suffix(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _is_read_only(field_name: str) -> bool:
    """Check if a field is read-only based on its name."""
    return field_name.lower() in _READ_ONLY_FIELDS


def _smart_value(field: SchemaField, enums: dict[str, EnumDefinition]) -> object:
    """Generate a smart value for a field based on its name, type, and enum references."""
    name_lower = field.name.lower()

    # Check if this field references an enum
    if field.ref_type and field.ref_type in enums:
        return enums[field.ref_type].values[0]

    # Check for URI type
    if "uri" in field.type.lower() or "url" in name_lower:
        return "https://example.com/test"

    # Check for UUID type
    if "uuid" in field.type.lower():
        return "00000000-1111-2222-3333-444444444444"

    # Check for date-time
    if "date-time" in field.type.lower():
        return "2025-01-01T00:00:00Z"

    # Check for password type
    if "password" in field.type.lower():
        return "test-secret-key-" + _random_suffix(12)

    # Match by field name
    for pattern, value in _NAME_VALUE_MAP.items():
        if pattern in name_lower:
            if "{random}" in str(value):
                return str(value).replace("{random}", _random_suffix())
            return value

    # Match by type
    base_type = field.type.split("(")[0].strip().lower()
    if base_type in _TYPE_VALUE_MAP:
        return _TYPE_VALUE_MAP[base_type]

    return "test-value"


def _build_object(
    fields: list[SchemaField],
    enums: dict[str, EnumDefinition],
    required_only: bool = False,
    enum_overrides: Optional[dict[str, str]] = None,
    identity_context: Optional[IdentityContext] = None,
    parent_name: str = "",
) -> dict:
    """Build a JSON object from a list of schema fields.

    Skips read-only fields and uses real identity values when available.
    """
    obj = {}

    for field in fields:
        if required_only and not field.required:
            continue

        # Skip read-only fields
        if _is_read_only(field.name):
            continue

        # Special handling for identity block
        if field.name == "identity" and field.ref_type and "Identity" in (field.ref_type or ""):
            if identity_context:
                # Determine identity type from enum overrides or default
                id_type = "SystemAssigned"
                if enum_overrides and "ManagedServiceIdentityType" in enum_overrides:
                    id_type = enum_overrides["ManagedServiceIdentityType"]
                obj["identity"] = build_identity_block(id_type, identity_context)
            elif not required_only:
                obj["identity"] = {"type": "SystemAssigned"}
            continue

        # Use enum override if provided
        if enum_overrides and field.ref_type and field.ref_type in enum_overrides:
            obj[field.name] = enum_overrides[field.ref_type]
        elif field.children:
            # Filter out read-only children before recursing
            writable_children = [c for c in field.children if not _is_read_only(c.name)]
            child_obj = _build_object(
                writable_children, enums, required_only, enum_overrides,
                identity_context, parent_name=field.name,
            )
            if child_obj or field.required:
                obj[field.name] = child_obj
        elif "[]" in field.type or "array" in field.type.lower():
            obj[field.name] = []
        elif field.type == "object" and not field.children:
            obj[field.name] = {}
        else:
            obj[field.name] = _smart_value(field, enums)

    return obj


def _find_enum_fields(fields: list[SchemaField], enums: dict[str, EnumDefinition]) -> list[tuple[str, EnumDefinition]]:
    """Recursively find all writable fields that reference enums."""
    results = []
    for field in fields:
        if _is_read_only(field.name):
            continue
        if field.ref_type and field.ref_type in enums and field.ref_type not in _READ_ONLY_ENUMS:
            results.append((field.ref_type, enums[field.ref_type]))
        if field.children:
            results.extend(_find_enum_fields(field.children, enums))
    return results


def generate_payloads(
    spec: ApiSpec,
    identity_context: Optional[IdentityContext] = None,
) -> list[tuple[str, Optional[dict]]]:
    """Generate all payload variants for the API spec.

    Returns list of (variant_name, payload_dict) tuples.
    For GET/DELETE/HEAD, returns payloads with None body.
    """
    # Methods that don't have request bodies
    if spec.http_method in ("GET", "DELETE", "HEAD", "OPTIONS"):
        return [("no-body", None)]

    payloads = []

    # 1. Docs sample (if available)
    if spec.sample_request_body:
        payloads.append(("docs-sample", copy.deepcopy(spec.sample_request_body)))

    # 2. Minimal payload (required fields only)
    if spec.request_body_fields:
        minimal = _build_object(
            spec.request_body_fields, spec.enums,
            required_only=True, identity_context=identity_context,
        )
        if minimal:
            payloads.append(("minimal-required", minimal))

    # 3. Full payload (all fields)
    if spec.request_body_fields:
        full = _build_object(
            spec.request_body_fields, spec.enums,
            required_only=False, identity_context=identity_context,
        )
        if full:
            payloads.append(("full", full))

    # If no fields were parsed but we have a sample, use it
    if not payloads and spec.sample_request_body:
        payloads.append(("docs-sample", copy.deepcopy(spec.sample_request_body)))

    # Fallback: empty body
    if not payloads:
        payloads.append(("empty-body", {}))

    return payloads
