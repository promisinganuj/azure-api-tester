"""CLI entry point — ties together all modules."""

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax
from rich.tree import Tree
from rich import box

from .doc_parser import parse_doc_url, ApiSpec, extract_prerequisites, SchemaField
from .payload_generator import generate_payloads
from .config import load_config, resolve_all_uri_params, substitute_url
from .api_caller import execute_call, get_cached_token
from .tracker import Tracker, get_run_history, get_run_details
from .cleanup import cleanup_resource
from .spec_enricher import enrich_from_openapi, OpenApiEnrichment
from .identity_resolver import get_identity_context

console = Console()


def _display_spec(spec: ApiSpec) -> None:
    """Display the parsed API specification."""
    console.print()
    console.print(Panel(
        f"[bold]{spec.title}[/bold]\n{spec.description}",
        title="📄 Parsed API Spec",
        border_style="cyan",
    ))

    console.print(f"  [bold]Method:[/bold]      {spec.http_method}")
    console.print(f"  [bold]URL:[/bold]         {spec.url_template}")
    console.print(f"  [bold]API Version:[/bold] {spec.api_version}")

    if spec.uri_parameters:
        console.print()
        table = Table(title="URI Parameters", box=box.SIMPLE_HEAVY)
        table.add_column("Name", style="cyan")
        table.add_column("In", style="dim")
        table.add_column("Required", style="bold")
        table.add_column("Type")
        for p in spec.uri_parameters:
            table.add_row(
                p.name, p.location,
                "✓" if p.required else "",
                p.type,
            )
        console.print(table)

    if spec.request_body_fields:
        console.print()
        table = Table(title="Request Body Schema", box=box.SIMPLE_HEAVY)
        table.add_column("Field", style="cyan")
        table.add_column("Required", style="bold")
        table.add_column("Type")
        for f in spec.request_body_fields:
            table.add_row(
                f.name,
                "✓" if f.required else "",
                f.type,
            )
        console.print(table)

    if spec.enums:
        console.print()
        for name, enum in spec.enums.items():
            console.print(f"  [bold]Enum {name}:[/bold] {', '.join(enum.values)}")


def _display_payloads(payloads: list[tuple[str, Optional[dict]]]) -> None:
    """Display generated payload variants."""
    console.print()
    console.print(Panel(
        f"Generated [bold]{len(payloads)}[/bold] payload variant(s)",
        title="🔧 Payload Variants",
        border_style="green",
    ))

    for name, body in payloads:
        if body is not None:
            json_str = json.dumps(body, indent=2)
            syntax = Syntax(json_str, "json", theme="monokai", line_numbers=False)
            console.print(f"\n  [bold cyan]{name}[/bold cyan]:")
            console.print(syntax)
        else:
            console.print(f"\n  [bold cyan]{name}[/bold cyan]: (no body)")


def _display_field_reference(spec: ApiSpec) -> None:
    """Display a field reference tree showing enums, constraints, and required markers."""
    from .payload_generator import _READ_ONLY_FIELDS, _READ_ONLY_ENUMS

    if not spec.request_body_fields:
        return

    tree = Tree("📖 [bold]Field Reference[/bold] — valid values & constraints for editing payloads")

    def _add_field(parent_node, field: SchemaField, enums: dict, depth: int = 0):
        """Add a field to the tree with annotations."""
        if field.name.lower() in _READ_ONLY_FIELDS:
            return
        if field.ref_type and field.ref_type in _READ_ONLY_ENUMS:
            return

        # Build the field label
        parts = []

        # Field name + required marker
        if field.required:
            parts.append(f"[bold cyan]{field.name}[/bold cyan] [red](required)[/red]")
        else:
            parts.append(f"[cyan]{field.name}[/cyan]")

        # Type info
        base_type = field.type
        # Clean up inline constraints from type string for separate display
        clean_type = base_type.split("minimum")[0].split("maximum")[0].strip()
        parts.append(f"[dim]{clean_type}[/dim]")

        # Enum values
        if field.ref_type and field.ref_type in enums and field.ref_type not in _READ_ONLY_ENUMS:
            values = enums[field.ref_type].values
            values_str = " | ".join(f"[green]{v}[/green]" for v in values)
            parts.append(f"→ {values_str}")

        # Format constraints
        annotations = []
        if "uri" in field.type.lower():
            annotations.append("[yellow]format: uri[/yellow]")
        if "arm-id" in field.type.lower():
            annotations.append("[yellow]format: arm-id[/yellow]")
        if "uuid" in field.type.lower():
            annotations.append("[yellow]format: uuid[/yellow]")
        if "password" in field.type.lower():
            annotations.append("[yellow]sensitive[/yellow]")

        # Numeric constraints
        import re
        min_match = re.search(r"minimum:\s*(\d+)", field.type)
        max_match = re.search(r"maximum:\s*(\d+)", field.type)
        if min_match:
            annotations.append(f"[yellow]min: {min_match.group(1)}[/yellow]")
        if max_match:
            annotations.append(f"[yellow]max: {max_match.group(1)}[/yellow]")

        if annotations:
            parts.append(f"({', '.join(annotations)})")

        label = "  ".join(parts)
        node = parent_node.add(label)

        # Recurse for children
        if field.children:
            for child in field.children:
                _add_field(node, child, enums, depth + 1)

    for field in spec.request_body_fields:
        _add_field(tree, field, spec.enums)

    console.print()
    console.print(tree)




