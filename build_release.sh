#!/bin/bash
# ────────────────────────────────────────────────────────────
#  Vault Invaders — build a release zip with SHA256 checksum
# ────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(cat "$SCRIPT_DIR/VERSION" | tr -d '[:space:]')"
NAME="vault-invaders-${VERSION}"
DIST="$SCRIPT_DIR/dist"
ARCHIVE="$NAME.zip"

echo "  Building $NAME ..."

rm -rf "$DIST/$NAME" "$DIST/$ARCHIVE" "$DIST/$ARCHIVE.sha256"
mkdir -p "$DIST/$NAME"

# Copy distribution files
cp "$SCRIPT_DIR/vault_invaders.py" "$DIST/$NAME/"
cp "$SCRIPT_DIR/install.sh"        "$DIST/$NAME/"
cp "$SCRIPT_DIR/LICENSE"           "$DIST/$NAME/"
cp "$SCRIPT_DIR/README.md"        "$DIST/$NAME/"
cp "$SCRIPT_DIR/VERSION"          "$DIST/$NAME/"
cp "$SCRIPT_DIR/vault_invaders.icns" "$DIST/$NAME/" 2>/dev/null || true

# Create zip
(cd "$DIST" && zip -rq "$ARCHIVE" "$NAME")

# Generate SHA256 checksum
(cd "$DIST" && shasum -a 256 "$ARCHIVE" > "$ARCHIVE.sha256")

# Clean up staging dir
rm -rf "$DIST/$NAME"

echo ""
echo "  Release built:"
echo "    $DIST/$ARCHIVE"
echo "    $DIST/$ARCHIVE.sha256"
echo ""
echo "  Verify with:"
echo "    cd dist && shasum -a 256 -c $ARCHIVE.sha256"
echo ""
