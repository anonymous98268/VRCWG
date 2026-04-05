import sys
import os
import json
import glob
import re
import threading
import tkinter as tk
from tkinter import PhotoImage
import unicodedata
import webbrowser
import win32gui
import win32process
import time
from datetime import datetime

for stream in (sys.stdin, sys.stdout):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

SETTINGS_PATH = os.path.join(BASE_DIR, "_data", "settings", "user_counter_settings.json")
ICONS_DIR = os.path.join(BASE_DIR, "_data", "images", "icons")
LOGGED_EXTERNAL_DIR = os.path.join(BASE_DIR, "_data", "logged", "players", "external")
LOGGED_LOCAL_DIR = os.path.join(BASE_DIR, "_data", "logged", "players", "local")
REASONS_CUSTOM_DIR = os.path.join(BASE_DIR, "_data", "info", "reasons", "players", "custom")
REASONS_DEFAULT_DIR = os.path.join(BASE_DIR, "_data", "info", "reasons", "players", "default")
TAGS_CUSTOM_DIR = os.path.join(BASE_DIR, "_data", "info", "tags", "players", "custom")
TAGS_DEFAULT_DIR = os.path.join(BASE_DIR, "_data", "info", "tags", "players", "default")
NOTES_DIR = os.path.join(BASE_DIR, "_data", "settings", "notes")

DEFAULT_SETTINGS = {
    "movable": False,
    "shrinkable": False,
    "window_width": 200,
    "window_height": 140,
    "window_x": None,
    "window_y": None,
}

FIELD_PATTERN = re.compile(r'"([^"]+)"\s*:\s*"([^"]*)"')


def load_settings():
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            return {**DEFAULT_SETTINGS, **saved}
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(settings):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4)


settings = load_settings()


def normalize_text(value):
    return unicodedata.normalize("NFKC", str(value or "")).replace("\u200b", "").strip()


def normalize_key(value):
    return normalize_text(value).casefold()


def normalize_user_id(value):
    return normalize_key(value).strip("{}")


def build_user_key(username, user_id):
    normalized_id = normalize_user_id(user_id)
    if normalized_id and normalized_id != "unknown":
        return normalized_id
    return normalize_key(username)


def _parse_line(line):
    fields = {normalize_text(k): normalize_text(v) for k, v in FIELD_PATTERN.findall(line)}
    return fields if fields else None


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
        entry = _parse_line(line)
        if entry:
            entries.append(entry)
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


def find_closest_log_file(directory, prefix="logged_players_"):
    if not os.path.isdir(directory):
        return None
    now = datetime.now()
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
            file_date = datetime(year, month, day)
            delta = abs((now - file_date).total_seconds())
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_path = path
        except Exception:
            continue
    return best_path


def load_logged_players():
    entries = []
    external_path = find_closest_log_file(LOGGED_EXTERNAL_DIR)
    if external_path:
        entries.extend(parse_file(external_path))
    local_path = get_local_logged_players_path()
    if local_path:
        entries.extend(parse_file(local_path))
    return entries


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


def get_dated_filename(prefix):
    return f"{prefix}{datetime.now().strftime('%d_%m_%Y')}.txt"


def ensure_file(path, placeholder_line):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            if placeholder_line:
                f.write(placeholder_line + "\n")


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


def ensure_notes_dir():
    os.makedirs(NOTES_DIR, exist_ok=True)


def get_user_note_path(user_id):
    ensure_notes_dir()
    normalized_id = normalize_user_id(user_id) or "unknown"
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", normalized_id)
    return os.path.join(NOTES_DIR, f"{safe_name}.txt")


def load_user_note(user_id):
    path = get_user_note_path(user_id)
    if not os.path.isfile(path):
        return ""
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as f:
                return normalize_text(f.read())
        except UnicodeDecodeError:
            continue
        except OSError:
            return ""
    return ""


def save_user_note(user_id, note_text):
    path = get_user_note_path(user_id)
    cleaned_note = str(note_text or "").strip()
    if not cleaned_note:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
        return
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(cleaned_note)


def write_entries(path, entries, ordered_fields):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for entry in entries:
            parts = []
            for field in ordered_fields:
                value = normalize_text(entry.get(field, ""))
                parts.append(f'{{"{field}": "{value}"}}')
            f.write(", ".join(parts) + ",\n")


def upsert_entry(entries, key_field, key_value, payload):
    normalized_target = normalize_key(key_value)
    for entry in entries:
        if normalize_key(entry.get(key_field, "")) == normalized_target:
            entry.update(payload)
            return entries
    entries.append(dict(payload))
    return entries


def remove_entry(entries, key_field, key_value):
    normalized_target = normalize_key(key_value)
    return [entry for entry in entries if normalize_key(entry.get(key_field, "")) != normalized_target]


logged_players_cache = []
all_reasons_cache = []
all_tags_cache = []
logged_players_by_id = {}
logged_players_by_name = {}
reason_warning_map = {}
tag_image_map = {}
reason_dropdown_values = []
tag_dropdown_values = []


def lookup_user(user_id, username=""):
    user_id_key = normalize_user_id(user_id)
    if user_id_key:
        match = logged_players_by_id.get(user_id_key)
        if match:
            return match
    username_key = normalize_key(username)
    if username_key:
        return logged_players_by_name.get(username_key)
    return None


def find_reason_warning(reason_text):
    for value in split_lookup_values(reason_text):
        warning = reason_warning_map.get(normalize_key(value))
        if warning:
            return warning
    return ""


def has_watch_reason(reason_text):
    return any(normalize_key(value) == "watch" for value in split_lookup_values(reason_text))


def find_tag_image(tag_text):
    values = split_lookup_values(tag_text)
    for value in values:
        image_name = tag_image_map.get(normalize_key(value))
        if image_name:
            return image_name
    if values:
        return "add_icon.png"
    return ""


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


def refresh_caches():
    global logged_players_cache, all_reasons_cache, all_tags_cache
    global logged_players_by_id, logged_players_by_name, reason_warning_map, tag_image_map
    global reason_dropdown_values, tag_dropdown_values

    logged_players_cache = load_logged_players()
    all_reasons_cache = load_all_reasons()
    all_tags_cache = load_all_tags()

    logged_players_by_id = {}
    logged_players_by_name = {}
    for entry in logged_players_cache:
        user_id = normalize_user_id(entry.get("UserID", ""))
        username = normalize_key(entry.get("Username", ""))
        if user_id:
            logged_players_by_id[user_id] = entry
        if username:
            logged_players_by_name[username] = entry

    reason_warning_map = {}
    for entry in all_reasons_cache:
        reason = normalize_key(entry.get("Reason", ""))
        warning = normalize_text(entry.get("Warning", ""))
        if reason and warning:
            reason_warning_map[reason] = warning

    tag_image_map = {}
    for entry in all_tags_cache:
        tag = normalize_key(entry.get("Tag", ""))
        image_name = normalize_text(entry.get("Image", ""))
        if tag and image_name:
            tag_image_map[tag] = image_name

    reason_dropdown_values = build_dropdown_values(all_reasons_cache, "Reason")
    tag_dropdown_values = build_dropdown_values(all_tags_cache, "Tag", blocked_values={"creator"})


