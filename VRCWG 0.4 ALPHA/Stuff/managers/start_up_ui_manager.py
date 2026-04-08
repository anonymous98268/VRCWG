import os
import threading
import subprocess
import tkinter as tk
import datetime
import time
import json
import ctypes

from integrity_guard_manager import ensure_project_integrity_or_exit
from runtime_status_manager import clear_status, install_exception_hooks

BG = "#05080c"
FG = "#d6e6ff"
FG_DIM = "#7aa2d6"
ACCENT = "#4da3ff"

FONT_L = ("Consolas", 18)
FONT_M = ("Consolas", 12)
FONT_S = ("Consolas", 10)

FADE_STEP = 0.05
FADE_DELAY = 16
DOT_DELAY = 200
TEXT_DELAY = 10
DRAG_SMOOTHNESS = 0.25

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "_data")
SETTINGS_DIR = os.path.join(DATA_DIR, "settings")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
AUDIOS_DIR = os.path.join(DATA_DIR, "audios")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "startup_info.json")
STARTUP_AUDIO_PATH = os.path.join(AUDIOS_DIR, "startup.mp3")


def run_non_blocking(path):
    try:
        return subprocess.Popen(
            f'"{os.sys.executable}" "{path}"',
            shell=True
        )
    except Exception as e:
        print(f"Failed to start {path}: {e}")


def can_play_audio_file(path):
    if os.name != "nt" or not os.path.isfile(path):
        return False
    try:
        return hasattr(ctypes, "windll") and getattr(ctypes.windll, "winmm", None) is not None
    except Exception:
        return False


def play_audio_file(path):
    if not can_play_audio_file(path):
        return

    alias = f"watchguard_startup_{time.time_ns()}"
    try:
        winmm = ctypes.windll.winmm
    except Exception:
        return

    try:
        if winmm.mciSendStringW(f'open "{path}" type mpegvideo alias {alias}', None, 0, 0) != 0:
            return
        winmm.mciSendStringW(f"play {alias} wait", None, 0, 0)
    finally:
        try:
            winmm.mciSendStringW(f"close {alias}", None, 0, 0)
        except Exception:
            pass


