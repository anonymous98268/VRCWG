import subprocess
import sys
import os

def run_blocking(path):
    subprocess.check_call(f'"{sys.executable}" "{path}"', shell=True)

if __name__ == "__main__":
    target = os.path.join("Stuff", "managers", "start_up_ui_manager.py")
    run_blocking(target)
