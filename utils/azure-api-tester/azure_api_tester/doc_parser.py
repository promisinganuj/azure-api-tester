"""Parse Azure REST API documentation pages from Microsoft Learn."""

import re
import json
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup, Tag


@dataclass
class UriParameter:
    name: str
    location: str  # "path" or "query"
    required: bool
    type: str
    description: str = ""
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None


@dataclass
class SchemaField:
    name: str
    type: str
    required: bool
    description: str = ""
    ref_type: Optional[str] = None  # reference to a definition (e.g., "BatchEndpointProperties")
    children: list["SchemaField"] = field(default_factory=list)


@dataclass
class EnumDefinition:
    name: str
    values: list[str]
    descriptions: dict[str, str] = field(default_factory=dict)


@dataclass
class ApiSpec:
    """Structured representation of an Azure REST API from its docs page."""
    title: str
    description: str
    http_method: str
    url_template: str
    api_version: str
    uri_parameters: list[UriParameter]
    request_body_fields: list[SchemaField]
    enums: dict[str, EnumDefinition]
    sample_request_body: Optional[dict] = None
    sample_response_body: Optional[dict] = None
    response_codes: list[dict] = field(default_factory=list)
    definitions: dict[str, list[SchemaField]] = field(default_factory=dict)


@dataclass
class PrerequisiteResource:
    """A parent resource that must exist before the target resource can be created."""
    resource_type: str        # e.g., "Microsoft.MachineLearningServices/workspaces"
    friendly_name: str        # e.g., "ML Workspace"
    param_name: str           # e.g., "workspaceName"
    url_path: str             # Full ARM path up to this resource


# Well-known Azure resource type friendly names
_RESOURCE_FRIENDLY_NAMES = {
    "resourcegroups": "Resource Group",
    "workspaces": "ML Workspace",
    "batchendpoints": "Batch Endpoint",
    "onlineendpoints": "Online Endpoint",
    "deployments": "Deployment",
    "storageaccounts": "Storage Account",
    "containers": "Blob Container",
    "vaults": "Key Vault",
    "servers": "SQL Server",
    "databases": "Database",
    "sites": "App Service",
    "registries": "Container Registry",
    "clusters": "Cluster",
    "namespaces": "Namespace",
    "virtualnetworks": "Virtual Network",
    "subnets": "Subnet",
    "managedclusters": "AKS Cluster",
    "accounts": "Account",
    "components": "Component",
    "environments": "Environment",
    "models": "Model",
    "datastores": "Datastore",
    "jobs": "Job",
    "computes": "Compute",
    "connections": "Connection",
}


def extract_prerequisites(url_template: str) -> list[PrerequisiteResource]:
    """Extract prerequisite (parent) resources from the URL path.

    Parses the ARM URL hierarchy to identify resources that must exist
    before the target resource can be created.
    """
    # Strip query string and base URL
    path = re.sub(r"\?.*$", "", url_template)
    path = re.sub(r"^https?://[^/]+", "", path)

    prerequisites = []

    # Parse /subscriptions/{sub}/resourceGroups/{rg}/providers/Ns/type/{name}/...
    # Split into segments
    segments = [s for s in path.split("/") if s]

    i = 0
    current_path = ""
    provider_ns = ""

    while i < len(segments):
        seg = segments[i]

        if seg.lower() == "subscriptions":
            # subscriptions/{id} — skip, not a prerequisite
            current_path += f"/{seg}/{segments[i+1]}" if i + 1 < len(segments) else f"/{seg}"
            i += 2
            continue

        if seg.lower() == "resourcegroups":
            if i + 1 < len(segments):
                param = segments[i + 1]
                current_path += f"/{seg}/{param}"
                param_name = param.strip("{}")
                prerequisites.append(PrerequisiteResource(
                    resource_type="Microsoft.Resources/resourceGroups",
                    friendly_name="Resource Group",
                    param_name=param_name,
                    url_path=current_path,
                ))
                i += 2
                continue

        if seg.lower() == "providers":
            if i + 1 < len(segments):
                provider_ns = segments[i + 1]
                current_path += f"/{seg}/{provider_ns}"
                i += 2
                continue

        # This is a resource type segment followed by its name/param
        if i + 1 < len(segments):
            resource_type_seg = seg
            param = segments[i + 1]
            current_path += f"/{resource_type_seg}/{param}"

            full_type = f"{provider_ns}/{resource_type_seg}" if provider_ns else resource_type_seg
            friendly = _RESOURCE_FRIENDLY_NAMES.get(resource_type_seg.lower(), resource_type_seg)
            param_name = param.strip("{}")

            prerequisites.append(PrerequisiteResource(
                resource_type=full_type,
                friendly_name=friendly,
                param_name=param_name,
                url_path=current_path,
            ))
            i += 2
        else:
            i += 1

    return prerequisites


