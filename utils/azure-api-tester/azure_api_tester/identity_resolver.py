"""Resolve real Azure identity values for payload generation."""

import json
import subprocess
from typing import Optional
from dataclasses import dataclass


@dataclass
class IdentityContext:
    """Identity information from the logged-in Azure session."""
    tenant_id: str
    principal_id: str  # Object ID of the logged-in user/SP
    client_id: Optional[str] = None  # Only for service principals
    user_type: str = "user"  # "user" or "servicePrincipal"
    uami_resource_ids: list[str] = None  # Full ARM resource IDs for UAMIs

    def __post_init__(self):
        if self.uami_resource_ids is None:
            self.uami_resource_ids = []


def _run_az(args: list[str]) -> Optional[str]:
    """Run an az CLI command and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["az"] + args,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def get_identity_context(config: dict) -> IdentityContext:
    """Auto-detect identity info from az CLI + config.

    Resolves:
    - tenantId from az account show
    - principalId/clientId from az ad sp show (SP) or az ad signed-in-user show (user)
    - UAMI resource IDs from config
    """
    # Get account info
    account_json = _run_az(["account", "show", "--output", "json"])
    if not account_json:
        return IdentityContext(
            tenant_id="",
            principal_id="",
            user_type="unknown",
        )

    account = json.loads(account_json)
    tenant_id = account.get("tenantId", "")
    user_type = account.get("user", {}).get("type", "user")
    user_name = account.get("user", {}).get("name", "")

    principal_id = ""
    client_id = None

    if user_type == "servicePrincipal":
        # For SPs: user.name is the client/app ID, get the object ID
        client_id = user_name
        sp_json = _run_az(["ad", "sp", "show", "--id", user_name, "--query", "id", "--output", "tsv"])
        if sp_json:
            principal_id = sp_json
    else:
        # For users: get the object ID
        user_json = _run_az(["ad", "signed-in-user", "show", "--query", "id", "--output", "tsv"])
        if user_json:
            principal_id = user_json

    # UAMI resource IDs from config
    uami_ids = config.get("defaults", {}).get("uamiResourceIds", [])
    if not uami_ids:
        uami_ids = config.get("overrides", {}).get("uamiResourceIds", [])

    return IdentityContext(
        tenant_id=tenant_id,
        principal_id=principal_id,
        client_id=client_id,
        user_type=user_type,
        uami_resource_ids=uami_ids if isinstance(uami_ids, list) else [uami_ids],
    )


def build_identity_block(
    identity_type: str,
    context: IdentityContext,
) -> dict:
    """Build a realistic identity JSON block for the given identity type.

    identity_type: one of "None", "SystemAssigned", "UserAssigned", "SystemAssigned,UserAssigned"
    """
    block = {"type": identity_type}

    if identity_type == "None":
        return block

    if "SystemAssigned" in identity_type:
        # principalId/tenantId are read-only (server-populated), don't include them
        pass

    if "UserAssigned" in identity_type:
        uami_map = {}
        if context.uami_resource_ids:
            for rid in context.uami_resource_ids:
                uami_map[rid] = {}
        else:
            # Placeholder — user needs to configure UAMI resource IDs
            uami_map["/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{name}"] = {}
        block["userAssignedIdentities"] = uami_map

    return block
