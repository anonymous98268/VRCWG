import ctypes
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from datetime import datetime

for stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

JOIN_PATTERNS = [
    re.compile(r"OnPlayerJoined\s+(.+?)\s*\((usr_[^)]+)\)"),
    re.compile(r"\[Behaviour\]\s*OnPlayerJoined\s+(.+?)\s*\((usr_[^)]+)\)"),
    re.compile(r"OnPlayerJoined:\s+(.+?)\s*\((usr_[^)]+)\)"),
    re.compile(r"Player\s+(.+?)\s+joined.*\((usr_[^)]+)\)"),
    re.compile(r"(.+?)\s+has\s+joined.*\((usr_[^)]+)\)"),
]

LEAVE_PATTERNS = [
    re.compile(r"OnPlayerLeft\s+(.+?)\s*\((usr_[^)]+)\)"),
    re.compile(r"\[Behaviour\]\s*OnPlayerLeft\s+(.+?)\s*\((usr_[^)]+)\)"),
    re.compile(r"OnPlayerLeft:\s+(.+?)\s*\((usr_[^)]+)\)"),
    re.compile(r"Player\s+(.+?)\s+left.*\((usr_[^)]+)\)"),
    re.compile(r"(.+?)\s+has\s+left.*\((usr_[^)]+)\)"),
]

LINK_PATTERN = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)
FIELD_PATTERN = re.compile(r'"([^"]+)"\s*:\s*"([^"]*)"')

ENV_PATTERN = re.compile(r"Environment Info:")
BUILD_PATTERN = re.compile(r"VRChat Build:\s*(.+)")
CPU_PATTERN = re.compile(r"Processor Type:\s*(.+)")
RAM_PATTERN = re.compile(r"System Memory Size:\s*(.+)")
GPU_PATTERN = re.compile(r"Graphics Device Name:\s*(.+)")
VRAM_PATTERN = re.compile(r"Graphics Memory Size:\s*(.+)")
OS_PATTERN = re.compile(r"Operating System:\s*(.+)")
AUTH_PATTERN = re.compile(r"User Authenticated:\s*(.+?)\s*\((usr_[^)]+)\)")
ROOM_PATTERN = re.compile(r"Entering Room:\s*(.+)")

DEFAULT_MAIN_SETTINGS = {
    "osc_display": False,
    "user_counter_display": False,
    "sounds": False,
}

RUNTIME_DEFAULT = {
    "resynth_requested_at": 0.0,
}

WARNING_SOUND_FILES = {
    ("join", "creator"): "creator_join.mp3",
    ("leave", "creator"): "creator_leave.mp3",
    ("join", "red"): "join_red.mp3",
    ("leave", "red"): "leave_red.mp3",
    ("join", "orange"): "join_orange.mp3",
    ("leave", "orange"): "leave_orange.mp3",
    ("join", "yellow"): "join_yellow.mp3",
    ("leave", "yellow"): "leave_yellow.mp3",
}

script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
data_dir = os.path.join(parent_dir, "_data")

startup_path = os.path.join(data_dir, "settings", "startup_info.json")
settings_path = os.path.join(data_dir, "settings", "main_settings.json")
runtime_path = os.path.join(data_dir, "settings", "manager_runtime.json")
links_path = os.path.join(data_dir, "info", "detected_links.json")

osc_script = os.path.join(parent_dir, "modules", "osc_user_log_module.py")
counter_script = os.path.join(parent_dir, "modules", "user_counter_module.py")
ui_script = os.path.join(parent_dir, "managers", "main_ui_manager.py")

audios_dir = os.path.join(data_dir, "audios")
logged_external_dir = os.path.join(data_dir, "logged", "players", "external")
logged_local_dir = os.path.join(data_dir, "logged", "players", "local")
reasons_default_dir = os.path.join(data_dir, "info", "reasons", "players", "default")
reasons_custom_dir = os.path.join(data_dir, "info", "reasons", "players", "custom")

settings_cache = dict(DEFAULT_MAIN_SETTINGS)
settings_mtime = None
runtime_mtime = None
last_resynth_request = 0.0

osc_process = None
counter_process = None

local_user = None
local_user_id = None

