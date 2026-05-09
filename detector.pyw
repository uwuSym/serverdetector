import os
import re
import sys
import time
import json
import threading
import requests
import ipaddress
import tkinter as tk
from tkinter import messagebox
from datetime import datetime

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

# ================= CONFIG =================

LOG_DIR = os.path.expandvars(r"%localappdata%\Roblox\logs")

REGION_MAP = {
    "Chicago": "US Central",
    "Ashburn": "US East",
    "Miami": "US Southeast",
    "Dallas": "US South Central",
    "Los Angeles": "US West",
    "San Jose": "US West",
    "New York City": "US East",
}

GAME_SERVER_KEYWORDS = (
    "UDMUX", "udp", "joinGameServer", "GameServerIP", "ServerIP", "ConnectToServer",
)

MENU_KEYWORDS = (
    "leaveGame", "disconnect", "Disconnect", "leaving game",
)

PLACE_ID_RE  = re.compile(r"Joining game '[^']+' place (\d+) at ")
_USERID_RE   = re.compile(r"userid:(\d+)")

POLL_INTERVAL = 1.0
SETTLE_DELAY  = 3.0

SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
DEBUG_LOG_PATH = os.path.join(SCRIPT_DIR, "roblox_detector_debug.log")
CONFIG_PATH    = os.path.join(SCRIPT_DIR, "detector_config.json")

# ── Version ────────────────────────────────────────────────────────────────────
DETECTOR_VERSION = "v15"

# ── Auto-update ────────────────────────────────────────────────────────────────
GITHUB_REPO = "uwuSym/serverdetector"
GITHUB_FILE = "detector.pyw"
GITHUB_BRANCH = "main"

# ── Discord Webhook ────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1502510641439834152/VEH2fPY6u5Tum9jX1MS41QPWgmTlgb6iHIYn1nRp2iTJorMfiRZOlpf2R4eJUOJMP5Yt"

# ── Discord Rich Presence ──────────────────────────────────────────────────────
DISCORD_CLIENT_ID = "1367088498389159966"
# ──────────────────────────────────────────────────────────────────────────────

_ip_cache    = {}
_place_cache = {}
_user_cache  = {}

# ================= THEME =================

BG        = "#0d0d0d"
BG_BUTTON = "#1a1a1a"
BG_HOVER  = "#2a2a2a"
FG        = "#e8e8e8"
FG_DIM    = "#666666"
ACCENT    = "#ffffff"
BORDER    = "#2a2a2a"
GREEN     = "#44aa44"
RED       = "#cc4444"


# ================= CONFIG PERSISTENCE =================

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config():
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "auto_detect": auto_var.get(),
                "rpc":         rpc_var.get(),
                "debug":       debug_var.get(),
                "sound":       sound_var.get(),
                "settingscog": _cfg.get("settingscog", False),
            }, f, indent=2)
    except Exception:
        pass


_cfg = load_config()
_rpc_status = {"text": "", "color": FG_DIM}


# ================= DEBUG LOGGER =================

