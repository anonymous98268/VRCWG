import time
import os
import json
import psutil
import sys
import threading
import unicodedata
from queue import Queue
from datetime import datetime
from pythonosc.udp_client import SimpleUDPClient

OSC_IP = "127.0.0.1"
OSC_PORT = 9000

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

script_path = os.path.abspath(__file__)
script_dir = os.path.dirname(script_path)
parent_dir = os.path.dirname(script_dir)
managers_dir = os.path.join(parent_dir, "managers")

if managers_dir not in sys.path:
    sys.path.insert(0, managers_dir)

from integrity_guard_manager import ensure_project_integrity_or_exit
from runtime_status_manager import clear_status, install_exception_hooks

data_dir = os.path.join(parent_dir, "_data")
settings_dir = os.path.join(data_dir, "settings")
info_dir = os.path.join(data_dir, "info")

settings_path = os.path.join(settings_dir, "osc_settings.json")
version_path = os.path.join(info_dir, "version.txt")
session_info_path = os.path.join(settings_dir, "session_info.json")

os.makedirs(settings_dir, exist_ok=True)
os.makedirs(info_dir, exist_ok=True)

default_settings = {
    "show_time": True,
    "show_tabbed": False,
    "show_version": False,
    "show_total_users": True,
    
    "show_pcvr": False,

    "show_vrc_build": False,

    "show_cpu_type": False,
    "show_ram_type": False,
    "show_gpu_type": False,
    "show_vram_type": False,

    "show_os": False,
}

if not os.path.exists(settings_path):
    with open(settings_path, "w") as f:
        json.dump(default_settings, f, indent=4)

settings = default_settings.copy()
settings_mtime = 0

version_text = ""
session_info = {}
session_info_mtime = 0

last_user = "None"
last_event = ""

total_users = 0

last_send = 0
slot_state = {
    "slot_1": {"index": 0, "last_swap": 0.0},
    "slot_2": {"index": 0, "last_swap": 0.0},
    "slot_3": {"index": 0, "last_swap": 0.0},
    "slot_4": {"index": 0, "last_swap": 0.0},
    "slot_5": {"index": 0, "last_swap": 0.0},
}

client = SimpleUDPClient(OSC_IP, OSC_PORT)

input_queue = Queue()

clear_status("osc")
install_exception_hooks("osc")
ensure_project_integrity_or_exit(source="osc")


def clean_text(text):
    try:
        return unicodedata.normalize("NFKC", str(text)).replace("\u200b", "").strip()
    except:
        return "?"


def parse_user(line):
    try:
        raw = line.split(":", 1)[1].strip()

        if ", usrid:" in raw:
            raw = raw.split(", usrid:")[0].strip()

        return clean_text(raw)
    except:
        return "Unknown"


def stdin_reader():
    while True:
        try:
            line = sys.stdin.readline()
            if line:
                input_queue.put(line.strip())
        except:
            pass


def load_settings():
    global settings_mtime, settings
    try:
        m = os.path.getmtime(settings_path)
        if m != settings_mtime:
            settings_mtime = m

            with open(settings_path, "r") as f:
                loaded = json.load(f)

            updated = False
            for key, value in default_settings.items():
                if key not in loaded:
                    loaded[key] = value
                    updated = True

            settings = loaded

            if updated:
                with open(settings_path, "w") as f:
                    json.dump(settings, f, indent=4)
    except:
        settings = default_settings.copy()


def load_version():
    global version_text
    try:
        with open(version_path, "r") as f:
            version_text = f.read().strip()
    except:
        version_text = ""


def load_session_info():
    global session_info, session_info_mtime
    try:
        mtime = os.path.getmtime(session_info_path)
        if mtime == session_info_mtime:
            return
        session_info_mtime = mtime
        with open(session_info_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        session_info = loaded if isinstance(loaded, dict) else {}
    except Exception:
        session_info = {}


def fit_line(text, max_len=40):
    cleaned = clean_text(text)
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3].rstrip() + "..."


def build_slot_1_segments():
    segments = []
    if settings.get("show_time"):
        segments.append(fit_line(f"Time: {datetime.now().strftime('%I:%M %p').lstrip('0')}"))
    if settings.get("show_cpu_type") and clean_text(session_info.get("cpu", "")):
        segments.append(fit_line(f"CPU: {session_info.get('cpu', '')}"))
    return [segment for segment in segments if segment]


