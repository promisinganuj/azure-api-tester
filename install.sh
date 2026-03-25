#!/usr/bin/env bash
# install.sh — Azure API Tester installer
# Installs the CLI tool, creates a wrapper script, and optionally
# installs the Copilot skill and prompt.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_DIR="$SCRIPT_DIR/utils/azure-api-tester"
COPILOT_DIR="$HOME/.copilot"
SHARED_VENV="$COPILOT_DIR/env/.venv"
BIN_DIR="$COPILOT_DIR/bin"

CONFIG_DIR="$HOME/.azure-api-tester"
CONFIG_FILE="$CONFIG_DIR/azure-config.yaml"

echo "=== Azure API Tester — Installer ==="
echo ""

# ---------------------------------------------------------------
# 1. Install the CLI tool
# ---------------------------------------------------------------
echo -n "[1/4] Installing CLI tool..."

if [[ -f "$SHARED_VENV/bin/azure-api-tester" && -f "$BIN_DIR/azure-api-tester" ]]; then
    echo " already installed, skipping."
else
    if ! command -v python3 &>/dev/null; then
        echo ""
        echo "ERROR: python3 is not installed. Please install Python 3.9+ first."
        exit 1
    fi

    # Install uv if not already available
    if ! command -v uv &>/dev/null; then
        echo ""
        echo "  Installing uv package manager..."
        curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null
        export PATH="$HOME/.local/bin:$PATH"
    fi

    # Create venv under COPILOT_DIR if it doesn't exist
    VENV_DIR="$SHARED_VENV"
    if [[ ! -f "$VENV_DIR/bin/python" ]]; then
        mkdir -p "$(dirname "$VENV_DIR")"
        uv venv "$VENV_DIR" --quiet
    fi

    uv pip install -e "$TOOL_DIR" --python "$VENV_DIR/bin/python" --quiet

    # Create wrapper script
    mkdir -p "$BIN_DIR"
    cat > "$BIN_DIR/azure-api-tester" << WRAPPER
#!/usr/bin/env bash
exec "$VENV_DIR/bin/azure-api-tester" "\$@"
WRAPPER

    echo " ✓ Done"
fi
VENV_DIR="$SHARED_VENV"
echo ""

# ---------------------------------------------------------------
# 2. Create default configuration file
# ---------------------------------------------------------------
echo -n "[2/4] Setting up default configuration..."

if [[ -f "$CONFIG_FILE" ]]; then
    echo " already exists, skipping."
else
    mkdir -p "$CONFIG_DIR"
    cat > "$CONFIG_FILE" << 'CONFIG'
defaults:
  subscriptionId: "auto"            # "auto" reads from az account show
  resourceGroupName: "rg-azure-api-tester"
  location: "eastus"

  # User Assigned Managed Identity ARM resource IDs (for APIs that use UAMI)
  uamiResourceIds:
    - "/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{name}"

settings:
  autoCleanup: false  # Set to true to auto-DELETE created resources after testing

overrides:
  workspaceName: "my-aml-workspace"
  endpointName: "test-ep-{random}"  # {random} generates a unique suffix
CONFIG
    echo " ✓ Done"
fi
echo ""

# ---------------------------------------------------------------
# 3. Install Copilot skill (optional)
# ---------------------------------------------------------------
if [[ -f "$COPILOT_DIR/skills/azure-api-tester/SKILL.md" ]]; then
    echo "[3/4] Copilot skill — already installed, skipping."
else
    read -rp "[3/4] Install the Copilot skill? (y/N) " install_skill
    if [[ "${install_skill:-n}" =~ ^[Yy]$ ]]; then
        mkdir -p "$COPILOT_DIR/skills/azure-api-tester/references"
        mkdir -p "$COPILOT_DIR/skills/azure-api-tester/scripts"

        cp "$SCRIPT_DIR/skills/azure-api-tester/SKILL.md" \
           "$COPILOT_DIR/skills/azure-api-tester/"
        cp "$SCRIPT_DIR/skills/azure-api-tester/references/dependency-rules.md" \
           "$COPILOT_DIR/skills/azure-api-tester/references/"
        cp "$SCRIPT_DIR/skills/azure-api-tester/scripts/create-prerequisites.sh" \
           "$COPILOT_DIR/skills/azure-api-tester/scripts/"

        echo "  ✓ Copilot skill installed"
    else
        echo "  — Skipped"
    fi
fi
echo ""

# ---------------------------------------------------------------
# 4. Install companion prompt (optional)
# ---------------------------------------------------------------
if [[ -f "$COPILOT_DIR/.github/prompts/azure-api-dependency-analysis.prompt.md" ]]; then
    echo "[4/4] Dependency-analysis prompt — already installed, skipping."
else
    read -rp "[4/4] Install the dependency-analysis prompt? (y/N) " install_prompt
    if [[ "${install_prompt:-n}" =~ ^[Yy]$ ]]; then
        mkdir -p "$COPILOT_DIR/.github/prompts"

        cp "$SCRIPT_DIR/prompts/azure-api-dependency-analysis.prompt.md" \
           "$COPILOT_DIR/.github/prompts/"

        echo "  ✓ Prompt installed"
    else
        echo "  — Skipped"
    fi
fi
echo ""

# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------
echo "=== Done ==="
echo ""
echo "  CLI tool:  $VENV_DIR"
echo "  Wrapper:   $BIN_DIR/azure-api-tester"
echo "  Config:    $CONFIG_FILE"
echo ""

# PATH warning
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo "  ⚠  $BIN_DIR is not in your PATH."
    echo "     Add this to your shell profile (~/.bashrc, ~/.zshrc, etc.):"
    echo ""
    echo "       export PATH=\"\$HOME/.copilot/bin:\$PATH\""
    echo ""
fi

echo "  ℹ  $CONFIG_FILE is the global configuration."
echo "     If you have stable deployed resources, add them here so they"
echo "     are automatically referenced during API execution."
echo ""
echo "  Verify:  azure-api-tester --help"
echo "  Usage:   azure-api-tester test \"<docs-url>\" --dry-run"
echo ""
