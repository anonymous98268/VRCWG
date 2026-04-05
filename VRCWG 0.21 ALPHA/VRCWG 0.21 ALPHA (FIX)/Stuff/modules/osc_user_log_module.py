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

data_dir = os.path.join(parent_dir, "_data")
settings_dir = os.path.join(data_dir, "settings")
info_dir = os.path.join(data_dir, "info")

settings_path = os.path.join(settings_dir, "osc_settings.json")
version_path = os.path.join(info_dir, "version.txt")

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

last_user = "None"
last_event = ""

total_users = 0

last_send = 0
last_swap = 0
show_time_toggle = True

client = SimpleUDPClient(OSC_IP, OSC_PORT)

input_queue = Queue()


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
    global show_time_toggle, last_swap

    parts = ["VRChat WatchGuard:"]
    now = time.time()

    if settings.get("show_pcvr") and settings.get("show_time"):
        if now - last_swap > 5:
            show_time_toggle = not show_time_toggle
            last_swap = now
    else:
        show_time_toggle = True

    if settings.get("show_time") and show_time_toggle:
        t = datetime.now().strftime("%I:%M %p").lstrip("0")
        parts.append(f"Current Time: {t}")
    elif settings.get("show_pcvr"):
        parts.append("SteamVR Mode" if steamvr_running() else "Desktop Mode")

    if settings.get("show_tabbed"):
        p = get_active_window_process()
        if p:
            parts.append(f"Tabbed Into {p}")

    if settings.get("show_version"):
        parts.append(f"Version: {clean_text(version_text)}")

    if last_event:
        parts.append(f"User: {clean_text(last_user)} {last_event}")

    if settings.get("show_total_users"):
        parts.append(f"Total Users: {total_users}")

    return clean_text("\n".join(parts))


threading.Thread(target=stdin_reader, daemon=True).start()


while True:
    load_settings()
    load_version()

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
