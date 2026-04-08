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
"$VENV_DIR/bin/pip" install "cryptography==46.0.6" "argon2-cffi==25.1.0"

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

# ── Desktop shortcut ─────────────────────────────────────
read -rp "  Create a Desktop shortcut? [y/N] " create_shortcut
if [[ "$create_shortcut" =~ ^[Yy]$ ]]; then
  DESKTOP="$HOME/Desktop"
  APP_NAME="Vault Invaders.command"
  SHORTCUT="$DESKTOP/$APP_NAME"

  cat > "$SHORTCUT" <<SHORTCUTEOF
#!/bin/bash
"$VENV_DIR/bin/python3" "$VAULT_SCRIPT"
SHORTCUTEOF
  chmod +x "$SHORTCUT"

  # Apply icon if .icns exists
  ICON="$SCRIPT_DIR/vault_invaders.icns"
  if [[ -f "$ICON" ]]; then
    # Set custom icon via AppleScript + Finder
    osascript -e "
      use framework \"AppKit\"
      set iconImage to (current application's NSImage's alloc()'s initWithContentsOfFile:\"$ICON\")
      (current application's NSWorkspace's sharedWorkspace()'s setIcon:iconImage forFile:\"$SHORTCUT\" options:0)
    " 2>/dev/null && echo "  Icon applied."
  fi

  echo "  Created: $SHORTCUT"
  echo "  Double-click it to launch Vault Invaders."
  echo ""
fi

echo "  Run 'source ~/.zshrc' or open a new terminal, then type:"
echo "    invade"
echo ""