root = tk.Tk()
root.overrideredirect(True)
root.attributes("-topmost", True)
root.config(bg="#111111")
root.wm_attributes("-transparentcolor", "#000001")

corner = 20
bg = "#111111"
panel_bg = "#171b22"
panel_fg = "#d7e5f7"
panel_accent = "#4a647f"
panel_dim = "#7aa2d6"

canvas = tk.Canvas(root, highlightthickness=0, bg="#000001", bd=0)
canvas.pack(fill="both", expand=True)

world_label = tk.Label(root, text="World: N/A", fg="white", bg=bg, font=("Segoe UI", 10, "bold"))
total_label = tk.Label(root, text="Total Users: 0", fg="white", bg=bg, font=("Segoe UI", 10))
sep = tk.Label(root, text="--------------------------", fg="white", bg=bg, font=("Segoe UI", 10))

users_container = tk.Frame(root, bg=bg)
users_canvas = tk.Canvas(users_container, highlightthickness=0, bg=bg, bd=0)
users_scrollbar = tk.Scrollbar(users_container, orient="vertical", command=users_canvas.yview)
users_canvas.configure(yscrollcommand=users_scrollbar.set)
users_frame = tk.Frame(users_canvas, bg=bg)
users_window = users_canvas.create_window((0, 0), window=users_frame, anchor="nw")
users_canvas.pack(side="left", fill="both", expand=True)
users_scrollbar.pack(side="right", fill="y")

vrc_hwnd = None
user_data = {}
user_rows = {}
icon_cache = {}
selected_detail_user = None
detail_panel = None
detail_expanded = False
last_follow_position = {"x": None, "y": None}
manager_total_override = None
detail_widgets = {}
detail_vars = {
    "reason_choice": tk.StringVar(value=""),
    "reason_custom": tk.StringVar(value=""),
    "tag_choice": tk.StringVar(value=""),
    "tag_custom": tk.StringVar(value=""),
    "warning": tk.StringVar(value="Yellow"),
    "status": tk.StringVar(value=""),
}
WARNING_TYPES = ("Red", "Orange", "Yellow")
WARNING_COLORS = {
    "Red": "#7a2d2d",
    "Orange": "#8a5120",
    "Yellow": "#7d6a1f",
}
tooltip_labels = {}

