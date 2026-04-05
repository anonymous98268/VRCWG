import ctypes
import glob
import json
import os
import re
import time
import tkinter as tk
import webbrowser

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
RUNTIME_PATH = os.path.join(SETTINGS_DIR, "manager_runtime.json")
LINKS_PATH = os.path.join(INFO_DIR, "detected_links.json")
STARTUP_INFO_PATH = os.path.join(SETTINGS_DIR, "startup_info.json")
APP_ID = "VRChatWatchGuard.Settings"
FIELD_PATTERN = re.compile(r'"([^"]+)"\s*:\s*"([^"]*)"')
SPECIAL_TABS = ("Log User", "Links")

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
        save_json(RUNTIME_PATH, {"resynth_requested_at": 0.0})


def ensure_links_file():
    os.makedirs(INFO_DIR, exist_ok=True)
    if not os.path.isfile(LINKS_PATH):
        save_json(LINKS_PATH, [])


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


LOCAL_LOG_PLACEHOLDER = '{"UserID": ""}, {"Username": ""}, {"CurrentName": ""}, {"OldName": ""}, {"Reasons": ""}, {"Tag": ""},'


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
                write_entries(today_path, parse_file(existing_path), ("UserID", "Username", "CurrentName", "OldName", "Reasons", "Tag"))
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
        self._suspend_refresh_until = 0
        self.log_form_vars = {}
        self.playtime_label = None

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
        self.win.after(80, self._apply_taskbar_style)
        self.win.after(250, self._watch_settings_files)
        self.win.after(1000, self._update_playtime_label)

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
            self._refresh_visible_values_from_disk()
        current_links = self._read_file_signature(LINKS_PATH)
        if current_links != self.last_links_signature:
            self.last_links_signature = current_links
            if self.current_tab == "Links":
                self.show_tab("Links", None)
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

    def _update_playtime_label(self):
        if self.playtime_label and self.playtime_label.winfo_exists():
            self.playtime_label.configure(text=format_playtime_text())
        self.win.after(1000, self._update_playtime_label)

    def close(self):
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
        reason_text = normalize_text(self.log_form_vars["reason"].get())
        tag_text = normalize_text(self.log_form_vars["tag"].get())
        old_name = normalize_text(self.log_form_vars["old_name"].get())
        warning_type = normalize_text(self.log_form_vars["warning"].get()) or "Yellow"

        if not current_username or not user_id:
            self.status_label.configure(text="Current username and user id are required.", fg="#ffb1b1")
            return

        if normalize_key(tag_text) == "creator":
            self.status_label.configure(text="Creator tag is protected and cannot be assigned here.", fg="#ffb1b1")
            return

        if reason_text:
            self._ensure_custom_reason_definition(reason_text, warning_type)
        if tag_text:
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
            },
        )
        write_entries(
            get_local_logged_players_path(for_write=True),
            entries,
            ("UserID", "Username", "CurrentName", "OldName", "Reasons", "Tag"),
        )
        self.status_label.configure(text=f"Saved log entry for {current_username}.", fg=FG_DIM)

    def _render_log_user_tab(self):
        self.log_form_vars = {
            "current_username": tk.StringVar(),
            "user_id": tk.StringVar(),
            "reason": tk.StringVar(),
            "tag": tk.StringVar(),
            "old_name": tk.StringVar(),
            "warning": tk.StringVar(value="Yellow"),
        }

        tk.Label(self.form_frame, text="Manual User Logger", fg=ACCENT, bg=PANEL, font=FONT_M).pack(
            anchor="w", padx=16, pady=(16, 10)
        )

        fields = [
            ("Current Username", "current_username"),
            ("User ID", "user_id"),
            ("Reason", "reason"),
            ("Tag", "tag"),
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

        warning_row = tk.Frame(self.form_frame, bg=PANEL)
        warning_row.pack(fill="x", padx=16, pady=6)
        tk.Label(warning_row, text="Warning Type", fg=FG, bg=PANEL, font=FONT_S, width=24, anchor="w").pack(side="left")
        tk.OptionMenu(warning_row, self.log_form_vars["warning"], "Yellow", "Orange", "Red").pack(side="left", anchor="w")

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
            if label == "Log User":
                self._render_log_user_tab()
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

            self.field_vars[key] = var

        if label == "Main":
            self._render_main_extras()

        self._bind_scroll_events(self.form_frame)
        self._refresh_visible_values_from_disk()
        self.status_label.configure(text=f"Editing {label}")
        self.canvas.yview_moveto(0)

    def _mark_local_edit(self, _event=None):
        self._suspend_refresh_until = 0

    def save_current(self):
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
                self.status_label.configure(text=f"Invalid value for {key}", fg="#ffb1b1")
                return

        save_json(target, updated)
        self.last_signatures = self._snapshot_settings_signatures()
        self._refresh_visible_values_from_disk()
        self.status_label.configure(text=f"Saved {self.current_tab}", fg=FG_DIM)

        if self.current_tab == "Main":
            self.rebuild_tabs(preferred_tab=self.current_tab)


if __name__ == "__main__":
    root = tk.Tk()
    SettingsWindow(root)
    root.mainloop()
