#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║               V A U L T   I N V A D E R S                   ║
║     Quantum-Hardened Encrypted Password Manager TUI          ║
║                                                              ║
║  Encryption stack:                                           ║
║   ┌─ Argon2id KDF (memory-hard, quantum-resistant)           ║
║   ├─ SHAKE-256 key expansion (SHA-3 sponge, post-quantum)    ║
║   ├─ AES-256-GCM  (layer 1 — 128-bit post-quantum security) ║
║   └─ ChaCha20-Poly1305 (layer 2 — defense in depth)         ║
╚══════════════════════════════════════════════════════════════╝

Usage:
  python3 vault_invaders.py

Dependencies:
  pip3 install cryptography argon2-cffi

Add to .zshrc:
  alias vault='python3 ~/vault_invaders.py'
"""

import curses
import json
import os
import sys
import time
import hashlib
import base64
import subprocess
import random
import math
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
except ImportError:
    print("\n  ⚠  pip3 install cryptography\n")
    sys.exit(1)

try:
    from argon2.low_level import hash_secret_raw, Type
except ImportError:
    print("\n  ⚠  pip3 install argon2-cffi\n")
    sys.exit(1)

# ── Config file ─────────────────────────────────────────────────────────
CONFIG_PATH = Path.home() / ".vault_invaders.conf"
DEFAULT_VAULT = Path.home() / ".vault_invaders.enc"
LOCKOUT_PATH = Path.home() / ".vault_invaders.lock"

def load_config() -> dict:
    defaults = {"vault_path": str(DEFAULT_VAULT)}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
            defaults.update(cfg)
        except Exception:
            pass
    return defaults

def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    CONFIG_PATH.chmod(0o600)

def get_vault_path() -> Path:
    return Path(load_config()["vault_path"])

def load_lockout() -> tuple:
    try:
        data = json.loads(LOCKOUT_PATH.read_text())
        return data.get("attempts", 0), data.get("until", 0)
    except Exception:
        return 0, 0

def save_lockout(attempts: int, until: float):
    LOCKOUT_PATH.write_text(json.dumps({"attempts": attempts, "until": until}))
    LOCKOUT_PATH.chmod(0o600)

def clear_lockout():
    LOCKOUT_PATH.unlink(missing_ok=True)

# ── Crypto constants ────────────────────────────────────────────────────
SALT_LEN = 32
NONCE_GCM = 12
NONCE_CHA = 12
KEY_LEN = 32
ARGON2_TIME = 4
ARGON2_MEM = 256 * 1024  # 256 MB
ARGON2_PARALLEL = 2

VAULT_VERSION = b'\x02'  # v2 = quantum-hardened

# ── Quantum-Hardened Crypto ─────────────────────────────────────────────
def derive_master_key(password: str, salt: bytes) -> bytes:
    raw = hash_secret_raw(
        secret=password.encode(), salt=salt,
        time_cost=ARGON2_TIME, memory_cost=ARGON2_MEM,
        parallelism=ARGON2_PARALLEL, hash_len=64, type=Type.ID,
    )
    shake = hashlib.shake_256(salt + raw + b"vault-invaders-quantum-v2")
    return shake.digest(64)

def encrypt_vault(data: list, password: str) -> bytes:
    salt = os.urandom(SALT_LEN)
    master = derive_master_key(password, salt)
    key_aes, key_cha = master[:32], master[32:]
    nonce1 = os.urandom(NONCE_GCM)
    nonce2 = os.urandom(NONCE_CHA)
    plaintext = json.dumps(data, ensure_ascii=False).encode("utf-8")
    ct1 = AESGCM(key_aes).encrypt(nonce1, plaintext, salt)
    ct2 = ChaCha20Poly1305(key_cha).encrypt(nonce2, ct1, salt)
    blob = VAULT_VERSION + salt + nonce1 + nonce2 + ct2
    return base64.b64encode(blob)

def decrypt_vault(blob: bytes, password: str) -> list:
    raw = base64.b64decode(blob)
    ver = raw[0:1]
    if ver == VAULT_VERSION:
        off = 1
        salt = raw[off:off+SALT_LEN]; off += SALT_LEN
        nonce1 = raw[off:off+NONCE_GCM]; off += NONCE_GCM
        nonce2 = raw[off:off+NONCE_CHA]; off += NONCE_CHA
        ct2 = raw[off:]
        master = derive_master_key(password, salt)
        key_aes, key_cha = master[:32], master[32:]
        ct1 = ChaCha20Poly1305(key_cha).decrypt(nonce2, ct2, salt)
        plaintext = AESGCM(key_aes).decrypt(nonce1, ct1, salt)
        return json.loads(plaintext.decode("utf-8"))
    else:
        # Legacy v1 — decrypt then auto-upgrade to v2
        salt = raw[:16]; nonce = raw[16:28]; ct = raw[28:]
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000, dklen=32)
        pt = AESGCM(key).decrypt(nonce, ct, None)
        entries = json.loads(pt.decode())
        # Auto-upgrade to v2 format
        save_vault(entries, password)
        return entries

def save_vault(entries: list, password: str):
    vp = get_vault_path()
    vp.write_bytes(encrypt_vault(entries, password))
    vp.chmod(0o600)

def load_vault_data(password: str) -> list:
    return decrypt_vault(get_vault_path().read_bytes(), password)

# ── Clipboard ───────────────────────────────────────────────────────────
def copy_to_clipboard(text: str) -> bool:
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
        elif sys.platform.startswith("linux"):
            for cmd in [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"], ["wl-copy"]]:
                try:
                    subprocess.run(cmd, input=text.encode(), check=True)
                    return True
                except FileNotFoundError:
                    continue
            return False
        else:
            return False
        return True
    except Exception:
        return False

def clear_clipboard_after(seconds: int = 15):
    """Clear clipboard after a delay (runs in background thread)."""
    import threading
    def _clear():
        time.sleep(seconds)
        try:
            if sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=b"", check=True)
            elif sys.platform.startswith("linux"):
                for cmd in [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"], ["wl-copy", "--clear"]]:
                    try:
                        subprocess.run(cmd, input=b"", check=True)
                        return
                    except FileNotFoundError:
                        continue
        except Exception:
            pass
    threading.Thread(target=_clear, daemon=True).start()

def read_from_clipboard() -> str:
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["pbpaste"], capture_output=True, check=True)
            return r.stdout.decode("utf-8", errors="replace")
        elif sys.platform.startswith("linux"):
            for cmd in [["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"], ["wl-paste"]]:
                try:
                    r = subprocess.run(cmd, capture_output=True, check=True)
                    return r.stdout.decode("utf-8", errors="replace")
                except FileNotFoundError:
                    continue
        return ""
    except Exception:
        return ""

# ── Fuzzy match ─────────────────────────────────────────────────────────
def fuzzy_match(needle: str, haystack: str) -> bool:
    n, h, j = needle.lower(), haystack.lower(), 0
    for ch in h:
        if j < len(n) and ch == n[j]:
            j += 1
    return j == len(n)

# ── Sprites ─────────────────────────────────────────────────────────────
INVADER_1 = [
    "  ▀▄   ▄▀  ", " ▄█▀███▀█▄ ", "█▀███████▀█",
    "█ █▀▀▀▀▀█ █", "   ▀▀ ▀▀   ",
]
INVADER_SM = ["▄▀ ▀▄", "██▀██", " ▀ ▀ "]

ALIEN_LOGO = [
    "    █     █     █    ",
    "     █   █ █   █     ",
    "     █████████████     ",
    "    ███ ███████ ███    ",
    "   █████████████████   ",
    "   █ █████████████ █   ",
    "   █ █           █ █   ",
    "        ██   ██        ",
]
LOGO = [
    "██╗   ██╗ █████╗ ██╗   ██╗██╗  ████████╗",
    "██║   ██║██╔══██╗██║   ██║██║  ╚══██╔══╝",
    "██║   ██║███████║██║   ██║██║     ██║   ",
    "╚██╗ ██╔╝██╔══██║██║   ██║██║     ██║   ",
    " ╚████╔╝ ██║  ██║╚██████╔╝███████╗██║   ",
    "  ╚═══╝  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝   ",
]
TITLE_LINE = "▓▓ I N V A D E R S ▓▓"

# ── Colors ──────────────────────────────────────────────────────────────
C_GREEN, C_DIM, C_RED, C_YELLOW, C_CYAN = 1, 2, 3, 4, 5
C_BG, C_SELECTED = 6, 7
C_GREEN_INV, C_RED_INV, C_YELLOW_INV, C_CYAN_INV = 8, 9, 10, 11
C_WHITE, C_STARS, C_MAGENTA = 12, 13, 14

def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(C_GREEN, curses.COLOR_GREEN, -1)
    curses.init_pair(C_DIM, 8, -1)
    curses.init_pair(C_RED, curses.COLOR_RED, -1)
    curses.init_pair(C_YELLOW, curses.COLOR_YELLOW, -1)
    curses.init_pair(C_CYAN, curses.COLOR_CYAN, -1)
    curses.init_pair(C_BG, curses.COLOR_GREEN, -1)
    curses.init_pair(C_SELECTED, curses.COLOR_BLACK, curses.COLOR_GREEN)
    curses.init_pair(C_GREEN_INV, curses.COLOR_BLACK, curses.COLOR_GREEN)
    curses.init_pair(C_RED_INV, curses.COLOR_BLACK, curses.COLOR_RED)
    curses.init_pair(C_YELLOW_INV, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    curses.init_pair(C_CYAN_INV, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(C_WHITE, curses.COLOR_WHITE, -1)
    curses.init_pair(C_STARS, curses.COLOR_WHITE, -1)
    curses.init_pair(C_MAGENTA, curses.COLOR_MAGENTA, -1)

# ── Stars ───────────────────────────────────────────────────────────────
class Stars:
    def __init__(self, h, w):
        self.stars = [(random.randint(0, h-1), random.randint(0, w-1),
                       random.uniform(0.5, 3.0), random.random()*3) for _ in range(30)]
        self.t0 = time.time()

    def draw(self, scr):
        t = time.time() - self.t0
        for r, c, freq, phase in self.stars:
            brightness = (math.sin((t + phase) * freq) + 1) / 2
            ch = "·" if brightness < 0.5 else "∗" if brightness < 0.8 else "✦"
            try:
                scr.addstr(r, c, ch, curses.color_pair(C_DIM) if brightness < 0.5 else curses.color_pair(C_WHITE))
            except curses.error:
                pass

# ═══════════════════════════════════════════════════════════════════════
# TUI App
# ═══════════════════════════════════════════════════════════════════════
TABS = ["⌕ Credentials", "+ Add New", "📥 Import", "⚙ Config"]

class VaultApp:
    def __init__(self, scr):
        self.scr = scr
        self.entries = []
        self.master_pw = ""
        self.search = ""
        self.cursor = 0
        self.scroll = 0
        self.tab = 0
        self.mode = "list"
        self.last_activity = time.time()
        self.inactivity_timeout = 120  # 2 minutes
        self.toast_msg = ""
        self.toast_time = 0
        self.show_pw = False
        self.form = {}
        self.form_field = 0
        self.form_fields = ["system", "username", "password", "hostname", "port", "url", "description", "notes", "env"]
        self.stars = None
        self.detail_cursor = 0
        self.confirm_input = ""
        self.click_zones = []
        self._edit_id = None
        self._edit_ref = None
        # Notes editor state
        self.notes_lines = [""]
        self.notes_cx = 0
        self.notes_cy = 0
        self.notes_scroll = 0
        self._notes_backup = ""
        # Config state
        self.cfg_cursor = 0
        self.cfg_mode = "menu"
        self.cfg_pw_fields = {"current": "", "new": "", "confirm": ""}
        self.cfg_pw_field = 0
        self.cfg_path_input = ""
        self.cfg_error = ""
        # Export/Import file state
        self.cfg_export_fields = {"password": "", "confirm": ""}
        self.cfg_export_field = 0
        self.cfg_import_fields = {"path": str(Path.home() / "vault-invaders-export.enc"), "password": ""}
        self.cfg_import_field = 0
        self.cfg_import_preview = None
        self.cfg_erase_pw = ""
        # Import state
        self.import_buffer = ""
        self.import_error = ""
        self.import_preview = None

    def toast(self, msg):
        self.toast_msg = msg
        self.toast_time = time.time()

    def zone(self, y, x, h, w, action, data=None):
        self.click_zones.append((y, x, h, w, action, data))

    def filtered(self):
        items = self.entries if not self.search else [e for e in self.entries if fuzzy_match(self.search, e.get("system","")) or fuzzy_match(self.search, e.get("username",""))]
        return sorted(items, key=lambda e: e.get("system", "").lower())

    def draw_box(self, y, x, h, w, color=C_GREEN):
        cp = curses.color_pair(color)
        try:
            self.scr.addstr(y, x, "┌" + "─"*(w-2) + "┐", cp)
            for i in range(1, h-1):
                self.scr.addstr(y+i, x, "│", cp)
                self.scr.addstr(y+i, x+w-1, "│", cp)
            self.scr.addstr(y+h-1, x, "└" + "─"*(w-2) + "┘", cp)
        except curses.error:
            pass

    def s(self, y, x, text, attr=0):
        """Safe addstr."""
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        text = text[:max(0, w - x - 1)]
        try:
            self.scr.addstr(y, x, text, attr)
        except curses.error:
            pass

    # ── Header + Tabs ───────────────────────────────────────────────
    def draw_header(self):
        h, w = self.scr.getmaxyx()
        cp = curses.color_pair(C_GREEN) | curses.A_BOLD
        self.s(0, 0, "─"*w, curses.color_pair(C_DIM))
        self.s(1, 2, "👾", cp)
        self.s(1, 5, " VAULT INVADERS ", cp)
        count = f" {len(self.entries)} entries "
        self.s(1, w - len(count) - 16, count, curses.color_pair(C_DIM))
        # Inactivity countdown
        remaining = max(0, int(self.inactivity_timeout - (time.time() - self.last_activity)))
        mins, secs = divmod(remaining, 60)
        timer_str = f" {mins}:{secs:02d} "
        if remaining <= 5:
            timer_attr = curses.color_pair(C_RED) | curses.A_BOLD | curses.A_BLINK
        elif remaining <= 15:
            timer_attr = curses.color_pair(C_RED) | curses.A_BOLD
        elif remaining <= 30:
            timer_attr = curses.color_pair(C_YELLOW)
        else:
            timer_attr = curses.color_pair(C_DIM)
        self.s(0, w - len(timer_str), timer_str, timer_attr)

        self.s(1, w - 15, "[R]EFRESH", curses.color_pair(C_CYAN))
        self.zone(1, w-15, 1, 9, "refresh")
        self.s(1, w - 5, "[L]CK", curses.color_pair(C_RED))
        self.zone(1, w-5, 1, 5, "lock")
        self.s(2, 0, "─"*w, curses.color_pair(C_DIM))

        # Tab bar
        tx = 1
        for i, label in enumerate(TABS):
            is_active = i == self.tab
            if is_active and self.mode == "tabs":
                # Focused + selected: blinking cursor style
                attr = curses.color_pair(C_GREEN_INV) | curses.A_BOLD | curses.A_BLINK
            elif is_active:
                attr = curses.color_pair(C_GREEN_INV) | curses.A_BOLD
            else:
                attr = curses.color_pair(C_DIM)
            padded = f" {label} "
            self.s(3, tx, padded, attr)
            self.zone(3, tx, 1, len(padded), "tab", i)
            tx += len(padded) + 1

        # Tab focus arrows hint
        if self.mode == "tabs":
            self.s(3, tx + 1, "◄ ► ENTER", curses.color_pair(C_DIM))

        enc_label = " 🛡 QUANTUM "
        self.s(3, w - len(enc_label) - 1, enc_label, curses.color_pair(C_MAGENTA))
        self.s(4, 0, "═"*w, curses.color_pair(C_DIM))

    # ── Search bar ──────────────────────────────────────────────────
    def draw_search(self, y, x, w):
        cp = curses.color_pair(C_GREEN)
        self.draw_box(y, x, 3, w, C_DIM)
        label = f" ⌕ {self.search}" if self.search else " ⌕ fuzzy search..."
        attr = cp if self.search else curses.color_pair(C_DIM)
        self.s(y+1, x+1, label[:w-3], attr)
        if self.mode == "list" and self.tab == 0:
            cx = x + 4 + len(self.search)
            if cx < x + w - 2:
                self.s(y+1, cx, "█", cp | curses.A_BOLD)

    # ── Entry list ──────────────────────────────────────────────────
    def draw_list(self, y, x, w, max_h):
        items = self.filtered()
        if not items:
            msg = "NO ENTRIES" if not self.entries else "NO MATCH"
            self.s(y+2, x + w//2 - len(msg)//2, msg, curses.color_pair(C_DIM))
            return

        visible = max_h
        if self.cursor >= self.scroll + visible:
            self.scroll = self.cursor - visible + 1
        if self.cursor < self.scroll:
            self.scroll = self.cursor

        for i in range(self.scroll, min(len(items), self.scroll + visible)):
            e = items[i]
            row = y + (i - self.scroll) * 2
            is_sel = i == self.cursor
            self.zone(row, x, 2, w, "select_entry", i)

            if is_sel and self.mode in ("list", "detail"):
                active = self.mode == "detail"
                sel_c = C_RED_INV if active else C_GREEN_INV
                sel_attr = curses.color_pair(sel_c) | curses.A_BOLD
                self.s(row, x, " " * w, sel_attr)
                self.s(row+1, x, " " * w, sel_attr)
                if active:
                    border_c = curses.color_pair(C_RED) | curses.A_BOLD
                    try:
                        self.scr.addstr(row, x, "▐", border_c)
                        self.scr.addstr(row, x + w - 1, "▌", border_c)
                        self.scr.addstr(row+1, x, "▐", border_c)
                        self.scr.addstr(row+1, x + w - 1, "▌", border_c)
                    except curses.error:
                        pass
                self.s(row, x+2, " ▸ ", sel_attr)
                self.s(row, x+5, e.get("system","")[:w-18], sel_attr)
                self.s(row+1, x+2, " └ " + e.get("username","")[:w-12], sel_attr)
            else:
                self.s(row, x, "  ", curses.color_pair(C_DIM))
                self.s(row, x+2, e.get("system","")[:w-16], curses.color_pair(C_GREEN))
                self.s(row+1, x+2, "└ " + e.get("username","")[:w-10], curses.color_pair(C_DIM))

            # env badge
            env = e.get("env", "DEV")
            env_c = {"DEV": C_GREEN_INV, "TEST": C_YELLOW_INV, "PROD": C_RED_INV}
            badge = f" {env} "
            badge_attr = curses.color_pair(env_c.get(env, C_GREEN_INV))
            if is_sel:
                badge_attr |= curses.A_BOLD
            self.s(row, x + w - len(badge) - 1, badge, badge_attr)

    # ── Detail view ─────────────────────────────────────────────────
    def draw_detail(self, y, x, w):
        items = self.filtered()
        if not items or self.cursor >= len(items):
            for i, ln in enumerate(INVADER_1):
                self.s(y+4+i, x + w//2 - len(ln)//2, ln, curses.color_pair(C_DIM))
            self.s(y+10, x + w//2 - 8, "SELECT AN ENTRY", curses.color_pair(C_DIM))
            return

        e = items[self.cursor]
        env = e.get("env", "DEV")
        env_c = {"DEV": C_GREEN_INV, "TEST": C_YELLOW_INV, "PROD": C_RED_INV}
        env_t = {"DEV": C_GREEN, "TEST": C_YELLOW, "PROD": C_RED}
        ec = curses.color_pair(env_t.get(env, C_GREEN))

        self.s(y, x+2, "╔"+"═"*(w-6)+"╗", ec)
        self.s(y+1, x+2, "║", ec)
        self.s(y+1, x+4, e.get("system","")[:w-12], curses.color_pair(C_GREEN)|curses.A_BOLD)
        badge = f" {env} "
        self.s(y+1, x+w-len(badge)-5, badge, curses.color_pair(env_c.get(env, C_GREEN_INV)))
        self.s(y+1, x+w-4, "║", ec)
        self.s(y+2, x+2, "╚"+"═"*(w-6)+"╝", ec)

        row = y + 4
        # Username
        self.s(row, x+2, "USERNAME", curses.color_pair(C_DIM))
        self.s(row+1, x+4, e.get("username",""), curses.color_pair(C_GREEN))
        b = "[C]opy User"
        bx = x+w-len(b)-3
        self.s(row+1, bx, b, curses.color_pair(C_CYAN))
        self.zone(row+1, bx, 1, len(b), "copy_user")
        row += 3

        # Password
        self.s(row, x+2, "PASSWORD", curses.color_pair(C_DIM))
        pw = e.get("password","")
        disp = pw if self.show_pw else "•"*min(len(pw), 16)
        self.s(row+1, x+4, disp, curses.color_pair(C_GREEN))
        sb = "[S]how" if not self.show_pw else "[H]ide"
        sx = x+w-22
        self.s(row+1, sx, sb, curses.color_pair(C_YELLOW))
        self.zone(row+1, sx, 1, len(sb), "toggle_pw")
        pb = "[P]opy Pass"
        px = x+w-len(pb)-3
        self.s(row+1, px, pb, curses.color_pair(C_CYAN))
        self.zone(row+1, px, 1, len(pb), "copy_pass")
        row += 3

        # Optional fields in a compact section
        opt_fields = [
            ("HOSTNAME", "hostname"),
            ("PORT", "port"),
            ("URL", "url"),
            ("DESCRIPTION", "description"),
            ("NOTES", "notes"),
        ]
        for label, key_name in opt_fields:
            val = e.get(key_name, "")
            if val or key_name == "notes":
                self.s(row, x+2, label, curses.color_pair(C_DIM))
                if key_name == "notes":
                    nb = "[N]otes"
                    nx = x + w - len(nb) - 3
                    self.s(row, nx, nb, curses.color_pair(C_CYAN))
                    self.zone(row, nx, 1, len(nb), "open_notes")
                if val:
                    cw = max(1, w-8)
                    display = val.replace("\n", " ")
                    lines = [display[i:i+cw] for i in range(0, len(display), cw)]
                    for li, ln in enumerate(lines[:3]):
                        self.s(row+1+li, x+4, ln, curses.color_pair(C_GREEN))
                    row += 1 + min(len(lines), 3) + 1
                else:
                    self.s(row+1, x+4, "(empty)", curses.color_pair(C_DIM))
                    row += 3
                continue
            if val:
                self.s(row, x+2, label, curses.color_pair(C_DIM))
                cw = max(1, w-8)
                lines = [val[i:i+cw] for i in range(0, len(val), cw)]
                for li, ln in enumerate(lines[:3]):
                    self.s(row+1+li, x+4, ln, curses.color_pair(C_GREEN))
                row += 1 + min(len(lines), 3) + 1

        # Separator + actions
        self.s(row, x+2, "─"*(w-6), curses.color_pair(C_DIM))
        row += 1
        ox = x + 4
        for act, aid in [("[E]dit","edit_entry"), ("[W] Dup","dup_entry"), ("[X]port","export_entry"), ("[D]elete","delete_entry"), ("[B]ack","back")]:
            c = C_RED if "Delete" in act else C_CYAN if "port" in act else C_GREEN
            self.s(row, ox, act, curses.color_pair(c))
            self.zone(row, ox, 1, len(act), aid)
            ox += len(act) + 3

    # ── Add/Edit form ───────────────────────────────────────────────
    def draw_form(self, y, x, w):
        title = "EDIT ENTRY" if self._edit_id is not None else "✦ ADD NEW CREDENTIALS ✦"
        for i, ln in enumerate(INVADER_SM):
            self.s(y+i, x + w//2 - len(ln)//2, ln, curses.color_pair(C_CYAN))
        self.s(y+3, x + w//2 - len(title)//2, title, curses.color_pair(C_CYAN)|curses.A_BOLD)

        labels = {"system":"SYSTEM NAME *","username":"USERNAME *","password":"PASSWORD *",
                  "hostname":"HOSTNAME","port":"PORT","url":"URL",
                  "description":"DESCRIPTION","notes":"NOTES","env":"ENVIRONMENT"}

        row = y + 5
        for fi, field in enumerate(self.form_fields):
            active = fi == self.form_field
            lc = curses.color_pair(C_GREEN) if active else curses.color_pair(C_DIM)
            self.s(row, x+2, labels[field], lc)
            self.zone(row, x, 2, w, "form_field", fi)
            row += 1
            if field == "env":
                ex = x + 4
                for env in ["DEV","TEST","PROD"]:
                    ec = {"DEV":C_GREEN_INV,"TEST":C_YELLOW_INV,"PROD":C_RED_INV}
                    a = curses.color_pair(ec[env]) if self.form.get("env")==env else curses.color_pair(C_DIM)
                    self.s(row, ex, f" {env} ", a)
                    self.zone(row, ex, 1, 6, "set_env", env)
                    ex += 8
                if active:
                    self.s(row, ex+2, "← → to change", curses.color_pair(C_DIM))
            elif field == "notes":
                val = self.form.get(field, "")
                preview = val.replace("\n", " ")[:w-20] if val else "(empty)"
                line_count = len(val.split("\n")) if val else 0
                self.s(row, x+4, preview, curses.color_pair(C_GREEN) if val else curses.color_pair(C_DIM))
                if active:
                    hint = f"[ENTER] Edit ({line_count} lines)" if line_count > 0 else "[ENTER] Edit"
                    self.s(row, x + w - len(hint) - 2, hint, curses.color_pair(C_CYAN))
            else:
                val = self.form.get(field, "")
                if field == "password" and val:
                    d = "•"*len(val)
                else:
                    d = val if val else "(empty)"
                self.s(row, x+4, d[:w-8], curses.color_pair(C_GREEN) if val else curses.color_pair(C_DIM))
                if active:
                    cx = x+4+(len("•"*len(val)) if field=="password" and val else len(val) if val else 0)
                    if cx < x+w-2:
                        self.s(row, cx, "█", curses.color_pair(C_GREEN)|curses.A_BOLD)
            row += 2
        row += 1
        self.s(row, x+4, "[ENTER] Save", curses.color_pair(C_CYAN)|curses.A_BOLD)
        self.zone(row, x+4, 1, 12, "form_save")
        self.s(row, x+22, "[ESC] Cancel", curses.color_pair(C_DIM))
        self.zone(row, x+22, 1, 12, "form_cancel")

    # ── Confirm delete ──────────────────────────────────────────────
    def draw_confirm(self):
        h, w = self.scr.getmaxyx()
        items = self.filtered()
        name = items[self.cursor].get("system","?") if items and self.cursor < len(items) else "?"
        bw = max(44, len(name)+12)
        bh = 9
        bx, by = w//2 - bw//2, h//2 - bh//2
        for row in range(by, by+bh):
            self.s(row, bx, " "*bw, curses.color_pair(C_RED))
        self.draw_box(by, bx, bh, bw, C_RED)
        self.s(by+1, bx+bw//2-8, "⚠ CONFIRM DELETE", curses.color_pair(C_RED)|curses.A_BOLD)
        self.s(by+3, bx+3, f'Destroy "{name}"?', curses.color_pair(C_GREEN))
        self.s(by+5, bx+3, 'Type "delete" to confirm:', curses.color_pair(C_DIM))
        self.s(by+6, bx+3, "> "+self.confirm_input, curses.color_pair(C_RED)|curses.A_BOLD)
        cx = bx+5+len(self.confirm_input)
        if cx < bx+bw-2:
            self.s(by+6, cx, "█", curses.color_pair(C_RED)|curses.A_BOLD)
        self.s(by+7, bx+3, "[ESC] Cancel", curses.color_pair(C_DIM))

    # ── Notes editor popup ──────────────────────────────────────────
    def draw_notes_editor(self):
        h, w = self.scr.getmaxyx()
        bw = min(w - 4, max(60, int(w * 0.8)))
        bh = min(h - 4, max(16, int(h * 0.8)))
        bx = w // 2 - bw // 2
        by = h // 2 - bh // 2
        text_w = bw - 4
        text_h = bh - 5

        # Background fill
        for row in range(by, by + bh):
            self.s(row, bx, " " * bw, curses.color_pair(C_GREEN))
        self.draw_box(by, bx, bh, bw, C_GREEN)

        # Title
        title = " NOTES VIEWER " if self._notes_readonly else " NOTES EDITOR "
        self.s(by, bx + bw // 2 - len(title) // 2, title, curses.color_pair(C_GREEN) | curses.A_BOLD)

        # Scroll if cursor is off screen
        if self.notes_cy < self.notes_scroll:
            self.notes_scroll = self.notes_cy
        if self.notes_cy >= self.notes_scroll + text_h:
            self.notes_scroll = self.notes_cy - text_h + 1

        # Draw lines
        for i in range(text_h):
            li = i + self.notes_scroll
            if li < len(self.notes_lines):
                line = self.notes_lines[li][:text_w]
                self.s(by + 2 + i, bx + 2, line, curses.color_pair(C_GREEN))

        # Cursor (edit mode only)
        if not self._notes_readonly:
            cy_screen = self.notes_cy - self.notes_scroll
            if 0 <= cy_screen < text_h:
                cx_draw = bx + 2 + min(self.notes_cx, text_w)
                self.s(by + 2 + cy_screen, cx_draw, "█", curses.color_pair(C_GREEN) | curses.A_BOLD)

        # Line count and position
        pos = f"Ln {self.notes_cy+1}/{len(self.notes_lines)}  Col {self.notes_cx+1}"
        self.s(by + bh - 2, bx + 2, pos, curses.color_pair(C_DIM))

        # Footer
        if self._notes_readonly:
            footer = "[C] Copy   [ESC] Close"
        else:
            footer = "[ESC] Save   [CTRL+X] Cancel"
        self.s(by + bh - 1, bx + bw // 2 - len(footer) // 2, footer, curses.color_pair(C_DIM))

    def handle_notes_editor_input(self, key):
        lines = self.notes_lines
        cy, cx = self.notes_cy, self.notes_cx

        if key == 27:  # ESC
            if self._notes_readonly:
                self._cancel_notes_editor()
            else:
                self._save_notes_editor()
            return
        if self._notes_readonly:
            # View mode: scrolling and copy
            if key in (ord("c"), ord("C")):
                text = "\n".join(lines)
                if copy_to_clipboard(text):
                    self.toast("NOTES COPIED")
                else:
                    self.toast("CLIPBOARD FAILED")
            elif key == curses.KEY_UP and cy > 0:
                self.notes_cy -= 1
            elif key == curses.KEY_DOWN and cy < len(lines) - 1:
                self.notes_cy += 1
            elif key == curses.KEY_HOME:
                self.notes_cy = 0
            elif key == curses.KEY_END:
                self.notes_cy = len(lines) - 1
            return
        if key == 24:  # CTRL+X — cancel
            self._cancel_notes_editor()
        elif key == ord("\n"):  # Enter — new line
            rest = lines[cy][cx:]
            lines[cy] = lines[cy][:cx]
            lines.insert(cy + 1, rest)
            self.notes_cy += 1
            self.notes_cx = 0
        elif key in (127, curses.KEY_BACKSPACE, 8):  # Backspace
            if cx > 0:
                lines[cy] = lines[cy][:cx-1] + lines[cy][cx:]
                self.notes_cx -= 1
            elif cy > 0:
                self.notes_cx = len(lines[cy - 1])
                lines[cy - 1] += lines.pop(cy)
                self.notes_cy -= 1
        elif key == curses.KEY_DC:  # Delete
            if cx < len(lines[cy]):
                lines[cy] = lines[cy][:cx] + lines[cy][cx+1:]
            elif cy < len(lines) - 1:
                lines[cy] += lines.pop(cy + 1)
        elif key == curses.KEY_LEFT:
            if cx > 0:
                self.notes_cx -= 1
            elif cy > 0:
                self.notes_cy -= 1
                self.notes_cx = len(lines[self.notes_cy])
        elif key == curses.KEY_RIGHT:
            if cx < len(lines[cy]):
                self.notes_cx += 1
            elif cy < len(lines) - 1:
                self.notes_cy += 1
                self.notes_cx = 0
        elif key == curses.KEY_UP:
            if cy > 0:
                self.notes_cy -= 1
                self.notes_cx = min(self.notes_cx, len(lines[self.notes_cy]))
        elif key == curses.KEY_DOWN:
            if cy < len(lines) - 1:
                self.notes_cy += 1
                self.notes_cx = min(self.notes_cx, len(lines[self.notes_cy]))
        elif key == curses.KEY_HOME:
            self.notes_cx = 0
        elif key == curses.KEY_END:
            self.notes_cx = len(lines[cy])
        elif 32 <= key < 127:  # Printable char
            lines[cy] = lines[cy][:cx] + chr(key) + lines[cy][cx:]
            self.notes_cx += 1

    # ── Config screen ───────────────────────────────────────────────
    def draw_config(self, y, x, w):
        self.s(y, x + w//2 - 8, "⚙ CONFIGURATION", curses.color_pair(C_CYAN)|curses.A_BOLD)

        row = y + 2
        self.s(row, x+2, "ENCRYPTION", curses.color_pair(C_DIM))
        info = "Argon2id + AES-256-GCM + ChaCha20-Poly1305 + SHAKE-256"
        self.s(row+1, x+4, info[:w-8], curses.color_pair(C_GREEN))

        row += 3
        self.s(row, x+2, "VAULT LOCATION", curses.color_pair(C_DIM))
        self.s(row+1, x+4, str(get_vault_path())[:w-8], curses.color_pair(C_GREEN))

        row += 3
        self.s(row, x+2, "ENTRIES", curses.color_pair(C_DIM))
        sz = get_vault_path().stat().st_size if get_vault_path().exists() else 0
        self.s(row+1, x+4, f"{len(self.entries)} credentials   ({sz:,} bytes on disk)", curses.color_pair(C_GREEN))

        row += 3
        self.s(row, x+2, "─"*(w-6), curses.color_pair(C_DIM))
        row += 1

        if self.cfg_mode == "menu":
            opts = [
                ("1","Change Master Password","change_pw"),
                ("2","Change Vault Location","change_path"),
                ("3","Export All Credentials","export"),
                ("4","Import Credentials File","import_file"),
                ("5","Erase All Credentials","erase_all"),
            ]
            for oi, (num, label, act) in enumerate(opts):
                sel = oi == self.cfg_cursor
                pref = "▸ " if sel else "  "
                c = C_RED if act == "erase_all" else C_GREEN
                a = curses.color_pair(c)|curses.A_BOLD if sel else curses.color_pair(c)
                self.s(row, x+2, pref, a)
                self.s(row, x+4, f"[{num}] {label}", a)
                self.zone(row, x+2, 1, w-4, act)
                row += 2

        elif self.cfg_mode == "change_pw":
            self.s(row, x+2, "CHANGE MASTER PASSWORD", curses.color_pair(C_YELLOW)|curses.A_BOLD)
            row += 2
            for pi, (pl, pk) in enumerate(zip(["CURRENT PASSWORD","NEW PASSWORD","CONFIRM NEW PASSWORD"], ["current","new","confirm"])):
                active = pi == self.cfg_pw_field
                self.s(row, x+4, pl, curses.color_pair(C_GREEN) if active else curses.color_pair(C_DIM))
                self.zone(row, x+2, 2, w-4, "cfg_pw_field", pi)
                row += 1
                val = self.cfg_pw_fields.get(pk, "")
                d = "•"*len(val) if val else "(empty)"
                self.s(row, x+6, d[:w-12], curses.color_pair(C_GREEN) if val else curses.color_pair(C_DIM))
                if active:
                    cx = x+6+(len("•"*len(val)) if val else 0)
                    if cx < x+w-2:
                        self.s(row, cx, "█", curses.color_pair(C_GREEN)|curses.A_BOLD)
                row += 2
            self.s(row, x+4, "[ENTER] Apply", curses.color_pair(C_CYAN)|curses.A_BOLD)
            self.zone(row, x+4, 1, 13, "cfg_pw_save")
            self.s(row, x+22, "[ESC] Cancel", curses.color_pair(C_DIM))
            self.zone(row, x+22, 1, 12, "cfg_cancel")
            if self.cfg_error:
                self.s(row+2, x+4, f"⚠ {self.cfg_error}", curses.color_pair(C_RED)|curses.A_BOLD)

        elif self.cfg_mode == "change_path":
            self.s(row, x+2, "CHANGE VAULT LOCATION", curses.color_pair(C_YELLOW)|curses.A_BOLD)
            row += 2
            self.s(row, x+4, "CURRENT:", curses.color_pair(C_DIM))
            self.s(row+1, x+6, str(get_vault_path())[:w-10], curses.color_pair(C_GREEN))
            row += 3
            self.s(row, x+4, "NEW PATH:", curses.color_pair(C_GREEN))
            row += 1
            val = self.cfg_path_input
            d = val if val else str(get_vault_path())
            self.s(row, x+6, d[:w-10], curses.color_pair(C_GREEN) if val else curses.color_pair(C_DIM))
            cx = x+6+len(val)
            if cx < x+w-2:
                self.s(row, cx, "█", curses.color_pair(C_GREEN)|curses.A_BOLD)
            row += 2
            self.s(row, x+4, "[ENTER] Apply", curses.color_pair(C_CYAN)|curses.A_BOLD)
            self.zone(row, x+4, 1, 13, "cfg_path_save")
            self.s(row, x+22, "[ESC] Cancel", curses.color_pair(C_DIM))
            self.zone(row, x+22, 1, 12, "cfg_cancel")
            if self.cfg_error:
                self.s(row+2, x+4, f"⚠ {self.cfg_error}", curses.color_pair(C_RED)|curses.A_BOLD)

        elif self.cfg_mode == "export":
            self.s(row, x+2, "EXPORT ALL CREDENTIALS", curses.color_pair(C_YELLOW)|curses.A_BOLD)
            row += 2
            # Security warnings
            warn_attr = curses.color_pair(C_RED)|curses.A_BOLD
            warnings = [
                "╔══════════════════════════════════════════════╗",
                "║         ⚠  SECURITY WARNING  ⚠              ║",
                "╠══════════════════════════════════════════════╣",
                "║ This exports ALL credentials + passwords.   ║",
                "║ Anyone with the export password can read     ║",
                "║ EVERYTHING in your vault.                    ║",
                "║                                              ║",
                "║ If you lose the password, the exported file  ║",
                "║ CANNOT be recovered. There is no reset.      ║",
                "║                                              ║",
                "║ DO NOT share this file over email, chat,     ║",
                "║ or any insecure channel.                     ║",
                "╚══════════════════════════════════════════════╝",
            ]
            for wl in warnings:
                self.s(row, x+2, wl[:w-4], warn_attr)
                row += 1
            row += 1
            for pi, (pl, pk) in enumerate(zip(["EXPORT PASSWORD","CONFIRM PASSWORD"], ["password","confirm"])):
                active = pi == self.cfg_export_field
                self.s(row, x+4, pl, curses.color_pair(C_GREEN) if active else curses.color_pair(C_DIM))
                row += 1
                val = self.cfg_export_fields.get(pk, "")
                d = "•"*len(val) if val else "(empty)"
                self.s(row, x+6, d[:w-12], curses.color_pair(C_GREEN) if val else curses.color_pair(C_DIM))
                if active:
                    cx = x+6+(len("•"*len(val)) if val else 0)
                    if cx < x+w-2:
                        self.s(row, cx, "█", curses.color_pair(C_GREEN)|curses.A_BOLD)
                row += 2
            out_path = str(Path.home() / "vault-invaders-export.enc")
            self.s(row, x+4, f"Output: {out_path}"[:w-8], curses.color_pair(C_DIM))
            row += 2
            self.s(row, x+4, "[ENTER] Export", curses.color_pair(C_CYAN)|curses.A_BOLD)
            self.s(row, x+22, "[ESC] Cancel", curses.color_pair(C_DIM))
            if self.cfg_error:
                self.s(row+2, x+4, f"⚠ {self.cfg_error}", curses.color_pair(C_RED)|curses.A_BOLD)

        elif self.cfg_mode == "import_file":
            self.s(row, x+2, "IMPORT CREDENTIALS FILE", curses.color_pair(C_YELLOW)|curses.A_BOLD)
            row += 2
            warn_attr = curses.color_pair(C_YELLOW)|curses.A_BOLD
            warnings = [
                "╔══════════════════════════════════════════════╗",
                "║         ⚠  IMPORT WARNING  ⚠                ║",
                "╠══════════════════════════════════════════════╣",
                "║ Imported credentials will be ADDED to your  ║",
                "║ vault. Duplicates will NOT be detected.      ║",
                "║                                              ║",
                "║ Only import files from TRUSTED sources.      ║",
                "║ A tampered file could inject bad data.       ║",
                "╚══════════════════════════════════════════════╝",
            ]
            for wl in warnings:
                self.s(row, x+2, wl[:w-4], warn_attr)
                row += 1
            row += 1
            if self.cfg_import_preview is not None:
                count = len(self.cfg_import_preview)
                self.s(row, x+4, f"Found {count} credentials. Import them?", curses.color_pair(C_GREEN)|curses.A_BOLD)
                row += 2
                self.s(row, x+4, "[Y] Confirm Import", curses.color_pair(C_CYAN)|curses.A_BOLD)
                self.s(row, x+26, "[N] Cancel", curses.color_pair(C_DIM))
            else:
                fields = [("FILE PATH", "path"), ("IMPORT PASSWORD", "password")]
                for pi, (pl, pk) in enumerate(fields):
                    active = pi == self.cfg_import_field
                    self.s(row, x+4, pl, curses.color_pair(C_GREEN) if active else curses.color_pair(C_DIM))
                    row += 1
                    val = self.cfg_import_fields.get(pk, "")
                    if pk == "password":
                        d = "•"*len(val) if val else "(empty)"
                    else:
                        d = val if val else "(empty)"
                    self.s(row, x+6, d[:w-12], curses.color_pair(C_GREEN) if val else curses.color_pair(C_DIM))
                    if active:
                        cx = x+6+(len("•"*len(val)) if pk == "password" and val else len(val) if val else 0)
                        if cx < x+w-2:
                            self.s(row, cx, "█", curses.color_pair(C_GREEN)|curses.A_BOLD)
                    row += 2
                self.s(row, x+4, "[ENTER] Import", curses.color_pair(C_CYAN)|curses.A_BOLD)
                self.s(row, x+22, "[ESC] Cancel", curses.color_pair(C_DIM))
            if self.cfg_error:
                self.s(row+2, x+4, f"⚠ {self.cfg_error}", curses.color_pair(C_RED)|curses.A_BOLD)

        elif self.cfg_mode == "erase_all":
            self.s(row, x+2, "ERASE ALL CREDENTIALS", curses.color_pair(C_RED)|curses.A_BOLD)
            row += 2
            warn_attr = curses.color_pair(C_RED)|curses.A_BOLD
            warnings = [
                "╔══════════════════════════════════════════════╗",
                "║      ⚠  DANGER — IRREVERSIBLE ACTION  ⚠     ║",
                "╠══════════════════════════════════════════════╣",
                "║ This will PERMANENTLY DELETE every single    ║",
                "║ credential stored in your vault.             ║",
                "║                                              ║",
                "║ There is NO undo. There is NO recovery.      ║",
                "║ If you have not exported a backup, all       ║",
                "║ passwords will be LOST FOREVER.              ║",
                "║                                              ║",
                "║ Enter your master password to confirm.       ║",
                "╚══════════════════════════════════════════════╝",
            ]
            for wl in warnings:
                self.s(row, x+2, wl[:w-4], warn_attr)
                row += 1
            row += 1
            self.s(row, x+4, "MASTER PASSWORD", curses.color_pair(C_RED))
            row += 1
            val = self.cfg_erase_pw
            d = "•"*len(val) if val else "(empty)"
            self.s(row, x+6, d[:w-12], curses.color_pair(C_RED) if val else curses.color_pair(C_DIM))
            cx = x+6+(len("•"*len(val)) if val else 0)
            if cx < x+w-2:
                self.s(row, cx, "█", curses.color_pair(C_RED)|curses.A_BOLD)
            row += 2
            count = len(self.entries)
            self.s(row, x+4, f"This will destroy {count} credential{'s' if count != 1 else ''}.", curses.color_pair(C_RED))
            row += 2
            self.s(row, x+4, "[ENTER] Erase Everything", curses.color_pair(C_RED)|curses.A_BOLD)
            self.s(row, x+32, "[ESC] Cancel", curses.color_pair(C_DIM))
            if self.cfg_error:
                self.s(row+2, x+4, f"⚠ {self.cfg_error}", curses.color_pair(C_RED)|curses.A_BOLD)

    # ── Import screen ─────────────────────────────────────────────
    def draw_import(self, y, x, w):
        self.s(y, x + w//2 - 10, "📥 IMPORT CREDENTIALS", curses.color_pair(C_CYAN)|curses.A_BOLD)

        row = y + 2
        self.s(row, x+2, "Paste a JSON credential into your clipboard,", curses.color_pair(C_DIM))
        row += 1
        self.s(row, x+2, "then press ENTER to import it.", curses.color_pair(C_DIM))
        row += 2

        self.s(row, x+2, "EXPECTED FORMAT:", curses.color_pair(C_YELLOW))
        row += 1
        sample = '{"system":"...","username":"...","password":"...",'
        self.s(row, x+4, sample[:w-8], curses.color_pair(C_DIM))
        row += 1
        sample2 = ' "hostname":"...","port":"...","env":"DEV"}'
        self.s(row, x+4, sample2[:w-8], curses.color_pair(C_DIM))
        row += 2

        self.s(row, x+2, "─"*(w-6), curses.color_pair(C_DIM))
        row += 1

        # Action buttons
        imp_btn = "▸ [ENTER] Read from Clipboard & Import"
        self.s(row, x+4, imp_btn, curses.color_pair(C_GREEN)|curses.A_BOLD)
        self.zone(row, x+4, 1, len(imp_btn), "do_import")
        row += 2

        # Preview / error
        if self.import_error:
            self.s(row, x+4, f"⚠ {self.import_error}", curses.color_pair(C_RED)|curses.A_BOLD)
            row += 2

        if self.import_preview:
            self.s(row, x+2, "PREVIEW:", curses.color_pair(C_YELLOW)|curses.A_BOLD)
            row += 1
            preview_fields = [
                ("System", self.import_preview.get("system","")),
                ("User", self.import_preview.get("username","")),
                ("Pass", "•"*len(self.import_preview.get("password",""))),
                ("Host", self.import_preview.get("hostname","")),
                ("Port", self.import_preview.get("port","")),
                ("URL", self.import_preview.get("url","")),
                ("Env", self.import_preview.get("env","DEV")),
                ("Desc", self.import_preview.get("description","")),
            ]
            for label, val in preview_fields:
                if val:
                    self.s(row, x+4, f"{label}: ", curses.color_pair(C_DIM))
                    self.s(row, x+4+len(label)+2, val[:w-len(label)-10], curses.color_pair(C_GREEN))
                    row += 1
            row += 1
            confirm_btn = "▸ [Y] Confirm Import"
            self.s(row, x+4, confirm_btn, curses.color_pair(C_GREEN)|curses.A_BOLD)
            self.zone(row, x+4, 1, len(confirm_btn), "confirm_import")
            cancel_btn = "[N] Cancel"
            self.s(row, x+30, cancel_btn, curses.color_pair(C_RED))
            self.zone(row, x+30, 1, len(cancel_btn), "cancel_import")

    def _do_import(self):
        """Read clipboard, parse JSON, show preview."""
        self.import_error = ""
        self.import_preview = None
        raw = read_from_clipboard().strip()
        if not raw:
            self.import_error = "CLIPBOARD IS EMPTY"
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            self.import_error = f"INVALID JSON: {e}"
            return
        if isinstance(data, list):
            if not data:
                self.import_error = "EMPTY ARRAY"; return
            data = data[0]  # take first entry
        if not isinstance(data, dict):
            self.import_error = "EXPECTED JSON OBJECT"; return
        if not data.get("system") or not data.get("username") or not data.get("password"):
            self.import_error = "MISSING: system, username, or password"; return
        # Normalize fields
        entry = {}
        for field in self.form_fields:
            entry[field] = str(data.get(field, ""))
        if not entry.get("env") or entry["env"] not in ("DEV","TEST","PROD"):
            entry["env"] = "DEV"
        self.import_preview = entry

    def _confirm_import(self):
        if self.import_preview:
            self.entries.append(dict(self.import_preview))
            save_vault(self.entries, self.master_pw)
            self.toast("CREDENTIAL IMPORTED")
            self.import_preview = None
            self.import_error = ""
            self.tab = 0
            self.mode = "list"

    def _export_entry(self):
        items = self.filtered()
        if items and self.cursor < len(items):
            e = items[self.cursor]
            export = {}
            for field in self.form_fields:
                val = e.get(field, "")
                if val:
                    export[field] = val
            json_str = json.dumps(export, indent=2, ensure_ascii=False)
            if copy_to_clipboard(json_str):
                self.toast("JSON COPIED TO CLIPBOARD")
            else:
                self.toast("CLIPBOARD FAILED")

    # ── Toast ───────────────────────────────────────────────────────
    def draw_toast(self):
        if self.toast_msg and time.time() - self.toast_time < 2:
            h, w = self.scr.getmaxyx()
            msg = f" 👾 {self.toast_msg} "
            self.s(h-2, w//2 - len(msg)//2, msg, curses.color_pair(C_GREEN_INV)|curses.A_BOLD)
        elif time.time() - self.toast_time >= 2:
            self.toast_msg = ""

    def draw_help(self):
        h, w = self.scr.getmaxyx()
        helps = {
            "tabs": " ←→/Tab:Switch  ENTER/↓:Open  R:Refresh  Q:Quit  L:Lock ",
            "list": "",
            "detail": " C:Copy User  P:Copy Pass  S:Show/Hide  N:Notes  E:Edit  W:Dup  X:Export  D:Delete  ESC:Back ",
            "form": " TAB/↑↓:Fields  ENTER:Save  ESC:Back  ←→:Env ",
            "confirm_delete": ' Type "delete" + ENTER   ESC:Cancel ',
            "config": " ↑↓:Navigate  ENTER:Select  ESC:Tab Bar ",
            "import": " ENTER:Read Clipboard  Y:Confirm  N:Cancel  ESC:Tab Bar ",
            "notes_editor": "",
        }
        ht = helps.get(self.mode, "")
        if self.mode == "config" and self.cfg_mode != "menu":
            ht = " TAB/↑↓:Fields  ENTER:Apply  ESC:Cancel "
        self.s(h-1, 0, ht[:w-1], curses.color_pair(C_DIM))

    # ── Main draw ───────────────────────────────────────────────────
    def draw(self):
        self.scr.erase()
        self.click_zones = []
        h, w = self.scr.getmaxyx()
        if self.stars:
            self.stars.draw(self.scr)
        self.draw_header()
        cy = 5

        if self.tab == 0:
            split = min(40, w//3)
            self.draw_search(cy, 0, split)
            self.zone(cy, 0, 3, split, "focus_search")
            for i in range(cy, h-2):
                self.s(i, split, "│", curses.color_pair(C_DIM))
            max_vis = max(1, (h - cy - 6) // 2)
            self.draw_list(cy+3, 0, split-1, max_vis)
            self.s(h-3, 1, "[+] NEW ENTRY", curses.color_pair(C_CYAN)|curses.A_BOLD)
            self.zone(h-3, 1, 1, 13, "add_entry")
            rx, rw = split+2, w-split-3
            if self.mode == "form":
                self.draw_form(cy, rx, rw)
            else:
                self.draw_detail(cy, rx, rw)

        elif self.tab == 1:
            fw = min(70, w-4)
            if self.mode == "form":
                self.draw_form(cy, max(1, w//2-fw//2), fw)
            else:
                # Passive view — show prompt to press Enter
                self.s(cy+4, w//2-12, "Press ENTER to add new", curses.color_pair(C_DIM))
                for i, ln in enumerate(INVADER_SM):
                    self.s(cy+1+i, w//2 - len(ln)//2, ln, curses.color_pair(C_CYAN))

        elif self.tab == 2:
            iw = min(70, w-4)
            if self.mode == "import":
                self.draw_import(cy, max(1, w//2-iw//2), iw)
            else:
                self.s(cy+3, w//2-14, "Press ENTER to import", curses.color_pair(C_DIM))
                self.s(cy+1, w//2-3, "📥 📥 📥", curses.color_pair(C_CYAN))

        elif self.tab == 3:
            cw = min(80, w-4)
            if self.mode == "config":
                self.draw_config(cy, max(1, w//2-cw//2), cw)
            else:
                self.s(cy+4, w//2-14, "Press ENTER to configure", curses.color_pair(C_DIM))
                self.s(cy+2, w//2-3, "⚙ ⚙ ⚙", curses.color_pair(C_CYAN))

        self.draw_toast()
        if self.mode == "confirm_delete":
            self.draw_confirm()
        if self.mode == "notes_editor":
            self.draw_notes_editor()
        self.draw_help()
        self.scr.refresh()

    # ── Input handlers ──────────────────────────────────────────────
    def handle_tabs_input(self, key):
        """Tab bar is focused. ←→/Tab switch tabs, Enter/↓ dives into content."""
        if key in (curses.KEY_LEFT, curses.KEY_BTAB):
            self.tab = (self.tab - 1) % len(TABS)
        elif key in (curses.KEY_RIGHT, ord("\t")):
            self.tab = (self.tab + 1) % len(TABS)
        elif key in (ord("\n"), curses.KEY_DOWN):
            self._enter_tab()
        elif key in (ord("q"), ord("Q")):
            return "quit"
        elif key in (ord("l"), ord("L")):
            return "lock"
        elif key in (ord("r"), ord("R")):
            self._refresh_vault()
        return None

    def _enter_tab(self):
        """Dive into the currently selected tab's content."""
        if self.tab == 0:
            self.mode = "list"
        elif self.tab == 1:
            self._open_add_form()
        elif self.tab == 2:
            self.mode = "import"
            self.import_error = ""
            self.import_preview = None
        elif self.tab == 3:
            self.mode = "config"
            self.cfg_mode = "menu"
            self.cfg_error = ""

    def handle_list_input(self, key):
        items = self.filtered()
        if key == 27:  # ESC → clear search or tab bar
            if self.search:
                self.search = ""
                self.cursor = 0
                self.scroll = 0
            else:
                self.mode = "tabs"
        elif key == curses.KEY_UP:
            self.cursor = max(0, self.cursor-1)
        elif key == curses.KEY_DOWN:
            self.cursor = min(len(items)-1, self.cursor+1) if items else 0
        elif key == ord("\n"):
            if items:
                self.mode = "detail"
                self.show_pw = False
        elif key in (127, curses.KEY_BACKSPACE, 8):
            self.search = self.search[:-1]; self.cursor = 0
        elif 32 <= key < 127:
            self.search += chr(key); self.cursor = 0
        return None

    def handle_detail_input(self, key):
        items = self.filtered()
        if not items or self.cursor >= len(items):
            self.mode = "list"; return None
        e = items[self.cursor]
        if key == 27:
            self.mode = "list"; self.show_pw = False
        elif key in (ord("c"), ord("C")):
            self.toast("USERNAME COPIED" if copy_to_clipboard(e.get("username","")) else "CLIPBOARD FAILED")
        elif key in (ord("p"), ord("P")):
            ok = copy_to_clipboard(e.get("password",""))
            if ok:
                clear_clipboard_after(15)
                self.toast("PASSWORD COPIED (15s)")
            else:
                self.toast("CLIPBOARD FAILED")
        elif key in (ord("s"), ord("S"), ord("h"), ord("H")):
            self.show_pw = not self.show_pw
        elif key in (ord("e"), ord("E")):
            self._open_edit_form(e)
        elif key in (ord("d"), ord("D")):
            self.confirm_input = ""; self.mode = "confirm_delete"
        elif key in (ord("x"), ord("X")):
            self._export_entry()
        elif key in (ord("w"), ord("W")):
            self._open_duplicate_form(e)
        elif key in (ord("n"), ord("N")):
            self._open_notes_editor(entry=e, readonly=True)
        elif key in (ord("b"), ord("B")):
            self.mode = "list"; self.show_pw = False
        return None

    def handle_form_input(self, key):
        field = self.form_fields[self.form_field]
        if key == 27:
            self._close_form()
            self.mode = "tabs"
            return None
        elif key in (ord("\t"), curses.KEY_DOWN):
            self.form_field = (self.form_field+1) % len(self.form_fields)
        elif key == curses.KEY_UP:
            self.form_field = (self.form_field-1) % len(self.form_fields)
        elif key == curses.KEY_LEFT and field == "env":
            envs = ["DEV","TEST","PROD"]
            self.form["env"] = envs[(envs.index(self.form.get("env","DEV"))-1)%3]
        elif key == curses.KEY_RIGHT and field == "env":
            envs = ["DEV","TEST","PROD"]
            self.form["env"] = envs[(envs.index(self.form.get("env","DEV"))+1)%3]
        elif key == ord("\n"):
            if field == "notes":
                self._open_notes_editor()
                return None
            self._save_form()
        elif field != "env" and field != "notes":
            if key in (127, curses.KEY_BACKSPACE, 8):
                self.form[field] = self.form.get(field,"")[:-1]
            elif 32 <= key < 127:
                self.form[field] = self.form.get(field,"") + chr(key)
        return None

    def handle_confirm_input(self, key):
        if key == 27:
            self.mode = "detail"; self.confirm_input = ""
        elif key == ord("\n"):
            if self.confirm_input.strip().lower() == "delete":
                items = self.filtered()
                if items and self.cursor < len(items):
                    e = items[self.cursor]
                    for ri, re_ in enumerate(self.entries):
                        if re_ is e:
                            self.entries.pop(ri); break
                    save_vault(self.entries, self.master_pw)
                    self.cursor = max(0, self.cursor-1)
                    self.toast("ENTRY DESTROYED")
                self.mode = "list"; self.show_pw = False
            else:
                self.toast("TYPE 'delete' TO CONFIRM")
            self.confirm_input = ""
        elif key in (127, curses.KEY_BACKSPACE, 8):
            self.confirm_input = self.confirm_input[:-1]
        elif 32 <= key < 127:
            self.confirm_input += chr(key)
        return None

    def handle_config_input(self, key):
        if self.cfg_mode == "menu":
            if key == curses.KEY_UP:
                self.cfg_cursor = max(0, self.cfg_cursor-1)
            elif key == curses.KEY_DOWN:
                self.cfg_cursor = min(4, self.cfg_cursor+1)
            elif key == ord("\n"):
                [self._open_change_pw, self._open_change_path, self._open_export, self._open_import_file, self._open_erase_all][self.cfg_cursor]()
            elif key == 27:
                self.mode = "tabs"
            elif key in (ord("q"), ord("Q")):
                return "quit"
            elif key in (ord("l"), ord("L")):
                return "lock"
        elif self.cfg_mode == "change_pw":
            if key == 27:
                self.cfg_mode = "menu"; self.cfg_error = ""
            elif key in (ord("\t"), curses.KEY_DOWN):
                self.cfg_pw_field = (self.cfg_pw_field+1)%3
            elif key == curses.KEY_UP:
                self.cfg_pw_field = (self.cfg_pw_field-1)%3
            elif key == ord("\n"):
                self._apply_change_pw()
            else:
                pk = ["current","new","confirm"][self.cfg_pw_field]
                if key in (127, curses.KEY_BACKSPACE, 8):
                    self.cfg_pw_fields[pk] = self.cfg_pw_fields[pk][:-1]
                elif 32 <= key < 127:
                    self.cfg_pw_fields[pk] += chr(key)
        elif self.cfg_mode == "change_path":
            if key == 27:
                self.cfg_mode = "menu"; self.cfg_error = ""
            elif key == ord("\n"):
                self._apply_change_path()
            elif key in (127, curses.KEY_BACKSPACE, 8):
                self.cfg_path_input = self.cfg_path_input[:-1]
            elif 32 <= key < 127:
                self.cfg_path_input += chr(key)
        elif self.cfg_mode == "export":
            if key == 27:
                self.cfg_mode = "menu"; self.cfg_error = ""
            elif key in (ord("\t"), curses.KEY_DOWN):
                self.cfg_export_field = (self.cfg_export_field + 1) % 2
            elif key == curses.KEY_UP:
                self.cfg_export_field = (self.cfg_export_field - 1) % 2
            elif key == ord("\n"):
                self._apply_export()
            else:
                pk = ["password", "confirm"][self.cfg_export_field]
                if key in (127, curses.KEY_BACKSPACE, 8):
                    self.cfg_export_fields[pk] = self.cfg_export_fields[pk][:-1]
                elif 32 <= key < 127:
                    self.cfg_export_fields[pk] += chr(key)
        elif self.cfg_mode == "erase_all":
            if key == 27:
                self.cfg_mode = "menu"; self.cfg_error = ""
            elif key == ord("\n"):
                self._apply_erase_all()
            elif key in (127, curses.KEY_BACKSPACE, 8):
                self.cfg_erase_pw = self.cfg_erase_pw[:-1]
            elif 32 <= key < 127:
                self.cfg_erase_pw += chr(key)
        elif self.cfg_mode == "import_file":
            if key == 27:
                self.cfg_mode = "menu"; self.cfg_error = ""; self.cfg_import_preview = None
            elif self.cfg_import_preview is not None:
                if key in (ord("y"), ord("Y")):
                    self._confirm_import_file()
                elif key in (ord("n"), ord("N")):
                    self.cfg_import_preview = None; self.cfg_error = ""
            elif key in (ord("\t"), curses.KEY_DOWN):
                self.cfg_import_field = (self.cfg_import_field + 1) % 2
            elif key == curses.KEY_UP:
                self.cfg_import_field = (self.cfg_import_field - 1) % 2
            elif key == ord("\n"):
                self._apply_import_file()
            else:
                pk = ["path", "password"][self.cfg_import_field]
                if key in (127, curses.KEY_BACKSPACE, 8):
                    self.cfg_import_fields[pk] = self.cfg_import_fields[pk][:-1]
                elif 32 <= key < 127:
                    self.cfg_import_fields[pk] += chr(key)
        return None

    # ── Input: import ─────────────────────────────────────────────
    def handle_import_input(self, key):
        if key == 27:
            self.mode = "tabs"
            self.import_preview = None
            self.import_error = ""
        elif key == ord("\n"):
            if self.import_preview is None:
                self._do_import()
        elif key in (ord("y"), ord("Y")):
            if self.import_preview:
                self._confirm_import()
        elif key in (ord("n"), ord("N")):
            self.import_preview = None
            self.import_error = ""
        return None

    # ── Actions ─────────────────────────────────────────────────────
    def _switch_tab(self, idx):
        self.tab = idx

    def _refresh_vault(self):
        """Re-read vault from disk."""
        try:
            self.entries = load_vault_data(self.master_pw)
            self.cursor = min(self.cursor, max(0, len(self.entries)-1))
            self.toast("VAULT RELOADED")
        except Exception as e:
            self.toast(f"RELOAD FAILED: {e}"[:40])

    def _open_add_form(self):
        self.form = {"system":"","username":"","password":"","hostname":"","port":"","url":"","description":"","notes":"","env":"DEV"}
        self.form_field = 0; self._edit_id = None; self._edit_ref = None
        self.mode = "form"; self.tab = 1

    def _open_edit_form(self, entry):
        self.form = {k: entry.get(k,"") for k in self.form_fields}
        self.form_field = 0; self._edit_id = id(entry); self._edit_ref = entry
        self.mode = "form"

    def _open_duplicate_form(self, entry):
        self.form = {k: entry.get(k,"") for k in self.form_fields}
        self.form["notes"] = ""  # notes are never duplicated
        self.form_field = 0; self._edit_id = None; self._edit_ref = None
        self.mode = "form"; self.tab = 1

    def _close_form(self):
        self._edit_id = None; self._edit_ref = None
        self.tab = 0
        self.mode = "list"

    def _save_form(self):
        if not self.form.get("system") or not self.form.get("username") or not self.form.get("password"):
            self.toast("FILL REQUIRED FIELDS"); return
        if self._edit_id is not None and self._edit_ref is not None:
            for ri, re_ in enumerate(self.entries):
                if re_ is self._edit_ref:
                    self.entries[ri] = dict(self.form); break
            self.toast("ENTRY UPDATED")
        else:
            self.entries.append(dict(self.form))
            self.toast("ENTRY ADDED")
        save_vault(self.entries, self.master_pw)
        self._edit_id = None; self._edit_ref = None
        self.tab = 0
        self.mode = "list"

    def _open_notes_editor(self, entry=None, readonly=False):
        self._notes_entry = entry
        self._notes_readonly = readonly
        if entry is not None:
            text = entry.get("notes", "")
            self._notes_return_mode = self.mode
        else:
            text = self.form.get("notes", "")
            self._notes_return_mode = "form"
        self._notes_backup = text
        self.notes_lines = text.split("\n") if text else [""]
        self.notes_cy = 0
        self.notes_cx = 0
        self.notes_scroll = 0
        self.mode = "notes_editor"

    def _save_notes_editor(self):
        text = "\n".join(self.notes_lines)
        if self._notes_entry is not None:
            self._notes_entry["notes"] = text
            save_vault(self.entries, self.master_pw)
            self.toast("NOTES SAVED")
        else:
            self.form["notes"] = text
        self.mode = self._notes_return_mode

    def _cancel_notes_editor(self):
        if self._notes_entry is None:
            self.form["notes"] = self._notes_backup
        self.mode = self._notes_return_mode

    def _open_change_pw(self):
        self.cfg_mode = "change_pw"
        self.cfg_pw_fields = {"current":"","new":"","confirm":""}
        self.cfg_pw_field = 0; self.cfg_error = ""

    def _apply_change_pw(self):
        self.cfg_error = ""
        cur, new, conf = self.cfg_pw_fields["current"], self.cfg_pw_fields["new"], self.cfg_pw_fields["confirm"]
        if cur != self.master_pw:
            self.cfg_error = "WRONG CURRENT PASSWORD"; return
        if len(new) < 8:
            self.cfg_error = "MIN 8 CHARACTERS"; return
        if new != conf:
            self.cfg_error = "PASSWORDS DON'T MATCH"; return
        if new == cur:
            self.cfg_error = "SAME AS CURRENT"; return
        self.master_pw = new
        save_vault(self.entries, self.master_pw)
        self.cfg_mode = "menu"
        self.toast("PASSWORD CHANGED")

    def _open_change_path(self):
        self.cfg_mode = "change_path"
        self.cfg_path_input = str(get_vault_path()); self.cfg_error = ""

    def _apply_change_path(self):
        self.cfg_error = ""
        new_path = self.cfg_path_input.strip()
        if not new_path:
            self.cfg_error = "PATH CANNOT BE EMPTY"; return
        new_path = Path(os.path.expanduser(new_path)).resolve()
        old_path = get_vault_path()
        if new_path.is_symlink():
            self.cfg_error = "SYMLINKS NOT ALLOWED"; return
        if new_path.parent.is_symlink():
            self.cfg_error = "PARENT DIR IS A SYMLINK"; return
        try:
            new_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.cfg_error = f"CANNOT CREATE DIR: {e}"; return
        if old_path.exists() and old_path != new_path:
            try:
                import shutil
                shutil.move(str(old_path), str(new_path))
            except Exception as e:
                self.cfg_error = f"MOVE FAILED: {e}"; return
        cfg = load_config(); cfg["vault_path"] = str(new_path); save_config(cfg)
        self.cfg_mode = "menu"
        self.toast("VAULT RELOCATED")

    def _open_erase_all(self):
        self.cfg_mode = "erase_all"
        self.cfg_erase_pw = ""
        self.cfg_error = ""

    def _apply_erase_all(self):
        if not self.cfg_erase_pw:
            self.cfg_error = "ENTER YOUR MASTER PASSWORD"; return
        if self.cfg_erase_pw != self.master_pw:
            self.cfg_error = "WRONG MASTER PASSWORD"; return
        count = len(self.entries)
        self.entries.clear()
        save_vault(self.entries, self.master_pw)
        self.cfg_erase_pw = ""
        self.cfg_mode = "menu"
        self.cfg_error = ""
        self.cursor = 0
        self.scroll = 0
        self.toast(f"ERASED {count} CREDENTIALS")

    def _open_export(self):
        self.cfg_mode = "export"
        self.cfg_export_fields = {"password": "", "confirm": ""}
        self.cfg_export_field = 0
        self.cfg_error = ""

    def _apply_export(self):
        pw = self.cfg_export_fields["password"]
        confirm = self.cfg_export_fields["confirm"]
        if len(pw) < 8:
            self.cfg_error = "MIN 8 CHARACTERS"; return
        if pw != confirm:
            self.cfg_error = "PASSWORDS DON'T MATCH"; return
        if not self.entries:
            self.cfg_error = "NO CREDENTIALS TO EXPORT"; return
        try:
            encrypted = encrypt_vault(self.entries, pw)
            out_path = Path.home() / "vault-invaders-export.enc"
            out_path.write_bytes(b"VAULTEXP" + encrypted)
            out_path.chmod(0o600)
            count = len(self.entries)
            self.cfg_mode = "menu"
            self.cfg_error = ""
            self.toast(f"EXPORTED {count} CREDENTIALS")
        except Exception as e:
            self.cfg_error = f"EXPORT FAILED: {e}"

    def _open_import_file(self):
        self.cfg_mode = "import_file"
        self.cfg_import_fields = {"path": str(Path.home() / "vault-invaders-export.enc"), "password": ""}
        self.cfg_import_field = 0
        self.cfg_import_preview = None
        self.cfg_error = ""

    def _apply_import_file(self):
        path_str = self.cfg_import_fields["path"].strip()
        pw = self.cfg_import_fields["password"]
        if not path_str:
            self.cfg_error = "PATH CANNOT BE EMPTY"; return
        if not pw:
            self.cfg_error = "PASSWORD REQUIRED"; return
        fpath = Path(os.path.expanduser(path_str)).resolve()
        if not fpath.exists():
            self.cfg_error = "FILE NOT FOUND"; return
        try:
            raw = fpath.read_bytes()
        except Exception as e:
            self.cfg_error = f"READ FAILED: {e}"; return
        if not raw.startswith(b"VAULTEXP"):
            self.cfg_error = "NOT A VALID EXPORT FILE"; return
        try:
            entries = decrypt_vault(raw[8:], pw)
        except Exception:
            self.cfg_error = "WRONG PASSWORD OR CORRUPTED FILE"; return
        if not isinstance(entries, list):
            self.cfg_error = "INVALID FILE CONTENT"; return
        self.cfg_import_preview = entries
        self.cfg_error = ""

    def _confirm_import_file(self):
        if self.cfg_import_preview:
            count = len(self.cfg_import_preview)
            self.entries.extend(self.cfg_import_preview)
            save_vault(self.entries, self.master_pw)
            self.cfg_import_preview = None
            self.cfg_mode = "menu"
            self.cfg_error = ""
            self.toast(f"IMPORTED {count} CREDENTIALS")

    # ── Mouse ───────────────────────────────────────────────────────
    def handle_mouse(self, my, mx):
        for zy, zx, zh, zw, action, data in reversed(self.click_zones):
            if zy <= my < zy+zh and zx <= mx < zx+zw:
                return self._exec_click(action, data)
        return None

    def _exec_click(self, action, data):
        items = self.filtered()
        if action == "tab":
            self._switch_tab(data)
            self._enter_tab()
        elif action == "select_entry":
            if 0 <= data < len(items):
                self.cursor = data; self.mode = "detail"; self.show_pw = False
        elif action == "add_entry":
            self._open_add_form()
        elif action == "lock":
            return "lock"
        elif action == "focus_search":
            self.tab = 0; self.mode = "list"
        elif action == "copy_user":
            if items and self.cursor < len(items):
                self.toast("USERNAME COPIED" if copy_to_clipboard(items[self.cursor].get("username","")) else "CLIPBOARD FAILED")
        elif action == "copy_pass":
            if items and self.cursor < len(items):
                ok = copy_to_clipboard(items[self.cursor].get("password",""))
                if ok:
                    clear_clipboard_after(15)
                    self.toast("PASSWORD COPIED (15s)")
                else:
                    self.toast("CLIPBOARD FAILED")
        elif action == "toggle_pw":
            self.show_pw = not self.show_pw
        elif action == "open_notes":
            if items and self.cursor < len(items):
                self._open_notes_editor(entry=items[self.cursor], readonly=True)
        elif action == "edit_entry":
            if items and self.cursor < len(items):
                self._open_edit_form(items[self.cursor])
        elif action == "dup_entry":
            if items and self.cursor < len(items):
                self._open_duplicate_form(items[self.cursor])
        elif action == "delete_entry":
            if items and self.cursor < len(items):
                self.confirm_input = ""; self.mode = "confirm_delete"
        elif action == "back":
            self.mode = "list"; self.show_pw = False
        elif action == "form_field":
            self.form_field = data
        elif action == "set_env":
            self.form["env"] = data; self.form_field = self.form_fields.index("env")
        elif action == "form_save":
            self._save_form()
        elif action == "form_cancel":
            self._close_form()
        elif action == "change_pw":
            self._open_change_pw()
        elif action == "change_path":
            self._open_change_path()
        elif action == "export":
            self._open_export()
        elif action == "import_file":
            self._open_import_file()
        elif action == "erase_all":
            self._open_erase_all()
        elif action == "cfg_pw_field":
            self.cfg_pw_field = data
        elif action == "cfg_pw_save":
            self._apply_change_pw()
        elif action == "cfg_path_save":
            self._apply_change_path()
        elif action == "cfg_cancel":
            self.cfg_mode = "menu"; self.cfg_error = ""
        elif action == "export_entry":
            self._export_entry()
        elif action == "do_import":
            self._do_import()
        elif action == "confirm_import":
            self._confirm_import()
        elif action == "cancel_import":
            self.import_preview = None; self.import_error = ""
        elif action == "refresh":
            self._refresh_vault()
        return None

    # ── Main loop ───────────────────────────────────────────────────
    def run(self):
        h, w = self.scr.getmaxyx()
        self.stars = Stars(h, w)
        self.scr.timeout(150)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        curses.mouseinterval(0)

        while True:
            self.draw()
            try:
                key = self.scr.getch()
            except curses.error:
                continue
            if key == -1:
                if time.time() - self.last_activity >= self.inactivity_timeout:
                    return "lock"
                continue
            self.last_activity = time.time()
            if key == curses.KEY_RESIZE:
                h, w = self.scr.getmaxyx(); self.stars = Stars(h, w); continue

            result = None
            if key == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()
                    if bstate & curses.BUTTON1_CLICKED or bstate & curses.BUTTON1_PRESSED or bstate & curses.BUTTON1_RELEASED:
                        result = self.handle_mouse(my, mx)
                    elif bstate & curses.BUTTON4_PRESSED:
                        if self.mode == "list":
                            self.cursor = max(0, self.cursor-1)
                    elif bstate & (curses.BUTTON5_PRESSED if hasattr(curses, 'BUTTON5_PRESSED') else 0):
                        if self.mode == "list":
                            its = self.filtered()
                            self.cursor = min(len(its)-1, self.cursor+1) if its else 0
                except curses.error:
                    pass
            elif key == ord("\t") and self.mode != "form" and self.mode != "notes_editor":
                self.tab = (self.tab + 1) % len(TABS)
                self.mode = "tabs"
                self.search = ""
            elif key == curses.KEY_BTAB and self.mode != "form" and self.mode != "notes_editor":
                self.tab = (self.tab - 1) % len(TABS)
                self.mode = "tabs"
                self.search = ""
            elif self.mode == "tabs":
                result = self.handle_tabs_input(key)
            elif self.mode == "list":
                result = self.handle_list_input(key)
            elif self.mode == "detail":
                result = self.handle_detail_input(key)
            elif self.mode == "form":
                result = self.handle_form_input(key)
            elif self.mode == "confirm_delete":
                result = self.handle_confirm_input(key)
            elif self.mode == "config":
                result = self.handle_config_input(key)
            elif self.mode == "import":
                result = self.handle_import_input(key)
            elif self.mode == "notes_editor":
                self.handle_notes_editor_input(key)

            if result == "quit":
                break
            elif result == "lock":
                return "lock"
        return "quit"