def load_icon(filename, size=16):
    cache_key = (filename, size)
    if cache_key in icon_cache:
        return icon_cache[cache_key]
    path = os.path.join(ICONS_DIR, filename)
    image = None
    if os.path.exists(path):
        try:
            raw = PhotoImage(file=path)
            w, h = raw.width(), raw.height()
            fx = max(1, w // size)
            fy = max(1, h // size)
            image = raw.subsample(fx, fy)
        except Exception:
            image = None
    icon_cache[cache_key] = image
    return image


def apply_button_style(button, accent=False, danger=False):
    bg_color = "#1e242d"
    fg_color = "white"
    active_color = "#2b3440"
    if accent:
        bg_color = "#27415f"
        active_color = "#33567d"
    if danger:
        bg_color = "#5b2a2a"
        active_color = "#774040"
    button.configure(
        bg=bg_color,
        fg=fg_color,
        activebackground=active_color,
        activeforeground=fg_color,
        relief="flat",
        bd=0,
        highlightthickness=0,
        padx=8,
        pady=4,
        cursor="hand2",
    )


def sync_user_scrollregion(_event=None):
    users_canvas.configure(scrollregion=users_canvas.bbox("all"))


def resize_users_window(event):
    users_canvas.itemconfigure(users_window, width=event.width)


def on_mousewheel(event):
    if not root.winfo_ismapped() or event.delta == 0:
        return
    direction = -1 if event.delta > 0 else 1
    users_canvas.yview_scroll(direction, "units")


users_frame.bind("<Configure>", sync_user_scrollregion)
users_canvas.bind("<Configure>", resize_users_window)
root.bind_all("<MouseWheel>", on_mousewheel)


def draw_round():
    w = root.winfo_width()
    h = root.winfo_height()
    canvas.delete("all")
    canvas.create_polygon(
        corner, 0, w - corner, 0,
        w, corner, w, h - corner,
        w - corner, h, corner, h,
        0, h - corner, 0, corner,
        fill=bg, outline=bg,
    )
    root.lift()


def ensure_overlay_on_top():
    try:
        root.deiconify()
    except Exception:
        return
    try:
        root.attributes("-topmost", True)
        root.lift()
    except Exception:
        pass


def get_fallback_position():
    if settings.get("movable"):
        x = settings.get("window_x")
        y = settings.get("window_y")
        if x is not None and y is not None:
            return int(x), int(y)

    x = last_follow_position.get("x")
    y = last_follow_position.get("y")
    if x is not None and y is not None:
        return int(x), int(y)

    current_x = root.winfo_x()
    current_y = root.winfo_y()
    if current_x <= 0 and current_y <= 0:
        return 40, 60
    return int(current_x), int(current_y)


def place_overlay(x=None, y=None, remember=True):
    ensure_overlay_on_top()
    if x is None or y is None:
        x, y = get_fallback_position()
    try:
        x = int(x)
        y = int(y)
    except Exception:
        x, y = get_fallback_position()
    root.geometry(f"+{x}+{y}")
    if remember:
        last_follow_position["x"] = x
        last_follow_position["y"] = y


def layout_root():
    for widget in root.place_slaves():
        widget.place_forget()
    width = max(180, root.winfo_width())
    height = max(190, root.winfo_height())
    world_label.place(x=10, y=10)
    total_label.place(x=10, y=27)
    sep.place(x=10, y=50)
    users_container.place(x=10, y=72, width=width - 20, height=max(90, height - 82))
    if settings.get("shrinkable") and resize_grip is not None:
        resize_grip.place(relx=1.0, rely=1.0, anchor="se")


def update_height():
    display_total = manager_total_override if manager_total_override is not None else len(user_rows)
    total_label.config(text=f"Total Users: {display_total}")
    if not settings.get("shrinkable"):
        visible_rows = min(max(len(user_rows), 3), 12)
        root.geometry(f"{settings.get('window_width', 220)}x{max(190, 110 + visible_rows * 22)}")
    draw_round()
    layout_root()


def close_detail_panel(reset_selection=True):
    global selected_detail_user
    if reset_selection:
        selected_detail_user = None
    clear_all_tooltips()
    close_choice_panels()
    if detail_panel and detail_panel.winfo_exists():
        detail_panel.withdraw()


def clear_all_tooltips():
    for key, label in list(tooltip_labels.items()):
        try:
            label.destroy()
        except Exception:
            pass
        tooltip_labels.pop(key, None)


def set_detail_status(text, error=False):
    detail_vars["status"].set(text)
    if "status" in detail_widgets:
        detail_widgets["status"].configure(fg="#ffb1b1" if error else "#9fd4ff")


def hide_choice_panel(kind):
    panel = detail_widgets.get(f"{kind}_choice_panel")
    if panel and panel.winfo_manager():
        panel.pack_forget()


def close_choice_panels(except_kind=None):
    for kind in ("reason", "tag"):
        if kind != except_kind:
            hide_choice_panel(kind)
    refresh_choice_buttons()


def populate_choice_panel(kind, values, empty_label):
    listbox = detail_widgets.get(f"{kind}_choice_listbox")
    scrollbar = detail_widgets.get(f"{kind}_choice_scrollbar")
    if not listbox:
        return

    listbox.delete(0, "end")
    if values:
        for value in values:
            listbox.insert("end", value)
        listbox.configure(height=min(max(len(values), 1), 6))
    else:
        listbox.insert("end", empty_label)
        listbox.configure(height=1)

    if scrollbar:
        if len(values) > 6:
            scrollbar.pack(side="right", fill="y")
        else:
            scrollbar.pack_forget()


def toggle_choice_panel(kind):
    ensure_detail_panel()
    panel = detail_widgets.get(f"{kind}_choice_panel")
    if not panel:
        return

    if panel.winfo_manager():
        panel.pack_forget()
        refresh_choice_buttons()
        position_detail_panel()
        return

    close_choice_panels(except_kind=kind)
    panel.pack(fill="x", pady=(4, 0))
    refresh_choice_buttons()
    position_detail_panel()


def choose_from_choice_panel(kind):
    listbox = detail_widgets.get(f"{kind}_choice_listbox")
    if not listbox:
        return

    selection = listbox.curselection()
    if not selection:
        return

    value = normalize_text(listbox.get(selection[0]))
    values = reason_dropdown_values if kind == "reason" else tag_dropdown_values
    if not any(normalize_key(option) == normalize_key(value) for option in values):
        return

    choose_saved_value(f"{kind}_choice", f"{kind}_custom", value)


def refresh_detail_dropdowns():
    populate_choice_panel("reason", reason_dropdown_values, "No saved reasons")
    populate_choice_panel("tag", tag_dropdown_values, "No saved tags")
    refresh_choice_buttons()


def resolve_detail_value(choice_key, custom_key):
    custom_value = normalize_text(detail_vars[custom_key].get())
    if custom_value:
        return custom_value, True
    return normalize_text(detail_vars[choice_key].get()), False


def clear_custom_on_pick(choice_key, custom_key):
    if normalize_text(detail_vars[choice_key].get()):
        detail_vars[custom_key].set("")
    refresh_choice_buttons()


def clear_pick_on_custom(custom_key, choice_key):
    if normalize_text(detail_vars[custom_key].get()):
        detail_vars[choice_key].set("")
    refresh_choice_buttons()


def choose_saved_value(choice_key, custom_key, value):
    detail_vars[choice_key].set(normalize_text(value))
    detail_vars[custom_key].set("")
    close_choice_panels()
    refresh_choice_buttons()


def assign_form_value(choice_key, custom_key, value, available_values):
    normalized_value = normalize_key(value)
    detail_vars[choice_key].set("")
    detail_vars[custom_key].set("")
    if not normalized_value:
        return
    for option in available_values:
        if normalize_key(option) == normalized_value:
            detail_vars[choice_key].set(option)
            refresh_choice_buttons()
            return
    detail_vars[custom_key].set(normalize_text(value))
    refresh_choice_buttons()


def refresh_choice_buttons():
    placeholders = {
        "reason_choice_button": "Saved reasons",
        "tag_choice_button": "Saved tags",
    }
    bindings = {
        "reason_choice_button": "reason_choice",
        "tag_choice_button": "tag_choice",
    }
    for widget_key, choice_key in bindings.items():
        button = detail_widgets.get(widget_key)
        if button:
            value = normalize_text(detail_vars[choice_key].get())
            kind = "reason" if widget_key.startswith("reason") else "tag"
            arrow = "^" if detail_widgets.get(f"{kind}_choice_panel") and detail_widgets[f"{kind}_choice_panel"].winfo_manager() else "v"
            button.configure(text=f"{value or placeholders[widget_key]}  {arrow}")


def get_logged_names(logged_entry):
    names = []
    for field_name in ("Username", "CurrentName", "OldName"):
        names.extend(split_lookup_values(logged_entry.get(field_name, "")))

    deduped = []
    seen = set()
    for name in names:
        key = normalize_key(name)
        if key and key not in seen:
            seen.add(key)
            deduped.append(name)
    return deduped


def has_logged_name_change(logged_entry, current_name):
    current_key = normalize_key(current_name)
    return any(normalize_key(name) != current_key for name in get_logged_names(logged_entry))


def get_selected_context():
    if selected_detail_user is None:
        return None
    data = user_data.get(selected_detail_user)
    if not data:
        return None
    display_name = data.get("name", "Unknown")
    user_id = normalize_text(data.get("usrid", "Unknown")) or "Unknown"
    logged_entry = lookup_user(user_id, display_name)
    reason_text = normalize_text(logged_entry.get("Reasons", "")) if logged_entry else ""
    tag_text = normalize_text(logged_entry.get("Tag", "")) if logged_entry else ""
    warning_type = find_reason_warning(reason_text) or "Yellow"
    old_names = []
    if logged_entry:
        for name in get_logged_names(logged_entry):
            if normalize_key(name) != normalize_key(display_name):
                old_names.append(name)
    note_text = load_user_note(user_id)
    return {
        "data": data,
        "display_name": display_name,
        "user_id": user_id,
        "logged_entry": logged_entry,
        "reason": reason_text,
        "tag": tag_text,
        "warning": warning_type,
        "old_names": ", ".join(old_names),
        "note": note_text,
    }


def copy_selected_field(field_name):
    context = get_selected_context()
    if not context:
        return
    value = context["display_name"] if field_name == "username" else context["user_id"]
    try:
        root.clipboard_clear()
        root.clipboard_append(value)
        set_detail_status(f"Copied {field_name}.")
    except Exception as exc:
        set_detail_status(f"Copy failed: {exc}", error=True)


def get_note_editor_text():
    note_text_widget = detail_widgets.get("note_text")
    if not note_text_widget:
        return ""
    try:
        return note_text_widget.get("1.0", "end-1c").strip()
    except Exception:
        return ""


def set_note_editor_text(note_text):
    note_text_widget = detail_widgets.get("note_text")
    if not note_text_widget:
        return
    try:
        note_text_widget.delete("1.0", "end")
        note_text_widget.insert("1.0", note_text or "")
    except Exception:
        pass


def open_selected_profile():
    context = get_selected_context()
    if not context:
        return
    user_id = normalize_text(context["user_id"])
    if not user_id or normalize_key(user_id) == "unknown":
        set_detail_status("No valid user id to open.", error=True)
        return
    try:
        webbrowser.open(f"https://vrchat.com/home/user/{user_id}")
        set_detail_status("Opened VRChat profile.")
    except Exception as exc:
        set_detail_status(f"Could not open profile: {exc}", error=True)


def open_note_editor():
    set_detail_expanded(True)
    note_text_widget = detail_widgets.get("note_text")
    if note_text_widget:
        note_text_widget.focus_set()
        note_text_widget.mark_set("insert", "end-1c")
    position_detail_panel()


def save_selected_note():
    context = get_selected_context()
    if not context:
        return
    try:
        save_user_note(context["user_id"], get_note_editor_text())
        refresh_detail_panel(force_form=False)
        set_detail_status("Saved note.")
    except Exception as exc:
        set_detail_status(f"Note save failed: {exc}", error=True)


def ensure_reason_definition(reason_text, warning_type):
    if not reason_text:
        return
    entries = [entry for entry in parse_file(get_custom_reasons_path()) if normalize_text(entry.get("Reason", ""))]
    payload = {"Reason": reason_text, "Warning": warning_type}
    upsert_entry(entries, "Reason", reason_text, payload)
    write_entries(get_custom_reasons_path(), entries, ("Reason", "Warning"))


def ensure_tag_definition(tag_text):
    if not tag_text:
        return
    entries = [entry for entry in parse_file(get_custom_tags_path()) if normalize_text(entry.get("Tag", ""))]
    payload = {"Tag": tag_text, "Image": ""}
    upsert_entry(entries, "Tag", tag_text, payload)
    write_entries(get_custom_tags_path(), entries, ("Tag", "Image"))


def read_local_logged_entries():
    return [entry for entry in parse_file(get_local_logged_players_path()) if normalize_text(entry.get("UserID", ""))]


def save_local_logged_entries(entries):
    write_entries(
        get_local_logged_players_path(for_write=True),
        entries,
        ("UserID", "Username", "CurrentName", "OldName", "Reasons", "Tag"),
    )


def save_selected_logged_user(reason_value=None, tag_value=None, reason_is_custom=False, tag_is_custom=False):
    context = get_selected_context()
    if not context:
        return False
    if reason_value is None:
        reason_text, reason_is_custom = resolve_detail_value("reason_choice", "reason_custom")
    else:
        reason_text = normalize_text(reason_value)
    if tag_value is None:
        tag_text, tag_is_custom = resolve_detail_value("tag_choice", "tag_custom")
    else:
        tag_text = normalize_text(tag_value)
    warning_type = normalize_text(detail_vars["warning"].get()) or "Yellow"
    if normalize_key(tag_text) == "creator":
        set_detail_status("Creator tag is protected and cannot be assigned here.", error=True)
        return False
    if reason_text and reason_is_custom:
        ensure_reason_definition(reason_text, warning_type)
    if tag_text and tag_is_custom:
        ensure_tag_definition(tag_text)
    entries = read_local_logged_entries()
    existing_logged = context["logged_entry"] or {}
    previous_name = normalize_text(existing_logged.get("Username", ""))
    old_name = normalize_text(existing_logged.get("OldName", ""))
    if previous_name and normalize_key(previous_name) != normalize_key(context["display_name"]) and not old_name:
        old_name = previous_name
    payload = {
        "UserID": context["user_id"],
        "Username": context["display_name"],
        "CurrentName": context["display_name"],
        "OldName": old_name,
        "Reasons": reason_text,
        "Tag": tag_text,
    }
    upsert_entry(entries, "UserID", context["user_id"], payload)
    save_local_logged_entries(entries)
    refresh_caches()
    refresh_user_rows()
    refresh_detail_panel(force_form=True)
    return True


def remove_selected_logged_user():
    context = get_selected_context()
    if not context:
        return
    entries = remove_entry(read_local_logged_entries(), "UserID", context["user_id"])
    save_local_logged_entries(entries)
    refresh_caches()
    refresh_user_rows()
    refresh_detail_panel(force_form=True)
    set_detail_status("Removed local log entry.")


def handle_log_user():
    if save_selected_logged_user():
        set_detail_status("Saved local log entry.")


def handle_add_reason():
    reason_text, reason_is_custom = resolve_detail_value("reason_choice", "reason_custom")
    if not reason_text:
        set_detail_status("Choose a reason or type your own first.", error=True)
        return
    if save_selected_logged_user(reason_value=reason_text, reason_is_custom=reason_is_custom):
        set_detail_status("Reason saved.")


def handle_remove_reason():
    detail_vars["reason_choice"].set("")
    detail_vars["reason_custom"].set("")
    if save_selected_logged_user(reason_value=""):
        set_detail_status("Reason removed.")


def handle_add_tag():
    tag_text, tag_is_custom = resolve_detail_value("tag_choice", "tag_custom")
    if not tag_text:
        set_detail_status("Choose a tag or type your own first.", error=True)
        return
    if normalize_key(tag_text) == "creator":
        set_detail_status("Creator tag is protected and cannot be assigned here.", error=True)
        return
    if save_selected_logged_user(tag_value=tag_text, tag_is_custom=tag_is_custom):
        set_detail_status("Tag saved.")


def handle_remove_tag():
    detail_vars["tag_choice"].set("")
    detail_vars["tag_custom"].set("")
    if save_selected_logged_user(tag_value=""):
        set_detail_status("Tag removed.")


def refresh_warning_button():
    button = detail_widgets.get("warning_button")
    if not button:
        return
    warning = normalize_text(detail_vars["warning"].get()) or "Yellow"
    if warning not in WARNING_TYPES:
        warning = "Yellow"
        detail_vars["warning"].set(warning)
    button.configure(
        text=f"Warning: {warning}",
        bg=WARNING_COLORS.get(warning, "#1e242d"),
        activebackground=WARNING_COLORS.get(warning, "#2b3440"),
        fg="white",
        activeforeground="white",
    )


def cycle_warning_type():
    current = normalize_text(detail_vars["warning"].get()) or "Yellow"
    try:
        index = WARNING_TYPES.index(current)
    except ValueError:
        index = len(WARNING_TYPES) - 1
    detail_vars["warning"].set(WARNING_TYPES[(index + 1) % len(WARNING_TYPES)])
    refresh_warning_button()

def ensure_detail_panel():
    global detail_panel
    required_widgets = {"name", "uid", "old_name", "joined", "duration", "logged", "reason", "tag", "note", "expand", "editor", "advanced_editor", "note_text"}
    if detail_panel and detail_panel.winfo_exists() and required_widgets.issubset(detail_widgets):
        return detail_panel
    if detail_panel and detail_panel.winfo_exists():
        try:
            detail_panel.destroy()
        except Exception:
            pass
    detail_widgets.clear()

    detail_panel = tk.Toplevel(root)
    detail_panel.overrideredirect(True)
    detail_panel.attributes("-topmost", True)
    detail_panel.config(bg=panel_accent)
    detail_panel.withdraw()

    shell = tk.Frame(detail_panel, bg=panel_bg)
    shell.pack(fill="both", expand=True, padx=1, pady=1)

    header = tk.Frame(shell, bg=panel_bg)
    header.pack(fill="x", padx=10, pady=(10, 6))

    tk.Label(header, text="User Details", fg="white", bg=panel_bg, font=("Segoe UI", 10, "bold")).pack(side="left")

    expand_btn = tk.Button(header, text="More", command=lambda: set_detail_expanded(not detail_expanded))
    apply_button_style(expand_btn)
    expand_btn.pack(side="right", padx=(6, 0))

    close_btn = tk.Button(header, text="x", command=close_detail_panel)
    apply_button_style(close_btn, danger=True)
    close_btn.pack(side="right")

    summary = tk.Frame(shell, bg=panel_bg)
    summary.pack(fill="x", padx=10)

    name_label = tk.Label(summary, text="", fg="white", bg=panel_bg, font=("Segoe UI", 10, "bold"), anchor="w", justify="left", wraplength=320)
    name_label.pack(fill="x")

    uid_row = tk.Frame(summary, bg=panel_bg)
    uid_row.pack(fill="x", pady=(2, 0))

    uid_label = tk.Label(uid_row, text="", fg=panel_fg, bg=panel_bg, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=250)
    uid_label.pack(side="left", fill="x", expand=True)

    open_profile_btn = tk.Button(uid_row, image=load_icon("open_website_icon.png", 18), command=open_selected_profile)
    apply_button_style(open_profile_btn, accent=True)
    open_profile_btn.pack(side="right", padx=(6, 0))

    note_btn = tk.Button(uid_row, image=load_icon("add_note_icon.png", 18), command=open_note_editor)
    apply_button_style(note_btn)
    note_btn.pack(side="right", padx=(6, 0))

    old_name_label = tk.Label(summary, text="", fg="#ffd39b", bg=panel_bg, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=320)
    old_name_label.pack(fill="x", pady=(2, 0))
    joined_label = tk.Label(summary, text="", fg=panel_fg, bg=panel_bg, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=320)
    joined_label.pack(fill="x", pady=(2, 0))
    duration_label = tk.Label(summary, text="", fg=panel_fg, bg=panel_bg, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=320)
    duration_label.pack(fill="x", pady=(2, 0))
    logged_label = tk.Label(summary, text="", fg=panel_fg, bg=panel_bg, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=320)
    logged_label.pack(fill="x", pady=(6, 0))
    reason_label = tk.Label(summary, text="", fg="#ffd39b", bg=panel_bg, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=320)
    reason_label.pack(fill="x", pady=(2, 0))
    tag_label = tk.Label(summary, text="", fg="#9fd4ff", bg=panel_bg, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=320)
    tag_label.pack(fill="x", pady=(2, 0))
    note_label = tk.Label(summary, text="", fg="#8ecfc9", bg=panel_bg, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=320)
    note_label.pack(fill="x", pady=(2, 0))

    copy_row = tk.Frame(shell, bg=panel_bg)
    copy_row.pack(fill="x", padx=10, pady=(10, 0))

    copy_user_btn = tk.Button(copy_row, text="Copy Username", command=lambda: copy_selected_field("username"))
    apply_button_style(copy_user_btn)
    copy_user_btn.pack(side="left", fill="x", expand=True)

    copy_id_btn = tk.Button(copy_row, text="Copy UserID", command=lambda: copy_selected_field("userid"))
    apply_button_style(copy_id_btn)
    copy_id_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))

    editor = tk.Frame(shell, bg=panel_bg)
    editor.pack(fill="x", padx=10, pady=(10, 10))

    reason_row = tk.Frame(editor, bg=panel_bg)
    reason_row.pack(fill="x", pady=(0, 6))
    tk.Label(reason_row, text="Reason", fg=panel_fg, bg=panel_bg, font=("Segoe UI", 9, "bold")).pack(anchor="w")

    reason_select_row = tk.Frame(reason_row, bg=panel_bg)
    reason_select_row.pack(fill="x", pady=(4, 0))

    reason_choice_button = tk.Button(
        reason_select_row,
        text="Saved reasons  v",
        command=lambda: toggle_choice_panel("reason"),
        bg="#0f1319",
        fg="white",
        activebackground="#1a2430",
        activeforeground="white",
        relief="flat",
        bd=0,
        highlightthickness=0,
        cursor="hand2",
        anchor="w",
        padx=8,
        pady=5,
    )
    reason_choice_button.pack(side="left", fill="x", expand=True)

    reason_choice_panel = tk.Frame(reason_row, bg="#0f1319", highlightthickness=1, highlightbackground=panel_accent)
    reason_choice_list_wrap = tk.Frame(reason_choice_panel, bg="#0f1319")
    reason_choice_list_wrap.pack(fill="both", expand=True)
    reason_choice_listbox = tk.Listbox(
        reason_choice_list_wrap,
        bg="#0f1319",
        fg="white",
        selectbackground="#27415f",
        selectforeground="white",
        activestyle="none",
        exportselection=False,
        relief="flat",
        highlightthickness=0,
        borderwidth=0,
    )
    reason_choice_scrollbar = tk.Scrollbar(reason_choice_list_wrap, orient="vertical", command=reason_choice_listbox.yview)
    reason_choice_listbox.configure(yscrollcommand=reason_choice_scrollbar.set)
    reason_choice_listbox.pack(side="left", fill="both", expand=True)
    reason_choice_listbox.bind("<<ListboxSelect>>", lambda _event: choose_from_choice_panel("reason"))
    reason_choice_listbox.bind("<Return>", lambda _event: choose_from_choice_panel("reason"))

    warning_button = tk.Button(reason_select_row, text="Warning: Yellow", command=cycle_warning_type)
    apply_button_style(warning_button)
    warning_button.pack(side="left", padx=(6, 0))

    add_reason_btn = tk.Button(reason_select_row, image=load_icon("add_icon.png", 18), command=handle_add_reason)
    apply_button_style(add_reason_btn, accent=True)
    add_reason_btn.pack(side="left", padx=(6, 0))

    remove_reason_btn = tk.Button(reason_select_row, image=load_icon("remove_icon.png", 18), command=handle_remove_reason)
    apply_button_style(remove_reason_btn, danger=True)
    remove_reason_btn.pack(side="left", padx=(4, 0))

    tag_row = tk.Frame(editor, bg=panel_bg)
    tag_row.pack(fill="x", pady=(0, 6))
    tk.Label(tag_row, text="Tag", fg=panel_fg, bg=panel_bg, font=("Segoe UI", 9, "bold")).pack(anchor="w")

    tag_select_row = tk.Frame(tag_row, bg=panel_bg)
    tag_select_row.pack(fill="x", pady=(4, 0))

    tag_choice_button = tk.Button(
        tag_select_row,
        text="Saved tags  v",
        command=lambda: toggle_choice_panel("tag"),
        bg="#0f1319",
        fg="white",
        activebackground="#1a2430",
        activeforeground="white",
        relief="flat",
        bd=0,
        highlightthickness=0,
        cursor="hand2",
        anchor="w",
        padx=8,
        pady=5,
    )
    tag_choice_button.pack(side="left", fill="x", expand=True)

    tag_choice_panel = tk.Frame(tag_row, bg="#0f1319", highlightthickness=1, highlightbackground=panel_accent)
    tag_choice_list_wrap = tk.Frame(tag_choice_panel, bg="#0f1319")
    tag_choice_list_wrap.pack(fill="both", expand=True)
    tag_choice_listbox = tk.Listbox(
        tag_choice_list_wrap,
        bg="#0f1319",
        fg="white",
        selectbackground="#27415f",
        selectforeground="white",
        activestyle="none",
        exportselection=False,
        relief="flat",
        highlightthickness=0,
        borderwidth=0,
    )
    tag_choice_scrollbar = tk.Scrollbar(tag_choice_list_wrap, orient="vertical", command=tag_choice_listbox.yview)
    tag_choice_listbox.configure(yscrollcommand=tag_choice_scrollbar.set)
    tag_choice_listbox.pack(side="left", fill="both", expand=True)
    tag_choice_listbox.bind("<<ListboxSelect>>", lambda _event: choose_from_choice_panel("tag"))
    tag_choice_listbox.bind("<Return>", lambda _event: choose_from_choice_panel("tag"))

    add_tag_btn = tk.Button(tag_select_row, image=load_icon("add_icon.png", 18), command=handle_add_tag)
    apply_button_style(add_tag_btn, accent=True)
    add_tag_btn.pack(side="left", padx=(6, 0))

    remove_tag_btn = tk.Button(tag_select_row, image=load_icon("remove_icon.png", 18), command=handle_remove_tag)
    apply_button_style(remove_tag_btn, danger=True)
    remove_tag_btn.pack(side="left", padx=(4, 0))

    advanced_editor = tk.Frame(editor, bg=panel_bg)

    tk.Label(advanced_editor, text="Custom reason after pressing More", fg=panel_dim, bg=panel_bg, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 0))
    reason_custom_entry = tk.Entry(advanced_editor, textvariable=detail_vars["reason_custom"], bg="#0f1319", fg="white", insertbackground="white", relief="flat")
    reason_custom_entry.pack(fill="x", pady=(4, 8))
    reason_custom_entry.bind("<KeyRelease>", lambda _event: clear_pick_on_custom("reason_custom", "reason_choice"))

    tk.Label(advanced_editor, text="Custom tag after pressing More", fg=panel_dim, bg=panel_bg, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 0))
    tag_custom_entry = tk.Entry(advanced_editor, textvariable=detail_vars["tag_custom"], bg="#0f1319", fg="white", insertbackground="white", relief="flat")
    tag_custom_entry.pack(fill="x", pady=(4, 8))
    tag_custom_entry.bind("<KeyRelease>", lambda _event: clear_pick_on_custom("tag_custom", "tag_choice"))

    note_panel = tk.Frame(advanced_editor, bg=panel_bg)
    tk.Label(note_panel, text="Player note", fg=panel_fg, bg=panel_bg, font=("Segoe UI", 9, "bold")).pack(anchor="w")
    note_text = tk.Text(
        note_panel,
        bg="#0f1319",
        fg="white",
        insertbackground="white",
        relief="flat",
        height=4,
        wrap="word",
    )
    note_text.pack(fill="x", pady=(4, 6))
    note_save_btn = tk.Button(note_panel, text="Save Note", command=save_selected_note)
    apply_button_style(note_save_btn, accent=True)
    note_save_btn.pack(anchor="e")

    action_row = tk.Frame(editor, bg=panel_bg)
    action_row.pack(fill="x", pady=(2, 0))

    save_btn = tk.Button(action_row, text="Save Local Log", command=handle_log_user)
    apply_button_style(save_btn, accent=True)
    save_btn.pack(side="left", fill="x", expand=True)

    remove_btn = tk.Button(action_row, text="Remove Local", command=remove_selected_logged_user)
    apply_button_style(remove_btn, danger=True)
    remove_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))

    status_label = tk.Label(editor, textvariable=detail_vars["status"], fg="#9fd4ff", bg=panel_bg, font=("Segoe UI", 8), anchor="w", justify="left", wraplength=320)
    status_label.pack(fill="x", pady=(8, 0))

    detail_widgets.update({
        "expand": expand_btn,
        "name": name_label,
        "uid": uid_label,
        "open_profile": open_profile_btn,
        "note_button": note_btn,
        "old_name": old_name_label,
        "joined": joined_label,
        "duration": duration_label,
        "logged": logged_label,
        "reason": reason_label,
        "tag": tag_label,
        "note": note_label,
        "editor": editor,
        "advanced_editor": advanced_editor,
        "note_panel": note_panel,
        "note_text": note_text,
        "note_save": note_save_btn,
        "status": status_label,
        "save": save_btn,
        "warning_button": warning_button,
        "reason_choice_button": reason_choice_button,
        "reason_choice_panel": reason_choice_panel,
        "reason_choice_listbox": reason_choice_listbox,
        "reason_choice_scrollbar": reason_choice_scrollbar,
        "tag_choice_button": tag_choice_button,
        "tag_choice_panel": tag_choice_panel,
        "tag_choice_listbox": tag_choice_listbox,
        "tag_choice_scrollbar": tag_choice_scrollbar,
    })

    refresh_detail_dropdowns()
    refresh_warning_button()
    make_tooltip(open_profile_btn, "Open VRChat profile")
    make_tooltip(note_btn, "Open player note")
    set_detail_expanded(False)
    return detail_panel