log_path = ""
current_world = None
current_world_active = False
recent_world_events = []
recent_world_members = {}
join_count = 0

logged_players_by_id = {}
logged_players_by_name = {}
reason_warning_map = {}

state_lock = threading.Lock()
sound_cache_lock = threading.Lock()
send_lock = threading.RLock()
replay_queue_lock = threading.Lock()
module_report_lock = threading.Lock()

pending_replay_targets = set()
pending_replay_from_log = False
pending_replay_include_reset = False
pending_replay_deadline = 0.0

module_total_reports = {
    "osc": {"reported_total": None, "reported_at": 0.0},
    "counter": {"reported_total": None, "reported_at": 0.0},
}
scheduled_resynth_at = 0.0
scheduled_resynth_reason = ""


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_json_safe(path, default):
    try:
        data = load_json(path)
        merged = dict(default)
        merged.update(data)
        return merged
    except Exception:
        return dict(default)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def ensure_runtime_file():
    if not os.path.isfile(runtime_path):
        save_json(runtime_path, dict(RUNTIME_DEFAULT))


def ensure_links_file():
    os.makedirs(os.path.dirname(links_path), exist_ok=True)
    if not os.path.isfile(links_path):
        with open(links_path, "w", encoding="utf-8") as f:
            json.dump([], f, indent=4)


