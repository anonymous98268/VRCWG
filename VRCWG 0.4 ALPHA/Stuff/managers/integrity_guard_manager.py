import base64
import hashlib
import hmac
import json
import os
import sys
import tkinter as tk

from runtime_status_manager import set_status

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "Stuff", "_data")
INFO_DIR = os.path.join(DATA_DIR, "info")
MANIFEST_PATH = os.path.join(INFO_DIR, "core_manifest.dat")

_KEY_PARTS = (
    "VRCWG",
    "Integrity",
    "Guard",
    "April",
    "2026",
    "Signed",
    "Build",
)
HMAC_KEY = "::".join(_KEY_PARTS).encode("utf-8")

IGNORED_PREFIXES = {
    os.path.normpath(os.path.join("Stuff", "_data", "logged")),
    os.path.normpath(os.path.join("Stuff", "_data", "settings", "notes")),
    os.path.normpath(os.path.join("Stuff", "_data", "info", "reasons", "players", "custom")),
    os.path.normpath(os.path.join("Stuff", "_data", "info", "tags", "players", "custom")),
}
IGNORED_FILES = {
    os.path.normpath(os.path.join("Stuff", "_data", "settings", "main_settings.json")),
    os.path.normpath(os.path.join("Stuff", "_data", "settings", "osc_settings.json")),
    os.path.normpath(os.path.join("Stuff", "_data", "settings", "user_counter_settings.json")),
    os.path.normpath(os.path.join("Stuff", "_data", "settings", "startup_info.json")),
    os.path.normpath(os.path.join("Stuff", "_data", "settings", "manager_runtime.json")),
    os.path.normpath(os.path.join("Stuff", "_data", "settings", "runtime_status.json")),
    os.path.normpath(os.path.join("Stuff", "_data", "settings", "local_friends.json")),
    os.path.normpath(os.path.join("Stuff", "_data", "info", "detected_links.json")),
    os.path.normpath(os.path.join("Stuff", "_data", "info", "core_manifest.dat")),
    os.path.normpath(os.path.join("Stuff", "_data", "images", "icon.ico")),
}
PROTECTED_SUFFIXES = {".py", ".png", ".mp3", ".txt"}


def _normalize_relpath(path):
    return os.path.normpath(path).replace("/", os.sep)


def _is_protected(rel_path):
    rel_path = _normalize_relpath(rel_path)
    if rel_path in IGNORED_FILES:
        return False
    for prefix in IGNORED_PREFIXES:
        if rel_path == prefix or rel_path.startswith(prefix + os.sep):
            return False
    _, ext = os.path.splitext(rel_path)
    return ext.lower() in PROTECTED_SUFFIXES


def _iter_protected_files():
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git"}]
        for file_name in files:
            abs_path = os.path.join(root, file_name)
            rel_path = os.path.relpath(abs_path, PROJECT_ROOT)
            if _is_protected(rel_path):
                yield _normalize_relpath(rel_path), abs_path


def _hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _encode_payload(payload_bytes):
    mask = hashlib.sha256(HMAC_KEY).digest()
    encoded = bytes(payload_bytes[index] ^ mask[index % len(mask)] for index in range(len(payload_bytes)))
    return base64.b64encode(encoded).decode("ascii")


def _decode_payload(payload_text):
    raw = base64.b64decode(payload_text.encode("ascii"))
    mask = hashlib.sha256(HMAC_KEY).digest()
    return bytes(raw[index] ^ mask[index % len(mask)] for index in range(len(raw)))


