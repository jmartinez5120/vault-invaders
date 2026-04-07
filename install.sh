#!/bin/bash
# ────────────────────────────────────────────────────────────
#  Vault Invaders — installer for macOS
#  Adds the `invade` command to your .zshrc
# ────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VAULT_SCRIPT="$SCRIPT_DIR/vault_invaders.py"
ZSHRC="$HOME/.zshrc"
ALIAS_MARKER="# vault-invaders"
ALIAS_LINE="alias invade='python3 \"$VAULT_SCRIPT\"'  $ALIAS_MARKER"

# ── Preflight checks ───────────────────────────────────────
if [[ ! -f "$VAULT_SCRIPT" ]]; then
  echo "  ERROR: vault_invaders.py not found at $VAULT_SCRIPT"
  exit 1
fi

if ! command -v python3 &>/dev/null; then
  echo "  ERROR: python3 is not installed"
  exit 1
fi

# Check Python dependencies
missing=()
python3 -c "import cryptography" 2>/dev/null || missing+=("cryptography")
python3 -c "import argon2" 2>/dev/null || missing+=("argon2-cffi")

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "  Missing Python packages: ${missing[*]}"
  echo "  Run: pip3 install ${missing[*]}"
  echo ""
  read -rp "  Install them now? [y/N] " yn
  if [[ "$yn" =~ ^[Yy]$ ]]; then
    pip3 install "${missing[@]}"
  else
    echo "  Skipping. The app may not work without these."
  fi
fi

# ── Install alias ──────────────────────────────────────────
if [[ ! -f "$ZSHRC" ]]; then
  touch "$ZSHRC"
fi

if grep -qF "$ALIAS_MARKER" "$ZSHRC"; then
  # Update existing alias in place
  sed -i '' "/$ALIAS_MARKER/d" "$ZSHRC"
fi

echo "" >> "$ZSHRC"
echo "$ALIAS_LINE" >> "$ZSHRC"

echo ""
echo "  Done! Added to $ZSHRC:"
echo "    $ALIAS_LINE"
echo ""
echo "  Run 'source ~/.zshrc' or open a new terminal, then type:"
echo "    invade"
echo ""