class SplashScreen:
    def __init__(self, root):
        self.root = root
        self.root.withdraw()

        os.makedirs(SETTINGS_DIR, exist_ok=True)
        os.makedirs(IMAGES_DIR, exist_ok=True)
        os.makedirs(AUDIOS_DIR, exist_ok=True)

        if not os.path.isfile(SETTINGS_FILE):
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                f.write(json.dumps({"output_log_location": ""}))

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.0)
        self.win.configure(bg=BG)

        self.w, self.h = 560, 320
        x = (self.win.winfo_screenwidth() - self.w) // 2
        y = (self.win.winfo_screenheight() - self.h) // 2
        self.win.geometry(f"{self.w}x{self.h}+{x}+{y}")

        self._target_x = x
        self._target_y = y
        self._dot_state = 0
        self._closing = False

        self.required_packages = [
            "pillow",
            "requests",
            "psutil",
            "pywin32",
            "python-osc"
        ]

        self.pkg_index = 0
        self._base_text = "Preparing to Check Packages"

        self._build_ui()

        self._load_background_image()
        if can_play_audio_file(STARTUP_AUDIO_PATH):
            threading.Thread(target=play_audio_file, args=(STARTUP_AUDIO_PATH,), daemon=True).start()

        self._enable_drag()
        self._smooth_follow()
        self._fade_in()
        self._animate_dots()
        self._tick()

    def update_status(self, text):
        def apply():
            self._base_text = text
            self.status.config(text=text)
        self.win.after(TEXT_DELAY, apply)

    def save_setting(self, key, value):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            data = {}
        data[key] = value
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(data))

    def _load_background_image(self):
        try:
            from PIL import Image, ImageTk
        except ImportError:
            return

        valid_img = os.path.join(IMAGES_DIR, "splashscreen.png")

        if not os.path.isfile(valid_img):
            return

        try:
            img = Image.open(valid_img).convert("RGBA")
            img = img.resize((self.w, self.h), Image.LANCZOS)

            opacity = 0.20
            black_bg = Image.new("RGBA", img.size, (0, 0, 0, 255))
            img = Image.blend(black_bg, img, opacity)

            self.bg_img = ImageTk.PhotoImage(img)

            if hasattr(self, "background_label"):
                self.background_label.destroy()

            self.background_label = tk.Label(self.inner, image=self.bg_img, borderwidth=0)
            self.background_label.place(x=0, y=0, relwidth=1, relheight=1)
            self.background_label.lower()

        except:
            pass

    def _reload_background_safe(self):
        try:
            self._load_background_image()
            if hasattr(self, "background_label"):
                self.background_label.lower()
        except Exception as e:
            print("Background reload failed:", e)

    def _build_ui(self):
        border = tk.Frame(self.win, bg=ACCENT, bd=2)
        border.pack(fill="both", expand=True)

        self.inner = tk.Frame(border, bg=BG)
        self.inner.pack(fill="both", expand=True, padx=2, pady=2)

        self.title_label = tk.Label(self.inner, text="VRCWG", fg=ACCENT, bg=BG, font=FONT_L)
        self.title_label.pack(pady=(28, 6))

        self.win.after(875, self._animate_title)

        tk.Label(self.inner, text="Another VRC Tool", fg=FG, bg=BG, font=FONT_M).pack()

        self.status = tk.Label(self.inner, text=self._base_text, fg=FG_DIM, bg=BG, font=FONT_S)
        self.status.pack(pady=18)

        self.credit_y = 0.85
        self.credit_1 = tk.Label(self.inner, text="Created by ˋмүѕтєяу & zєηηу", fg=FG_DIM, bg=BG, font=("Consolas", 9))
        self.credit_1.place(relx=0.5, rely=self.credit_y, anchor="center")

        self.credit_2_alpha = 0.0
        self.credit_2 = tk.Label(self.inner, text="Powered By | 0x1FC Syndicate |", fg=BG, bg=BG, font=("Consolas", 9))
        self.credit_2.place(relx=0.5, rely=self.credit_y + 0.06, anchor="center")

        self.win.after(500, self._credit_sequence)

    def _credit_sequence(self):
        def slide(s=0):
            if s <= 20:
                self.credit_1.place_configure(rely=self.credit_y - s * 0.002)
                self.win.after(16, lambda: slide(s + 1))
            else:
                self._fade_in_credit_2()
        slide()

    def _fade_in_credit_2(self):
        if self.credit_2_alpha >= 1:
            return
        self.credit_2_alpha += 0.05
        r = int(122 * self.credit_2_alpha)
        g = int(162 * self.credit_2_alpha)
        b = int(214 * self.credit_2_alpha)
        self.credit_2.config(fg=f"#{r:02x}{g:02x}{b:02x}")
        self.win.after(30, self._fade_in_credit_2)

    def _enable_drag(self):
        self.win.bind("<ButtonPress-1>", self._start_drag)
        self.win.bind("<B1-Motion>", self._update_target)

    def _start_drag(self, e):
        self._offset_x = e.x
        self._offset_y = e.y

    def _update_target(self, e):
        self._target_x = e.x_root - self._offset_x
        self._target_y = e.y_root - self._offset_y

    def _smooth_follow(self):
        cx, cy = self.win.winfo_x(), self.win.winfo_y()
        nx = cx + (self._target_x - cx) * DRAG_SMOOTHNESS
        ny = cy + (self._target_y - cy) * DRAG_SMOOTHNESS
        self.win.geometry(f"+{int(nx)}+{int(ny)}")
        self.win.after(16, self._smooth_follow)

    def _is_installed(self, pkg):
        r = subprocess.run([os.sys.executable, "-m", "pip", "show", pkg],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        return r.returncode == 0

    def _install_or_update(self, pkg):
        subprocess.run([os.sys.executable, "-m", "pip", "install", pkg])

    def _tick(self):
        if self._closing:
            return

        if self.pkg_index >= len(self.required_packages):
            self.update_status("Looking For VRChat's Output Log")
            self.win.after(50, self._perform_log_scan)
            return

        pkg = self.required_packages[self.pkg_index]

        if not self._is_installed(pkg):
            self.update_status(f"Installing {pkg}")
            threading.Thread(target=self._install_then_next, args=(pkg,)).start()
            return

        self.pkg_index += 1
        self.win.after(10, self._tick)

    def _install_then_next(self, pkg):
        self._install_or_update(pkg)

        if pkg == "pillow":
            self.win.after(0, self._reload_background_safe)

        self.pkg_index += 1
        self.win.after(10, self._tick)

    def _perform_log_scan(self):
        username = os.getenv("USERNAME")
        vrchat_dir = f"C:/Users/{username}/AppData/LocalLow/VRChat/VRChat"

        closest_file = None
        newest_mtime = None

        if os.path.isdir(vrchat_dir):
            for entry in os.scandir(vrchat_dir):
                if not entry.is_file():
                    continue
                if not entry.name.startswith("output_log_") or not entry.name.endswith(".txt"):
                    continue
                mtime = entry.stat().st_mtime
                if newest_mtime is None or mtime > newest_mtime:
                    newest_mtime = mtime
                    closest_file = entry.name

        if closest_file:
            full_path = os.path.join(vrchat_dir, closest_file)
            self.save_setting("output_log_location", full_path)
            self.update_status(closest_file)
        else:
            self.update_status("No Output Log Found")

        self.win.after(315, self._finish_after_log)

    def _finish_after_log(self):
        self._closing = True
        self.win.after(315, self._fade_out)

    def _animate_dots(self):
        if self._closing:
            self.status.config(text=self._base_text)
            return
        self.status.config(text=f"{self._base_text}{'.' * (self._dot_state % 4)}")
        self._dot_state += 1
        self.win.after(DOT_DELAY, self._animate_dots)

    def _animate_title(self):
        seq_del = ["VRCWG", "VRCW", "VRC", "VR", "V", ""]
        seq_type = list("VRChat WatchGuard")

        def do_delete(i=0):
            if i < len(seq_del):
                self.title_label.config(text=seq_del[i])
                self.win.after(120, lambda: do_delete(i + 1))
            else:
                do_type()

        def do_type(i=0, out=""):
            if i < len(seq_type):
                out += seq_type[i]
                self.title_label.config(text=out)
                self.win.after(90, lambda: do_type(i + 1, out))

        do_delete()

    def _fade_in(self):
        a = self.win.attributes("-alpha")
        if a < 1.0:
            self.win.attributes("-alpha", min(1.0, a + FADE_STEP))
            self.win.after(FADE_DELAY, self._fade_in)

    def _fade_out(self):
        a = self.win.attributes("-alpha")
        self.update_status("Loading Finished.")
        if a > 0:
            self.win.attributes("-alpha", max(0.0, a - FADE_STEP))
            self.win.after(FADE_DELAY, self._fade_out)
        else:
            p = os.path.join(BASE_DIR, "main_manager.py")
            if os.path.isfile(p):
                run_non_blocking(p)
            self.win.destroy()
            self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    clear_status("startup")
    install_exception_hooks("startup", tk_root=root)
    ensure_project_integrity_or_exit(source="startup")
    SplashScreen(root)
    root.mainloop()
