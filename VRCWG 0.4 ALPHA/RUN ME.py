import subprocess
import sys
import os

MANAGERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Stuff", "managers")
if MANAGERS_DIR not in sys.path:
    sys.path.insert(0, MANAGERS_DIR)

from integrity_guard_manager import ensure_project_integrity_or_exit
from runtime_status_manager import clear_all_statuses


def run_blocking(path):
    subprocess.check_call(f'"{sys.executable}" "{path}"', shell=True)

if __name__ == "__main__":
    clear_all_statuses()
    ensure_project_integrity_or_exit(source="runme")
    target = os.path.join("Stuff", "managers", "start_up_ui_manager.py")
    run_blocking(target)