def _display_enrichment(enrichment: OpenApiEnrichment) -> None:
    """Display OpenAPI spec enrichment results."""
    if not enrichment.enriched:
        if enrichment.error:
            console.print(f'  [dim]OpenAPI enrichment skipped: {enrichment.error}[/dim]')
        return

    console.print()
    panel_text = '[bold]OpenAPI Spec Enrichment[/bold]' + '\n' + f'Source: [dim]{enrichment.spec_url}[/dim]'
    console.print(Panel(
        panel_text,
        title='🔬 Spec Enrichment',
        border_style='magenta',
    ))

    # ARM ID fields (resource references found in the spec)
    if enrichment.arm_id_fields:
        console.print()
        arm_table = Table(title='Fields with format: arm-id (Azure resource references)', box=box.SIMPLE_HEAVY)
        arm_table.add_column('Field', style='cyan')
        arm_table.add_column('Resource Type', style='yellow')
        arm_table.add_column('Description', max_width=50)
        for f in enrichment.arm_id_fields:
            arm_table.add_row(
                f['name'],
                f.get('resource_type', '') or '(unspecified)',
                (f.get('description', '') or '')[:80],
            )
        console.print(arm_table)

    # Confirmed required fields
    if enrichment.confirmed_required:
        console.print()
        console.print('  [bold]Confirmed required fields (from OpenAPI):[/bold]')
        for name in enrichment.confirmed_required:
            console.print(f'    [green]✓[/green] {name}')

    # Confirmed read-only fields
    if enrichment.confirmed_readonly:
        console.print()
        console.print('  [bold]Confirmed read-only fields (excluded from payloads):[/bold]')
        for name in enrichment.confirmed_readonly[:15]:  # cap display
            console.print(f'    [dim]⊘ {name}[/dim]')
        remaining = len(enrichment.confirmed_readonly) - 15
        if remaining > 0:
            console.print(f'    [dim]... and {remaining} more[/dim]')

    # Enum values discovered from spec
    if enrichment.enum_values:
        console.print()
        console.print('  [bold]Enum values (from OpenAPI spec):[/bold]')
        for field_name, values in list(enrichment.enum_values.items())[:10]:
            vals = ' | '.join(f'[green]{v}[/green]' for v in values[:8])
            extra = f' ... +{len(values)-8}' if len(values) > 8 else ''
            console.print(f'    {field_name}: {vals}{extra}')

    # Default values
    if enrichment.default_values:
        console.print()
        console.print('  [bold]Default values:[/bold]')
        for field_name, val in list(enrichment.default_values.items())[:10]:
            console.print(f'    {field_name} = [yellow]{val}[/yellow]')

