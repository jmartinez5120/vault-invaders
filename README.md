# Vault Invaders

A terminal-based password manager with arcade-style UI and strong encryption.

```
    █     █     █        ██╗   ██╗ █████╗ ██╗   ██╗██╗  ████████╗
     █   █ █   █         ██║   ██║██╔══██╗██║   ██║██║  ╚══██╔══╝
     █████████████         ██║   ██║███████║██║   ██║██║     ██║
    ███ ███████ ███        ╚██╗ ██╔╝██╔══██║██║   ██║██║     ██║
   █████████████████        ╚████╔╝ ██║  ██║╚██████╔╝███████╗██║
   █ █████████████ █         ╚═══╝  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝
   █ █           █ █
        ██   ██              ▓▓ I N V A D E R S ▓▓
```

## Encryption Stack

- **Argon2id** key derivation (256 MB memory-hard)
- **SHAKE-256** key expansion (SHA-3 sponge)
- **AES-256-GCM** (layer 1)
- **ChaCha20-Poly1305** (layer 2 - defense in depth)

## Features

- Fully offline - no network access, no cloud, no telemetry
- Encrypted vault stored locally with `0600` permissions
- Clipboard auto-clear after 15 seconds
- Brute-force protection with exponential backoff
- Symlink attack prevention on vault path
- Password generator built in
- Import/export support
- Space Invaders themed TUI

## Requirements

- Python 3.8+
- macOS or Linux

## Install

### Option 1: Quick Install (macOS)

```bash
git clone https://github.com/jmartinez5120/vault-invaders.git
cd vault-invaders
bash install.sh
```

This creates a virtual environment, installs dependencies, and adds the `invade` command to your shell. Then run:

```bash
source ~/.zshrc
invade
```

### Option 2: Download Release

Download the latest release from the [Releases page](https://github.com/jmartinez5120/vault-invaders/releases), then verify and install:

```bash
# Verify integrity
shasum -a 256 -c vault-invaders-*.zip.sha256

# Unzip and install
unzip vault-invaders-*.zip
cd vault-invaders-*
bash install.sh
```

### Option 3: Manual

```bash
git clone https://github.com/jmartinez5120/vault-invaders.git
cd vault-invaders
python3 -m venv .venv
.venv/bin/pip install cryptography==46.0.6 argon2-cffi==25.1.0
.venv/bin/python3 vault_invaders.py
```

## Usage

Run `invade` (or `python3 vault_invaders.py`) to launch. On first run you'll create a master password (minimum 8 characters), then you can:

- **A** - Add new entry
- **E** - Edit entry
- **C** - Copy username
- **P** - Copy password (auto-clears clipboard after 15s)
- **S** - Show/hide passwords
- **G** - Generate password
- **/** - Search entries
- **D** - Delete entry
- **L** - Lock vault
- **Q** - Quit

While editing the password field in the add/edit form:

- **Ctrl+G** - Open password generator
- **Ctrl+T** - Toggle show/hide the password you're typing

## License

MIT