class DebugLogger:
    def __init__(self, enabled: bool, path: str):
        self.enabled = enabled
        self.path    = path
        self._file   = None

    def open(self, label: str = "Detection run"):
        if not self.enabled:
            return
        self._file = open(self.path, "a", encoding="utf-8")
        self._write("=" * 60)
        self._write(f"{label} started: {datetime.now().isoformat()}")
        self._write("=" * 60)

    def log(self, message: str):
        if not self.enabled or self._file is None:
            return
        self._write(message)

    def close(self, label: str = "Detection run"):
        if self._file is not None:
            self._write(f"{label} ended: {datetime.now().isoformat()}\n")
            self._file.close()
            self._file = None

    def _write(self, message: str):
        self._file.write(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {message}\n")
        self._file.flush()


# ================= ROBLOX USER LOOKUP =================

def extract_roblox_userid(lines: list) -> str:
    """Extract numeric user ID from the GameJoinLoadTime log line."""
    for line in lines:
        m = _USERID_RE.search(line)
        if m:
            return m.group(1)
    return ""


def lookup_roblox_user(user_id: str, debug: DebugLogger) -> tuple:
    """
    Returns (username, display_name) via the public Roblox users API.
    No authentication required.
    """
    if not user_id:
        return "", ""

    if user_id in _user_cache:
        cached = _user_cache[user_id]
        debug.log(f"User cache hit for {user_id}: {cached}")
        return cached

    try:
        debug.log(f"Looking up Roblox user ID {user_id} ...")
        r = requests.get(
            f"https://users.roblox.com/v1/users/{user_id}",
            timeout=5,
        )
        r.raise_for_status()
        data         = r.json()
        username     = data.get("name", "")
        display_name = data.get("displayName", "")
        debug.log(f"  username={username!r} displayName={display_name!r}")
        _user_cache[user_id] = (username, display_name)
        return username, display_name
    except Exception as e:
        debug.log(f"  Roblox user lookup failed: {e}")
        return "", ""


# ================= AUTO-UPDATER =================

GITHUB_RAW_BASE = "https://raw.githubusercontent.com"

def _raw_url() -> str:
    return f"{GITHUB_RAW_BASE}/{GITHUB_REPO}/{GITHUB_BRANCH}/{GITHUB_FILE}"


def _parse_version_from_source(source: str) -> str:
    m = re.search(r'^DETECTOR_VERSION\s*=\s*["\'](.+?)["\']', source, re.MULTILINE)
    return m.group(1) if m else ""


def check_for_update() -> tuple:
    try:
        resp = requests.get(_raw_url(), timeout=8)
        resp.raise_for_status()
        remote_source  = resp.text
        remote_version = _parse_version_from_source(remote_source)
        if not remote_version:
            return False, "", ""
        return remote_version != DETECTOR_VERSION, remote_version, remote_source
    except Exception:
        return False, "", ""


def apply_update(remote_source: str) -> bool:
    script_path = os.path.abspath(__file__)
    backup_path = script_path + ".bak"
    try:
        with open(script_path, "r", encoding="utf-8") as f:
            current = f.read()
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(current)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(remote_source)
        return True
    except Exception as e:
        print(f"[Updater] Failed to write update: {e}")
        return False


def _do_update_check_on_startup():
    update_available, remote_version, remote_source = check_for_update()
    if not update_available or not remote_source:
        return

    def prompt():
        answer = messagebox.askyesno(
            "Update Available",
            f"A new version is available!\n\n"
            f"  Current version : {DETECTOR_VERSION}\n"
            f"  New version     : {remote_version}\n\n"
            f"Download and install the update now?\n"
            f"(The app will restart automatically.)",
            icon="info",
        )
        if not answer:
            return
        ok = apply_update(remote_source)
        if ok:
            messagebox.showinfo(
                "Update Installed",
                f"Updated to {remote_version}!\n\n"
                f"A backup of your old version was saved as:\n"
                f"{os.path.abspath(__file__)}.bak\n\n"
                f"The app will now restart.",
            )
            _restart_app()
        else:
            messagebox.showerror(
                "Update Failed",
                "Could not write the update to disk.\n"
                "Check that the script file is not read-only.",
            )

    root.after(0, prompt)


def _restart_app():
    python = sys.executable
    script = os.path.abspath(__file__)
    root.destroy()
    os.execv(python, [python, script] + sys.argv[1:])


# ================= DISCORD WEBHOOK =================

def send_discord_webhook(
    friendly: str,
    city: str,
    region: str,
    ip: str,
    game_name: str,
    place_name: str,
    roblox_username: str = "",
    display_name: str = "",
    thumbnail: str = "",
    auto_detected: bool = False,
):
    if not DISCORD_WEBHOOK_URL:
        return

    now           = datetime.now()
    timestamp_iso = now.isoformat()
    time_display  = now.strftime("%Y-%m-%d %I:%M:%S %p")
    location_str  = f"{city}, {region}" if region and region != city else city
    trigger       = "Auto-detected" if auto_detected else "Manual detect"

    fields = [
        {"name": "🎮 Roblox username", "value": f"`{roblox_username or 'Unknown'}`", "inline": True},
        {"name": "💬 Display name",    "value": f"`{display_name or 'Unknown'}`",    "inline": True},
        {"name": "📍 Server location", "value": f"`{location_str}`",                 "inline": True},
        {"name": "🌐 Server region",   "value": f"`{friendly}`",                     "inline": True},
        {"name": "🔌 IP address",      "value": f"`{ip}`",                           "inline": True},
        {"name": "🕒 Time",            "value": f"`{time_display}`",                 "inline": True},
        {"name": "⚙️ Version",         "value": f"`{DETECTOR_VERSION}`",             "inline": True},
        {"name": "🗺️ Game",            "value": f"`{game_name or 'N/A'}`",           "inline": True},
        {"name": "📌 Place",           "value": f"`{place_name or 'N/A'}`",          "inline": True},
        {"name": "🔍 Trigger",         "value": f"`{trigger}`",                      "inline": True},
    ]

    embed = {
        "title": "🟢 Roblox Server Detected",
        "color": 0x00ff88,
        "fields": fields,
        "timestamp": timestamp_iso,
        "footer": {"text": f"Roblox Server Detector {DETECTOR_VERSION}"},
    }
    if thumbnail:
        embed["thumbnail"] = {"url": thumbnail}

    payload = {"embeds": [embed]}

    def _send():
        try:
            resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=8)
            resp.raise_for_status()
        except Exception as e:
            print(f"[Webhook] Failed to send: {e}")

    threading.Thread(target=_send, daemon=True).start()


