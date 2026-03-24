#!/usr/bin/env bash
# install.sh — Azure API Tester installer
# Installs the CLI tool, and optionally the Copilot skill and prompt.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_DIR="$SCRIPT_DIR/utils/azure-api-tester"

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

cd "$TOOL_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install -e . --quiet

echo "  ✓ Virtual environment created at $TOOL_DIR/.venv"
echo "  ✓ azure-api-tester installed"
echo ""
echo "  To activate the environment in future sessions:"
echo "    source $TOOL_DIR/.venv/bin/activate"
echo ""

# ---------------------------------------------------------------
# 2. Install Copilot skill (optional)
# ---------------------------------------------------------------
read -rp "[2/3] Install the Copilot skill? (y/N) " install_skill
if [[ "${install_skill:-n}" =~ ^[Yy]$ ]]; then
    mkdir -p ~/.copilot/skills/azure-api-tester/references
    mkdir -p ~/.copilot/skills/azure-api-tester/scripts

    cp "$SCRIPT_DIR/skills/azure-api-tester/SKILL.md" \
       ~/.copilot/skills/azure-api-tester/
    cp "$SCRIPT_DIR/skills/azure-api-tester/references/dependency-rules.md" \
       ~/.copilot/skills/azure-api-tester/references/
    cp "$SCRIPT_DIR/skills/azure-api-tester/scripts/create-prerequisites.sh" \
       ~/.copilot/skills/azure-api-tester/scripts/

    echo "  ✓ Copilot skill installed to ~/.copilot/skills/azure-api-tester/"
else
    echo "  — Skipped"
fi
echo ""

# ---------------------------------------------------------------
# 3. Install companion prompt (optional)
# ---------------------------------------------------------------
read -rp "[3/3] Install the dependency-analysis prompt? (y/N) " install_prompt
if [[ "${install_prompt:-n}" =~ ^[Yy]$ ]]; then
    mkdir -p ~/.copilot/.github/prompts

    cp "$SCRIPT_DIR/prompts/azure-api-dependency-analysis.prompt.md" \
       ~/.copilot/.github/prompts/

    echo "  ✓ Prompt installed to ~/.copilot/.github/prompts/"
else
    echo "  — Skipped"
fi
echo ""

# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------
echo "=== Done ==="
echo ""
echo "  Next steps:"
echo "    1. az login                    (if not already logged in)"
echo "    2. source $TOOL_DIR/.venv/bin/activate"
echo "    3. azure-api-tester test \"<docs-url>\" --dry-run"
echo ""
