import ctypes
import glob
import json
import os
import re
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox

from integrity_guard_manager import ensure_project_integrity_or_exit
from runtime_status_manager import clear_status, get_active_status, install_exception_hooks, load_status

BG = "#05080c"
FG = "#d6e6ff"
FG_DIM = "#7aa2d6"
ACCENT = "#4da3ff"
PANEL = "#0b121a"

FONT_L = ("Consolas", 18)
FONT_M = ("Consolas", 12)
FONT_S = ("Consolas", 10)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "_data")
SETTINGS_DIR = os.path.join(DATA_DIR, "settings")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
INFO_DIR = os.path.join(DATA_DIR, "info")
LOGGED_LOCAL_DIR = os.path.join(DATA_DIR, "logged", "players", "local")
REASONS_CUSTOM_DIR = os.path.join(DATA_DIR, "info", "reasons", "players", "custom")
TAGS_CUSTOM_DIR = os.path.join(DATA_DIR, "info", "tags", "players", "custom")
REASONS_DEFAULT_DIR = os.path.join(DATA_DIR, "info", "reasons", "players", "default")
TAGS_DEFAULT_DIR = os.path.join(DATA_DIR, "info", "tags", "players", "default")
RUNTIME_PATH = os.path.join(SETTINGS_DIR, "manager_runtime.json")
UPDATE_STATE_PATH = os.path.join(SETTINGS_DIR, "update_state.json")
WEBHOOKS_PATH = os.path.join(SETTINGS_DIR, "webhook_settings.json")
LINKS_PATH = os.path.join(INFO_DIR, "detected_links.json")
STARTUP_INFO_PATH = os.path.join(SETTINGS_DIR, "startup_info.json")
FRIENDS_PATH = os.path.join(SETTINGS_DIR, "local_friends.json")
APP_ID = "VRChatWatchGuard.Settings"
FIELD_PATTERN = re.compile(r'"([^"]+)"\s*:\s*"([^"]*)"')
SPECIAL_TABS = ("Friends", "Log User", "Webhooks", "Links")

TAB_DEFS = [
    ("Main", "main_settings.json", lambda main: True),
    ("User Counter", "user_counter_settings.json", lambda main: main.get("user_counter_display", False)),
    ("OSC", "osc_settings.json", lambda main: main.get("osc_display", False)),
]


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_json_safe(path, default):
    try:
        return load_json(path)
    except Exception:
        return dict(default)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def ensure_runtime_file():
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    if not os.path.isfile(RUNTIME_PATH):
        save_json(RUNTIME_PATH, {"resynth_requested_at": 0.0, "shutdown_requested_at": 0.0, "tool_update_requested_at": 0.0})


def ensure_update_state_file():
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    if not os.path.isfile(UPDATE_STATE_PATH):
        save_json(UPDATE_STATE_PATH, {"tool": {"status": "unknown", "message": "", "local_version": "", "remote_version": "", "checked_at": 0.0}})


def ensure_webhooks_file():
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    if not os.path.isfile(WEBHOOKS_PATH):
        save_json(WEBHOOKS_PATH, {"webhooks": [""], "join_enabled": True, "leave_enabled": True, "reasons": {}})


def ensure_links_file():
    os.makedirs(INFO_DIR, exist_ok=True)
    if not os.path.isfile(LINKS_PATH):
        save_json(LINKS_PATH, [])


def ensure_friends_file():
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    if not os.path.isfile(FRIENDS_PATH):
        save_json(FRIENDS_PATH, {"friends": {}})


