# Azure API Tester

Automatically test Azure REST APIs by providing a Microsoft Learn documentation URL.

Parses the API spec, cross-references the OpenAPI specification from [`Azure/azure-rest-api-specs`](https://github.com/Azure/azure-rest-api-specs), identifies parent and payload dependencies, generates a prerequisite Azure CLI setup script, and guides you through resource creation before executing API calls.

## Prerequisites

- Python 3.9+
- [uv](https://docs.astral.sh/uv/) (auto-installed by `install.sh` if missing)
- Azure CLI (`az`) installed and authenticated (`az login`)
- [GitHub Copilot](https://github.com/features/copilot) (optional — for the Copilot skill integration)

## Installation

```bash
git clone https://github.com/promisinganuj/azure-api-tester.git
cd azure-api-tester
bash install.sh
```

The installer will:
1. Install [`uv`](https://docs.astral.sh/uv/) if not already available, then create a venv and install the CLI tool
2. Create a wrapper script at `~/.copilot/bin/azure-api-tester` so the command works from any terminal
3. Create a default global configuration file at `~/.azure-api-tester/azure-config.yaml`
4. Ask whether to install the **Copilot skill** (copies files to `~/.copilot/skills/`)
5. Ask whether to install the **dependency-analysis prompt** (copies to `~/.copilot/.github/prompts/`)

> **Note:** Ensure `~/.copilot/bin` is in your PATH. Add to your shell profile if needed:
> ```bash
> export PATH="$HOME/.copilot/bin:$PATH"
> ```

### Manual installation

If you prefer to install manually, see the individual steps below.

<details>
<summary>Manual steps</summary>

#### 1. Install the CLI tool

```bash
# Install uv if not already available
curl -LsSf https://astral.sh/uv/install.sh | sh

cd utils/azure-api-tester
uv venv .venv
uv pip install -e . --python .venv/bin/python

# Create wrapper so the command is available globally
mkdir -p ~/.copilot/bin
echo '#!/usr/bin/env bash' > ~/.copilot/bin/azure-api-tester
echo "exec \"$(pwd)/.venv/bin/azure-api-tester\" \"\\$@\"" >> ~/.copilot/bin/azure-api-tester
```

#### 2. Create default configuration file

```bash
mkdir -p ~/.azure-api-tester
cat > ~/.azure-api-tester/azure-config.yaml << 'EOF'
defaults:
  subscriptionId: "auto"
  resourceGroupName: "rg-azure-api-tester"
  location: "eastus"

  uamiResourceIds:
    - "/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{name}"

settings:
  autoCleanup: false

overrides:
  workspaceName: "my-aml-workspace"
  endpointName: "test-ep-{random}"
EOF
```

#### 3. (Optional) Install the Copilot skill

```bash
mkdir -p ~/.copilot/skills/azure-api-tester/references
mkdir -p ~/.copilot/skills/azure-api-tester/scripts

cp skills/azure-api-tester/SKILL.md       ~/.copilot/skills/azure-api-tester/
cp skills/azure-api-tester/references/dependency-rules.md \
                                          ~/.copilot/skills/azure-api-tester/references/
cp skills/azure-api-tester/scripts/create-prerequisites.sh \
                                          ~/.copilot/skills/azure-api-tester/scripts/
```

#### 4. (Optional) Install the companion prompt

```bash
mkdir -p ~/.copilot/.github/prompts
cp prompts/azure-api-dependency-analysis.prompt.md ~/.copilot/.github/prompts/
```

</details>

## Quick Start

```bash
# Login to Azure
az login

# Test an API — just provide the docs URL
azure-api-tester test "https://learn.microsoft.com/en-us/rest/api/azureml/batch-endpoints/create-or-update?view=rest-azureml-2025-12-01&tabs=HTTP"

# Dry run — generates payloads and saves them as editable JSON files
azure-api-tester test "<docs-url>" --dry-run

# Override URI parameters inline
azure-api-tester test "<docs-url>" --param resourceGroupName=my-rg --param workspaceName=ws1

# Execute all payloads without interactive picker
azure-api-tester test "<docs-url>" -y

# Enable auto-cleanup of created resources (off by default)
azure-api-tester test "<docs-url>" --cleanup

# View past test runs
azure-api-tester history

# View details of a specific run
azure-api-tester history --run-id <id>
```

If using the Copilot skill, just say:
```
Test this API: https://learn.microsoft.com/en-us/rest/api/...
```

## What It Does

1. **Parses** the Azure REST API docs page (extracts method, URL, params, body schema, enums, samples)
2. **Enriches** with the OpenAPI spec from `Azure/azure-rest-api-specs` (arm-id fields, required arrays, read-only markers, enums, defaults)
3. **Generates** up to 3 payload variants automatically:
   - **Docs Sample** — the exact example from the documentation
   - **Minimal** — only required fields with smart defaults (omitted if none required)
   - **Full** — all writable fields populated with realistic values
4. **Classifies** dependencies into 4 tiers (required → manual-only) and generates a prerequisite setup script
5. **Saves** payloads as editable JSON files (during `--dry-run`)
6. **Executes** selected API calls with your Azure credentials
7. **Tracks** every request/response in JSONL logs + SQLite database
8. **Cleans up** created resources on request (`--cleanup` flag or `autoCleanup: true` in config)

## Configuration

The installer creates a default configuration file at `~/.azure-api-tester/azure-config.yaml`. This file acts as the **global configuration** — if you have stable deployed resources (e.g., a resource group, workspace, or managed identity), add them here so they are automatically referenced during API execution.

You can edit the file at any time:

```yaml
defaults:
  subscriptionId: "auto"            # "auto" reads from az account show
  resourceGroupName: "my-test-rg"
  location: "eastus"

  # User Assigned Managed Identity ARM resource IDs (for APIs that use UAMI)
  uamiResourceIds:
    - "/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{name}"

settings:
  autoCleanup: false  # Set to true to auto-DELETE created resources after testing

overrides:
  workspaceName: "my-aml-workspace"
  endpointName: "test-ep-{random}"  # {random} generates a unique suffix
```

### Identity auto-detection

The tool automatically resolves identity values:
- **tenantId** — from `az account show`
- **principalId** — from `az ad sp show` (service principal) or `az ad signed-in-user show` (user)
- **UAMI resource IDs** — from the `uamiResourceIds` config list

Read-only fields (`provisioningState`, `scoringUri`, `swaggerUri`, etc.) are automatically excluded from generated payloads. Any URI parameter not found in the config will trigger an interactive prompt.

## Logs & History

- **JSONL logs**: `~/.azure-api-tester/logs/<timestamp>_<api>.jsonl`
- **SQLite DB**: `~/.azure-api-tester/tracker.db`

```bash
azure-api-tester history
azure-api-tester history --run-id <id>
```

## Repo Structure

```
├── install.sh                     # One-command installer
├── utils/
│   └── azure-api-tester/          # Python CLI package
│       ├── setup.py               # Package setup (pip install -e .)
│       ├── requirements.txt       # Python dependencies
│       └── azure_api_tester/      # Source modules
│           ├── __init__.py
│           ├── cli.py             # Main CLI entry point (Click-based)
│           ├── doc_parser.py      # MS Learn page scraper/parser
│           ├── spec_enricher.py   # OpenAPI spec fetcher/enricher
│           ├── payload_generator.py # Payload variant generator
│           ├── config.py          # Config + interactive prompt
│           ├── api_caller.py      # Token acquisition + HTTP calls
│           ├── tracker.py         # JSONL + SQLite logging
│           ├── cleanup.py         # Auto-cleanup with async polling
│           └── identity_resolver.py # Azure identity detection
├── skills/                        # Copilot skill definition
│   └── azure-api-tester/
│       ├── SKILL.md               # Skill workflow instructions
│       ├── references/
│       │   └── dependency-rules.md    # Tier classification rules
│       └── scripts/
│           └── create-prerequisites.sh # Template setup script
└── prompts/                       # Copilot prompt
    └── azure-api-dependency-analysis.prompt.md
```

## License

MIT