def build_slot_2_segments():
    segments = []
    if settings.get("show_tabbed"):
        process_name = get_active_window_process()
        if process_name:
            segments.append(fit_line(f"Tabbed: {process_name}"))
    if settings.get("show_vrc_build") and clean_text(session_info.get("build", "")):
        segments.append(fit_line(f"Build: {session_info.get('build', '')}"))
    return [segment for segment in segments if segment]


def build_slot_3_segments():
    segments = []
    if settings.get("show_version") and version_text:
        segments.append(fit_line(f"Version: {version_text}"))
    if settings.get("show_ram_type") and clean_text(session_info.get("ram", "")):
        segments.append(fit_line(f"RAM: {session_info.get('ram', '')}"))
    return [segment for segment in segments if segment]


def build_slot_4_segments():
    segments = []
    if settings.get("show_pcvr"):
        segments.append(fit_line(f"Mode: {'SteamVR' if steamvr_running() else 'Desktop'}"))
    if settings.get("show_gpu_type") and clean_text(session_info.get("gpu", "")):
        segments.append(fit_line(f"GPU: {session_info.get('gpu', '')}"))
    return [segment for segment in segments if segment]


def build_slot_5_segments():
    segments = []
    if settings.get("show_vram_type") and clean_text(session_info.get("vram", "")):
        segments.append(fit_line(f"VRAM: {session_info.get('vram', '')}"))
    if settings.get("show_os") and clean_text(session_info.get("os", "")):
        segments.append(fit_line(f"OS: {session_info.get('os', '')}"))
    return [segment for segment in segments if segment]


def pick_rotating_segment(segments, slot_name):
    if not segments:
        return ""
    state = slot_state.setdefault(slot_name, {"index": 0, "last_swap": 0.0})
    now = time.time()
    if state["index"] >= len(segments):
        state["index"] = 0
    if state["last_swap"] <= 0.0:
        state["last_swap"] = now
        return segments[state["index"]]
    if len(segments) > 1 and now - state["last_swap"] >= 5:
        state["index"] = (state["index"] + 1) % len(segments)
        state["last_swap"] = now
    return segments[state["index"]]


def get_active_window_process():
    try:
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)

        name = psutil.Process(pid).name().replace(".exe", "")
        return clean_text(name)
    except:
        return ""


def steamvr_running():
    try:
        for p in psutil.process_iter(['name']):
            name = (p.info['name'] or "").lower()
            if name in ["vrserver.exe", "vrmonitor.exe", "vrcompositor.exe"]:
                return True
    except:
        pass
    return False


def build_message():
    parts = [fit_line("VRChat Watch Guards:")]
    optional_lines = [
        pick_rotating_segment(build_slot_1_segments(), "slot_1"),
        pick_rotating_segment(build_slot_2_segments(), "slot_2"),
        pick_rotating_segment(build_slot_3_segments(), "slot_3"),
        pick_rotating_segment(build_slot_4_segments(), "slot_4"),
        pick_rotating_segment(build_slot_5_segments(), "slot_5"),
    ]
    parts.extend(line for line in optional_lines if line)
    if last_event:
        parts.append(fit_line(f"User: {clean_text(last_user)} {last_event}"))
    if settings.get("show_total_users"):
        parts.append(fit_line(f"Total Users: {total_users}"))
    return clean_text("\n".join(parts[:6]))


threading.Thread(target=stdin_reader, daemon=True).start()


while True:
    load_settings()
    load_version()
    load_session_info()

    while not input_queue.empty():
        try:
            line = input_queue.get()
        except:
            line = ""

        if not line:
            continue

        print("RECV:", line)

        if line == "0001":
            os._exit(0)

        if line == "ResetState":
            last_user = "None"
            last_event = ""
            total_users = 0
            continue

        if line.startswith("TotalCheck:"):
            print(f"TOTAL_REPORT:osc:{total_users}")
            continue

        if line.startswith("Join:"):
            user = parse_user(line)
            last_user = user
            last_event = "joined"
            total_users += 1

        elif line.startswith("Leave:"):
            user = parse_user(line)
            last_user = user
            last_event = "left"
            total_users -= 1
            if total_users < 0:
                total_users = 0

        elif line.startswith("Total:"):
            try:
                total_users = int(line.split(":")[1].strip())
            except:
                pass

    now = time.time()
    if now - last_send >= 1.6:
        msg = build_message()
        try:
            client.send_message("/chatbox/input", [msg, True])
        except:
            pass
        last_send = now

    time.sleep(0.05)