# ================= GAME INFO LOOKUP =================

def lookup_game_info(place_id: str, debug: DebugLogger) -> dict:
    if not place_id:
        return {}

    if place_id in _place_cache:
        cached = _place_cache[place_id]
        debug.log(f"Game info cache hit for place {place_id}: game={cached.get('game_name')!r}")
        return cached

    info = {"game_name": "", "place_name": "", "thumbnail": "", "universe_id": None}

    try:
        r1 = requests.get(
            f"https://apis.roblox.com/universes/v1/places/{place_id}/universe",
            timeout=5,
        )
        r1.raise_for_status()
        universe_id = r1.json().get("universeId")
        if not universe_id:
            _place_cache[place_id] = info
            return info
        info["universe_id"] = universe_id

        r2 = requests.get(
            f"https://games.roblox.com/v1/games?universeIds={universe_id}",
            timeout=5,
        )
        r2.raise_for_status()
        game_data = r2.json().get("data", [])
        root_place_id = None
        if game_data:
            info["game_name"] = game_data[0].get("name", "")
            root_place_id = str(game_data[0].get("rootPlaceId", ""))

        r3 = requests.get(
            f"https://thumbnails.roblox.com/v1/games/icons"
            f"?universeIds={universe_id}&size=512x512&format=Png&isCircular=false",
            timeout=5,
        )
        r3.raise_for_status()
        thumb_data = r3.json().get("data", [])
        if thumb_data and thumb_data[0].get("state") == "Completed":
            info["thumbnail"] = thumb_data[0].get("imageUrl", "")

        if root_place_id and root_place_id != place_id:
            if not BS4_AVAILABLE:
                info["place_name"] = "Sub-place"
            else:
                try:
                    r4 = requests.get(
                        f"https://www.roblox.com/games/{place_id}",
                        timeout=10,
                        headers={"User-Agent": "Mozilla/5.0"},
                        allow_redirects=True,
                    )
                    r4.raise_for_status()
                    soup = BeautifulSoup(r4.text, "html.parser")
                    sub_name = None
                    title_tag = soup.find("title")
                    if title_tag:
                        t = title_tag.get_text().strip()
                        for suffix in [" - Roblox", " | Roblox", " - Play on Roblox", " | Play on Roblox"]:
                            if suffix in t:
                                t = t.replace(suffix, "").strip()
                                break
                        sub_name = t
                    if not sub_name or sub_name in ("Roblox", ""):
                        meta = soup.find("meta", property="og:title")
                        if meta and meta.get("content"):
                            c = meta["content"].strip()
                            sub_name = c.split(" - ")[0].strip() if " - " in c else c
                    info["place_name"] = sub_name if sub_name and sub_name not in ("Roblox", "") else "Sub-place"
                except Exception as e:
                    debug.log(f"  Sub-place scrape error: {e}")
                    info["place_name"] = "Sub-place"
        else:
            info["place_name"] = info["game_name"]

    except Exception as e:
        debug.log(f"Game info lookup error: {e}")

    _place_cache[place_id] = info
    return info


def extract_place_id_from_lines(lines: list, udmux_line_index: int, debug: DebugLogger) -> str:
    for i in range(udmux_line_index, -1, -1):
        m = PLACE_ID_RE.search(lines[i])
        if m:
            debug.log(f"Found place ID {m.group(1)} on line {i + 1}")
            return m.group(1)
    debug.log("No place ID found.")
    return ""


def build_discord_details(game_name: str, place_name: str) -> str:
    if place_name and game_name and place_name != game_name:
        return f"{game_name} — {place_name}"[:128]
    return game_name or place_name or "Playing Roblox"


# ================= AUTO-DETECT WATCHER =================