def set_detail_expanded(expanded):
    global detail_expanded
    detail_expanded = expanded
    panel = ensure_detail_panel()
    advanced_editor = detail_widgets["advanced_editor"]
    note_panel = detail_widgets.get("note_panel")
    if expanded:
        advanced_editor.pack(fill="x", pady=(0, 10))
        if note_panel:
            note_panel.pack(fill="x", pady=(2, 0))
    else:
        close_choice_panels()
        if note_panel and note_panel.winfo_manager():
            note_panel.pack_forget()
        advanced_editor.pack_forget()
    detail_widgets["expand"].configure(text="Less" if expanded else "More")
    panel.update_idletasks()
    position_detail_panel()


def position_detail_panel():
    if selected_detail_user is None or selected_detail_user not in user_rows:
        close_detail_panel(reset_selection=False)
        return
    panel = ensure_detail_panel()
    row = user_rows[selected_detail_user]
    panel.update_idletasks()
    panel_width = panel.winfo_reqwidth()
    panel_height = panel.winfo_reqheight()
    row_x = row.winfo_rootx()
    row_y = row.winfo_rooty()
    row_width = row.winfo_width()
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    x = row_x + row_width + 8
    if x + panel_width > screen_w - 10:
        x = max(10, row_x - panel_width - 8)
    y = max(10, min(row_y, screen_h - panel_height - 10))
    panel.geometry(f"+{x}+{y}")
    panel.deiconify()
    panel.lift()