def load_json_list(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def normalize_text(value):
    return str(value or "").strip()


def normalize_key(value):
    return normalize_text(value).casefold()


def normalize_user_id(value):
    return normalize_key(value).strip("{}")


def format_seconds_compact(total_seconds):
    total_seconds = max(0, int(total_seconds or 0))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h {minutes}m {seconds}s"
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def load_local_friends():
    ensure_friends_file()
    raw = load_json_safe(FRIENDS_PATH, {"friends": {}})
    friends = raw.get("friends", {})
    if not isinstance(friends, dict):
        return {}

    normalized = {}
    changed = False
    for raw_user_id, payload in friends.items():
        record = payload if isinstance(payload, dict) else {}
        user_id = normalize_text(record.get("user_id", "") or raw_user_id)
        user_id_key = normalize_user_id(user_id)
        if not user_id_key or user_id_key == "unknown":
            changed = True
            continue
        username = normalize_text(record.get("username", "") or record.get("last_seen_name", ""))
        normalized_record = {
            "user_id": user_id,
            "username": username,
            "last_seen_name": normalize_text(record.get("last_seen_name", "") or username),
            "added_at": normalize_text(record.get("added_at", "") or record.get("updated_at", "")),
            "updated_at": normalize_text(record.get("updated_at", "") or record.get("added_at", "")),
            "last_met_at": normalize_text(record.get("last_met_at", "")),
            "met_count": max(0, int(float(record.get("met_count", 0) or 0))),
            "total_seconds_together": max(0.0, float(record.get("total_seconds_together", 0.0) or 0.0)),
            "active_since": max(0.0, float(record.get("active_since", 0.0) or 0.0)),
        }
        normalized[user_id_key] = normalized_record
        if record != normalized_record or normalize_key(raw_user_id) != user_id_key:
            changed = True
    if changed:
        save_local_friends(normalized)
    return normalized


def save_local_friends(friends):
    ensure_friends_file()
    save_json(FRIENDS_PATH, {"friends": friends})


def remove_local_friend(user_id):
    friends = load_local_friends()
    removed = friends.pop(normalize_user_id(user_id), None)
    save_local_friends(friends)
    return removed is not None


def load_all_reasons():
    entries = []
    for directory in (REASONS_DEFAULT_DIR, REASONS_CUSTOM_DIR):
        if not os.path.isdir(directory):
            continue
        for path in glob.glob(os.path.join(directory, "*.txt")):
            entries.extend(parse_file(path))
    return entries


def load_all_tags():
    entries = []
    for directory in (TAGS_DEFAULT_DIR, TAGS_CUSTOM_DIR):
        if not os.path.isdir(directory):
            continue
        for path in glob.glob(os.path.join(directory, "*.txt")):
            entries.extend(parse_file(path))
    return entries


def split_lookup_values(value):
    text = normalize_text(value)
    if not text or normalize_key(text) in {"none", "n/a", "unknown"}:
        return []
    values = [text]
    for separator in ("|", ";", ","):
        if separator in text:
            for part in text.split(separator):
                part = normalize_text(part)
                if part and normalize_key(part) not in {"none", "n/a", "unknown"}:
                    values.append(part)
    deduped = []
    seen = set()
    for item in values:
        key = normalize_key(item)
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def build_dropdown_values(entries, field_name, blocked_values=None):
    blocked_keys = {normalize_key(value) for value in (blocked_values or [])}
    values = []
    seen = set()
    for entry in entries:
        for value in split_lookup_values(entry.get(field_name, "")):
            key = normalize_key(value)
            if not key or key in blocked_keys or key in seen:
                continue
            seen.add(key)
            values.append(value)
    return sorted(values, key=normalize_key)


def find_reason_warning(reason_text):
    reason_map = {}
    for entry in load_all_reasons():
        reason = normalize_key(entry.get("Reason", ""))
        warning = normalize_text(entry.get("Warning", ""))
        if reason and warning and reason not in reason_map:
            reason_map[reason] = warning
    for value in split_lookup_values(reason_text):
        warning = reason_map.get(normalize_key(value))
        if warning:
            return warning
    return ""


def normalize_warning_type(value, default=""):
    return {
        "none": "",
        "": "",
        "red": "Red",
        "orange": "Orange",
        "yellow": "Yellow",
    }.get(normalize_key(value), default)


def find_latest_output_log():
    vrchat_dir = os.path.join(os.path.expanduser("~"), "AppData", "LocalLow", "VRChat", "VRChat")
    if not os.path.isdir(vrchat_dir):
        return ""

    latest_path = ""
    latest_mtime = -1.0
    try:
        with os.scandir(vrchat_dir) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                if not entry.name.startswith("output_log_") or not entry.name.endswith(".txt"):
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    latest_path = entry.path
    except OSError:
        return ""

    return latest_path


def resolve_active_log_path():
    configured_path = normalize_text(load_json_safe(STARTUP_INFO_PATH, {"output_log_location": ""}).get("output_log_location", ""))
    if configured_path and os.path.isfile(configured_path):
        return configured_path
    return find_latest_output_log()


def format_playtime_text():
    log_path = resolve_active_log_path()
    if not log_path or not os.path.isfile(log_path):
        return "You Have Been Playing VRChat For Unknown"
    try:
        started_at = os.path.getctime(log_path)
    except OSError:
        return "You Have Been Playing VRChat For Unknown"

    elapsed = max(0, int(time.time() - started_at))
    days, remainder = divmod(elapsed, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"You Have Been Playing VRChat For {days}d {hours}h {minutes}m {seconds}s"


def read_text_lines(path):
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=encoding, errors="strict") as f:
                return f.readlines()
        except UnicodeDecodeError:
            continue
        except Exception:
            return []
    return []


def parse_file(path):
    entries = []
    for line in read_text_lines(path):
        fields = {normalize_text(k): normalize_text(v) for k, v in FIELD_PATTERN.findall(line)}
        if fields:
            entries.append(fields)
    return entries


def write_entries(path, entries, ordered_fields):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for entry in entries:
            parts = []
            for field in ordered_fields:
                parts.append(f'{{"{field}": "{normalize_text(entry.get(field, ""))}"}}')
            f.write(", ".join(parts) + ",\n")


def upsert_entry(entries, key_field, key_value, payload):
    key_value = normalize_key(key_value)
    for entry in entries:
        if normalize_key(entry.get(key_field, "")) == key_value:
            entry.update(payload)
            return entries
    entries.append(dict(payload))
    return entries


def get_dated_filename(prefix):
    return f"{prefix}{time.strftime('%d_%m_%Y')}.txt"


def ensure_file(path, placeholder_line):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            if placeholder_line:
                f.write(placeholder_line + "\n")


def find_closest_log_file(directory, prefix="logged_players_"):
    if not os.path.isdir(directory):
        return None
    now = time.time()
    best_path = None
    best_delta = None
    for path in glob.glob(os.path.join(directory, f"{prefix}*.txt")):
        name = os.path.basename(path)
        stem = name.replace(prefix, "").replace(".txt", "")
        parts = stem.split("_")
        if len(parts) != 3:
            continue
        try:
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            if year == 0:
                continue
            file_stamp = time.mktime((year, month, day, 0, 0, 0, 0, 0, -1))
            delta = abs(now - file_stamp)
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_path = path
        except Exception:
            continue
    return best_path


LOCAL_LOG_PLACEHOLDER = '{"UserID": ""}, {"Username": ""}, {"CurrentName": ""}, {"OldName": ""}, {"Reasons": ""}, {"Tag": ""}, {"Warning": ""},'


def get_local_logged_players_path(for_write=False):
    today_path = os.path.join(LOGGED_LOCAL_DIR, get_dated_filename("logged_players_"))
    existing_path = find_closest_log_file(LOGGED_LOCAL_DIR)

    if os.path.exists(today_path):
        ensure_file(today_path, LOCAL_LOG_PLACEHOLDER)
        return today_path

    if for_write:
        if existing_path and os.path.normcase(os.path.abspath(existing_path)) != os.path.normcase(os.path.abspath(today_path)):
            os.makedirs(os.path.dirname(today_path), exist_ok=True)
            try:
                os.replace(existing_path, today_path)
            except OSError:
                write_entries(today_path, parse_file(existing_path), ("UserID", "Username", "CurrentName", "OldName", "Reasons", "Tag", "Warning"))
                try:
                    os.remove(existing_path)
                except OSError:
                    pass
        ensure_file(today_path, LOCAL_LOG_PLACEHOLDER)
        return today_path

    if existing_path:
        return existing_path

    ensure_file(today_path, LOCAL_LOG_PLACEHOLDER)
    return today_path


def get_custom_reasons_path():
    path = os.path.join(REASONS_CUSTOM_DIR, "custom_reasons.txt")
    ensure_file(path, "")
    return path


def get_custom_tags_path():
    path = os.path.join(TAGS_CUSTOM_DIR, "custom_tags.txt")
    ensure_file(path, "")
    return path


_icon_cache = {}


def load_icon(filename, size=16):
    cache_key = (filename, size)
    if cache_key in _icon_cache:
        return _icon_cache[cache_key]
    path = os.path.join(IMAGES_DIR, "icons", filename)
    image = None
    if os.path.exists(path):
        try:
            raw = tk.PhotoImage(file=path)
            w, h = raw.width(), raw.height()
            fx = max(1, w // size)
            fy = max(1, h // size)
            image = raw.subsample(fx, fy)
        except Exception:
            image = None
    _icon_cache[cache_key] = image
    return image


class SettingsWindow:
    def __init__(self, root):
        self.root = root
        self.current_tab = None
        self.current_path = None
        self.field_vars = {}
        self.field_types = {}
        self.tab_buttons = {}
        self._target_x = 100
        self._target_y = 100
        self._is_minimized = False
        self.last_signatures = {}
        self.last_links_signature = ""
        self.last_friends_signature = ""
        self._suspend_refresh_until = 0
        self.log_form_vars = {}
        self.playtime_label = None
        self.update_button = None
        self.update_status_label = None
        self.friend_info_labels = {}
        self.alert_frame = None
        self.alert_icon_label = None
        self.alert_text_label = None

        self.win = root
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=BG)
        self.win.title("VRChat WatchGuard Settings")
        self.win.bind("<Map>", self._on_map)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self._configure_app_identity()
        ensure_runtime_file()
        ensure_links_file()
        ensure_friends_file()
        ensure_update_state_file()
        ensure_webhooks_file()

        self.w, self.h = 760, 520
        x = (self.win.winfo_screenwidth() - self.w) // 2
        y = (self.win.winfo_screenheight() - self.h) // 2
        self.win.geometry(f"{self.w}x{self.h}+{x}+{y}")
        self._target_x = x
        self._target_y = y

        self._build_ui()
        self._load_app_icon()
        self._load_background_image()
        self._enable_drag()
        self._smooth_follow()
        self.rebuild_tabs()
        self.last_signatures = self._snapshot_settings_signatures()
        self.last_links_signature = self._read_file_signature(LINKS_PATH)
        self.last_friends_signature = self._read_file_signature(FRIENDS_PATH)
        self.win.after(80, self._apply_taskbar_style)
        self.win.after(250, self._watch_settings_files)
        self.win.after(1000, self._update_playtime_label)
        self.win.after(500, self._refresh_runtime_status)

    def _build_ui(self):
        border = tk.Frame(self.win, bg=ACCENT, bd=2)
        border.pack(fill="both", expand=True)

        self.inner = tk.Frame(border, bg=BG)
        self.inner.pack(fill="both", expand=True, padx=2, pady=2)

        header = tk.Frame(self.inner, bg=BG)
        header.pack(fill="x", padx=18, pady=(18, 10))

        self.title_label = tk.Label(header, text="VRChat WatchGuard", fg=ACCENT, bg=BG, font=FONT_L)
        self.title_label.pack(side="left")

        self.min_button = tk.Button(header, text="_", command=self.minimize, bg="#27415f", fg="white",
                                    activebackground="#33567d", activeforeground="white",
                                    relief="flat", bd=0, highlightthickness=0, cursor="hand2")
        self.min_button.pack(side="right", padx=(0, 8))

        self.close_button = tk.Button(header, text="x", command=self.close, bg="#5b2a2a", fg="white",
                                      activebackground="#774040", activeforeground="white",
                                      relief="flat", bd=0, highlightthickness=0, cursor="hand2")
        self.close_button.pack(side="right")

        tk.Label(self.inner, text="Settings Manager", fg=FG, bg=BG, font=FONT_M).pack(anchor="w", padx=20)

        self.tabs_bar = tk.Frame(self.inner, bg=BG)
        self.tabs_bar.pack(fill="x", padx=20, pady=(14, 10))

        self.alert_frame = tk.Frame(self.inner, bg="#23070b")
        self.alert_icon_label = tk.Label(self.alert_frame, bg="#23070b")
        self.alert_icon_label.pack(side="left", padx=(12, 8), pady=8)
        self.alert_text_label = tk.Label(self.alert_frame, text="", fg="white", bg="#23070b", font=FONT_S, anchor="w", justify="left", wraplength=600)
        self.alert_text_label.pack(side="left", fill="x", expand=True, padx=(0, 12), pady=8)

        content_shell = tk.Frame(self.inner, bg=PANEL)
        content_shell.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        self.canvas = tk.Canvas(content_shell, bg=PANEL, highlightthickness=0, bd=0, yscrollincrement=24)
        self.scrollbar = tk.Scrollbar(content_shell, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.form_frame = tk.Frame(self.canvas, bg=PANEL)
        self.form_window = self.canvas.create_window((0, 0), window=self.form_frame, anchor="nw")
        self.form_frame.bind("<Configure>", self._sync_scrollregion)
        self.canvas.bind("<Configure>", self._resize_form)
        self._bind_scroll_events(self.canvas)
        self._bind_scroll_events(self.scrollbar)
        self._bind_scroll_events(self.form_frame)

        footer = tk.Frame(self.inner, bg=BG)
        footer.pack(fill="x", padx=20, pady=(0, 18))

        self.status_label = tk.Label(footer, text="", fg=FG_DIM, bg=BG, font=FONT_S, anchor="w")
        self.status_label.pack(side="left", fill="x", expand=True)

        self.resynth_button = tk.Button(
            footer,
            text="Resynth",
            command=self.queue_resynth,
            bg="#1f4c3a",
            fg="white",
            activebackground="#2b6850",
            activeforeground="white",
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self.resynth_button.pack(side="right", padx=(0, 8))

        self.save_button = tk.Button(footer, text="Save", command=self.save_current, bg="#27415f", fg="white",
                                     activebackground="#33567d", activeforeground="white",
                                     relief="flat", bd=0, highlightthickness=0, cursor="hand2")
        self.save_button.pack(side="right")

    def _load_background_image(self):
        try:
            from PIL import Image, ImageTk
        except ImportError:
            return

        path = os.path.join(IMAGES_DIR, "splashscreen.png")
        if not os.path.isfile(path):
            return

        try:
            image = Image.open(path).convert("RGBA")
            image = image.resize((self.w, self.h), Image.LANCZOS)
            image = Image.blend(Image.new("RGBA", image.size, (0, 0, 0, 255)), image, 0.18)
            self.bg_img = ImageTk.PhotoImage(image)
            bg_label = tk.Label(self.inner, image=self.bg_img, borderwidth=0)
            bg_label.place(x=0, y=0, relwidth=1, relheight=1)
            bg_label.lower()
        except Exception:
            pass

    def _load_app_icon(self):
        path = os.path.join(IMAGES_DIR, "icon.png")
        if not os.path.isfile(path):
            return
        try:
            self.icon_img = tk.PhotoImage(file=path)
            self.win.iconphoto(True, self.icon_img)
        except Exception:
            pass
        if os.name == "nt":
            self.icon_ico_path = os.path.join(IMAGES_DIR, "icon.ico")
            if not os.path.isfile(self.icon_ico_path):
                try:
                    from PIL import Image
                    Image.open(path).save(self.icon_ico_path, format="ICO")
                except Exception:
                    self.icon_ico_path = ""
            self._apply_native_icon()

    def _configure_app_identity(self):
        if os.name != "nt":
            return
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass

    def _apply_native_icon(self):
        ico_path = getattr(self, "icon_ico_path", "")
        if os.name != "nt" or not ico_path or not os.path.isfile(ico_path):
            return
        try:
            self.win.iconbitmap(default=ico_path)
        except Exception:
            pass
        try:
            user32 = ctypes.windll.user32
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x0010
            LR_DEFAULTSIZE = 0x0040
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ICON_BIG = 1
            self._native_icon_handle = user32.LoadImageW(
                0,
                ico_path,
                IMAGE_ICON,
                0,
                0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            )
            if self._native_icon_handle:
                hwnd = self.win.winfo_id()
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, self._native_icon_handle)
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, self._native_icon_handle)
        except Exception:
            pass

    def _enable_drag(self):
        self.win.bind("<ButtonPress-1>", self._start_drag)
        self.win.bind("<B1-Motion>", self._update_target)

    def _start_drag(self, event):
        self._offset_x = event.x
        self._offset_y = event.y

    def _update_target(self, event):
        self._target_x = event.x_root - self._offset_x
        self._target_y = event.y_root - self._offset_y

    def _smooth_follow(self):
        if self._is_minimized or self.win.state() == "iconic":
            self.win.after(16, self._smooth_follow)
            return
        cx, cy = self.win.winfo_x(), self.win.winfo_y()
        nx = cx + (self._target_x - cx) * 0.25
        ny = cy + (self._target_y - cy) * 0.25
        self.win.geometry(f"+{int(nx)}+{int(ny)}")
        self.win.after(16, self._smooth_follow)

    def _on_map(self, _event=None):
        if self._is_minimized:
            self.win.after(40, self._restore_after_minimize)

    def _restore_after_minimize(self):
        if self.win.state() != "normal":
            return
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self._is_minimized = False
        self.win.lift()
        self.win.after(80, self._apply_taskbar_style)
        self.win.after(120, self._apply_native_icon)

    def minimize(self):
        if self._is_minimized:
            return
        self._is_minimized = True
        self.win.overrideredirect(False)
        self.win.attributes("-topmost", False)
        self.win.iconify()

    def _apply_taskbar_style(self):
        if os.name != "nt":
            return
        try:
            hwnd = self.win.winfo_id()
            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
            self._apply_native_icon()
        except Exception:
            pass

    def _bind_scroll_events(self, widget):
        widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
        widget.bind("<Button-4>", self._on_mousewheel_linux, add="+")
        widget.bind("<Button-5>", self._on_mousewheel_linux, add="+")
        for child in widget.winfo_children():
            self._bind_scroll_events(child)

    def _scroll_canvas(self, amount):
        bounds = self.canvas.bbox("all")
        if not bounds:
            return
        content_height = bounds[3] - bounds[1]
        if content_height <= self.canvas.winfo_height():
            return
        self.canvas.yview_scroll(amount, "units")

    def _on_mousewheel(self, event):
        self._scroll_canvas(-1 if event.delta > 0 else 1)
        return "break"

    def _on_mousewheel_linux(self, event):
        self._scroll_canvas(-1 if event.num == 4 else 1)
        return "break"

    def _sync_scrollregion(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_form(self, event):
        self.canvas.itemconfigure(self.form_window, width=event.width)

    def _snapshot_settings_signatures(self):
        signatures = {}
        for _, filename, _ in TAB_DEFS:
            path = os.path.join(SETTINGS_DIR, filename)
            signatures[path] = self._read_file_signature(path)
        return signatures

    def _read_file_signature(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    def _set_alert_state(self, payload):
        if not self.alert_frame:
            return
        if not payload:
            self.alert_frame.pack_forget()
            return

        kind = payload.get("kind", "error")
        code = normalize_text(payload.get("code", "")) or "WG-STATUS"
        message = normalize_text(payload.get("message", "")) or "Unknown issue."
        bg_color = "#23070b" if kind == "error" else "#1b2f47"
        icon_name = "error_face_icon.png" if kind == "error" else "connection_lost_Icon.png"
        icon = load_icon(icon_name, 35)
        self.alert_frame.configure(bg=bg_color)
        self.alert_icon_label.configure(bg=bg_color, image=icon)
        self.alert_icon_label.image = icon
        self.alert_text_label.configure(bg=bg_color, text=f"{code}: {message}")
        if not self.alert_frame.winfo_manager():
            self.alert_frame.pack(fill="x", padx=20, pady=(0, 10), before=self.canvas.master)

    def _refresh_runtime_status(self):
        self._set_alert_state(get_active_status(load_status()))
        self.win.after(500, self._refresh_runtime_status)

    def _refresh_visible_values_from_disk(self):
        if not self.current_path or not os.path.isfile(self.current_path):
            return
        try:
            data = load_json(self.current_path)
        except Exception:
            return
        if set(data.keys()) != set(self.field_vars.keys()):
            self.show_tab(self.current_tab, self.current_path)
            return
        for key, value in data.items():
            if key not in self.field_vars:
                continue
            if isinstance(value, bool):
                self.field_vars[key].set(bool(value))
            else:
                self.field_vars[key].set(str(value))

    def _watch_settings_files(self):
        current = self._snapshot_settings_signatures()
        if current != self.last_signatures:
            self.last_signatures = current
            preferred_tab = self.current_tab
            self.rebuild_tabs(preferred_tab=preferred_tab)
            if time.time() >= self._suspend_refresh_until:
                self._refresh_visible_values_from_disk()
        current_links = self._read_file_signature(LINKS_PATH)
        if current_links != self.last_links_signature:
            self.last_links_signature = current_links
            if self.current_tab == "Links":
                self.show_tab("Links", None)
        current_friends = self._read_file_signature(FRIENDS_PATH)
        if current_friends != self.last_friends_signature:
            self.last_friends_signature = current_friends
            if self.current_tab == "Friends":
                self.show_tab("Friends", None)
        self.win.after(250, self._watch_settings_files)

    def _render_main_extras(self):
        info_row = tk.Frame(self.form_frame, bg=PANEL)
        info_row.pack(fill="x", padx=16, pady=(12, 8))
        self.playtime_label = tk.Label(
            info_row,
            text=format_playtime_text(),
            fg="#9fd4ff",
            bg=PANEL,
            font=FONT_S,
            anchor="w",
            justify="left",
            wraplength=660,
        )
        self.playtime_label.pack(fill="x")

        update_row = tk.Frame(self.form_frame, bg=PANEL)
        update_row.pack(fill="x", padx=16, pady=(0, 10))
        self.update_status_label = tk.Label(update_row, text="", fg=FG_DIM, bg=PANEL, font=FONT_S, anchor="w", justify="left")
        self.update_status_label.pack(side="left", fill="x", expand=True)
        self.update_button = tk.Button(
            update_row,
            text="Checking Updates...",
            command=self.request_tool_update,
            bg="#111923",
            fg="white",
            activebackground="#1a2430",
            activeforeground="white",
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            padx=12,
            pady=6,
        )
        self.update_button.pack(side="right")
        self._refresh_update_button()

    def _update_playtime_label(self):
        if self.playtime_label and self.playtime_label.winfo_exists():
            self.playtime_label.configure(text=format_playtime_text())
        self._refresh_update_button()
        if self.current_tab == "Friends":
            self._refresh_friends_time_labels()
        self.win.after(1000, self._update_playtime_label)

    def _build_friend_info_text(self, entry):
        user_id = normalize_text(entry.get("user_id", ""))
        active_since = float(entry.get("active_since", 0.0) or 0.0)
        total_seconds = float(entry.get("total_seconds_together", 0.0) or 0.0)
        if active_since > 0.0:
            total_seconds += max(0.0, time.time() - active_since)
        info_lines = [
            f"User ID: {user_id or 'Unknown'}",
            f"Added: {normalize_text(entry.get('added_at', '')) or 'Unknown'}",
            f"Times Met: {max(0, int(entry.get('met_count', 0) or 0))}",
            f"Time Together: {format_seconds_compact(total_seconds)}",
        ]
        last_met = normalize_text(entry.get("last_met_at", ""))
        if last_met:
            info_lines.append(f"Last Met: {last_met}")
        if active_since > 0.0:
            info_lines.append("Status: Currently together")
        return "\n".join(info_lines)

    def _refresh_friends_time_labels(self):
        for user_id, label in list(self.friend_info_labels.items()):
            if not label.winfo_exists():
                self.friend_info_labels.pop(user_id, None)
                continue
            entry = getattr(label, "_friend_entry", {})
            label.configure(text=self._build_friend_info_text(entry))

    def _refresh_update_button(self):
        if not self.update_button or not self.update_button.winfo_exists():
            return
        state = load_json_safe(UPDATE_STATE_PATH, {"tool": {"status": "unknown", "message": ""}})
        tool_state = state.get("tool", {})
        status = normalize_text(tool_state.get("status", "unknown"))
        message = normalize_text(tool_state.get("message", ""))
        if status == "update_available":
            self.update_button.configure(text="Update Available", bg="#1f4c3a", activebackground="#2b6850")
            if self.update_status_label:
                self.update_status_label.configure(text=message or "A newer version is ready.", fg="#9fd4ff")
        elif status == "up_to_date":
            self.update_button.configure(text="Up To Date", bg="#27415f", activebackground="#33567d")
            if self.update_status_label:
                self.update_status_label.configure(text=message or "You are on the latest version.", fg=FG_DIM)
        elif status == "error":
            self.update_button.configure(text="Update Check Failed", bg="#5b2a2a", activebackground="#774040")
            if self.update_status_label:
                self.update_status_label.configure(text=message or "GitHub could not be reached.", fg="#ffb1b1")
        else:
            self.update_button.configure(text="Checking Updates...", bg="#111923", activebackground="#1a2430")
            if self.update_status_label:
                self.update_status_label.configure(text=message or "Checking GitHub for updates.", fg=FG_DIM)

    def request_tool_update(self):
        state = load_json_safe(UPDATE_STATE_PATH, {"tool": {"status": "unknown", "message": ""}})
        tool_state = state.get("tool", {})
        if normalize_text(tool_state.get("status", "")) != "update_available":
            self._refresh_update_button()
            self.status_label.configure(text=normalize_text(tool_state.get("message", "")) or "No update is ready right now.", fg=FG_DIM)
            return
        if not messagebox.askyesno("Update VRCWG", "An update is available. Do you want VRCWG to close and run the updater now?"):
            return
        runtime_data = load_json_safe(RUNTIME_PATH, {"resynth_requested_at": 0.0, "tool_update_requested_at": 0.0})
        runtime_data["tool_update_requested_at"] = time.time()
        save_json(RUNTIME_PATH, runtime_data)
        self.status_label.configure(text="Updater requested. VRCWG will close and update.", fg=FG_DIM)

    def close(self):
        if not messagebox.askyesno("Close VRCWG", "Are you sure you want to close VRCWG?"):
            return
        try:
            runtime_data = load_json_safe(RUNTIME_PATH, {"resynth_requested_at": 0.0})
            runtime_data["shutdown_requested_at"] = time.time()
            save_json(RUNTIME_PATH, runtime_data)
        except Exception:
            pass
        self.root.destroy()

    def queue_resynth(self):
        runtime_data = load_json_safe(RUNTIME_PATH, {"resynth_requested_at": 0.0})
        runtime_data["resynth_requested_at"] = time.time()
        save_json(RUNTIME_PATH, runtime_data)
        self.status_label.configure(text="Requested resynth.", fg=FG_DIM)

    def _ensure_custom_reason_definition(self, reason_text, warning_type):
        if not normalize_text(reason_text):
            return
        entries = [entry for entry in parse_file(get_custom_reasons_path()) if normalize_text(entry.get("Reason", ""))]
        upsert_entry(entries, "Reason", reason_text, {"Reason": reason_text, "Warning": warning_type})
        write_entries(get_custom_reasons_path(), entries, ("Reason", "Warning"))

    def _ensure_custom_tag_definition(self, tag_text):
        if not normalize_text(tag_text):
            return
        entries = [entry for entry in parse_file(get_custom_tags_path()) if normalize_text(entry.get("Tag", ""))]
        upsert_entry(entries, "Tag", tag_text, {"Tag": tag_text, "Image": ""})
        write_entries(get_custom_tags_path(), entries, ("Tag", "Image"))

    def _save_manual_logged_user(self):
        current_username = normalize_text(self.log_form_vars["current_username"].get())
        user_id = normalize_text(self.log_form_vars["user_id"].get())
        reason_text = normalize_text(self.log_form_vars["reason_custom"].get()) or normalize_text(self.log_form_vars["reason_choice"].get())
        tag_text = normalize_text(self.log_form_vars["tag_custom"].get()) or normalize_text(self.log_form_vars["tag_choice"].get())
        old_name = normalize_text(self.log_form_vars["old_name"].get())
        warning_type = normalize_warning_type(find_reason_warning(reason_text), default=normalize_warning_type(self.log_form_vars["warning"].get(), default=""))

        if not current_username or not user_id:
            self.status_label.configure(text="Current username and user id are required.", fg="#ffb1b1")
            return

        if normalize_key(tag_text) == "creator":
            self.status_label.configure(text="Creator tag is protected and cannot be assigned here.", fg="#ffb1b1")
            return

        if reason_text and not find_reason_warning(reason_text):
            self._ensure_custom_reason_definition(reason_text, warning_type)
        if tag_text and normalize_key(tag_text) not in {normalize_key(value) for value in build_dropdown_values(load_all_tags(), "Tag", blocked_values={"creator"})}:
            self._ensure_custom_tag_definition(tag_text)

        entries = [entry for entry in parse_file(get_local_logged_players_path()) if normalize_text(entry.get("UserID", ""))]
        upsert_entry(
            entries,
            "UserID",
            user_id,
            {
                "UserID": user_id,
                "Username": current_username,
                "CurrentName": current_username,
                "OldName": old_name,
                "Reasons": reason_text,
                "Tag": tag_text,
                "Warning": warning_type,
            },
        )
        write_entries(
            get_local_logged_players_path(for_write=True),
            entries,
            ("UserID", "Username", "CurrentName", "OldName", "Reasons", "Tag", "Warning"),
        )
        self.status_label.configure(text=f"Saved log entry for {current_username}.", fg=FG_DIM)

    def _remove_friend_from_tab(self, user_id):
        if not normalize_user_id(user_id):
            self.status_label.configure(text="No valid user id to remove.", fg="#ffb1b1")
            return
        if remove_local_friend(user_id):
            self.last_friends_signature = self._read_file_signature(FRIENDS_PATH)
            self.show_tab("Friends", None)
            self.status_label.configure(text=f"Removed local friend {user_id}.", fg=FG_DIM)
        else:
            self.status_label.configure(text=f"Friend {user_id} was not saved.", fg="#ffb1b1")

    def _save_webhook_settings(self, urls_text_widget, join_var, leave_var, reason_vars):
        urls = []
        for line in urls_text_widget.get("1.0", "end-1c").splitlines():
            url = normalize_text(line)
            if url:
                urls.append(url)
        payload = {
            "webhooks": urls or [""],
            "join_enabled": bool(join_var.get()),
            "leave_enabled": bool(leave_var.get()),
            "reasons": {reason: bool(var.get()) for reason, var in reason_vars.items()},
        }
        save_json(WEBHOOKS_PATH, payload)
        self.status_label.configure(text="Saved webhook settings.", fg=FG_DIM)

    def _render_webhooks_tab(self):
        tk.Label(self.form_frame, text="Discord Webhooks", fg=ACCENT, bg=PANEL, font=FONT_M).pack(
            anchor="w", padx=16, pady=(16, 10)
        )

        saved = load_json_safe(WEBHOOKS_PATH, {"webhooks": [""], "join_enabled": True, "leave_enabled": True, "reasons": {}})
        all_reasons = build_dropdown_values(load_all_reasons(), "Reason")

        tk.Label(self.form_frame, text="Webhook URLs (one per line)", fg=FG_DIM, bg=PANEL, font=FONT_S).pack(
            anchor="w", padx=16, pady=(0, 4)
        )
        urls_text = tk.Text(self.form_frame, bg="#0f1319", fg="white", insertbackground="white", relief="flat", height=5, wrap="none")
        urls_text.pack(fill="x", padx=16, pady=(0, 10))
        urls_text.insert("1.0", "\n".join([normalize_text(url) for url in saved.get("webhooks", [""]) if normalize_text(url)]))

        toggles = tk.Frame(self.form_frame, bg=PANEL)
        toggles.pack(fill="x", padx=16, pady=(0, 10))
        join_var = tk.BooleanVar(value=bool(saved.get("join_enabled", True)))
        leave_var = tk.BooleanVar(value=bool(saved.get("leave_enabled", True)))
        tk.Checkbutton(toggles, text="Send on Join", variable=join_var, bg=PANEL, fg=FG, activebackground=PANEL, selectcolor="#27415f").pack(side="left")
        tk.Checkbutton(toggles, text="Send on Leave", variable=leave_var, bg=PANEL, fg=FG, activebackground=PANEL, selectcolor="#27415f").pack(side="left", padx=(12, 0))

        tk.Label(self.form_frame, text="Reasons", fg=FG, bg=PANEL, font=FONT_S).pack(anchor="w", padx=16)
        reason_wrap = tk.Frame(self.form_frame, bg=PANEL)
        reason_wrap.pack(fill="x", padx=16, pady=(4, 10))
        reason_vars = {}
        for reason in all_reasons:
            var = tk.BooleanVar(value=bool(saved.get("reasons", {}).get(reason, False)))
            reason_vars[reason] = var
            tk.Checkbutton(
                reason_wrap,
                text=reason,
                variable=var,
                bg=PANEL,
                fg=FG,
                activebackground=PANEL,
                selectcolor="#27415f",
                anchor="w",
                justify="left",
            ).pack(anchor="w")

        tk.Button(
            self.form_frame,
            text="Save Webhooks",
            command=lambda: self._save_webhook_settings(urls_text, join_var, leave_var, reason_vars),
            bg="#27415f",
            fg="white",
            activebackground="#33567d",
            activeforeground="white",
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            padx=12,
            pady=6,
        ).pack(anchor="e", padx=16, pady=(0, 12))

        self._bind_scroll_events(self.form_frame)
        self.status_label.configure(text="Webhook settings", fg=FG_DIM)
        self.canvas.yview_moveto(0)

    def _render_friends_tab(self):
        self.friend_info_labels = {}
        tk.Label(self.form_frame, text="Local Friends", fg=ACCENT, bg=PANEL, font=FONT_M).pack(
            anchor="w", padx=16, pady=(16, 10)
        )

        friends = list(load_local_friends().values())
        friends.sort(key=lambda entry: (normalize_key(entry.get("username", "")), normalize_key(entry.get("user_id", ""))))
        if not friends:
            tk.Label(
                self.form_frame,
                text="No local friends saved yet.",
                fg=FG_DIM,
                bg=PANEL,
                font=FONT_S,
            ).pack(anchor="w", padx=16, pady=(0, 8))
        else:
            for entry in friends:
                user_id = normalize_text(entry.get("user_id", ""))
                username = normalize_text(entry.get("username", "") or entry.get("last_seen_name", "")) or "Unknown"
                active_since = float(entry.get("active_since", 0.0) or 0.0)
                total_seconds = float(entry.get("total_seconds_together", 0.0) or 0.0)
                if active_since > 0.0:
                    total_seconds += max(0.0, time.time() - active_since)

                card = tk.Frame(self.form_frame, bg="#0f1319", highlightthickness=1, highlightbackground="#162230")
                card.pack(fill="x", padx=16, pady=6)

                top_row = tk.Frame(card, bg="#0f1319")
                top_row.pack(fill="x", padx=10, pady=(10, 4))
                tk.Label(top_row, text=username, fg="#ffe37a", bg="#0f1319", font=FONT_M, anchor="w").pack(side="left", fill="x", expand=True)
                tk.Button(
                    top_row,
                    text="Remove Friend",
                    command=lambda uid=user_id: self._remove_friend_from_tab(uid),
                    bg="#5b2a2a",
                    fg="white",
                    activebackground="#774040",
                    activeforeground="white",
                    relief="flat",
                    bd=0,
                    highlightthickness=0,
                    cursor="hand2",
                    padx=12,
                    pady=6,
                ).pack(side="right")

                info_label = tk.Label(
                    card,
                    text=self._build_friend_info_text(entry),
                    fg=FG,
                    bg="#0f1319",
                    font=FONT_S,
                    anchor="w",
                    justify="left",
                    wraplength=620,
                )
                info_label._friend_entry = dict(entry)
                info_label.pack(fill="x", padx=10, pady=(0, 10))
                self.friend_info_labels[user_id] = info_label

        self._bind_scroll_events(self.form_frame)
        self.status_label.configure(text="Local friends", fg=FG_DIM)
        self.canvas.yview_moveto(0)

    def _render_log_user_tab(self):
        self.log_form_vars = {
            "current_username": tk.StringVar(),
            "user_id": tk.StringVar(),
            "reason_choice": tk.StringVar(),
            "tag_choice": tk.StringVar(),
            "reason_custom": tk.StringVar(),
            "tag_custom": tk.StringVar(),
            "old_name": tk.StringVar(),
            "warning": tk.StringVar(value="None"),
        }

        tk.Label(self.form_frame, text="Manual User Logger", fg=ACCENT, bg=PANEL, font=FONT_M).pack(
            anchor="w", padx=16, pady=(16, 10)
        )

        fields = [
            ("Current Username", "current_username"),
            ("User ID", "user_id"),
            ("Old Name", "old_name"),
        ]

        for label_text, key in fields:
            row = tk.Frame(self.form_frame, bg=PANEL)
            row.pack(fill="x", padx=16, pady=6)
            tk.Label(row, text=label_text, fg=FG, bg=PANEL, font=FONT_S, width=24, anchor="w").pack(side="left")
            tk.Entry(
                row,
                textvariable=self.log_form_vars[key],
                bg="#0f1319",
                fg="white",
                insertbackground="white",
                relief="flat",
            ).pack(side="left", fill="x", expand=True)

        saved_reasons = build_dropdown_values(load_all_reasons(), "Reason")
        saved_tags = build_dropdown_values(load_all_tags(), "Tag", blocked_values={"creator"})

        reason_row = tk.Frame(self.form_frame, bg=PANEL)
        reason_row.pack(fill="x", padx=16, pady=6)
        tk.Label(reason_row, text="Reason", fg=FG, bg=PANEL, font=FONT_S, width=24, anchor="w").pack(side="left")
        tk.OptionMenu(reason_row, self.log_form_vars["reason_choice"], *(saved_reasons or [""])).pack(side="left")
        tk.Entry(
            reason_row,
            textvariable=self.log_form_vars["reason_custom"],
            bg="#0f1319",
            fg="white",
            insertbackground="white",
            relief="flat",
        ).pack(side="left", fill="x", expand=True, padx=(8, 0))

        tag_row = tk.Frame(self.form_frame, bg=PANEL)
        tag_row.pack(fill="x", padx=16, pady=6)
        tk.Label(tag_row, text="Tag", fg=FG, bg=PANEL, font=FONT_S, width=24, anchor="w").pack(side="left")
        tk.OptionMenu(tag_row, self.log_form_vars["tag_choice"], *(saved_tags or [""])).pack(side="left")
        tk.Entry(
            tag_row,
            textvariable=self.log_form_vars["tag_custom"],
            bg="#0f1319",
            fg="white",
            insertbackground="white",
            relief="flat",
        ).pack(side="left", fill="x", expand=True, padx=(8, 0))

        warning_row = tk.Frame(self.form_frame, bg=PANEL)
        warning_row.pack(fill="x", padx=16, pady=6)
        tk.Label(warning_row, text="Warning Type", fg=FG, bg=PANEL, font=FONT_S, width=24, anchor="w").pack(side="left")
        tk.OptionMenu(warning_row, self.log_form_vars["warning"], "None", "Yellow", "Orange", "Red").pack(side="left", anchor="w")

        button_row = tk.Frame(self.form_frame, bg=PANEL)
        button_row.pack(fill="x", padx=16, pady=(12, 10))
        tk.Button(
            button_row,
            text="Save Logged User",
            command=self._save_manual_logged_user,
            bg="#27415f",
            fg="white",
            activebackground="#33567d",
            activeforeground="white",
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            padx=12,
            pady=6,
        ).pack(side="left")

        self._bind_scroll_events(self.form_frame)
        self.status_label.configure(text="Manual user logger", fg=FG_DIM)
        self.canvas.yview_moveto(0)

    def _open_link(self, url):
        try:
            webbrowser.open(url)
            self.status_label.configure(text=f"Opened {url}", fg=FG_DIM)
        except Exception:
            self.status_label.configure(text=f"Could not open {url}", fg="#ffb1b1")

    def _render_links_tab(self):
        tk.Label(self.form_frame, text="Detected Links", fg=ACCENT, bg=PANEL, font=FONT_M).pack(
            anchor="w", padx=16, pady=(16, 10)
        )

        links = list(reversed(load_json_list(LINKS_PATH)))
        if not links:
            tk.Label(self.form_frame, text="No links detected yet.", fg=FG_DIM, bg=PANEL, font=FONT_S).pack(
                anchor="w", padx=16, pady=(0, 8)
            )
        else:
            for entry in links:
                row = tk.Frame(self.form_frame, bg=PANEL)
                row.pack(fill="x", padx=16, pady=6)
                tk.Label(
                    row,
                    text=normalize_text(entry.get("detected_at", "")) or "Unknown Time",
                    fg=FG_DIM,
                    bg=PANEL,
                    font=FONT_S,
                    width=20,
                    anchor="w",
                ).pack(side="left")
                tk.Button(
                    row,
                    text=normalize_text(entry.get("url", "")),
                    command=lambda url=normalize_text(entry.get("url", "")): self._open_link(url),
                    bg="#111923",
                    fg="#9fd4ff",
                    activebackground="#1a2430",
                    activeforeground="white",
                    relief="flat",
                    bd=0,
                    highlightthickness=0,
                    cursor="hand2",
                    anchor="w",
                    justify="left",
                    wraplength=460,
                    padx=8,
                    pady=6,
                ).pack(side="left", fill="x", expand=True)

        self._bind_scroll_events(self.form_frame)
        self.status_label.configure(text="Detected links", fg=FG_DIM)
        self.canvas.yview_moveto(0)

    def available_tabs(self):
        main_settings = load_json(os.path.join(SETTINGS_DIR, "main_settings.json"))
        tabs = []
        for label, filename, predicate in TAB_DEFS:
            if predicate(main_settings):
                tabs.append((label, os.path.join(SETTINGS_DIR, filename)))
        for label in SPECIAL_TABS:
            tabs.append((label, None))
        return tabs

    def rebuild_tabs(self, preferred_tab=None):
        for child in self.tabs_bar.winfo_children():
            child.destroy()
        self.tab_buttons.clear()

        tabs = self.available_tabs()
        if not tabs:
            return

        valid_names = [label for label, _ in tabs]
        desired_tab = preferred_tab if preferred_tab in valid_names else self.current_tab
        if desired_tab not in valid_names:
            desired_tab = valid_names[0]
        self.current_tab = desired_tab

        for label, path in tabs:
            button = tk.Button(self.tabs_bar, text=label, command=lambda name=label, p=path: self.show_tab(name, p),
                               relief="flat", bd=0, highlightthickness=0, cursor="hand2")
            self.tab_buttons[label] = button
            button.pack(side="left", padx=(0, 8))

        for label, path in tabs:
            if label == self.current_tab:
                self.show_tab(label, path)
                break

    def show_tab(self, label, path):
        if self.current_path and (label != self.current_tab or path != self.current_path):
            self.save_current(silent=True)
        self.current_tab = label
        self.current_path = path
        for name, button in self.tab_buttons.items():
            active = name == label
            button.configure(
                bg="#27415f" if active else "#111923",
                fg="white" if active else FG_DIM,
                activebackground="#33567d" if active else "#1a2430",
                activeforeground="white",
                padx=12,
                pady=6,
            )

        for child in self.form_frame.winfo_children():
            child.destroy()

        self.field_vars.clear()
        self.field_types.clear()

        if path is None:
            self.save_button.configure(state="disabled")
            if label == "Friends":
                self._render_friends_tab()
            elif label == "Log User":
                self._render_log_user_tab()
            elif label == "Webhooks":
                self._render_webhooks_tab()
            elif label == "Links":
                self._render_links_tab()
            return

        self.save_button.configure(state="normal")
        data = load_json(path)

        tk.Label(self.form_frame, text=os.path.basename(path), fg=ACCENT, bg=PANEL, font=FONT_M).pack(
            anchor="w", padx=16, pady=(16, 10)
        )

        for key, value in data.items():
            row = tk.Frame(self.form_frame, bg=PANEL)
            row.pack(fill="x", padx=16, pady=6)

            tk.Label(row, text=key, fg=FG, bg=PANEL, font=FONT_S, width=24, anchor="w").pack(side="left")

            self.field_types[key] = type(value)
            if isinstance(value, bool):
                var = tk.BooleanVar(value=value)
                widget = tk.Checkbutton(row, variable=var, bg=PANEL, activebackground=PANEL,
                                        selectcolor="#27415f", highlightthickness=0,
                                        command=lambda: self.win.after(0, self.save_current))
                widget.pack(side="left")
            else:
                var = tk.StringVar(value=str(value))
                widget = tk.Entry(row, textvariable=var, bg="#0f1319", fg="white",
                                  insertbackground="white", relief="flat")
                widget.pack(side="left", fill="x", expand=True)
                widget.bind("<FocusIn>", self._mark_local_edit, add="+")
                widget.bind("<KeyRelease>", self._mark_local_edit, add="+")
                widget.bind("<FocusOut>", lambda _event: self.win.after(0, self.save_current), add="+")
                widget.bind("<Return>", lambda _event: self.win.after(0, self.save_current), add="+")

            self.field_vars[key] = var

        if label == "Main":
            self._render_main_extras()

        self._bind_scroll_events(self.form_frame)
        self._refresh_visible_values_from_disk()
        self.status_label.configure(text=f"Editing {label}")
        self.canvas.yview_moveto(0)

    def _mark_local_edit(self, _event=None):
        self._suspend_refresh_until = time.time() + 2.0

    def save_current(self, silent=False):
        if not self.current_tab:
            return

        target = None
        if self.current_path:
            target = self.current_path

        if not target:
            return

        current = load_json(target)
        updated = {}

        for key, value in current.items():
            raw = self.field_vars[key].get()
            value_type = self.field_types[key]
            try:
                if value_type is bool:
                    if isinstance(raw, bool):
                        updated[key] = raw
                    else:
                        updated[key] = str(raw).strip().lower() in {"1", "true", "yes", "on"}
                elif value_type is int:
                    updated[key] = int(raw)
                elif value_type is float:
                    updated[key] = float(raw)
                else:
                    updated[key] = str(raw)
            except Exception:
                if not silent:
                    self.status_label.configure(text=f"Invalid value for {key}", fg="#ffb1b1")
                return

        save_json(target, updated)
        self.last_signatures = self._snapshot_settings_signatures()
        self._refresh_visible_values_from_disk()
        self._suspend_refresh_until = 0
        if not silent:
            self.status_label.configure(text=f"Saved {self.current_tab}", fg=FG_DIM)

        if self.current_tab == "Main":
            self.rebuild_tabs(preferred_tab=self.current_tab)


if __name__ == "__main__":
    root = tk.Tk()
    clear_status("main_ui")
    install_exception_hooks("main_ui", tk_root=root)
    ensure_project_integrity_or_exit(source="main_ui")
    SettingsWindow(root)
    root.mainloop()