def _display_results(results: list[dict]) -> None:
    """Display execution results as a summary table."""
    console.print()
    table = Table(title="📊 Results Summary", box=box.ROUNDED)
    table.add_column("Variant", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Duration", justify="right")

    for r in results:
        status = r["status"]
        if 200 <= status < 300:
            status_str = f"[green]{status} ✓[/green]"
        elif status == 0:
            status_str = "[red]ERR ✗[/red]"
        else:
            status_str = f"[red]{status} ✗[/red]"

        table.add_row(
            r["variant_name"],
            status_str,
            f"{r['duration_ms']:.0f}ms",
        )

    console.print(table)


def _save_payloads_to_dir(
    payloads: list[tuple[str, Optional[dict]]],
    output_dir: str,
    api_title: str,
    spec: "ApiSpec" = None,
    doc_url: str = "",
) -> str:
    """Save generated payloads + api-spec.json as editable files. Returns the output directory path."""
    safe_title = "".join(c if c.isalnum() or c in "-_" else "-" for c in api_title).strip("-")[:40]
    payload_dir = os.path.join(output_dir or ".", f"api-test-payloads-{safe_title}")
    os.makedirs(payload_dir, exist_ok=True)

    for name, body in payloads:
        filepath = os.path.join(payload_dir, f"{name}.json")
        with open(filepath, "w") as f:
            json.dump(body, f, indent=2)
            f.write("\n")

    # Save API spec metadata for the execute subcommand
    if spec:
        spec_meta = {
            "title": spec.title,
            "http_method": spec.http_method,
            "url_template": spec.url_template,
            "api_version": spec.api_version,
            "doc_url": doc_url,
            "uri_parameters": [
                {"name": p.name, "location": p.location, "required": p.required,
                 "type": p.type, "description": getattr(p, "description", "")}
                for p in spec.uri_parameters
            ],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        spec_path = os.path.join(payload_dir, "api-spec.json")
        with open(spec_path, "w") as f:
            json.dump(spec_meta, f, indent=2)
            f.write("\n")

    return payload_dir


def _pick_payloads(
    payloads: list[tuple[str, Optional[dict]]],
) -> list[tuple[str, Optional[dict]]]:
    """Interactive picker — user selects which payloads to execute."""
    console.print()
    for i, (name, _) in enumerate(payloads, 1):
        console.print(f"  [bold cyan][{i}][/bold cyan] {name}")

    console.print()
    selection = input("  Select payloads (e.g. 1,2 / all / q to quit): ").strip().lower()

    if selection in ("q", "quit", "exit"):
        return []

    if selection in ("all", "a", ""):
        return payloads

    selected = []
    for part in selection.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(payloads):
                selected.append(payloads[idx])
    return selected


@click.group()
def main():
    """Azure API Tester — test Azure REST APIs from documentation URLs."""
    pass


@main.command()
@click.argument("doc_url")
@click.option("--dry-run", is_flag=True, help="Generate & save payloads without executing.")
@click.option("--cleanup", is_flag=True, default=False, help="Auto-cleanup created resources after testing (default: off, or set autoCleanup: true in config).")
@click.option("--no-cleanup", is_flag=True, help="Force skip cleanup even if enabled in config.")
@click.option("--param", multiple=True, help="Override URI param: --param name=value")
@click.option("--payload", "payload_files", multiple=True, type=click.Path(exists=True), help="Custom payload JSON file(s) to use.")
@click.option("--variant", "variants", multiple=True, help="Select payload variant(s) by name (e.g. --variant minimal --variant full).")
@click.option("--output-dir", type=click.Path(), help="Directory to save payload files.")
@click.option("-y", "--yes", is_flag=True, help="Skip interactive picker, run all payloads.")
def test(doc_url: str, dry_run: bool, cleanup: bool, no_cleanup: bool, param: tuple,
         payload_files: tuple, variants: tuple, output_dir: Optional[str], yes: bool):
    """Test an Azure REST API by providing its documentation URL."""

    # Parse CLI param overrides
    cli_overrides = {}
    for p in param:
        if "=" in p:
            key, val = p.split("=", 1)
            cli_overrides[key] = val

    # Step 1: Parse the doc page
    console.print("\n[bold]Step 1:[/bold] Parsing documentation page...", style="cyan")
    try:
        spec = parse_doc_url(doc_url)
    except Exception as e:
        console.print(f"[red]Failed to parse docs page:[/red] {e}")
        sys.exit(1)

    if not spec.http_method:
        console.print("[red]Could not extract HTTP method from the docs page.[/red]")
        sys.exit(1)

    _display_spec(spec)

    # Step 1b: Enrich with OpenAPI spec
    console.print("\n[bold]Step 1b:[/bold] Enriching with OpenAPI specification...", style="magenta")
    enrichment = enrich_from_openapi(spec.url_template, spec.http_method, spec.api_version)
    _display_enrichment(enrichment)

    # Step 2: Generate payloads
    console.print("\n[bold]Step 2:[/bold] Generating payload variants...", style="cyan")
    config = load_config()
    identity_ctx = get_identity_context(config)
    if identity_ctx.tenant_id:
        console.print(f"  [dim]Identity: {identity_ctx.user_type} (tenant: {identity_ctx.tenant_id[:8]}...)[/dim]")

    payloads = generate_payloads(spec, identity_context=identity_ctx)

    # Add custom payload files
    for pf in payload_files:
        try:
            with open(pf) as f:
                custom_body = json.load(f)
            name = os.path.splitext(os.path.basename(pf))[0]
            payloads.append((f"custom-{name}", custom_body))
            console.print(f"  [green]Loaded custom payload:[/green] {pf}")
        except (json.JSONDecodeError, OSError) as e:
            console.print(f"  [red]Failed to load {pf}:[/red] {e}")

    _display_payloads(payloads)

    # Step 3: Resolve URI parameters
    console.print("\n[bold]Step 3:[/bold] Resolving URI parameters...", style="cyan")
    resolved_params, param_sources, missing_params = resolve_all_uri_params(
        spec.uri_parameters, config, cli_overrides, interactive=not dry_run
    )

    # Show parameter resolution summary
    if resolved_params or missing_params:
        console.print()
        param_table = Table(title="URI Parameter Resolution", box=box.SIMPLE_HEAVY)
        param_table.add_column("Parameter", style="cyan")
        param_table.add_column("Value")
        param_table.add_column("Source", style="dim")

        for name, value in resolved_params.items():
            display_val = value if len(value) <= 40 else value[:37] + "..."
            param_table.add_row(name, f"[green]{display_val}[/green]", param_sources.get(name, ""))

        for name in missing_params:
            param_table.add_row(name, "[red]⚠ NOT SET[/red]", "[yellow]needs --param or config[/yellow]")

        console.print(param_table)

    if missing_params and not dry_run:
        console.print(f"\n[red]Missing required parameters: {', '.join(missing_params)}[/red]")
        console.print("[dim]Provide via --param name=value or add to ~/.azure-api-tester/azure-config.yaml[/dim]")
        sys.exit(1)

    resolved_url = substitute_url(spec.url_template, resolved_params)
    console.print(f"\n  [bold]Resolved URL:[/bold] {resolved_url}")

    # Show prerequisite resources
    prereqs = extract_prerequisites(spec.url_template)
    if prereqs and len(prereqs) > 1:
        parent_resources = prereqs[:-1]
        target = prereqs[-1]
        console.print()
        console.print(Panel(
            f"Target: [bold]{target.friendly_name}[/bold] ({target.resource_type})",
            title="📋 Prerequisites — these resources must exist before running",
            border_style="yellow",
        ))
        prereq_table = Table(box=box.SIMPLE_HEAVY)
        prereq_table.add_column("#", style="dim", justify="right")
        prereq_table.add_column("Resource", style="cyan")
        prereq_table.add_column("Type", style="dim")
        prereq_table.add_column("Param", style="green")
        prereq_table.add_column("Value")

        for idx, pr in enumerate(parent_resources, 1):
            value = resolved_params.get(pr.param_name, "[red]not set[/red]")
            prereq_table.add_row(
                str(idx),
                pr.friendly_name,
                pr.resource_type,
                pr.param_name,
                value,
            )
        console.print(prereq_table)

    # Always save payloads as editable files and create the output folder
    payload_dir = _save_payloads_to_dir(payloads, output_dir or ".", spec.title, spec=spec, doc_url=doc_url)
    console.print(f"\n  [green]Saved payloads to:[/green] {payload_dir}/")
    for name, _ in payloads:
        console.print(f"    {name}.json")

    if dry_run:
        # Show field reference with enums, constraints, and required markers
        _display_field_reference(spec)

        console.print(f"\n  [dim]Edit these files, then run with --payload {payload_dir}/<name>.json[/dim]")

        # Show hints for missing params
        if missing_params:
            console.print("\n[yellow]⚠ To execute this test, you will need to provide:[/yellow]")
            for name in missing_params:
                desc = ""
                for p in spec.uri_parameters:
                    if p.name == name:
                        desc = p.description
                        break
                hint = f"  --param {name}=<value>"
                if desc:
                    hint += f"  [dim]# {desc}[/dim]"
                console.print(hint)
            console.print(f"\n[dim]Or add them to ~/.azure-api-tester/azure-config.yaml under defaults/overrides.[/dim]")

        console.print("\n[yellow]Dry run — skipping execution.[/yellow]")
        return

    # Step 4: Select payloads to execute
    # Filter by --variant if specified
    if variants:
        variant_set = set(variants)
        selected = [(n, b) for n, b in payloads if n in variant_set]
        if not selected:
            console.print(f"[red]No matching variants found. Available: {', '.join(n for n, _ in payloads)}[/red]")
            sys.exit(1)
        payloads = selected
        console.print(f"\n  [dim]Selected variants: {', '.join(n for n, _ in payloads)}[/dim]")
    elif not yes:
        # Interactive picker
        console.print(f"\n[bold]Step 4:[/bold] Select payloads to execute:", style="cyan")
        payloads = _pick_payloads(payloads)
        if not payloads:
            console.print("[yellow]Aborted.[/yellow]")
            return

    n_calls = len(payloads)
    if not yes and not variants:
        console.print(f"\n  Ready to execute [bold]{n_calls}[/bold] API call(s).")
        if not click.confirm("  Proceed?", default=True):
            console.print("[yellow]Aborted.[/yellow]")
            return

    # Step 5: Execute
    console.print("\n[bold]Step 5:[/bold] Executing API calls...\n", style="cyan")

    run_id = str(uuid.uuid4())[:8]
    tracker = Tracker(
        run_id=run_id,
        doc_url=doc_url,
        api_title=spec.title,
        http_method=spec.http_method,
        url_template=spec.url_template,
        log_dir=payload_dir,
    )

    # Acquire a token once for all calls
    try:
        token = get_cached_token(resolved_url)
    except RuntimeError as e:
        console.print(f"[red]Auth failed:[/red] {e}")
        tracker.close()
        sys.exit(1)

    results = []
    for i, (variant_name, body) in enumerate(payloads, 1):
        console.print(f"  [{i}/{n_calls}] {variant_name}...", end=" ")

        result = execute_call(
            method=spec.http_method,
            url=resolved_url,
            body=body,
            variant_name=variant_name,
            tracker=tracker,
            run_id=run_id,
            token=token,
        )
        results.append(result)

        status = result["status"]
        if 200 <= status < 300:
            console.print(f"[green]{status} ✓[/green]  ({result['duration_ms']:.0f}ms)")
        else:
            console.print(f"[red]{status} ✗[/red]  ({result['duration_ms']:.0f}ms)")

    # Step 6: Display results
    _display_results(results)

    # Step 7: Cleanup
    # Step 7: Cleanup (off by default; enabled via --cleanup flag or config)
    do_cleanup = cleanup or config.get("settings", {}).get("autoCleanup", False)
    if no_cleanup:
        do_cleanup = False

    if do_cleanup and spec.http_method in ("PUT", "POST", "PATCH"):
        console.print("\n[bold]Step 6:[/bold] Cleaning up created resources...", style="cyan")
        cleanup_result = cleanup_resource(
            url=resolved_url,
            tracker=tracker,
            run_id=run_id,
            token=token,
        )
        cleanup_status = cleanup_result["status"]
        if 200 <= cleanup_status < 300 or cleanup_status == 202 or cleanup_status == 204:
            console.print(f"  [green]Cleanup: {cleanup_status} ✓[/green]")
        elif cleanup_status == 404:
            console.print("  [yellow]Cleanup: 404 — resource not found (may have already been deleted)[/yellow]")
        else:
            console.print(f"  [red]Cleanup: {cleanup_status} ✗[/red]")

    # Finalize
    tracker.finish()
    console.print(f"\n  [dim]Run ID: {run_id}[/dim]")
    console.print(f"  [dim]Logs:   {tracker.jsonl_path}[/dim]")
    tracker.close()

    # Exit with error if any calls failed
    if tracker.failure_count > 0:
        sys.exit(1)




def _pick_payload_files(payload_dir: str) -> list[tuple[str, dict]]:
    """List .json payload files in a directory and let the user pick which to execute."""
    excluded = {"api-spec.json"}
    json_files = sorted(
        f for f in os.listdir(payload_dir)
        if f.endswith(".json") and f not in excluded
    )
    if not json_files:
        console.print("[red]No payload JSON files found in the directory.[/red]")
        return []

    console.print()
    console.print(f"  [bold]Available payloads in {payload_dir}/:[/bold]")
    for i, name in enumerate(json_files, 1):
        console.print(f"  [bold cyan][{i}][/bold cyan] {name}")

    console.print()
    selection = input("  Select payloads (e.g. 1,2 / all / q to quit): ").strip().lower()

    if selection in ("q", "quit", "exit"):
        return []

    if selection in ("all", "a", ""):
        indices = list(range(len(json_files)))
    else:
        indices = []
        for part in selection.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(json_files):
                    indices.append(idx)

    selected = []
    for idx in indices:
        filepath = os.path.join(payload_dir, json_files[idx])
        try:
            with open(filepath) as f:
                body = json.load(f)
            selected.append((json_files[idx], body))
        except (json.JSONDecodeError, OSError) as e:
            console.print(f"  [red]Failed to load {json_files[idx]}:[/red] {e}")
    return selected


@main.command()
@click.argument("payload_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--param", multiple=True, help="Override URI param: --param name=value")
@click.option("--cleanup", is_flag=True, default=False, help="Auto-cleanup created resources after testing.")
@click.option("--no-cleanup", is_flag=True, help="Force skip cleanup even if enabled in config.")
@click.option("-y", "--yes", is_flag=True, help="Skip interactive picker, run all payloads.")
def execute(payload_dir: str, param: tuple, cleanup: bool, no_cleanup: bool, yes: bool):
    """Execute API test from an existing output folder (no re-parsing needed)."""

    # Load api-spec.json
    spec_path = os.path.join(payload_dir, "api-spec.json")
    if not os.path.exists(spec_path):
        console.print(f"[red]api-spec.json not found in {payload_dir}.[/red]")
        console.print("[dim]Run \'azure-api-tester test <URL> --dry-run\' first to create the output folder.[/dim]")
        sys.exit(1)

    with open(spec_path) as f:
        spec_meta = json.load(f)

    console.print()
    console.print(Panel(
        f"[bold]{spec_meta['title']}[/bold]\n"
        f"Method: {spec_meta['http_method']}  API: {spec_meta['api_version']}\n"
        f"URL: [dim]{spec_meta['url_template']}[/dim]",
        title="📄 API Spec (from cached metadata)",
        border_style="cyan",
    ))

    # Step 1: Offer to run create-prerequisites.sh
    prereq_script = os.path.join(payload_dir, "create-prerequisites.sh")
    if os.path.exists(prereq_script):
        console.print()
        console.print(f"  [yellow]Found prerequisite script:[/yellow] {prereq_script}")
        if click.confirm("  Run create-prerequisites.sh now?", default=False):
            console.print()
            console.print("[bold]Running create-prerequisites.sh...[/bold]")
            result = subprocess.run(["bash", prereq_script], cwd=payload_dir)
            if result.returncode != 0:
                console.print(f"[red]Script exited with code {result.returncode}[/red]")
                if not click.confirm("  Continue with execution anyway?", default=False):
                    sys.exit(1)
            else:
                console.print("[green]Prerequisites script completed successfully.[/green]")
            console.print()

    # Step 2: Parse CLI param overrides
    cli_overrides = {}
    for p in param:
        if "=" in p:
            key, val = p.split("=", 1)
            cli_overrides[key] = val

    # Step 3: Resolve URI parameters
    config = load_config()

    # Build lightweight param objects from the saved metadata
    class _Param:
        def __init__(self, d):
            self.name = d["name"]
            self.location = d.get("location", "path")
            self.required = d.get("required", True)
            self.type = d.get("type", "string")
            self.description = d.get("description", "")

    uri_params = [_Param(p) for p in spec_meta.get("uri_parameters", [])]

    resolved_params, param_sources, missing_params = resolve_all_uri_params(
        uri_params, config, cli_overrides, interactive=True
    )

    if missing_params:
        console.print(f"\n[red]Missing required parameters: {', '.join(missing_params)}[/red]")
        console.print("[dim]Provide via --param name=value or add to ~/.azure-api-tester/azure-config.yaml[/dim]")
        sys.exit(1)

    resolved_url = substitute_url(spec_meta["url_template"], resolved_params)
    console.print(f"\n  [bold]Resolved URL:[/bold] {resolved_url}")

    # Step 4: Pick payloads
    if yes:
        # Load all payload files
        excluded = {"api-spec.json"}
        json_files = sorted(
            f for f in os.listdir(payload_dir)
            if f.endswith(".json") and f not in excluded
        )
        selected = []
        for fname in json_files:
            try:
                with open(os.path.join(payload_dir, fname)) as f:
                    selected.append((fname, json.load(f)))
            except (json.JSONDecodeError, OSError):
                pass
    else:
        console.print("\n[bold]Select payloads to execute:[/bold]")
        selected = _pick_payload_files(payload_dir)

    if not selected:
        console.print("[yellow]No payloads selected. Aborted.[/yellow]")
        return

    n_calls = len(selected)
    if not yes:
        console.print(f"\n  Ready to execute [bold]{n_calls}[/bold] API call(s).")
        if not click.confirm("  Proceed?", default=True):
            console.print("[yellow]Aborted.[/yellow]")
            return

    # Step 5: Execute
    console.print(f"\n[bold]Executing {n_calls} API call(s)...[/bold]\n", style="cyan")

    run_id = str(uuid.uuid4())[:8]
    tracker = Tracker(
        run_id=run_id,
        doc_url=spec_meta.get("doc_url", ""),
        api_title=spec_meta["title"],
        http_method=spec_meta["http_method"],
        url_template=spec_meta["url_template"],
        log_dir=payload_dir,
    )

    try:
        token = get_cached_token(resolved_url)
    except RuntimeError as e:
        console.print(f"[red]Auth failed:[/red] {e}")
        tracker.close()
        sys.exit(1)

    results = []
    for i, (variant_name, body) in enumerate(selected, 1):
        display_name = variant_name.replace(".json", "")
        console.print(f"  [{i}/{n_calls}] {display_name}...", end=" ")

        result = execute_call(
            method=spec_meta["http_method"],
            url=resolved_url,
            body=body,
            variant_name=display_name,
            tracker=tracker,
            run_id=run_id,
            token=token,
        )
        results.append(result)

        status = result["status"]
        if 200 <= status < 300:
            console.print(f"[green]{status} ✓[/green]  ({result['duration_ms']:.0f}ms)")
        else:
            console.print(f"[red]{status} ✗[/red]  ({result['duration_ms']:.0f}ms)")

    # Display results
    _display_results(results)

    # Cleanup
    do_cleanup = cleanup or config.get("settings", {}).get("autoCleanup", False)
    if no_cleanup:
        do_cleanup = False

    if do_cleanup and spec_meta["http_method"] in ("PUT", "POST", "PATCH"):
        console.print("\n[bold]Cleaning up created resources...[/bold]", style="cyan")
        cleanup_result = cleanup_resource(
            url=resolved_url, tracker=tracker, run_id=run_id, token=token,
        )
        cleanup_status = cleanup_result["status"]
        if 200 <= cleanup_status < 300 or cleanup_status in (202, 204):
            console.print(f"  [green]Cleanup: {cleanup_status} ✓[/green]")
        elif cleanup_status == 404:
            console.print("  [yellow]Cleanup: 404 — resource not found[/yellow]")
        else:
            console.print(f"  [red]Cleanup: {cleanup_status} ✗[/red]")

    # Finalize
    tracker.finish()
    console.print(f"\n  [dim]Run ID: {run_id}[/dim]")
    console.print(f"  [dim]Logs:   {tracker.jsonl_path}[/dim]")
    tracker.close()

    if tracker.failure_count > 0:
        sys.exit(1)


@main.command()
@click.option("--run-id", help="Show details for a specific run.")
@click.option("--limit", default=20, help="Number of recent runs to show.")
def history(run_id: Optional[str], limit: int):
    """View past test run history."""

    if run_id:
        run_info, calls = get_run_details(run_id)
        if not run_info:
            console.print(f"[red]Run not found: {run_id}[/red]")
            sys.exit(1)

        console.print(Panel(
            f"[bold]{run_info['api_title']}[/bold]\n"
            f"Method: {run_info['http_method']}\n"
            f"URL: {run_info['url_template']}\n"
            f"Started: {run_info['started_at']}\n"
            f"Calls: {run_info['total_calls']} "
            f"([green]{run_info['success_count']} pass[/green] / "
            f"[red]{run_info['failure_count']} fail[/red])",
            title=f"Run {run_id}",
            border_style="cyan",
        ))

        if calls:
            table = Table(box=box.SIMPLE_HEAVY)
            table.add_column("Variant", style="cyan")
            table.add_column("Status", justify="center")
            table.add_column("Duration", justify="right")
            table.add_column("Cleanup", justify="center")

            for c in calls:
                status = c["response_status"]
                if 200 <= status < 300:
                    status_str = f"[green]{status}[/green]"
                else:
                    status_str = f"[red]{status}[/red]"

                table.add_row(
                    c["variant_name"],
                    status_str,
                    f"{c['duration_ms']:.0f}ms",
                    "🧹" if c["is_cleanup"] else "",
                )
            console.print(table)

            # Show request/response for each call
            for c in calls:
                console.print(f"\n[bold cyan]── {c['variant_name']} ──[/bold cyan]")
                if c["request_body"]:
                    try:
                        body = json.loads(c["request_body"]) if isinstance(c["request_body"], str) else c["request_body"]
                        console.print("[dim]Request:[/dim]")
                        console.print(Syntax(json.dumps(body, indent=2), "json", theme="monokai"))
                    except (json.JSONDecodeError, TypeError):
                        console.print(f"[dim]Request:[/dim] {c['request_body']}")

                if c["response_body"]:
                    console.print("[dim]Response:[/dim]")
                    try:
                        resp = json.loads(c["response_body"]) if isinstance(c["response_body"], str) else c["response_body"]
                        console.print(Syntax(json.dumps(resp, indent=2), "json", theme="monokai"))
                    except (json.JSONDecodeError, TypeError):
                        console.print(c["response_body"])

    else:
        runs = get_run_history(limit=limit)
        if not runs:
            console.print("[dim]No test runs recorded yet.[/dim]")
            return

        table = Table(title="📋 Test Run History", box=box.ROUNDED)
        table.add_column("Run ID", style="cyan")
        table.add_column("API", max_width=30)
        table.add_column("Method", justify="center")
        table.add_column("Started", style="dim")
        table.add_column("Calls", justify="center")
        table.add_column("Pass", justify="center", style="green")
        table.add_column("Fail", justify="center", style="red")
        table.add_column("Folder", style="dim", max_width=40)

        for r in runs:
            folder = r.get("payload_dir", "")
            table.add_row(
                r["id"],
                r["api_title"] or "—",
                r["http_method"] or "—",
                r["started_at"][:19] if r["started_at"] else "—",
                str(r["total_calls"]),
                str(r["success_count"]),
                str(r["failure_count"]),
                os.path.basename(folder) if folder else "—",
            )

        console.print(table)
        console.print("\n[dim]Use --run-id <id> for details.[/dim]")


if __name__ == "__main__":
    main()