def refresh_detail_panel(force_form=False):
    context = get_selected_context()
    if not context or selected_detail_user not in user_rows:
        close_detail_panel()
        return
    panel = ensure_detail_panel()
    refresh_detail_dropdowns()
    detail_widgets["name"].configure(text=context["display_name"])
    detail_widgets["uid"].configure(text=f"User ID: {context['user_id']}")
    detail_widgets["old_name"].configure(text=f"Previous Name: {context['old_names']}" if context["old_names"] else "")
    detail_widgets["joined"].configure(text=f"Joined: {context['data']['time'].strftime('%H:%M:%S')}")
    detail_widgets["duration"].configure(text=f"In world: {str(datetime.now() - context['data']['time']).split('.')[0]}")
    detail_widgets["logged"].configure(text=f"Logged user: {'Yes' if context['logged_entry'] else 'No'}")
    detail_widgets["reason"].configure(text=f"Reason: {context['reason'] or 'N/A'}")
    detail_widgets["tag"].configure(text=f"Tag: {context['tag'] or 'N/A'}")
    note_preview = context["note"]
    if len(note_preview) > 60:
        note_preview = note_preview[:57].rstrip() + "..."
    detail_widgets["note"].configure(text=f"Note: {note_preview}" if note_preview else "")
    detail_widgets["save"].configure(text="Update Local Log" if context["logged_entry"] else "Save Local Log")
    note_button = detail_widgets.get("note_button")
    if note_button:
        apply_button_style(note_button, accent=bool(context["note"]))
    if force_form:
        close_choice_panels()
        assign_form_value("reason_choice", "reason_custom", context["reason"], reason_dropdown_values)
        assign_form_value(
            "tag_choice",
            "tag_custom",
            "" if normalize_key(context["tag"]) == "creator" else context["tag"],
            tag_dropdown_values,
        )
        detail_vars["warning"].set(context["warning"] or "Yellow")
        detail_vars["status"].set("")
        set_note_editor_text(context["note"])
        refresh_warning_button()
    panel.update_idletasks()
    position_detail_panel()