def fetch_doc_page(url: str) -> str:
    """Fetch the HTML content of a Microsoft Learn REST API docs page."""
    resp = requests.get(url, timeout=30, headers={
        "User-Agent": "azure-api-tester/0.1.0"
    })
    resp.raise_for_status()
    return resp.text


def _find_section(soup: BeautifulSoup, heading_text: str) -> Optional[Tag]:
    """Find a section by its heading text. Returns the heading element."""
    for heading in soup.find_all(["h2", "h3"]):
        if heading.get_text(strip=True).lower() == heading_text.lower():
            return heading
    return None


def _get_section_table(heading: Tag) -> Optional[Tag]:
    """Get the first table after a heading, stopping at the next same-level heading."""
    tag_name = heading.name
    sibling = heading.find_next_sibling()
    while sibling:
        if sibling.name == tag_name:
            break
        if sibling.name == "table":
            return sibling
        sibling = sibling.find_next_sibling()
    return None


def _get_section_tables(heading: Tag) -> list[Tag]:
    """Get all tables after a heading, stopping at the next same-level heading."""
    tag_name = heading.name
    tables = []
    sibling = heading.find_next_sibling()
    while sibling:
        if sibling.name == tag_name:
            break
        if sibling.name == "table":
            tables.append(sibling)
        sibling = sibling.find_next_sibling()
    return tables


def _parse_table_rows(table: Tag) -> list[dict[str, str]]:
    """Parse an HTML table into a list of dicts keyed by header text.

    Handles MS Learn's structure where headers may be <th> in the first <tr>
    of <tbody> (no separate <thead>).
    """
    headers = []

    # Try <thead> first
    thead = table.find("thead")
    if thead:
        for th in thead.find_all("th"):
            headers.append(th.get_text(strip=True).lower())

    # Fallback: look for <th> in any <tr> (MS Learn puts them in <tbody>)
    if not headers:
        for tr in table.find_all("tr"):
            ths = tr.find_all("th")
            if ths:
                headers = [th.get_text(strip=True).lower() for th in ths]
                break

    rows = []
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        row = {}
        for i, td in enumerate(cells):
            key = headers[i] if i < len(headers) else f"col{i}"
            # Strip <wbr> tags and normalize whitespace
            text = td.get_text(strip=True)
            row[key] = text
        rows.append(row)
    return rows


def _get_code_blocks_after(heading: Tag, stop_tag_name: str = "h2") -> list[str]:
    """Get all code block contents after a heading until the next stop heading."""
    blocks = []
    sibling = heading.find_next_sibling()
    while sibling:
        if sibling.name == stop_tag_name:
            break
        code = sibling.find("code")
        if code:
            blocks.append(code.get_text())
        sibling = sibling.find_next_sibling()
    return blocks


def _extract_method_and_url(soup: BeautifulSoup) -> tuple[str, str, str]:
    """Extract HTTP method, URL template, and api-version from the first code block."""
    # Look for the first code block that contains an HTTP method line
    for code in soup.find_all("code"):
        text = code.get_text(strip=True)
        match = re.match(
            r"(GET|PUT|POST|PATCH|DELETE|HEAD|OPTIONS)\s+(https?://\S+)",
            text,
        )
        if match:
            method = match.group(1)
            url = match.group(2)
            # Extract api-version
            version_match = re.search(r"api-version=([^\s&}]+)", url)
            api_version = version_match.group(1) if version_match else ""
            return method, url, api_version
    return "", "", ""


def _parse_uri_params(soup: BeautifulSoup) -> list[UriParameter]:
    """Parse the URI Parameters section."""
    heading = _find_section(soup, "URI Parameters")
    if not heading:
        return []

    table = _get_section_table(heading)
    if not table:
        return []

    params = []
    for row in _parse_table_rows(table):
        name = row.get("name", "")
        if not name:
            continue

        location = row.get("in", "path")
        required = row.get("required", "").lower() == "true"
        type_str = row.get("type", "string")

        # Separate type from constraints (MS Learn puts minLength etc. inline)
        clean_type = re.split(r"(?:minLength|maxLength|pattern):", type_str)[0].strip()

        desc = row.get("description", "")

        param = UriParameter(
            name=name,
            location=location,
            required=required,
            type=clean_type,
            description=desc,
        )

        # Extract constraints from type string or description
        combined = type_str + " " + desc
        min_match = re.search(r"minLength:\s*(\d+)", combined)
        max_match = re.search(r"maxLength:\s*(\d+)", combined)
        pattern_match = re.search(r"pattern:\s*(\S+)", combined)

        if min_match:
            param.min_length = int(min_match.group(1))
        if max_match:
            param.max_length = int(max_match.group(1))
        if pattern_match:
            param.pattern = pattern_match.group(1)

        params.append(param)

    return params


