# Project Guidelines

## Overview

Azure API Tester — a Python CLI tool + Copilot skill that tests Azure REST APIs from a Microsoft Learn documentation URL. See [README.md](../README.md) for full project docs, installation, and usage.

## Architecture

Linear pipeline orchestrated by a Click CLI (`cli.py`):

```
doc_parser → spec_enricher → identity_resolver → payload_generator → config → api_caller → tracker → cleanup
```

- **doc_parser**: Scrapes MS Learn HTML → `ApiSpec` dataclass (method, URL, params, body schema, enums)
- **spec_enricher**: Fetches OpenAPI JSON from `Azure/azure-rest-api-specs` GitHub → `OpenApiEnrichment` (arm-id fields, required/readonly, defaults). 24h file cache in `~/.azure-api-tester/specs/`
- **identity_resolver**: Shells out to `az` CLI → `IdentityContext` (tenantId, principalId, UAMI)
- **payload_generator**: Produces up to 3 variants (docs-sample, minimal, full). Skips read-only fields
- **config**: YAML at `~/.azure-api-tester/azure-config.yaml`. Priority: CLI `--param` > overrides > defaults > auto-detect > interactive prompt
- **api_caller**: Bearer token via `az account get-access-token`. Tokens are redacted in logs
- **tracker**: Dual-write JSONL + SQLite per output folder; global SQLite index at `~/.azure-api-tester/index.db`
- **cleanup**: DELETE + async polling (max 300s, 10s intervals)

All data structures are Python `dataclasses` — no Pydantic or attrs.

## Build and Test

```bash
# Install (editable)
cd utils/azure-api-tester
pip install -e .

# Or use the one-command installer
bash install.sh
```

- Entry point: `azure-api-tester` → `azure_api_tester.cli:main`
- Python ≥ 3.9, dependencies in [requirements.txt](../utils/azure-api-tester/requirements.txt)
- No test suite, CI pipeline, or linters exist yet

## Conventions

- **Type hints everywhere** — use Python 3.9+ syntax (`list[str]`, `dict[str, Any]`, `Optional[X]`)
- **Dataclasses** for all data structures (not Pydantic)
- **Private functions** prefixed with `_` (e.g., `_smart_value`, `_resolve_ref`)
- **No Python `logging` module** — all output uses `rich.console.Console` with Rich markup (`[green]`, `[red]`, `[dim]`)
- **Relative imports** between sibling modules (`from .doc_parser import ApiSpec`)
- **Error handling**: `try/except` with `sys.exit(1)` for fatal CLI errors; enrichment returns empty result with `error=` field instead of crashing
- **HTTP timeouts**: always specified (15–120s); `requests.RequestException` caught broadly
- **Security**: bearer tokens redacted in logged headers, passwords get random suffixes, credentials delegated to `az` CLI

## Key Documentation

- [SKILL.md](../skills/azure-api-tester/SKILL.md) — Copilot skill workflow (4-step guided process)
- [dependency-rules.md](../skills/azure-api-tester/references/dependency-rules.md) — 4-tier dependency classification (required → manual-only) with OpenAPI signal mappings
- [create-prerequisites.sh](../skills/azure-api-tester/scripts/create-prerequisites.sh) — template setup script for Azure resources
- [azure-api-dependency-analysis.prompt.md](../prompts/azure-api-dependency-analysis.prompt.md) — planning-only Copilot prompt (no execution)