def toggle_user_details(user_key):
    global selected_detail_user
    if selected_detail_user == user_key and detail_panel and detail_panel.winfo_exists() and detail_panel.winfo_viewable():
        close_detail_panel()
        return
    selected_detail_user = user_key
    refresh_detail_panel(force_form=True)


def detail_panel_loop():
    if detail_panel and detail_panel.winfo_exists() and detail_panel.winfo_viewable():
        refresh_detail_panel(force_form=False)
    root.after(1000, detail_panel_loop)


def is_logged_user(user_key):
    data = user_data.get(user_key, {})
    return lookup_user(data.get("usrid", ""), data.get("name", "")) is not None


def get_sorted_user_keys():
    return sorted(
        user_data.keys(),
        key=lambda user_key: (
            0 if is_logged_user(user_key) else 1,
            normalize_key(user_data.get(user_key, {}).get("name", "")),
            normalize_user_id(user_data.get(user_key, {}).get("usrid", "")),
        )
    )


def make_tooltip(widget, text):
    tip = [None]

    def show(event):
        hide(None)
        host = detail_panel if detail_panel and detail_panel.winfo_exists() and detail_panel.winfo_viewable() and widget.winfo_toplevel() == detail_panel else root
        label = tk.Label(
            host,
            text=text,
            bg="#2a2a2a",
            fg="white",
            font=("Segoe UI", 9),
            padx=6,
            pady=4,
            relief="solid",
            borderwidth=1,
            justify="left",
            wraplength=280,
        )
        host.update_idletasks()
        host_x = host.winfo_rootx()
        host_y = host.winfo_rooty()
        x = max(8, event.x_root - host_x + 12)
        y = max(8, event.y_root - host_y + 10)
        max_x = max(8, host.winfo_width() - label.winfo_reqwidth() - 8)
        max_y = max(8, host.winfo_height() - label.winfo_reqheight() - 8)
        x = min(x, max_x)
        y = min(y, max_y)
        label.place(x=x, y=y)
        label.lift()
        tooltip_labels[str(widget)] = label
        tip[0] = label

    def hide(_event):
        existing = tip[0] or tooltip_labels.pop(str(widget), None)
        if existing:
            try:
                existing.destroy()
            except Exception:
                pass
        tip[0] = None

    widget.bind("<Enter>", show)
    widget.bind("<Leave>", hide)
    widget.bind("<ButtonPress>", hide, add="+")


