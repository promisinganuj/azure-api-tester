#!/usr/bin/env bash
# install.sh — Azure API Tester installer
# Installs the CLI tool, creates a wrapper script, and optionally
# installs the Copilot skill and prompt.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_DIR="$SCRIPT_DIR/utils/azure-api-tester"
COPILOT_DIR="$HOME/.copilot"
SHARED_VENV="$COPILOT_DIR/env/.venv"
LOCAL_VENV="$TOOL_DIR/.venv"
BIN_DIR="$COPILOT_DIR/bin"

echo "=== Azure API Tester — Installer ==="
echo ""

# ---------------------------------------------------------------
# 1. Install the CLI tool
# ---------------------------------------------------------------
echo "[1/3] Installing CLI tool..."

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is not installed. Please install Python 3.9+ first."
    exit 1
fi

# Prefer the shared Copilot venv if it exists; otherwise create a local one
if [[ -f "$SHARED_VENV/bin/python" ]]; then
    VENV_DIR="$SHARED_VENV"
    echo "  Using shared Copilot venv: $SHARED_VENV"
else
    VENV_DIR="$LOCAL_VENV"
    echo "  Creating local venv: $LOCAL_VENV"
    python3 -m venv "$LOCAL_VENV"
fi

"$VENV_DIR/bin/pip" install -e "$TOOL_DIR" --quiet
echo "  ✓ azure-api-tester installed into $VENV_DIR"

# ---------------------------------------------------------------
# Create wrapper script at ~/.copilot/bin/azure-api-tester
# ---------------------------------------------------------------
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/azure-api-tester" << WRAPPER
#!/usr/bin/env bash
exec "$VENV_DIR/bin/azure-api-tester" "\$@"
WRAPPER
echo "  ✓ Wrapper created at $BIN_DIR/azure-api-tester"

# Ensure ~/.copilot/bin is in PATH (hint for user)
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "  ⚠  $BIN_DIR is not in your PATH."
    echo "     Add this to your shell profile (~/.bashrc, ~/.zshrc, etc.):"
    echo ""
    echo "       export PATH=\"\$HOME/.copilot/bin:\$PATH\""
    echo ""
fi
echo ""

# ---------------------------------------------------------------
# 2. Install Copilot skill (optional)
# ---------------------------------------------------------------
read -rp "[2/3] Install the Copilot skill? (y/N) " install_skill
if [[ "${install_skill:-n}" =~ ^[Yy]$ ]]; then
    mkdir -p "$COPILOT_DIR/skills/azure-api-tester/references"
    mkdir -p "$COPILOT_DIR/skills/azure-api-tester/scripts"

    cp "$SCRIPT_DIR/skills/azure-api-tester/SKILL.md" \
       "$COPILOT_DIR/skills/azure-api-tester/"
    cp "$SCRIPT_DIR/skills/azure-api-tester/references/dependency-rules.md" \
       "$COPILOT_DIR/skills/azure-api-tester/references/"
    cp "$SCRIPT_DIR/skills/azure-api-tester/scripts/create-prerequisites.sh" \
       "$COPILOT_DIR/skills/azure-api-tester/scripts/"

    echo "  ✓ Copilot skill installed to $COPILOT_DIR/skills/azure-api-tester/"
else
    echo "  — Skipped"
fi
echo ""

# ---------------------------------------------------------------
# 3. Install companion prompt (optional)
# ---------------------------------------------------------------
read -rp "[3/3] Install the dependency-analysis prompt? (y/N) " install_prompt
if [[ "${install_prompt:-n}" =~ ^[Yy]$ ]]; then
    mkdir -p "$COPILOT_DIR/.github/prompts"

    cp "$SCRIPT_DIR/prompts/azure-api-dependency-analysis.prompt.md" \
       "$COPILOT_DIR/.github/prompts/"

    echo "  ✓ Prompt installed to $COPILOT_DIR/.github/prompts/"
else
    echo "  — Skipped"
fi
echo ""

# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------
echo "=== Done ==="
echo ""
echo "  Verify:  azure-api-tester --help"
echo "  Usage:   azure-api-tester test \"<docs-url>\" --dry-run"
echo ""