def remove_file_if_exists(path):
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def load_json_list(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


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


def clean_username(name):
    cleaned = normalize_text(name)
    while "  " in cleaned:
        cleaned = cleaned.replace("  ", " ")
    return cleaned


def get_settings_dict(force=False):
    global settings_cache, settings_mtime

    try:
        mtime = os.path.getmtime(settings_path)
    except OSError:
        settings_cache = dict(DEFAULT_MAIN_SETTINGS)
        settings_mtime = None
        return dict(settings_cache)

    if force or mtime != settings_mtime:
        settings_mtime = mtime
        settings_cache = load_json_safe(settings_path, DEFAULT_MAIN_SETTINGS)

    return dict(settings_cache)


def get_runtime_dict():
    global runtime_mtime
    ensure_runtime_file()

    try:
        mtime = os.path.getmtime(runtime_path)
    except OSError:
        runtime_mtime = None
        return dict(RUNTIME_DEFAULT)

    if runtime_mtime != mtime:
        runtime_mtime = mtime
    return load_json_safe(runtime_path, RUNTIME_DEFAULT)


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
    for directory in (logged_external_dir, logged_local_dir):
        path = find_closest_log_file(directory)
        if path:
            entries.extend(parse_file(path))
    return entries


def load_all_reasons():
    entries = []
    for directory in (reasons_default_dir, reasons_custom_dir):
        if not os.path.isdir(directory):
            continue
        for path in glob.glob(os.path.join(directory, "*.txt")):
            entries.extend(parse_file(path))
    return entries


def refresh_sound_caches():
    global logged_players_by_id, logged_players_by_name, reason_warning_map

    logged_entries = load_logged_players()
    reason_entries = load_all_reasons()

    new_by_id = {}
    new_by_name = {}
    for entry in logged_entries:
        user_id = normalize_user_id(entry.get("UserID", ""))
        username = normalize_key(entry.get("Username", ""))
        if user_id:
            new_by_id[user_id] = entry
        if username:
            new_by_name[username] = entry

    new_reason_map = {}
    for entry in reason_entries:
        reason = normalize_key(entry.get("Reason", ""))
        warning = normalize_key(entry.get("Warning", ""))
        if reason and warning:
            new_reason_map[reason] = warning

    with sound_cache_lock:
        logged_players_by_id = new_by_id
        logged_players_by_name = new_by_name
        reason_warning_map = new_reason_map


def lookup_logged_user(user_id, username):
    user_id_key = normalize_user_id(user_id)
    username_key = normalize_key(username)

    with sound_cache_lock:
        if user_id_key and user_id_key in logged_players_by_id:
            return logged_players_by_id[user_id_key]
        if username_key and username_key in logged_players_by_name:
            return logged_players_by_name[username_key]
    return None


def find_sound_profile_for_user(username, user_id):
    logged_entry = lookup_logged_user(user_id, username)
    if not logged_entry:
        return ""

    for tag in split_lookup_values(logged_entry.get("Tag", "")):
        if normalize_key(tag) == "creator":
            return "creator"

    for reason in split_lookup_values(logged_entry.get("Reasons", "")):
        with sound_cache_lock:
            warning = reason_warning_map.get(normalize_key(reason))
        if warning in {"red", "orange", "yellow"}:
            return warning
    return ""


def play_audio_file(path):
    if os.name != "nt" or not os.path.isfile(path):
        return

    alias = f"watchguard_{time.time_ns()}"
    winmm = ctypes.windll.winmm

    try:
        if winmm.mciSendStringW(f'open "{path}" type mpegvideo alias {alias}', None, 0, 0) != 0:
            return
        winmm.mciSendStringW(f"play {alias} wait", None, 0, 0)
    finally:
        try:
            winmm.mciSendStringW(f"close {alias}", None, 0, 0)
        except Exception:
            pass


def maybe_play_user_sound(event_type, username, user_id):
    settings = get_settings_dict()
    if not settings.get("sounds", False):
        return

    sound_profile = find_sound_profile_for_user(username, user_id)
    if not sound_profile:
        return

    sound_file = WARNING_SOUND_FILES.get((event_type, sound_profile))
    if not sound_file:
        return

    sound_path = os.path.join(audios_dir, sound_file)
    if not os.path.isfile(sound_path):
        return

    threading.Thread(target=play_audio_file, args=(sound_path,), daemon=True).start()


def append_detected_link(link):
    ensure_links_file()
    entries = load_json_list(links_path)
    entries.append({
        "url": link,
        "detected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    entries = entries[-300:]
    with open(links_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=4)


def get_target_process(target_name):
    if target_name == "osc":
        return osc_process
    if target_name == "counter":
        return counter_process
    return None


def clear_module_report(target_name):
    with module_report_lock:
        if target_name in module_total_reports:
            module_total_reports[target_name]["reported_total"] = None
            module_total_reports[target_name]["reported_at"] = 0.0


def clear_scheduled_resynth():
    global scheduled_resynth_at, scheduled_resynth_reason
    scheduled_resynth_at = 0.0
    scheduled_resynth_reason = ""


def all_active_modules_match(expected_total):
    active_targets = resolve_targets()
    if not active_targets:
        return False

    with module_report_lock:
        for target_name in active_targets:
            reported_total = module_total_reports.get(target_name, {}).get("reported_total")
            if reported_total != expected_total:
                return False

    return True


def schedule_delayed_resynth(reason):
    global scheduled_resynth_at, scheduled_resynth_reason

    requested_deadline = time.time() + 300.0
    if scheduled_resynth_at and scheduled_resynth_at <= requested_deadline:
        return

    scheduled_resynth_at = requested_deadline
    scheduled_resynth_reason = reason
    print(f"Resynth scheduled in 5 minutes: {reason}")


def handle_module_total_report(target_name, reported_total):
    snapshot = get_state_snapshot()
    manager_total = snapshot.get("count", 0)

    with module_report_lock:
        if target_name in module_total_reports:
            module_total_reports[target_name]["reported_total"] = reported_total
            module_total_reports[target_name]["reported_at"] = time.time()

    print(f"Total compare [{target_name}] manager={manager_total} module={reported_total}")

    if manager_total == reported_total:
        if all_active_modules_match(manager_total):
            clear_scheduled_resynth()
        return

    if manager_total > reported_total:
        proc = get_target_process(target_name)
        if proc:
            print(f"Correcting {target_name} total to {manager_total}")
            safe_send(proc, f"Total: {manager_total}")
        return

    schedule_delayed_resynth(
        f"{target_name} reported {reported_total} while manager expected {manager_total}"
    )


def handle_process_output(target_name, proc, line):
    line = line.rstrip("\r\n")
    if not line:
        return

    print(f"[{target_name}] {line}")

    if get_target_process(target_name) is not proc:
        return

    if line.startswith("TOTAL_REPORT:"):
        parts = line.split(":", 2)
        if len(parts) == 3:
            try:
                reported_total = int(parts[2].strip())
            except ValueError:
                return
            handle_module_total_report(target_name, reported_total)


def process_output_reader(target_name, proc):
    stream = proc.stdout
    if not stream:
        return

    try:
        while True:
            raw_line = stream.readline()
            if not raw_line:
                break
            handle_process_output(target_name, proc, raw_line)
    except Exception as exc:
        print(f"[{target_name}] reader error: {exc}")
    finally:
        try:
            stream.close()
        except Exception:
            pass


def run_non_blocking(path, capture_output=False, target_name=None):
    proc = subprocess.Popen(
        [sys.executable, "-u", path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.STDOUT if capture_output else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if capture_output and target_name:
        threading.Thread(target=process_output_reader, args=(target_name, proc), daemon=True).start()
    return proc


def stop_process(proc):
    if not proc:
        return
    try:
        proc.terminate()
        time.sleep(0.5)
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass


def is_running(proc):
    return proc is not None and proc.poll() is None


def safe_send(proc, text):
    if not is_running(proc) or not proc.stdin:
        return
    try:
        proc.stdin.write(text + "\n")
        proc.stdin.flush()
    except Exception as exc:
        print("SEND ERROR:", exc)


def resolve_targets(targets=None):
    active = set()
    if targets is None or "osc" in targets:
        if is_running(osc_process):
            active.add("osc")
    if targets is None or "counter" in targets:
        if is_running(counter_process):
            active.add("counter")
    return active


def broadcast_without_lock(text, targets=None):
    active_targets = resolve_targets(targets)
    if "osc" in active_targets:
        safe_send(osc_process, text)
    if "counter" in active_targets:
        safe_send(counter_process, text)


def broadcast_line(text, targets=None):
    with send_lock:
        broadcast_without_lock(text, targets)


def broadcast_reset(targets=None):
    with send_lock:
        broadcast_without_lock("ResetState", targets)


def request_total_check(expected_total, targets=None):
    print(f"New total should be: {expected_total}")
    with send_lock:
        broadcast_without_lock(f"TotalCheck: {expected_total}", targets)


def check_scheduled_resynth():
    global scheduled_resynth_at, scheduled_resynth_reason

    if not scheduled_resynth_at or time.time() < scheduled_resynth_at:
        return

    reason = scheduled_resynth_reason or "module total mismatch"
    scheduled_resynth_at = 0.0
    scheduled_resynth_reason = ""
    print(f"Running delayed resynth: {reason}")
    schedule_replay(rebuild_from_log=True, include_reset=True)


def set_state_snapshot(snapshot):
    global current_world, current_world_active, recent_world_events, recent_world_members, join_count

    with state_lock:
        current_world = snapshot.get("world")
        current_world_active = bool(snapshot.get("active"))
        recent_world_events = [dict(event) for event in snapshot.get("events", [])]
        recent_world_members = {key: dict(value) for key, value in snapshot.get("members", {}).items()}
        join_count = len(recent_world_members)


def get_state_snapshot():
    with state_lock:
        return {
            "world": current_world,
            "active": current_world_active,
            "events": [dict(event) for event in recent_world_events],
            "members": {key: dict(value) for key, value in recent_world_members.items()},
            "count": join_count,
        }


def set_current_world(world):
    world_name = normalize_text(world)
    with state_lock:
        global current_world, current_world_active, recent_world_events, recent_world_members, join_count
        current_world = world_name
        current_world_active = True
        recent_world_events = []
        recent_world_members = {}
        join_count = 0
    return world_name


def clear_current_world():
    with state_lock:
        global current_world, current_world_active, recent_world_events, recent_world_members, join_count
        current_world = None
        current_world_active = False
        recent_world_events = []
        recent_world_members = {}
        join_count = 0


def format_player_line(event_type, username, user_id):
    label = "Join" if event_type == "join" else "Leave"
    if user_id:
        return f"{label}: {username}, usrid: {user_id}"
    return f"{label}: {username}"


def remember_player_event(event_type, username, user_id):
    username = clean_username(username)
    user_id = normalize_text(user_id)
    user_key = build_user_key(username, user_id)

    with state_lock:
        if current_world_active and current_world:
            recent_world_events.append({
                "type": event_type,
                "username": username,
                "usrid": user_id,
            })
            if event_type == "join":
                recent_world_members[user_key] = {"username": username, "usrid": user_id}
            else:
                recent_world_members.pop(user_key, None)

            global join_count
            join_count = len(recent_world_members)

    return username, user_id


def parse_player_event(line, patterns, event_type):
    for pattern in patterns:
        match = pattern.search(line)
        if not match:
            continue
        return {
            "type": event_type,
            "username": clean_username(match.group(1)),
            "usrid": normalize_text(match.group(2)),
        }
    return None


def replay_recent_world_state(targets=None, rebuild_from_log=False, include_reset=True):
    active_targets = resolve_targets(targets)
    if not active_targets:
        return

    snapshot = rebuild_recent_world_state_from_log() if rebuild_from_log else get_state_snapshot()

    with send_lock:
        if include_reset:
            broadcast_without_lock("ResetState", active_targets)
            time.sleep(0.075)

        world = snapshot.get("world")
        if world:
            world_line = f"World: {world}"
            print(world_line)
            broadcast_without_lock(world_line, active_targets)
            time.sleep(0.075)

        for event in snapshot.get("events", []):
            line = format_player_line(event["type"], event["username"], event["usrid"])
            print(line)
            broadcast_without_lock(line, active_targets)
            time.sleep(0.075)

        expected_total = snapshot.get("count", 0)
        if "osc" in active_targets:
            safe_send(osc_process, f"Total: {expected_total}")

    request_total_check(expected_total, active_targets)


def schedule_replay(targets=None, rebuild_from_log=False, include_reset=True):
    global pending_replay_from_log, pending_replay_include_reset, pending_replay_deadline

    requested_targets = {"osc", "counter"} if targets is None else set(targets)
    with replay_queue_lock:
        pending_replay_targets.update(requested_targets)
        pending_replay_from_log = pending_replay_from_log or rebuild_from_log
        pending_replay_include_reset = pending_replay_include_reset or include_reset
        pending_replay_deadline = time.time() + 0.15


def replay_dispatch_loop():
    global pending_replay_from_log, pending_replay_include_reset, pending_replay_deadline

    while True:
        replay_payload = None

        with replay_queue_lock:
            if pending_replay_targets and time.time() >= pending_replay_deadline:
                replay_payload = (
                    set(pending_replay_targets),
                    pending_replay_from_log,
                    pending_replay_include_reset,
                )
                pending_replay_targets.clear()
                pending_replay_from_log = False
                pending_replay_include_reset = False
                pending_replay_deadline = 0.0

        if replay_payload:
            targets, rebuild_from_log, include_reset = replay_payload
            replay_recent_world_state(
                targets=targets,
                rebuild_from_log=rebuild_from_log,
                include_reset=include_reset,
            )

        time.sleep(0.05)


def check_runtime_commands():
    global last_resynth_request

    runtime_data = get_runtime_dict()
    resynth_requested_at = float(runtime_data.get("resynth_requested_at", 0.0) or 0.0)

    if resynth_requested_at > last_resynth_request:
        last_resynth_request = resynth_requested_at
        print("Resynth requested.")
        schedule_replay(rebuild_from_log=True, include_reset=True)


def process_watcher():
    global osc_process, counter_process

    while True:
        settings = get_settings_dict()
        started_targets = set()

        if settings.get("osc_display", False):
            if not is_running(osc_process):
                clear_module_report("osc")
                osc_process = run_non_blocking(osc_script, capture_output=True, target_name="osc")
                started_targets.add("osc")
        elif is_running(osc_process):
            safe_send(osc_process, "0001")
            stop_process(osc_process)
            osc_process = None
            clear_module_report("osc")
        else:
            clear_module_report("osc")

        if settings.get("user_counter_display", False):
            if not is_running(counter_process):
                clear_module_report("counter")
                counter_process = run_non_blocking(counter_script, capture_output=True, target_name="counter")
                started_targets.add("counter")
        elif is_running(counter_process):
            safe_send(counter_process, "0001")
            stop_process(counter_process)
            counter_process = None
            clear_module_report("counter")
        else:
            clear_module_report("counter")

        if started_targets:
            time.sleep(0.05)
            schedule_replay(targets=started_targets, rebuild_from_log=True, include_reset=True)

        check_runtime_commands()
        check_scheduled_resynth()
        time.sleep(0.1)


def sound_cache_loop():
    while True:
        refresh_sound_caches()
        time.sleep(15)


def scan_startup_info():
    global local_user, local_user_id

    env_found = False
    build = cpu = ram = gpu = vram = os_info = None

    if not os.path.isfile(log_path):
        return

    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        for _ in range(400):
            line = f.readline()
            if not line:
                break

            line = line.strip()

            if ENV_PATTERN.search(line):
                env_found = True

            if env_found:
                if not build:
                    match = BUILD_PATTERN.search(line)
                    if match:
                        build = match.group(1)
                if not cpu:
                    match = CPU_PATTERN.search(line)
                    if match:
                        cpu = match.group(1)
                if not ram:
                    match = RAM_PATTERN.search(line)
                    if match:
                        ram = match.group(1)
                if not gpu:
                    match = GPU_PATTERN.search(line)
                    if match:
                        gpu = match.group(1)
                if not vram:
                    match = VRAM_PATTERN.search(line)
                    if match:
                        vram = match.group(1)
                if not os_info:
                    match = OS_PATTERN.search(line)
                    if match:
                        os_info = match.group(1)

            if not local_user:
                match = AUTH_PATTERN.search(line)
                if match:
                    local_user = clean_username(match.group(1))
                    local_user_id = normalize_text(match.group(2))

    print(f"VRChat Build: {build}")
    print(f"Processor Type: {cpu}")
    print(f"System Memory Size: {ram}")
    print(f"Graphics Device Name: {gpu}")
    print(f"Graphics Memory Size: {vram}")
    print(f"Operating System: {os_info}")

    if local_user:
        print(f"Local User Detected: {local_user}, usrid: {local_user_id}")


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


def resolve_log_path():
    startup_info = load_json_safe(startup_path, {"output_log_location": ""})
    configured_path = os.path.normpath(startup_info.get("output_log_location", "").replace("/", os.sep))

    if configured_path and os.path.isfile(configured_path):
        return configured_path

    latest_path = find_latest_output_log()
    if latest_path:
        save_json(startup_path, {"output_log_location": latest_path})
        return latest_path

    return configured_path


def line_matches_local_session(line):
    local_id_key = normalize_user_id(local_user_id)
    local_name_key = normalize_key(local_user)

    auth_match = AUTH_PATTERN.search(line)
    if auth_match:
        auth_name = clean_username(auth_match.group(1))
        auth_user_id = normalize_text(auth_match.group(2))
        if local_id_key and normalize_user_id(auth_user_id) == local_id_key:
            return True
        if local_name_key and normalize_key(auth_name) == local_name_key:
            return True

    join_event = parse_player_event(line, JOIN_PATTERNS, "join")
    if join_event:
        if local_id_key and normalize_user_id(join_event["usrid"]) == local_id_key:
            return True
        if local_name_key and normalize_key(join_event["username"]) == local_name_key:
            return True

    return False


def find_recent_rebuild_start_index(lines):
    if not lines:
        return 0

    last_room_index = None
    fallback_index = max(0, len(lines) - 4000)
    local_marker_index = None

    for index in range(len(lines) - 1, -1, -1):
        line = lines[index].strip()
        if not line:
            continue

        if ROOM_PATTERN.search(line) and last_room_index is None:
            last_room_index = index

        if line_matches_local_session(line):
            local_marker_index = index
            break

    if local_marker_index is not None:
        for index in range(local_marker_index, -1, -1):
            if ROOM_PATTERN.search(lines[index].strip()):
                return index
        return local_marker_index

    if last_room_index is not None:
        return last_room_index
    return fallback_index


def rebuild_recent_world_state_from_log():
    snapshot = {
        "world": None,
        "active": False,
        "events": [],
        "members": {},
        "count": 0,
    }

    if not os.path.isfile(log_path):
        set_state_snapshot(snapshot)
        return snapshot

    current_room = None
    room_active = False
    room_events = []
    room_members = {}

    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    start_index = find_recent_rebuild_start_index(lines)

    for raw_line in lines[start_index:]:
            line = raw_line.strip()

            room_match = ROOM_PATTERN.search(line)
            if room_match:
                current_room = normalize_text(room_match.group(1))
                room_active = True
                room_events = []
                room_members = {}
                continue

            if "Unloading scenes" in line:
                current_room = None
                room_active = False
                room_events = []
                room_members = {}
                continue

            if not room_active or not current_room:
                continue

            join_event = parse_player_event(line, JOIN_PATTERNS, "join")
            if join_event:
                room_events.append(join_event)
                room_members[build_user_key(join_event["username"], join_event["usrid"])] = {
                    "username": join_event["username"],
                    "usrid": join_event["usrid"],
                }
                continue

            leave_event = parse_player_event(line, LEAVE_PATTERNS, "leave")
            if leave_event:
                room_events.append(leave_event)
                room_members.pop(build_user_key(leave_event["username"], leave_event["usrid"]), None)

    if current_room and room_active:
        snapshot = {
            "world": current_room,
            "active": True,
            "events": room_events,
            "members": room_members,
            "count": len(room_members),
        }

    set_state_snapshot(snapshot)
    return snapshot


def warm_recent_world_state():
    try:
        rebuild_recent_world_state_from_log()
    except Exception as exc:
        print(f"Warm rebuild failed: {exc}")


def main():
    global log_path, last_resynth_request

    remove_file_if_exists(runtime_path)
    remove_file_if_exists(links_path)
    ensure_runtime_file()
    ensure_links_file()
    get_settings_dict(force=True)
    refresh_sound_caches()
    last_resynth_request = float(get_runtime_dict().get("resynth_requested_at", 0.0) or 0.0)

    log_path = resolve_log_path()
    if not log_path or not os.path.isfile(log_path):
        print("No VRChat output log could be found.")
        return

    scan_startup_info()

    threading.Thread(target=replay_dispatch_loop, daemon=True).start()
    threading.Thread(target=process_watcher, daemon=True).start()
    threading.Thread(target=sound_cache_loop, daemon=True).start()

    if os.path.isfile(ui_script):
        run_non_blocking(ui_script)

    threading.Thread(target=warm_recent_world_state, daemon=True).start()

    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(0, os.SEEK_END)

        while True:
            line = f.readline()
            if not line:
                time.sleep(0.1)
                continue

            line = line.strip()

            if line == "Total_User_Check":
                snapshot = get_state_snapshot()
                total_line = f"Total: {snapshot['count']}"
                print(total_line)
                broadcast_line(total_line, {"osc"})
                request_total_check(snapshot["count"])
                continue

            room_match = ROOM_PATTERN.search(line)
            if room_match:
                world = set_current_world(room_match.group(1))
                world_line = f"World: {world}"
                print(world_line)
                broadcast_line(world_line)
                continue

            if "Unloading scenes" in line:
                clear_current_world()
                print("Leave: LocalHost")
                broadcast_reset()
                request_total_check(0)
                continue

            links = LINK_PATTERN.findall(line)
            for link in links:
                print(f"Link: {link}")
                append_detected_link(link)

            join_event = parse_player_event(line, JOIN_PATTERNS, "join")
            if join_event:
                username, user_id = remember_player_event("join", join_event["username"], join_event["usrid"])
                join_line = format_player_line("join", username, user_id)
                print(join_line)
                broadcast_line(join_line)
                request_total_check(get_state_snapshot()["count"])
                maybe_play_user_sound("join", username, user_id)
                continue

            leave_event = parse_player_event(line, LEAVE_PATTERNS, "leave")
            if leave_event:
                username, user_id = remember_player_event("leave", leave_event["username"], leave_event["usrid"])
                leave_line = format_player_line("leave", username, user_id)
                print(leave_line)
                broadcast_line(leave_line)
                request_total_check(get_state_snapshot()["count"])
                maybe_play_user_sound("leave", username, user_id)


if __name__ == "__main__":
    main()