def _parse_body_fields(soup: BeautifulSoup) -> list[SchemaField]:
    """Parse the Request Body section."""
    heading = _find_section(soup, "Request Body")
    if not heading:
        return []

    table = _get_section_table(heading)
    if not table:
        return []

    fields = []
    for row in _parse_table_rows(table):
        name = row.get("name", "")
        if not name:
            continue

        type_str = row.get("type", "string")
        required = row.get("required", "").lower() == "true"
        desc = row.get("description", "")

        # Check if type references another definition (resolved later in parse_doc_url)
        ref_type = None
        clean_type = re.sub(r"\(.*?\)", "", type_str).strip().rstrip("[]")
        # Handle dict-like types: <string, FooBar>
        dict_match = re.search(r"<[^,]+,\s*([A-Z]\w+)>", type_str)
        if dict_match:
            ref_type = dict_match.group(1)
        elif clean_type and clean_type[0].isupper() and clean_type.lower() not in (
            "true", "false", "string", "integer", "number", "boolean", "object", "array"
        ):
            ref_type = clean_type

        fields.append(SchemaField(
            name=name,
            type=type_str,
            required=required,
            description=desc,
            ref_type=ref_type,
        ))

    return fields


def _parse_enums(soup: BeautifulSoup) -> dict[str, EnumDefinition]:
    """Parse enum definitions from the Definitions section."""
    enums = {}

    # Find all h3 headings in the definitions section
    definitions_heading = _find_section(soup, "Definitions")
    if not definitions_heading:
        return enums

    # Look for h3 subheadings (each definition)
    sibling = definitions_heading.find_next_sibling()
    while sibling:
        if sibling.name == "h2":
            break

        if sibling.name == "h3":
            def_name = sibling.get_text(strip=True)
            # Check if the next element says "Enumeration"
            next_sib = sibling.find_next_sibling()
            is_enum = False
            while next_sib and next_sib.name not in ("h2", "h3"):
                text = next_sib.get_text(strip=True)
                if "Enumeration" in text:
                    is_enum = True
                if is_enum and next_sib.name == "table":
                    rows = _parse_table_rows(next_sib)
                    values = [r.get("value", "") for r in rows if r.get("value")]
                    descriptions = {
                        r.get("value", ""): r.get("description", "")
                        for r in rows
                        if r.get("value")
                    }
                    if values:
                        enums[def_name] = EnumDefinition(
                            name=def_name,
                            values=values,
                            descriptions=descriptions,
                        )
                    break
                next_sib = next_sib.find_next_sibling()

        sibling = sibling.find_next_sibling()

    return enums


def _parse_definitions(soup: BeautifulSoup) -> dict[str, list[SchemaField]]:
    """Parse object definitions from the Definitions section."""
    definitions = {}

    definitions_heading = _find_section(soup, "Definitions")
    if not definitions_heading:
        return definitions

    sibling = definitions_heading.find_next_sibling()
    while sibling:
        if sibling.name == "h2":
            break

        if sibling.name == "h3":
            def_name = sibling.get_text(strip=True)
            # Check if this is an Object definition (not Enumeration)
            next_sib = sibling.find_next_sibling()
            is_object = False
            while next_sib and next_sib.name not in ("h2", "h3"):
                text = next_sib.get_text(strip=True)
                if "Object" in text:
                    is_object = True
                if is_object and next_sib.name == "table":
                    rows = _parse_table_rows(next_sib)
                    fields = []
                    for row in rows:
                        name = row.get("name", "")
                        if not name:
                            continue
                        type_str = row.get("type", "string")
                        desc = row.get("description", "")

                        ref_type = None
                        clean_type = re.sub(r"\(.*?\)", "", type_str).strip().rstrip("[]")
                        # Also handle dict-like types: <string, FooBar>
                        dict_match = re.search(r"<[^,]+,\s*([A-Z]\w+)>", type_str)
                        if dict_match:
                            ref_type = dict_match.group(1)
                        elif clean_type and clean_type[0].isupper() and clean_type.lower() not in (
                            "true", "false", "string", "integer", "number", "boolean", "object", "array"
                        ):
                            ref_type = clean_type

                        fields.append(SchemaField(
                            name=name,
                            type=type_str,
                            required=False,
                            description=desc,
                            ref_type=ref_type,
                        ))

                    if fields:
                        definitions[def_name] = fields
                    break
                next_sib = next_sib.find_next_sibling()

        sibling = sibling.find_next_sibling()

    return definitions


