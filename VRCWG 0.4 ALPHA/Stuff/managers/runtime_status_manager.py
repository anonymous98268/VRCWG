import hashlib
import json
import os
import sys
import time
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "_data")
SETTINGS_DIR = os.path.join(DATA_DIR, "settings")
STATUS_PATH = os.path.join(SETTINGS_DIR, "runtime_status.json")

STATUS_DEFAULT = {"sources": {}}
STATUS_PRIORITY = {"error": 3, "connection": 2, "warning": 1, "info": 0}


def ensure_status_file():
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    if not os.path.isfile(STATUS_PATH):
        with open(STATUS_PATH, "w", encoding="utf-8") as f:
            json.dump(STATUS_DEFAULT, f, indent=4)


def load_status():
    ensure_status_file()
    try:
        with open(STATUS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"sources": {}}
        data.setdefault("sources", {})
        return data
    except Exception:
        return {"sources": {}}


def save_status(data):
    ensure_status_file()
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def clear_status(source):
    data = load_status()
    removed = data.setdefault("sources", {}).pop(str(source), None)
    save_status(data)
    if removed is not None:
        print(f"[runtime_status_manager] cleared {source}")


def clear_all_statuses():
    save_status({"sources": {}})
    print("[runtime_status_manager] cleared all statuses")


def set_status(source, kind, code, message="", details=""):
    data = load_status()
    data.setdefault("sources", {})[str(source)] = {
        "kind": str(kind or "info"),
        "code": str(code or "WG-INFO"),
        "message": str(message or "").strip(),
        "details": str(details or "").strip(),
        "updated_at": time.time(),
    }
    save_status(data)
    print(f"[runtime_status_manager] set {source} {kind} {code} {str(message or '').strip()}")


def get_active_status(data=None):
    data = data or load_status()
    best = None
    best_priority = -1
    best_updated = -1.0
    for source, payload in data.get("sources", {}).items():
        if not isinstance(payload, dict):
            continue
        kind = str(payload.get("kind", "info"))
        priority = STATUS_PRIORITY.get(kind, 0)
        updated_at = float(payload.get("updated_at", 0.0) or 0.0)
        if priority > best_priority or (priority == best_priority and updated_at > best_updated):
            best = dict(payload)
            best["source"] = source
            best_priority = priority
            best_updated = updated_at
    return best


def build_exception_code(source, exc_type, exc_value):
    raw = f"{source}|{getattr(exc_type, '__name__', 'Exception')}|{exc_value}"
    suffix = hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:8].upper()
    return f"WG-{str(source).upper()}-{suffix}"


def report_exception(source, exc_type, exc_value, exc_tb, kind="error"):
    code = build_exception_code(source, exc_type, exc_value)
    message = f"{getattr(exc_type, '__name__', 'Exception')}: {exc_value}"
    details = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))[-4000:]
    set_status(source, kind, code, message=message, details=details)


def install_exception_hooks(source, tk_root=None):
    source_name = str(source)
    print(f"[runtime_status_manager] install hooks for {source_name}")

    def excepthook(exc_type, exc_value, exc_tb):
        report_exception(source_name, exc_type, exc_value, exc_tb, kind="error")
        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook

    if tk_root is not None:
        def report_callback_exception(exc_type, exc_value, exc_tb):
            report_exception(source_name, exc_type, exc_value, exc_tb, kind="error")
            traceback.print_exception(exc_type, exc_value, exc_tb)

        tk_root.report_callback_exception = report_callback_exception
