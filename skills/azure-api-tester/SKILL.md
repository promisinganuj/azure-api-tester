---
name: azure-api-tester
description: Automatically test Azure REST APIs from a Microsoft Learn documentation URL. Parses the API spec, cross-references the OpenAPI specification from azure-rest-api-specs, identifies parent and payload dependencies, generates a prerequisite Azure CLI setup script, and guides the user through resource creation before executing API calls.
---

## When to Use

Load this skill when the user wants to:
- Test an Azure REST API from a Microsoft Learn documentation URL
- Discover what Azure resources the API depends on
- Generate an az CLI setup script for missing prerequisite resources
- Execute an Azure REST API call and see the results

Trigger phrases:
- "Test this API: https://learn.microsoft.com/en-us/rest/api/..."
- "Figure out what Azure resources this API depends on"
- "Create a setup script for the API prerequisites"
- "Run this Azure REST API"

## Workflow

### Step 1 — Parse the API documentation and enrich with OpenAPI spec

Run the tool in dry-run mode to parse the documentation URL without making any API calls:

```bash
azure-api-tester test "<DOCS_URL>" --dry-run
```

If `azure-api-tester` is not found in PATH:
```bash
python3 -m azure_api_tester.cli test "<DOCS_URL>" --dry-run
```

The dry-run performs two passes:

**Pass 1 — HTML parsing** (from Microsoft Learn docs page):
- The API method and URI path template
- All required and optional path parameters
- All required and optional body fields
- Any prerequisite resource statements in the docs

**Pass 2 — OpenAPI spec enrichment** (from `Azure/azure-rest-api-specs` on GitHub):
- Automatically maps the provider namespace and api-version to the corresponding spec file
- Fetches and parses the OpenAPI JSON to extract:
  - **`format: arm-id`** fields — explicitly identifies body fields referencing other Azure resources
  - **`required` arrays** — corrects HTML-scraped required/optional markings
  - **`readOnly: true`** — more accurate than pattern matching field names
  - **Enum values** — complete enum sets with names and descriptions
  - **Default values** — values the HTML may have missed

If the OpenAPI spec cannot be located, the tool falls back to HTML-only mode.

The tool always creates an output folder at `./api-test-payloads-<API-Title>/` containing:
- Generated payload JSON files (editable by the user)
- `api-spec.json` — cached API metadata for the `execute` subcommand

Then classify every dependency using [./references/dependency-rules.md](./references/dependency-rules.md) and present the summary.

**Formatting rules:**
- Never use markdown tables outside fenced code blocks in user-facing output.
- Use fenced `text` blocks for all summaries.
- Keep the layout close to CLI output for readability.
- When a field has `format: arm-id`, mark it as `openapi-confirmed` in the Dependency Discovery block.

### Step 2 — Generate the prerequisite setup script

Using the classification from Step 1 and the template at [./scripts/create-prerequisites.sh](./scripts/create-prerequisites.sh), generate a filled-in `create-prerequisites.sh` script and save it into the output folder (e.g. `./api-test-payloads-<API-Title>/create-prerequisites.sh`).

Script content rules:
- Fill in resource names and parameter values inferred from the API URL and dry-run output.
- **Tier 1 blocks** (URI parent resources): uncommented, safe to run as-is.
- **Tier 2 blocks** (payload-linked resources): commented out by default. If confirmed via `format: arm-id`, note it is spec-confirmed.
- **Tier 3 fields**: do not script. Mention them only in the printed output.
- **Tier 4 values**: add `# MANUAL: Supply <description> as --param <fieldName>=<value>` comments.
- End the script with an `echo` block listing all `--param` flags to pass to `azure-api-tester`.

### Step 3 — Present information to the user

After generating the script, present this fixed summary:

```text
Prerequisite Setup Script
  Path     : ./api-test-payloads-<API-Title>/create-prerequisites.sh
  Scripted : <Tier 1 resource names>                 [uncommented -- safe to run]
  Suggested: <Tier 2 resource names>                 [commented -- review & enable if needed]
  Manual   : Secrets, keys, connection strings       [instructions only]

Output folder contents:
  api-spec.json              (API metadata -- do not edit)
  docs-sample.json           (payload -- editable)
  full.json                  (payload -- editable)
  create-prerequisites.sh    (setup script -- review before running)
```

Remind the user:
- They can edit the payload JSON files before execution.
- They can add custom `.json` payload files to the folder.
- The `execute` subcommand will pick up all `.json` files in the folder.

### Step 4 — Execute from the output folder

Use the `execute` subcommand to run the API test from the output folder **without re-parsing**:

```bash
azure-api-tester execute ./api-test-payloads-<API-Title>/
```

Or with parameter overrides:
```bash
azure-api-tester execute ./api-test-payloads-<API-Title>/ --param accountName=my-account
```

The `execute` subcommand will:
1. Load the cached API metadata from `api-spec.json`
2. Ask: "Run `create-prerequisites.sh` first?" — if yes, runs it via subprocess
3. Resolve URI parameters from config + `--param` overrides
4. Present the payload file picker — user selects which `.json` file(s) to execute
5. Execute the selected payload(s) against the API
6. Store logs locally in `./api-test-payloads-<API-Title>/logs/`

After the test runs, summarize:
```text
Test Results
  Run ID   : <id>
  Variants : <count>

  Variant          Status   HTTP
  -------------------------------------------
  <variant-name>   pass     200
  <variant-name>   fail     400   <error>

  Logs  : ./api-test-payloads-<API-Title>/logs/
```

To skip the interactive picker and run all payloads:
```bash
azure-api-tester execute ./api-test-payloads-<API-Title>/ -y
```

### View history

```bash
# List recent test runs (from any folder)
azure-api-tester history

# Show details of a specific run
azure-api-tester history --run-id <ID>
```

History uses a global index that maps each run to its output folder. Logs and call details are stored locally in each folder's `tracker.db`.

## Configuration

```yaml
defaults:
  subscriptionId: "auto"
  resourceGroupName: "my-test-rg"
  location: "eastus"

overrides:
  workspaceName: "my-workspace"
```

Config location: `~/.azure-api-tester.yaml`.
Parameters can be supplied either with `--param key=value` or under `defaults`/`overrides` in config.

## Notes

- The user must be logged in to Azure (`az login`) before testing APIs.
- The tool auto-detects `subscriptionId`, `tenantId`, and `principalId` from `az` CLI context.
- Read-only fields are automatically excluded from generated payloads.
- Logs are stored locally in each output folder's `logs/` subdirectory.
- A global index at `~/.azure-api-tester/index.db` maps run IDs to folders for `history` lookups.
- Generated prerequisite scripts are review-before-run artifacts -- never auto-execute them without user confirmation.
- OpenAPI spec files are cached in `~/.azure-api-tester/specs/` for 24 hours.
- Set `GITHUB_TOKEN` env var to increase GitHub API rate limits for spec fetching (optional).
