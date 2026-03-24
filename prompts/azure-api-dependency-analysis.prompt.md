---
name: azure-api-dependency-analysis
description: Analyze Azure REST API dependencies and generate a setup plan without running any API calls or tests. Identifies Tier 1 parent resources from the URI path, Tier 2 payload-linked resources from body fields, and produces a tiered dependency report with an optional az CLI setup script. Use this before running azure-api-tester when you want to plan infrastructure first.
argument-hint: "<Microsoft Learn REST API docs URL>"
---

Analyze the prerequisites for the Azure REST API documentation URL provided, without running any API calls or tests.

## Steps

### 1. Parse the API documentation

Fetch and parse the Microsoft Learn documentation page to extract:
- The full URI path template and all path parameter names
- All required and optional body fields in the request payload
- Any explicit prerequisite statements in the docs (e.g. "this resource must exist before...")

### 2. Classify every dependency

Apply the classification rules from [../skills/azure-api-tester/references/dependency-rules.md](../skills/azure-api-tester/references/dependency-rules.md):

- **Tier 1** — URI parent resources: segments like `/resourceGroups/{name}` or `/workspaces/{name}` found directly in the path template
- **Tier 2** — Payload-linked resources: body fields matching patterns like `storageAccountId`, `keyVaultId`, `subnetId`, `applicationInsightsId`, `containerRegistryId`
- **Tier 3** — Advanced dependencies: `userAssignedIdentityId`, `logAnalyticsWorkspaceId`, and similar context-dependent fields
- **Tier 4** — Manual-only: secrets, keys, connection strings, role assignments — never scripted

### 3. Present the dependency report

Use this fixed format:

```text
Dependency Analysis Report
  API      : <operation name from docs>
  Method   : <HTTP method>
  Endpoint : <URI path template>

  Tier 1 — URI parent resources (required, safe to script)
    Resource Group              : required by /resourceGroups/{resourceGroupName}
    <other Tier 1 resources from path>

  Tier 2 — Payload-linked resources (likely needed, commented in script)
    storageAccountId            : Storage Account   (likely required)
    keyVaultId                  : Key Vault          (likely required)
    <other Tier 2 fields>

  Tier 3 — Advanced dependencies (Field Recommendations only, not scripted)
    userAssignedIdentityId      : User Assigned Managed Identity   (context-dependent)
    <other Tier 3 fields>

  Tier 4 — Manual-only (cannot be automated)
    <secrets, keys, policies, role assignments>
```

### 4. Ask whether to generate the setup script

> "Would you like me to generate a prerequisite setup script based on this analysis?"
>
> - **Yes** -> Copy and fill in [../skills/azure-api-tester/scripts/create-prerequisites.sh](../skills/azure-api-tester/scripts/create-prerequisites.sh) with the specific resource names inferred from this API. Tier 1 blocks are uncommented. Tier 2 blocks are commented with clear enable-instructions. Present the script path and a summary of what it covers.
> - **No, I will supply existing values** -> List the exact --param flags or config.yml entries the user needs to populate, grouped by tier.

### 5. Do not call any Azure APIs or run azure-api-tester

This prompt is for planning only. No API calls, no CLI executions, and no test runs.