def _extract_sample_json(soup: BeautifulSoup, section_name: str) -> Optional[dict]:
    """Extract sample request or response JSON from the Examples section."""
    examples_heading = _find_section(soup, "Examples")
    if not examples_heading:
        return None

    # Find all code blocks in the examples section
    sibling = examples_heading.find_next_sibling()
    found_request = False
    found_response = False

    while sibling:
        if sibling.name == "h2":
            break

        text = sibling.get_text(strip=True)

        if "sample request" in text.lower():
            found_request = True
            found_response = False
        elif "sample response" in text.lower():
            found_response = True
            found_request = False

        if sibling.name in ("pre", "div") or (sibling.name and sibling.find("code")):
            code = sibling.find("code")
            if code:
                code_text = code.get_text()
                # Try to extract JSON (skip the HTTP method line if present)
                json_match = re.search(r"\{[\s\S]*\}", code_text)
                if json_match:
                    try:
                        parsed = json.loads(json_match.group())
                        if section_name == "request" and found_request:
                            return parsed
                        elif section_name == "response" and found_response:
                            return parsed
                    except json.JSONDecodeError:
                        pass

        sibling = sibling.find_next_sibling()

    return None


def _parse_responses(soup: BeautifulSoup) -> list[dict]:
    """Parse the Responses section."""
    heading = _find_section(soup, "Responses")
    if not heading:
        return []

    table = _get_section_table(heading)
    if not table:
        return []

    responses = []
    for row in _parse_table_rows(table):
        name = row.get("name", "")
        desc = row.get("description", "")
        type_str = row.get("type", "")

        status_match = re.search(r"(\d{3})", name)
        status_code = int(status_match.group(1)) if status_match else 0

        responses.append({
            "name": name,
            "status_code": status_code,
            "type": type_str,
            "description": desc,
        })

    return responses


def parse_doc_url(url: str) -> ApiSpec:
    """Parse an Azure REST API documentation URL and return a structured ApiSpec."""
    html = fetch_doc_page(url)
    soup = BeautifulSoup(html, "html.parser")

    # Title and description
    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else "Unknown API"

    # Get description from the first paragraph after h1
    desc = ""
    if title_tag:
        next_p = title_tag.find_next_sibling("p")
        if next_p:
            desc = next_p.get_text(strip=True)

    method, url_template, api_version = _extract_method_and_url(soup)
    uri_params = _parse_uri_params(soup)
    body_fields = _parse_body_fields(soup)
    enums = _parse_enums(soup)
    definitions = _parse_definitions(soup)
    sample_request = _extract_sample_json(soup, "request")
    sample_response = _extract_sample_json(soup, "response")
    response_codes = _parse_responses(soup)

    # Resolve nested schema: attach children from definitions to body fields
    _resolve_children(body_fields, definitions, enums, visited=set())

    return ApiSpec(
        title=title,
        description=desc,
        http_method=method,
        url_template=url_template,
        api_version=api_version,
        uri_parameters=uri_params,
        request_body_fields=body_fields,
        enums=enums,
        sample_request_body=sample_request,
        sample_response_body=sample_response,
        response_codes=response_codes,
        definitions=definitions,
    )


def _resolve_children(
    fields: list[SchemaField],
    definitions: dict[str, list[SchemaField]],
    enums: dict[str, EnumDefinition],
    visited: set[str],
) -> None:
    """Recursively resolve definition references into nested SchemaField children."""
    for f in fields:
        if f.ref_type and f.ref_type in definitions and f.ref_type not in visited:
            visited.add(f.ref_type)
            f.children = [
                SchemaField(
                    name=d.name,
                    type=d.type,
                    required=d.required,
                    description=d.description,
                    ref_type=d.ref_type,
                )
                for d in definitions[f.ref_type]
            ]
            _resolve_children(f.children, definitions, enums, visited)
            visited.discard(f.ref_type)