# ═══════════════════════════════════════════════════════════════════════
# Login Screen
# ═══════════════════════════════════════════════════════════════════════
def login_screen(scr) -> tuple:
    init_colors()
    curses.curs_set(0)
    scr.timeout(150)
    h, w = scr.getmaxyx()
    stars = Stars(h, w)
    vault_exists = get_vault_path().exists()
    password, confirm, field, error = "", "", 0, ""
    reset_mode, reset_input = False, ""
    failed_attempts, lockout_until = load_lockout()

    while True:
        scr.erase()
        stars.draw(scr)

        ly = max(1, h//2 - 12)
        # Draw alien on the left, VAULT text on the right
        alien_w = 24
        logo_w = 41
        gap = 3
        total_w = alien_w + gap + logo_w
        ax = w//2 - total_w//2
        lx = ax + alien_w + gap

        for i, ln in enumerate(ALIEN_LOGO):
            if ax > 0:
                try: scr.addstr(ly+i, ax, ln, curses.color_pair(C_GREEN)|curses.A_BOLD)
                except curses.error: pass

        logo_top = ly + 2
        for i, ln in enumerate(LOGO):
            if lx > 0:
                try: scr.addstr(logo_top+i, lx, ln, curses.color_pair(C_GREEN)|curses.A_BOLD)
                except curses.error: pass

        ty = logo_top + len(LOGO)
        try: scr.addstr(ty, lx, TITLE_LINE, curses.color_pair(C_GREEN))
        except curses.error: pass

        iy = max(ly + len(ALIEN_LOGO), ty) + 2
        enc = "Argon2id+AES-256-GCM+ChaCha20+SHAKE-256"
        try: scr.addstr(iy, w//2-len(enc)//2, enc, curses.color_pair(C_MAGENTA))
        except curses.error: pass

        by = iy + 2
        bw = 50
        bx = w//2 - bw//2
        sub = "ENTER MASTER PASSWORD" if vault_exists else "CREATE MASTER PASSWORD"
        try: scr.addstr(by, w//2-len(sub)//2, sub, curses.color_pair(C_DIM))
        except curses.error: pass

        fy = by + 2
        try:
            scr.addstr(fy, bx+2, "PASSWORD:", curses.color_pair(C_GREEN) if field==0 else curses.color_pair(C_DIM))
            scr.addstr(fy, bx+12, "•"*len(password), curses.color_pair(C_GREEN))
            if field == 0:
                cx = bx+12+len(password)
                if cx < bx+bw-2:
                    scr.addstr(fy, cx, "█", curses.color_pair(C_GREEN)|curses.A_BOLD)
        except curses.error: pass

        if not vault_exists:
            fy2 = fy + 2
            try:
                scr.addstr(fy2, bx+2, "CONFIRM: ", curses.color_pair(C_GREEN) if field==1 else curses.color_pair(C_DIM))
                scr.addstr(fy2, bx+12, "•"*len(confirm), curses.color_pair(C_GREEN))
                if field == 1:
                    cx = bx+12+len(confirm)
                    if cx < bx+bw-2:
                        scr.addstr(fy2, cx, "█", curses.color_pair(C_GREEN)|curses.A_BOLD)
            except curses.error: pass

        if error:
            try: scr.addstr(by+8, w//2-len(error)//2, error, curses.color_pair(C_RED)|curses.A_BOLD)
            except curses.error: pass

        if failed_attempts > 0 and vault_exists:
            now = time.time()
            remaining = max(0, int(lockout_until - now) + 1)
            ay = by + 9
            if remaining > 0:
                # Alien blockade - more aliens appear with more failures
                alien_count = min(failed_attempts, 8)
                alien_row = " ".join(["👾"] * alien_count)
                try: scr.addstr(ay, w//2-len(alien_row)//2, alien_row, curses.color_pair(C_RED)|curses.A_BOLD)
                except curses.error: pass
                taunts = [
                    "INTRUDER DETECTED!",
                    "ACCESS DENIED, HUMAN!",
                    "THE HIVE REJECTS YOU!",
                    "ALIEN FIREWALL ENGAGED!",
                    "RESISTANCE IS FUTILE!",
                    "YOUR SIGNAL IS JAMMED!",
                    "MOTHERSHIP SAYS NO!",
                    "NICE TRY, EARTHLING!",
                ]
                taunt = taunts[min(failed_attempts - 1, len(taunts) - 1)]
                try: scr.addstr(ay+1, w//2-len(taunt)//2, taunt, curses.color_pair(C_RED)|curses.A_BOLD)
                except curses.error: pass
                lock_msg = f"LOCKED {remaining}s  [{failed_attempts} FAILED]"
                try: scr.addstr(ay+2, w//2-len(lock_msg)//2, lock_msg, curses.color_pair(C_RED))
                except curses.error: pass
            else:
                warn = f"👾 {failed_attempts} FAILED ATTEMPTS 👾"
                try: scr.addstr(ay, w//2-len(warn)//2, warn, curses.color_pair(C_RED))
                except curses.error: pass

        try: scr.addstr(h-2, w//2-13, "[ENTER] Submit   [Q] Quit", curses.color_pair(C_DIM))
        except curses.error: pass

        if vault_exists and not reset_mode:
            try: scr.addstr(h-3, w//2-8, "[R] Reset Vault", curses.color_pair(C_RED))
            except curses.error: pass

        if reset_mode:
            ry = by + 6
            try:
                scr.addstr(ry, w//2-18, '⚠ TYPE "RESET" TO DESTROY VAULT ⚠', curses.color_pair(C_RED)|curses.A_BOLD)
                scr.addstr(ry+1, w//2-10, "> " + reset_input, curses.color_pair(C_RED)|curses.A_BOLD)
                cx = w//2-8+len(reset_input)
                if cx < w-2:
                    scr.addstr(ry+1, cx, "█", curses.color_pair(C_RED)|curses.A_BOLD)
                scr.addstr(ry+2, w//2-6, "[ESC] Cancel", curses.color_pair(C_DIM))
            except curses.error: pass

        scr.refresh()
        try: key = scr.getch()
        except curses.error: continue
        if key == -1: continue
        if key == curses.KEY_RESIZE:
            h, w = scr.getmaxyx(); stars = Stars(h, w); continue

        if key in (ord("q"), ord("Q")) and not password and not confirm:
            raise SystemExit(0)
        if key in (ord("\t"), curses.KEY_DOWN, curses.KEY_UP) and not vault_exists:
            field = 1 - field; continue
        if key in (ord("r"), ord("R")) and vault_exists and not password and not reset_mode:
            reset_mode = True; reset_input = ""; continue

        if reset_mode:
            if key == 27:
                reset_mode = False; reset_input = ""; continue
            elif key == ord("\n"):
                if reset_input.strip().upper() == "RESET":
                    get_vault_path().unlink(missing_ok=True); vault_exists = False
                    reset_mode = False; reset_input = ""; error = "VAULT RESET"
                else:
                    reset_input = ""
                continue
            elif key in (127, curses.KEY_BACKSPACE, 8):
                reset_input = reset_input[:-1]; continue
            elif 32 <= key < 127:
                reset_input += chr(key); continue

        if key == ord("\n"):
            error = ""
            if vault_exists:
                now = time.time()
                if now < lockout_until:
                    remaining = int(lockout_until - now) + 1
                    error = f"⚠ LOCKED ({remaining}s)"; continue
                try:
                    entries = load_vault_data(password)
                    clear_lockout()
                    return password, entries
                except Exception:
                    failed_attempts += 1
                    delay = min(2 ** failed_attempts, 30)
                    lockout_until = time.time() + delay
                    save_lockout(failed_attempts, lockout_until)
                    error = f"⚠ WRONG PASSWORD (wait {delay}s)"; password = ""
            else:
                if len(password) < 8: error = "⚠ MIN 8 CHARACTERS"
                elif password != confirm: error = "⚠ PASSWORDS DON'T MATCH"
                else:
                    save_vault([], password)
                    return password, []
            continue

        if key in (127, curses.KEY_BACKSPACE, 8):
            if field == 0: password = password[:-1]
            else: confirm = confirm[:-1]
        elif 32 <= key < 127:
            if field == 0: password += chr(key)
            else: confirm += chr(key)


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════
def main(scr):
    curses.set_escdelay(25)
    curses.curs_set(0)
    init_colors()
    while True:
        try:
            master_pw, entries = login_screen(scr)
        except SystemExit:
            return
        app = VaultApp(scr)
        app.entries = entries
        app.master_pw = master_pw
        result = app.run()
        if result == "lock":
            continue
        else:
            break

if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
    print("\n  👾 Vault locked. Stay safe, space defender.\n")