def build_manifest():
    files = {}
    for rel_path, abs_path in sorted(_iter_protected_files()):
        files[rel_path.replace(os.sep, "/")] = _hash_file(abs_path)
    payload = {
        "version": 1,
        "files": files,
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_text = _encode_payload(payload_bytes)
    signature = hmac.new(HMAC_KEY, payload_text.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "version": 1,
        "payload": payload_text,
        "signature": signature,
    }


def write_manifest():
    os.makedirs(INFO_DIR, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(build_manifest(), f, indent=2)


def load_manifest():
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        container = json.load(f)

    payload_text = str(container.get("payload", ""))
    signature = str(container.get("signature", ""))
    expected_signature = hmac.new(HMAC_KEY, payload_text.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("Invalid manifest signature")

    payload_bytes = _decode_payload(payload_text)
    payload = json.loads(payload_bytes.decode("utf-8"))
    payload.setdefault("files", {})
    return payload


def verify_manifest():
    if not os.path.isfile(MANIFEST_PATH):
        return False, "WG-INTEGRITY-MISSING", "Missing integrity manifest."

    try:
        manifest = load_manifest()
    except Exception:
        return False, "WG-INTEGRITY-SIGNATURE", "Integrity signature check failed."

    expected_files = manifest.get("files", {})
    current_files = {
        rel_path.replace(os.sep, "/"): _hash_file(abs_path)
        for rel_path, abs_path in sorted(_iter_protected_files())
    }

    missing = sorted(path for path in expected_files if path not in current_files)
    added = sorted(path for path in current_files if path not in expected_files)
    changed = sorted(path for path in expected_files if current_files.get(path) != expected_files.get(path))

    if not missing and not added and not changed:
        return True, "WG-INTEGRITY-OK", ""

    parts = []
    if changed:
        parts.append("Changed: " + ", ".join(changed[:4]))
    if added:
        parts.append("Added: " + ", ".join(added[:4]))
    if missing:
        parts.append("Missing: " + ", ".join(missing[:4]))
    message = " | ".join(parts) or "Core file mismatch detected."
    return False, "WG-INTEGRITY-MODIFIED", message


def show_integrity_failure_window(code, message):
    try:
        root = tk.Tk()
        root.title("VRCWG Verification")
        root.configure(bg="#05080c")
        root.attributes("-topmost", True)
        root.resizable(False, False)

        width, height = 560, 300
        x = (root.winfo_screenwidth() - width) // 2
        y = (root.winfo_screenheight() - height) // 2
        root.geometry(f"{width}x{height}+{x}+{y}")

        border = tk.Frame(root, bg="#4da3ff", bd=2)
        border.pack(fill="both", expand=True)
        inner = tk.Frame(border, bg="#05080c")
        inner.pack(fill="both", expand=True, padx=2, pady=2)

        tk.Label(inner, text="VRCWG Verification Failed", fg="#4da3ff", bg="#05080c", font=("Consolas", 18)).pack(pady=(24, 12))

        body = tk.Frame(inner, bg="#23070b")
        body.pack(fill="x", padx=22, pady=(0, 14))
        icon_path = os.path.join(PROJECT_ROOT, "Stuff", "_data", "images", "icons", "connection_lost_Icon.png")
        if os.path.isfile(icon_path):
            try:
                icon_image = tk.PhotoImage(file=icon_path)
                body_icon = tk.Label(body, image=icon_image, bg="#23070b")
                body_icon.image = icon_image
                body_icon.pack(side="left", padx=(14, 10), pady=14)
            except Exception:
                pass

        text = (
            "VRCWG cannot continue because its core files are no longer verified.\n\n"
            "This usually means the files were modified, replaced, or the verification data no longer matches this build."
        )
        if message:
            text += f"\n\n{message}"
        tk.Label(
            body,
            text=text,
            fg="white",
            bg="#23070b",
            justify="left",
            anchor="w",
            wraplength=380,
            font=("Consolas", 10),
        ).pack(side="left", fill="both", expand=True, padx=(0, 14), pady=14)

        tk.Label(inner, text=code, fg="#7aa2d6", bg="#05080c", font=("Consolas", 10, "bold")).pack(pady=(0, 12))
        tk.Button(
            inner,
            text="Close",
            command=root.destroy,
            bg="#5b2a2a",
            fg="white",
            activebackground="#774040",
            activeforeground="white",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=16,
            pady=8,
            cursor="hand2",
        ).pack(pady=(0, 22))

        root.mainloop()
    except Exception:
        pass


def ensure_project_integrity_or_exit(source="integrity", allow_bypass_env=False):
    if allow_bypass_env and os.getenv("VRCWG_ALLOW_INTEGRITY_BYPASS") == "1":
        print(f"[integrity_guard_manager] bypass enabled for {source}")
        return True

    ok, code, message = verify_manifest()
    if ok:
        print(f"[integrity_guard_manager] verified {source}")
        return True

    set_status(source, "error", code, message=message or "Integrity check failed.")
    print(f"[integrity_guard_manager] {code}: {message}")
    show_integrity_failure_window(code, message)
    raise SystemExit(1)


if __name__ == "__main__":
    if "--refresh" in sys.argv:
        write_manifest()
        print(f"[integrity_guard_manager] updated manifest at {MANIFEST_PATH}")
    else:
        ok, code, message = verify_manifest()
        print(f"[integrity_guard_manager] {code}")
        if message:
            print(f"[integrity_guard_manager] {message}")
        raise SystemExit(0 if ok else 1)