def add_user_row(user_key):
    data = user_data.get(user_key, {})
    display_name = data.get("name", "Unknown")
    user_id = data.get("usrid", "Unknown")
    logged_entry = lookup_user(user_id, display_name)
    reason_text = normalize_text(logged_entry.get("Reasons", "")) if logged_entry else ""
    tag_text = normalize_text(logged_entry.get("Tag", "")) if logged_entry else ""
    warning_type = find_reason_warning(reason_text) if reason_text else ""
    watch_reason = has_watch_reason(reason_text) if reason_text else False
    tag_image_name = find_tag_image(tag_text) if tag_text else ""

    row_bg = "#181f27" if logged_entry else bg
    row = tk.Frame(users_frame, bg=row_bg)

    warning_widget = None
    if warning_type:
        icon_file = {
            "red": "red_warning_icon.png",
            "orange": "orange_warning_icon.png",
            "yellow": "yellow_warning_icon.png",
        }.get(normalize_key(warning_type), "")
        if icon_file:
            image = load_icon(icon_file)
            if image:
                warning_widget = tk.Label(row, image=image, bg=row_bg, cursor="hand2")
                warning_widget.image = image
                warning_widget.pack(side="left", padx=(0, 2))
                make_tooltip(warning_widget, f"Reason: {reason_text}")

    tag_widget = None
    watch_widget = None
    if watch_reason:
        image = load_icon("watch_icon.png")
        if image:
            watch_widget = tk.Label(row, image=image, bg=row_bg, cursor="hand2")
            watch_widget.image = image
            watch_widget.pack(side="left", padx=(0, 2))
            make_tooltip(watch_widget, "Watch reason")

    if tag_image_name:
        image = load_icon(tag_image_name)
        if image:
            tag_widget = tk.Label(row, image=image, bg=row_bg, cursor="hand2")
            tag_widget.image = image
            tag_widget.pack(side="left", padx=(0, 2))
            make_tooltip(tag_widget, f"Tag: {tag_text}")

    name_change_widget = None
    if logged_entry and has_logged_name_change(logged_entry, display_name):
        image = load_icon("name_change_icon.png")
        if image:
            logged_names = ", ".join(get_logged_names(logged_entry)) or "Unknown"
            name_change_widget = tk.Label(row, image=image, bg=row_bg, cursor="hand2")
            name_change_widget.image = image
            name_change_widget.pack(side="left", padx=(0, 2))
            make_tooltip(name_change_widget, f"Logged names: {logged_names}")

    name_label = tk.Label(row, text=display_name, fg="white", bg=row_bg, font=("Segoe UI", 10), anchor="w")
    name_label.pack(side="left", fill="x", expand=True)

    row._uname = display_name
    row._uid = user_id
    row._logged = logged_entry is not None
    row._reason = reason_text if reason_text else "N/A"
    row._tag = tag_text if tag_text else "N/A"

    def on_right_click(_event, key=user_key):
        toggle_user_details(key)
        return "break"

    for widget in (row, name_label, warning_widget, watch_widget, tag_widget, name_change_widget):
        if widget:
            widget.bind("<Button-3>", on_right_click)

    row.pack(fill="x", pady=1)
    user_rows[user_key] = row


def remove_user_row(user_key):
    row = user_rows.pop(user_key, None)
    if row:
        row.destroy()
    if selected_detail_user == user_key:
        close_detail_panel()


