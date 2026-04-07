#!/bin/bash
# ────────────────────────────────────────────────────────────
#  Vault Invaders — installer for macOS
#  Adds the `invade` command to your .zshrc
# ────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VAULT_SCRIPT="$SCRIPT_DIR/vault_invaders.py"
VENV_DIR="$SCRIPT_DIR/.venv"
ZSHRC="$HOME/.zshrc"
ALIAS_MARKER="# vault-invaders"
ALIAS_LINE="alias invade='\"$VENV_DIR/bin/python3\" \"$VAULT_SCRIPT\"'  $ALIAS_MARKER"

# ── Preflight checks ───────────────────────────────────────
if [[ ! -f "$VAULT_SCRIPT" ]]; then
  echo "  ERROR: vault_invaders.py not found at $VAULT_SCRIPT"
  exit 1
fi

if ! command -v python3 &>/dev/null; then
  echo "  ERROR: python3 is not installed"
  exit 1
fi

# ── Create virtual environment ─────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
  echo "  Creating virtual environment at $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
fi

echo "  Installing dependencies ..."
"$VENV_DIR/bin/pip" install --quiet cryptography argon2-cffi

# ── Install alias ──────────────────────────────────────────
if [[ ! -f "$ZSHRC" ]]; then
  touch "$ZSHRC"
fi

if grep -qF "$ALIAS_MARKER" "$ZSHRC"; then
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
