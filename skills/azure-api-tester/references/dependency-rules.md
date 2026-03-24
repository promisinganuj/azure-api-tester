# Dependency Classification Rules

Use these rules to classify Azure dependencies discovered from API documentation URLs.
Apply when building the Dependency Discovery output block and generating the prerequisite setup script.

## Confidence Tiers

| Tier | Label | Script behavior |
|------|-------|-----------------|
| 1 | `required` | Always create. Blocks execution if missing. Uncommented in script. |
| 2 | `likely` | Usually required. Commented in script — user enables if needed. |
| 3 | `advanced` | Optional or context-dependent. Listed in Field Recommendations only. |
| 4 | `manual-only` | Cannot be scripted. Comment with instructions only. |

---

## OpenAPI Spec Signals

When the OpenAPI spec from `Azure/azure-rest-api-specs` is available, use these signals to **confirm or upgrade** tier classification:

| Signal | Effect |
|--------|--------|
| `format: "arm-id"` on a body field | Confirms Tier 2 classification. Add `(openapi-confirmed)` tag in Dependency Discovery. |
| `x-ms-arm-id-details.allowedResources[].type` | Identifies the exact Azure resource type the field references. Use this to generate the correct `az` CLI command in the script. |
| `readOnly: true` on a field | Confirms the field should be excluded from payloads. More reliable than name-based matching. |
| `x-ms-mutability: ["read"]` | Same as `readOnly: true` — exclude from payloads. |
| `x-ms-mutability: ["create"]` | Field can only be set at creation time, not updated. |
| `required` array in the spec | **Overrides** HTML-inferred required/optional status. OpenAPI always wins. |
| `enum` values in the spec | **Supplements** HTML-scraped enum values. Use the spec's list as authoritative. |
| `default` value in the spec | Pre-fill in generated payloads when the HTML didn't provide a default. |

If the spec is unavailable, fall back to HTML-only classification using the field name patterns below.

---

## Tier 1 — URI Parent Resources (required, safe to script)

Classify as **Tier 1** when the segment appears directly in the URI path template:

| URI segment | Azure resource | az CLI command |
|-------------|---------------|----------------|
| `/resourceGroups/{name}` | Resource Group | `az group create` |
| `/workspaces/{name}` | ML / Synapse / AML workspace | `az ml workspace create` or `az synapse workspace create` |
| `/accounts/{name}` | Cognitive Services / Storage account | `az cognitiveservices account create` |
| `/vaults/{name}` | Key Vault | `az keyvault create` |
| `/namespaces/{name}` | Event Hub / Service Bus namespace | `az eventhubs namespace create` / `az servicebus namespace create` |
| `/clusters/{name}` | AKS / HDInsight cluster | `az aks create` / `az hdinsight create` |
| `/servers/{name}` | SQL / PostgreSQL / MySQL server | `az sql server create` / `az postgres server create` |
| `/registries/{name}` | Container Registry | `az acr create` |

---

## Tier 2 — Payload-Linked Resources (likely, commented in script)

Classify as **Tier 2** when a required or commonly-set body field matches these patterns:

| Field name pattern | Azure resource type | Commented script command |
|--------------------|--------------------|--------------------------|
| `storageId`, `storageAccountId`, `storage_account_resource_id` | Storage Account | `az storage account create` |
| `keyVaultId`, `key_vault_id`, `keyVaultResourceId` | Key Vault | `az keyvault create` |
| `subnetId`, `subnet_resource_id` | Virtual Network + Subnet | `az network vnet create` + `az network vnet subnet create` |
| `applicationInsightsId`, `app_insights_id` | Application Insights | `az monitor app-insights component create` |
| `containerRegistryId`, `container_registry_id` | Container Registry | `az acr create` |

After a commented Tier 2 command, add an `export <VAR>=$(az ... --query id -o tsv)` line so the user can capture the ID for `--param` once they enable the block.

---

## Tier 3 — Advanced Dependencies (Field Recommendations only, never scripted)

| Field name pattern | Resource | Reason not scripted |
|--------------------|----------|---------------------|
| `userAssignedIdentityId`, `user_assigned_identity_resource_id` | User Assigned Managed Identity | Requires workload-specific role assignment decisions |
| `logAnalyticsWorkspaceId`, `workspaceId` (Log Analytics) | Log Analytics Workspace | Optional observability; usually pre-existing |
| `acrLoginServer`, `imageUri` | Container images | Build-pipeline concern, not infrastructure provisioning |
| `privateLinkServiceId` | Private Link service | High-risk networking change; always manual |

List Tier 3 fields in the **Field Recommendations** section only. Do not generate any `az` commands for them.

---

## Tier 4 — Manual-Only (never script, always comment)

Never generate creation or mutation commands for:

- Secrets, passwords, access keys, SAS tokens, connection strings
- Role assignments and custom policy assignments
- Private endpoints and private DNS zones
- Certificates and TLS/SSL configurations
- Cross-tenant or cross-subscription references

Annotate in the script with:

```bash
# MANUAL: Supply <description> as --param <fieldName>=<value> or in config.yml
# Do not automate. See API docs for the required format.
```

---

## Script Structure Rules (Balanced Mode)

1. Set environment variables at the top for all known configuration values.
2. Use clearly labelled comment headers:
   - `# === A: PARENT RESOURCES (Tier 1 — required) ===`
   - `# === B: PAYLOAD-LINKED RESOURCES (Tier 2 — review before enabling) ===`
   - `# === C: MANUAL STEPS (Tier 4 — do not automate) ===`
3. Tier 2 blocks must be commented-out by default with a short explanation above each block:
   ```
   # Enable this block if the API requires a storageAccountId in the payload.
   # STORAGE_NAME="..."
   # az storage account create ...
   ```
4. After each Tier 1 or Tier 2 command that produces an ID needed downstream, capture it:
   ```
   STORAGE_ID=$(az storage account show --name "$STORAGE_NAME" \
     --resource-group "$RG_NAME" --query id -o tsv)
   export STORAGE_ID
   ```
5. End the script with a summary `echo` block listing the `--param` flags to pass to `azure-api-tester`.