def refresh_user_rows():
    clear_all_tooltips()
    for row in list(user_rows.values()):
        row.destroy()
    user_rows.clear()
    for user_key in get_sorted_user_keys():
        add_user_row(user_key)
    sync_user_scrollregion()
    update_height()
    if selected_detail_user not in user_data:
        close_detail_panel()
    elif detail_panel and detail_panel.winfo_exists() and detail_panel.winfo_viewable():
        refresh_detail_panel(force_form=False)


def reset_all():
    global manager_total_override
    clear_all_tooltips()
    close_detail_panel()
    for user_key in list(user_rows.keys()):
        remove_user_row(user_key)
    user_data.clear()
    manager_total_override = None
    world_label.config(text="World: N/A")
    refresh_user_rows()

def find_vrc_window():
    global vrc_hwnd
    while True:
        hwnd = win32gui.FindWindow(None, "VRChat")
        vrc_hwnd = hwnd if hwnd else None
        time.sleep(1)


def is_vrchat_focused():
    try:
        foreground = win32gui.GetForegroundWindow()
        if foreground == vrc_hwnd:
            return True
        _, fg_pid = win32process.GetWindowThreadProcessId(foreground)
        _, our_pid = win32process.GetWindowThreadProcessId(root.winfo_id())
        if fg_pid == our_pid:
            return True
    except Exception:
        pass
    return False


def follow_vrc():
    while True:
        try:
            target_x = None
            target_y = None
            if not settings.get("movable") and vrc_hwnd:
                rect = win32gui.GetWindowRect(vrc_hwnd)
                target_x = rect[0] + (rect[2] - rect[0]) - root.winfo_width() - 20
                target_y = rect[1] + 40
            elif settings.get("movable"):
                target_x = settings.get("window_x")
                target_y = settings.get("window_y")

            root.after(0, lambda x=target_x, y=target_y: place_overlay(x, y))
        except Exception:
            root.after(0, place_overlay)
        time.sleep(0.05)


def overlay_guard_loop():
    try:
        ensure_overlay_on_top()
        if not root.winfo_viewable():
            place_overlay()
    except Exception:
        pass
    root.after(400, overlay_guard_loop)


_drag = {"x": 0, "y": 0}


def _drag_start(event):
    _drag["x"] = event.x
    _drag["y"] = event.y


def _drag_move(event):
    x = root.winfo_x() + event.x - _drag["x"]
    y = root.winfo_y() + event.y - _drag["y"]
    root.geometry(f"+{x}+{y}")
    settings["window_x"] = x
    settings["window_y"] = y


def _drag_end(_event):
    save_settings(settings)


if settings.get("movable"):
    for widget in (canvas, world_label, total_label, sep):
        widget.bind("<ButtonPress-1>", _drag_start)
        widget.bind("<B1-Motion>", _drag_move)
        widget.bind("<ButtonRelease-1>", _drag_end)


resize_grip = None
_rsz = {"x": 0, "y": 0, "w": 0, "h": 0}


def _rsz_start(event):
    _rsz.update({"x": event.x_root, "y": event.y_root, "w": root.winfo_width(), "h": root.winfo_height()})


def _rsz_move(event):
    nw = max(180, _rsz["w"] + event.x_root - _rsz["x"])
    nh = max(190, _rsz["h"] + event.y_root - _rsz["y"])
    root.geometry(f"{nw}x{nh}")
    settings["window_width"] = nw
    settings["window_height"] = nh
    update_height()


def _rsz_end(_event):
    save_settings(settings)


if settings.get("shrinkable"):
    resize_grip = tk.Label(root, text="<>", fg="#555555", bg=bg, font=("Segoe UI", 10), cursor="sizing")
    resize_grip.bind("<ButtonPress-1>", _rsz_start)
    resize_grip.bind("<B1-Motion>", _rsz_move)
    resize_grip.bind("<ButtonRelease-1>", _rsz_end)


def clean_username(name):
    name = normalize_text(name)
    while "  " in name:
        name = name.replace("  ", " ")
    return name


def stdin_reader():
    global manager_total_override
    last_world = None

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        print("RECV:", line)

        if line == "0001":
            os._exit(0)

        if line in {"ResetState", "Leave: LocalHost"}:
            root.after(0, reset_all)
            last_world = None
            continue

        if line.startswith("TotalCheck:"):
            display_total = manager_total_override if manager_total_override is not None else len(user_data)
            print(f"TOTAL_REPORT:counter:{display_total}")
            continue

        if line.startswith("Total:"):
            try:
                manager_total_override = int(line.split(":", 1)[1].strip())
                root.after(0, update_height)
            except Exception:
                pass
            continue

        if line.startswith("World:"):
            world = line[len("World:"):].strip()
            manager_total_override = None
            if last_world != world:
                root.after(0, close_detail_panel)
                root.after(0, user_data.clear)
            last_world = world
            root.after(0, lambda world=world: world_label.config(text=f"World: {world}"))
            root.after(0, refresh_user_rows)
            continue

        if line.startswith("Join:"):
            raw = line[len("Join:"):].strip()
            manager_total_override = None
            if ", usrid:" in raw:
                name_raw, uid_raw = raw.rsplit(", usrid:", 1)
                name = clean_username(name_raw)
                user_id = uid_raw.strip()
            else:
                name = clean_username(raw)
                user_id = "Unknown"
            user_key = build_user_key(name, user_id)
            existing = user_data.get(user_key, {})
            joined_at = existing.get("time", datetime.now())
            user_data[user_key] = {"name": name, "usrid": user_id, "time": joined_at}
            root.after(0, refresh_user_rows)
            continue

        if line.startswith("Leave:"):
            raw = line[len("Leave:"):].strip()
            manager_total_override = None
            if ", usrid:" in raw:
                name_raw, uid_raw = raw.rsplit(", usrid:", 1)
                name = clean_username(name_raw)
                user_id = uid_raw.strip()
            else:
                name = clean_username(raw)
                user_id = "Unknown"
            user_key = build_user_key(name, user_id)
            user_data.pop(user_key, None)
            root.after(0, refresh_user_rows)
            continue


def cache_loop():
    while True:
        refresh_caches()
        root.after(0, refresh_user_rows)
        time.sleep(30)


w = settings.get("window_width", 220)
h = settings.get("window_height", 240)
x = settings.get("window_x")
y = settings.get("window_y")

if settings.get("movable") and x is not None and y is not None:
    root.geometry(f"{w}x{h}+{x}+{y}")
else:
    root.geometry(f"{w}x{h}")
    last_follow_position["x"] = root.winfo_x()
    last_follow_position["y"] = root.winfo_y()

refresh_caches()
update_height()
root.bind("<Configure>", lambda _event: position_detail_panel())

threading.Thread(target=find_vrc_window, daemon=True).start()
threading.Thread(target=follow_vrc, daemon=True).start()
threading.Thread(target=stdin_reader, daemon=True).start()
threading.Thread(target=cache_loop, daemon=True).start()
root.after(1000, detail_panel_loop)
root.after(400, overlay_guard_loop)

root.mainloop()
