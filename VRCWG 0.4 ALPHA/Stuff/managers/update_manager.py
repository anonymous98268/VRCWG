import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

import requests

from integrity_guard_manager import write_manifest
from runtime_status_manager import clear_status, install_exception_hooks, set_status

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUNME_PATH = os.path.join(PROJECT_ROOT, "RUN ME.py")
TOOL_REPO = "anonymous98268/VRCWG"
BRANCHES = ("main", "master")

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
    os.path.normpath(os.path.join("Stuff", "_data", "settings", "update_state.json")),
    os.path.normpath(os.path.join("Stuff", "_data", "settings", "local_friends.json")),
    os.path.normpath(os.path.join("Stuff", "_data", "settings", "session_info.json")),
    os.path.normpath(os.path.join("Stuff", "_data", "info", "detected_links.json")),
    os.path.normpath(os.path.join("Stuff", "_data", "info", "core_manifest.dat")),
    os.path.normpath(os.path.join("Stuff", "_data", "images", "icon.ico")),
}
MANAGED_SUFFIXES = {".py", ".png", ".mp3", ".txt", ".json"}


def normalize_relpath(path):
    return os.path.normpath(path).replace("/", os.sep)


def is_managed_path(rel_path):
    rel_path = normalize_relpath(rel_path)
    if rel_path in IGNORED_FILES:
        return False
    for prefix in IGNORED_PREFIXES:
        if rel_path == prefix or rel_path.startswith(prefix + os.sep):
            return False
    _, ext = os.path.splitext(rel_path)
    return ext.lower() in MANAGED_SUFFIXES


def fetch_repo_zip():
    last_error = "Unknown GitHub error"
    for branch in BRANCHES:
        url = f"https://github.com/{TOOL_REPO}/archive/refs/heads/{branch}.zip"
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                print(f"[update_manager] downloaded branch {branch}")
                return response.content
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:
            last_error = str(exc)
    raise RuntimeError(last_error)


def find_extracted_root(base_dir):
    direct_runme = os.path.join(base_dir, "RUN ME.py")
    if os.path.isfile(direct_runme):
        return base_dir
    for name in os.listdir(base_dir):
        candidate = os.path.join(base_dir, name)
        if os.path.isdir(candidate) and os.path.isfile(os.path.join(candidate, "RUN ME.py")):
            return candidate
    raise RuntimeError("Could not locate extracted project root.")


def collect_managed_files(root_dir):
    files = {}
    for current_root, dirs, file_names in os.walk(root_dir):
        dirs[:] = [name for name in dirs if name not in {".git", "__pycache__"}]
        for file_name in file_names:
            abs_path = os.path.join(current_root, file_name)
            rel_path = os.path.relpath(abs_path, root_dir)
            if is_managed_path(rel_path):
                files[normalize_relpath(rel_path)] = abs_path
    return files


def sync_project_from_root(source_root):
    remote_files = collect_managed_files(source_root)
    local_files = collect_managed_files(PROJECT_ROOT)

    for rel_path, src_path in remote_files.items():
        dest_path = os.path.join(PROJECT_ROOT, rel_path)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copy2(src_path, dest_path)
        print(f"[update_manager] copied {rel_path}")

    for rel_path in sorted(set(local_files) - set(remote_files), reverse=True):
        abs_path = os.path.join(PROJECT_ROOT, rel_path)
        try:
            os.remove(abs_path)
            print(f"[update_manager] removed {rel_path}")
        except OSError:
            pass


def main():
    clear_status("updater")
    install_exception_hooks("updater")
    time.sleep(1.0)
    try:
        zip_bytes = fetch_repo_zip()
        with tempfile.TemporaryDirectory(prefix="vrcwg_update_") as temp_dir:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
                archive.extractall(temp_dir)
            source_root = find_extracted_root(temp_dir)
            sync_project_from_root(source_root)
        write_manifest()
        clear_status("updater")
        print("[update_manager] update completed")
        subprocess.Popen([sys.executable, RUNME_PATH], cwd=PROJECT_ROOT)
    except Exception as exc:
        set_status("updater", "connection", "WG-UPDATER-FAIL", str(exc))
        print(f"[update_manager] failed: {exc}")
        raise


if __name__ == "__main__":
    main()