class LogWatcher:
    def __init__(self, on_server_found, on_menu, get_debug):
        self.on_server_found = on_server_found
        self.on_menu         = on_menu
        self.get_debug       = get_debug
        self._stop_event     = threading.Event()
        self._thread         = None
        self._settle_lock    = threading.Lock()
        self._pending_ip     = None
        self._pending_place  = None
        self._pending_timer  = None
        self.tail_lines      = []   # shared so callbacks can read userid

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        with self._settle_lock:
            if self._pending_timer is not None:
                self._pending_timer.cancel()
                self._pending_timer = None
                self._pending_ip    = None
                self._pending_place = None

    def _schedule_lookup(self, ip, place_id, debug):
        with self._settle_lock:
            if self._pending_timer is not None:
                self._pending_timer.cancel()
            self._pending_ip    = ip
            self._pending_place = place_id

            def fire():
                with self._settle_lock:
                    self._pending_timer = None
                    fired_ip    = self._pending_ip
                    fired_place = self._pending_place
                    self._pending_ip    = None
                    self._pending_place = None
                debug.log(f"Settle delay elapsed — firing lookup for {fired_ip}.")
                self.on_server_found(fired_ip, fired_place)

            self._pending_timer        = threading.Timer(SETTLE_DELAY, fire)
            self._pending_timer.daemon = True
            self._pending_timer.start()

    def _run(self):
        debug = self.get_debug()
        debug.open("Auto-detect watcher")

        watched_file     = None
        file_pos         = 0
        last_reported_ip = None

        try:
            while not self._stop_event.is_set():
                current_file = self._latest_log()

                if current_file != watched_file:
                    if current_file is None:
                        time.sleep(POLL_INTERVAL)
                        continue

                    debug.log(f"Watching: {os.path.basename(current_file)}")
                    watched_file     = current_file
                    last_reported_ip = None
                    self.tail_lines  = []

                    with self._settle_lock:
                        if self._pending_timer:
                            self._pending_timer.cancel()
                            self._pending_timer = None
                            self._pending_ip    = None
                            self._pending_place = None

                    try:
                        with open(watched_file, "r", encoding="utf-8", errors="ignore") as f:
                            self.tail_lines = f.readlines()
                            file_pos        = f.tell()
                    except OSError:
                        self.tail_lines = []
                        file_pos        = 0

                try:
                    with open(watched_file, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(file_pos)
                        new_lines = f.readlines()
                        file_pos  = f.tell()
                except OSError:
                    time.sleep(POLL_INTERVAL)
                    continue

                if not new_lines:
                    time.sleep(POLL_INTERVAL)
                    continue

                menu_fired     = False
                batch_candidates = []

                for line in new_lines:
                    self.tail_lines.append(line)

                    if not menu_fired and any(kw in line for kw in MENU_KEYWORDS):
                        menu_fired       = True
                        last_reported_ip = None
                        self.on_menu()
                        continue

                    matched_kw = next((kw for kw in GAME_SERVER_KEYWORDS if kw in line), None)
                    if matched_kw is None:
                        continue

                    public_ips = [
                        ip for ip in re.findall(r"(\d+\.\d+\.\d+\.\d+)", line)
                        if is_public(ip)
                    ]
                    if not public_ips:
                        continue

                    ip        = public_ips[0]
                    udmux_idx = len(self.tail_lines) - 1
                    place_id  = extract_place_id_from_lines(self.tail_lines, udmux_idx, debug)
                    batch_candidates.append((ip, place_id))

                if len(self.tail_lines) > 5000:
                    self.tail_lines = self.tail_lines[-5000:]

                if batch_candidates:
                    latest_ip, latest_place = batch_candidates[-1]
                    if latest_ip != last_reported_ip:
                        self._schedule_lookup(latest_ip, latest_place, debug)
                        last_reported_ip = latest_ip

                time.sleep(POLL_INTERVAL)

        finally:
            debug.close("Auto-detect watcher")

    @staticmethod
    def _latest_log():
        try:
            files = [
                os.path.join(LOG_DIR, f)
                for f in os.listdir(LOG_DIR)
                if f.endswith(".log") and "Player" in f
            ]
        except OSError:
            return None
        if not files:
            return None
        return max(files, key=lambda f: os.stat(f).st_ctime)


# ================= LOGIC =================

def is_public(ip):
    try:
        return ipaddress.ip_address(ip).is_global
    except Exception:
        return False


def play_alert():
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass


def lookup_ip(ip, debug: DebugLogger):
    try:
        if ip in _ip_cache:
            data = _ip_cache[ip]
        else:
            debug.log(f"Looking up {ip} via ipinfo.io ...")
            response = requests.get(f"https://ipinfo.io/{ip}/json", timeout=5)
            if response.status_code == 429:
                set_result("Rate limited by ipinfo.io.\nWait a moment and try again.")
                return None
            response.raise_for_status()
            data = response.json()
            _ip_cache[ip] = data

        if "Roblox" not in data.get("org", ""):
            debug.log(f"IP {ip} — not a Roblox server.")
            return None

        city     = data.get("city", "Unknown")
        region   = data.get("region", "Unknown")
        friendly = REGION_MAP.get(city, city)
        return friendly, city, region, ip

    except requests.exceptions.Timeout:
        set_result(f"Timed out looking up IP: {ip}")
    except requests.exceptions.RequestException as e:
        set_result(f"Network error: {e}")
    except Exception as e:
        set_result(f"Unexpected error: {e}")
    return None


def format_result(friendly, city, region, ip, game_name, place_name,
                  roblox_username="", display_name="", auto=False):
    lines = []
    if roblox_username:
        tag = f"  ({display_name})" if display_name and display_name != roblox_username else ""
        lines.append(f"Roblox user: {roblox_username}{tag}")
    if game_name:
        lines.append(f"Game: {game_name}")
    if place_name and place_name != game_name:
        lines.append(f"Place: {place_name}")
    lines += [
        f"Server Region: {friendly}",
        f"City: {city}",
        f"Region: {region}",
        f"IP: {ip}",
    ]
    if auto:
        lines.append("[Auto-detected]")
    return "\n".join(lines)


def detect_server():
    if sys.platform != "win32":
        result_label.config(text="This tool only works on Windows.")
        return

    _ip_cache.clear()
    detect_button.config(state="disabled")
    result_label.config(text="Detecting...")

    debug = DebugLogger(enabled=debug_var.get(), path=DEBUG_LOG_PATH)

    def run():
        debug.open()
        try:
            _detect_from_file(debug)
        except Exception as e:
            set_result(f"Unexpected error: {e}")
        finally:
            debug.close()
            root.after(0, lambda: detect_button.config(state="normal"))

    threading.Thread(target=run, daemon=True).start()


def _detect_from_file(debug: DebugLogger):
    if not os.path.exists(LOG_DIR):
        set_result("Roblox log directory not found.")
        return

    try:
        log_files = [
            os.path.join(LOG_DIR, f)
            for f in os.listdir(LOG_DIR)
            if f.endswith(".log") and "Player" in f
        ]
    except PermissionError:
        set_result("Permission denied reading log directory.")
        return

    if not log_files:
        set_result("No Roblox log files found.\nTry launching Roblox first.")
        return

    log_files.sort(key=lambda f: os.stat(f).st_ctime, reverse=True)

    try:
        with open(log_files[0], "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError as e:
        set_result(f"Could not read log file:\n{e}")
        return

    # Extract Roblox user ID then look up username via API
    user_id                       = extract_roblox_userid(lines)
    roblox_username, display_name = lookup_roblox_user(user_id, debug)
    debug.log(f"userid={user_id!r} username={roblox_username!r} display={display_name!r}")

    all_matches = []
    for line_num, line in enumerate(lines, start=1):
        matched_kw = next((kw for kw in GAME_SERVER_KEYWORDS if kw in line), None)
        if matched_kw is None:
            continue
        public_ips = [ip for ip in re.findall(r"(\d+\.\d+\.\d+\.\d+)", line) if is_public(ip)]
        if public_ips:
            for ip in public_ips:
                place_id = extract_place_id_from_lines(lines, line_num - 1, debug)
                all_matches.append((line_num, matched_kw, ip, place_id))
        else:
            all_matches.append((line_num, matched_kw, None, ""))

    candidates = [(ln, kw, ip, pid) for ln, kw, ip, pid in all_matches if ip]

    if not candidates:
        set_result("No Roblox server IP found.\nMake sure you're in a game.")
        return

    _, _, best_ip, best_place = candidates[-1]

    result = lookup_ip(best_ip, debug)
    if not result:
        set_result(f"Could not confirm server region.\nIP {best_ip} is not a Roblox server.")
        return

    friendly, city, region, ip = result
    game_info  = lookup_game_info(best_place, debug)
    game_name  = game_info.get("game_name", "")
    place_name = game_info.get("place_name", "")
    thumbnail  = game_info.get("thumbnail", "")

    set_result(format_result(friendly, city, region, ip, game_name, place_name,
                             roblox_username, display_name))

    send_discord_webhook(
        friendly=friendly, city=city, region=region, ip=ip,
        game_name=game_name, place_name=place_name,
        roblox_username=roblox_username, display_name=display_name,
        thumbnail=thumbnail, auto_detected=False,
    )

    if rpc_var.get() and _presence.connected:
        _presence.update(
            details=build_discord_details(game_name, place_name),
            state=f"{city}, {region}  |  {ip}",
            large_image=thumbnail or "roblox",
            large_text=game_name or "Roblox",
        )


def set_result(text):
    root.after(0, lambda: result_label.config(text=text))


# ================= DISCORD PRESENCE =================

class DiscordPresence:
    def __init__(self, client_id: str):
        self._client_id  = client_id
        self._rpc        = None
        self._connected  = False
        self._start_time = int(time.time())
        self._lock       = threading.Lock()

    def connect(self):
        try:
            from pypresence import Presence
        except ImportError:
            return False
        with self._lock:
            try:
                self._rpc = Presence(self._client_id)
                self._rpc.connect()
                self._connected  = True
                self._start_time = int(time.time())
                return True
            except Exception:
                self._rpc       = None
                self._connected = False
                return False

    def update(self, state, details, large_image="roblox", large_text="Roblox"):
        with self._lock:
            if not self._connected:
                return
            try:
                self._rpc.update(state=state, details=details, start=self._start_time,
                                 large_image=large_image, large_text=large_text)
            except Exception:
                try:
                    self._rpc.connect()
                    self._rpc.update(state=state, details=details, start=self._start_time,
                                     large_image=large_image, large_text=large_text)
                except Exception:
                    self._connected = False

    def clear(self):
        with self._lock:
            if not self._connected:
                return
            try:
                self._rpc.clear()
            except Exception:
                self._connected = False

    def disconnect(self):
        with self._lock:
            if self._rpc is not None:
                try:
                    self._rpc.close()
                except Exception:
                    pass
            self._rpc       = None
            self._connected = False

    @property
    def connected(self):
        return self._connected


_presence = DiscordPresence(DISCORD_CLIENT_ID)


# ================= WATCHER CALLBACKS =================

_watcher: LogWatcher = None


def on_auto_server_found(ip, place_id):
    debug = DebugLogger(enabled=debug_var.get(), path=DEBUG_LOG_PATH)
    debug.open("Auto-detect lookup")

    # Snapshot tail_lines at fire time so we have the full log up to this point
    tail_snapshot = list(_watcher.tail_lines) if _watcher else []

    def run():
        try:
            result = lookup_ip(ip, debug)
            if result:
                friendly, city, region, found_ip = result
                game_info  = lookup_game_info(place_id, debug)
                game_name  = game_info.get("game_name", "")
                place_name = game_info.get("place_name", "")
                thumbnail  = game_info.get("thumbnail", "")

                user_id                       = extract_roblox_userid(tail_snapshot)
                roblox_username, display_name = lookup_roblox_user(user_id, debug)

                set_result(format_result(friendly, city, region, found_ip,
                                         game_name, place_name,
                                         roblox_username, display_name, auto=True))

                send_discord_webhook(
                    friendly=friendly, city=city, region=region, ip=found_ip,
                    game_name=game_name, place_name=place_name,
                    roblox_username=roblox_username, display_name=display_name,
                    thumbnail=thumbnail, auto_detected=True,
                )

                if sound_var.get():
                    root.after(0, play_alert)

                if rpc_var.get() and _presence.connected:
                    _presence.update(
                        details=build_discord_details(game_name, place_name),
                        state=f"{city}, {region}  |  {found_ip}",
                        large_image=thumbnail or "roblox",
                        large_text=game_name or "Roblox",
                    )
        except Exception as e:
            debug.log(f"Auto-detect error: {e}")
        finally:
            debug.close("Auto-detect lookup")

    threading.Thread(target=run, daemon=True).start()


def on_auto_menu():
    set_result("In menu — waiting for next game...")
    if rpc_var.get() and _presence.connected:
        _presence.clear()


def toggle_auto_detect():
    global _watcher
    if auto_var.get():
        if sys.platform != "win32":
            result_label.config(text="Auto-detect only works on Windows.")
            auto_var.set(False)
            return
        _watcher = LogWatcher(
            on_server_found=on_auto_server_found,
            on_menu=on_auto_menu,
            get_debug=lambda: DebugLogger(enabled=debug_var.get(), path=DEBUG_LOG_PATH),
        )
        _watcher.start()
        result_label.config(text="Auto-detect enabled — waiting for a game join...")
    else:
        if _watcher:
            _watcher.stop()
            _watcher = None
        result_label.config(text="Auto-detect disabled.")
    save_config()


def toggle_rich_presence():
    if rpc_var.get():
        ok = _presence.connect()
        if ok:
            _set_rpc_status("● Discord connected", GREEN)
        else:
            _set_rpc_status("✕ Could not connect — is Discord running?", RED)
            rpc_var.set(False)
    else:
        _presence.disconnect()
        _set_rpc_status("", FG_DIM)
    save_config()


def _set_rpc_status(text: str, color: str):
    _rpc_status["text"]  = text
    _rpc_status["color"] = color
    try:
        if rpc_status_label.winfo_exists():
            rpc_status_label.config(text=text, fg=color)
    except Exception:
        pass


def on_sound_toggle():
    save_config()


# ================= SETTINGS WINDOW =================

_settings_win = None


def open_settings():
    global _settings_win, rpc_status_label
    if _settings_win is not None:
        try:
            if _settings_win.winfo_exists():
                _settings_win.lift()
                _settings_win.focus_force()
                return
        except Exception:
            pass

    _settings_win = tk.Toplevel(root)
    _settings_win.title("Settings")
    _settings_win.configure(bg=BG)
    _settings_win.resizable(False, False)
    _settings_win.transient(root)

    rx = root.winfo_x() + root.winfo_width() - 310
    ry = root.winfo_y() + 44
    _settings_win.geometry(f"300x230+{rx}+{ry}")

    frame = tk.Frame(_settings_win, bg=BG, padx=16, pady=12)
    frame.pack(fill="both", expand=True)

    tk.Label(frame, text="Settings", font=("Arial", 12, "bold"), bg=BG, fg=ACCENT).pack(anchor="w", pady=(0, 8))

    tk.Checkbutton(
        frame, text="Auto-detect when joining a game",
        variable=auto_var, font=("Arial", 10),
        bg=BG, fg=FG, selectcolor=BG_BUTTON,
        activebackground=BG, activeforeground=FG,
        cursor="hand2", command=toggle_auto_detect,
    ).pack(anchor="w")

    rpc_row = tk.Frame(frame, bg=BG)
    rpc_row.pack(anchor="w", fill="x", pady=(4, 0))

    tk.Checkbutton(
        rpc_row, text="Discord Rich Presence",
        variable=rpc_var, font=("Arial", 10),
        bg=BG, fg=FG, selectcolor=BG_BUTTON,
        activebackground=BG, activeforeground=FG,
        cursor="hand2", command=toggle_rich_presence,
    ).pack(side="left")

    rpc_status_label = tk.Label(rpc_row, text=_rpc_status["text"],
                                 font=("Arial", 9), bg=BG, fg=_rpc_status["color"])
    rpc_status_label.pack(side="left", padx=(6, 0))

    tk.Checkbutton(
        frame, text="Sound on auto-detect",
        variable=sound_var, font=("Arial", 10),
        bg=BG, fg=FG, selectcolor=BG_BUTTON,
        activebackground=BG, activeforeground=FG,
        cursor="hand2", command=on_sound_toggle,
    ).pack(anchor="w", pady=(4, 0))

    tk.Checkbutton(
        frame, text="Debug mode  (writes log next to script)",
        variable=debug_var, font=("Arial", 9),
        bg=BG, fg=FG_DIM, selectcolor=BG_BUTTON,
        activebackground=BG, activeforeground=FG_DIM,
        cursor="hand2", command=save_config,
    ).pack(anchor="w", pady=(4, 0))

    tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", pady=(10, 6))

    tk.Button(
        frame,
        text=f"Check for Updates  (current: {DETECTOR_VERSION})",
        font=("Arial", 9),
        bg=BG_BUTTON, fg=FG,
        activebackground=BG_HOVER, activeforeground=FG,
        relief="flat", bd=0, cursor="hand2",
        command=manual_update_check,
    ).pack(anchor="w")


def manual_update_check():
    def run():
        update_available, remote_version, remote_source = check_for_update()

        def show():
            if not update_available and not remote_version:
                messagebox.showinfo("Updates", "Could not reach GitHub.\nCheck your internet connection.")
                return
            if not update_available:
                messagebox.showinfo("Up to Date", f"You're on the latest version ({DETECTOR_VERSION}).")
                return
            answer = messagebox.askyesno(
                "Update Available",
                f"A new version is available!\n\n"
                f"  Current : {DETECTOR_VERSION}\n"
                f"  New     : {remote_version}\n\n"
                f"Install now? (App will restart.)",
                icon="info",
            )
            if not answer:
                return
            ok = apply_update(remote_source)
            if ok:
                messagebox.showinfo("Updated", f"Updated to {remote_version}!\nApp will restart.")
                _restart_app()
            else:
                messagebox.showerror("Failed", "Could not write update to disk.")

        root.after(0, show)

    threading.Thread(target=run, daemon=True).start()


# ================= BUTTON HOVER =================

def on_enter(e): detect_button.config(bg=BG_HOVER)
def on_leave(e): detect_button.config(bg=BG_BUTTON)


# ================= GUI =================

root = tk.Tk()
root.title(f"Roblox Server Detector by IsaacSets  •  {DETECTOR_VERSION}")
root.configure(bg=BG)
root.resizable(False, False)

USE_COG = bool(_cfg.get("settingscog", False))
root.geometry("500x330" if USE_COG else "500x480")

auto_var  = tk.BooleanVar(value=_cfg.get("auto_detect", False))
rpc_var   = tk.BooleanVar(value=_cfg.get("rpc",         False))
debug_var = tk.BooleanVar(value=_cfg.get("debug",       False))
sound_var = tk.BooleanVar(value=_cfg.get("sound",       True))

header = tk.Frame(root, bg=BG)
header.pack(fill="x", padx=20, pady=(18, 0))

tk.Label(
    header, text="Roblox Server Detector by IsaacSets",
    font=("Arial", 16, "bold"), bg=BG, fg=ACCENT,
).pack(side="left", expand=True, fill="x")

if USE_COG:
    tk.Button(
        header, text="⚙", font=("Arial", 14),
        bg=BG, fg=FG_DIM, activebackground=BG, activeforeground=FG,
        relief="flat", bd=0, cursor="hand2", command=open_settings,
    ).pack(side="right")

tk.Label(root, text=DETECTOR_VERSION, font=("Arial", 9), bg=BG, fg=FG_DIM).pack()

detect_button = tk.Button(
    root, text="Detect Current Server",
    font=("Arial", 13, "bold"),
    bg=BG_BUTTON, fg=FG,
    activebackground=BG_HOVER, activeforeground=FG,
    relief="flat", bd=0, padx=24, pady=10,
    cursor="hand2", command=detect_server,
)
detect_button.pack(pady=(12, 20))
detect_button.bind("<Enter>", on_enter)
detect_button.bind("<Leave>", on_leave)

tk.Frame(root, bg=BORDER, height=1).pack(fill="x", padx=40)

result_label = tk.Label(
    root, text="Press the button to detect your server.",
    font=("Arial", 12), bg=BG, fg=FG, justify="left",
)
result_label.pack(pady=20)

tk.Frame(root, bg=BORDER, height=1).pack(fill="x", padx=40)

if not USE_COG:
    options_frame = tk.Frame(root, bg=BG)
    options_frame.pack(side="bottom", pady=12)

    tk.Checkbutton(
        options_frame, text="Auto-detect when joining a game",
        variable=auto_var, font=("Arial", 10),
        bg=BG, fg=FG, selectcolor=BG_BUTTON,
        activebackground=BG, activeforeground=FG,
        cursor="hand2", command=toggle_auto_detect,
    ).pack(anchor="w")

    rpc_row = tk.Frame(options_frame, bg=BG)
    rpc_row.pack(anchor="w", pady=(4, 0))

    tk.Checkbutton(
        rpc_row, text="Discord Rich Presence",
        variable=rpc_var, font=("Arial", 10),
        bg=BG, fg=FG, selectcolor=BG_BUTTON,
        activebackground=BG, activeforeground=FG,
        cursor="hand2", command=toggle_rich_presence,
    ).pack(side="left")

    rpc_status_label = tk.Label(rpc_row, text=_rpc_status["text"],
                                 font=("Arial", 9), bg=BG, fg=_rpc_status["color"])
    rpc_status_label.pack(side="left", padx=(6, 0))

    tk.Checkbutton(
        options_frame, text="Sound on auto-detect",
        variable=sound_var, font=("Arial", 10),
        bg=BG, fg=FG, selectcolor=BG_BUTTON,
        activebackground=BG, activeforeground=FG,
        cursor="hand2", command=on_sound_toggle,
    ).pack(anchor="w", pady=(4, 0))

    tk.Checkbutton(
        options_frame,
        text="Debug mode  (writes roblox_detector_debug.log next to script)",
        variable=debug_var, font=("Arial", 9),
        bg=BG, fg=FG_DIM, selectcolor=BG_BUTTON,
        activebackground=BG, activeforeground=FG_DIM,
        cursor="hand2", command=save_config,
    ).pack(anchor="w", pady=(4, 0))

else:
    rpc_status_label = tk.Label(root, text="", bg=BG, fg=FG_DIM)

if _cfg.get("auto_detect"):
    toggle_auto_detect()
if _cfg.get("rpc"):
    toggle_rich_presence()

threading.Thread(target=_do_update_check_on_startup, daemon=True).start()

root.mainloop()
