"""
Maple Classic Exp Overlay: EXP/hr, mesos/hr and time to level for MapleStory Classic World.

Finds the MapleStory window, locates the "EXP. 12,345 [12.34%]" text in its status bar at
any window size, reads the digits by matching them against examples of the game's font
(Tesseract OCR as a fallback), and calculates EXP per hour, %/hr, and time to next level.

While tracking it also reads the map name from the minimap, your mesos from the inventory
and your party size from the party list, and keeps a log of EXP/hr and mesos/hr per day,
map, level and party size.

It only looks at the screen: it never reads or changes the game's files or memory and
never sends input to the game. Your settings and log are kept in the MapleEXP folder
inside %APPDATA% (named after its old name, Maple EXP/hr, so updating keeps them).
"""
import ctypes
import hashlib
import hmac
import json
import math
import os
import platform
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
import urllib.parse
import urllib.request
import webbrowser
from collections import Counter, deque, namedtuple
from ctypes import wintypes
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from tkinter import font as tkfont, messagebox, simpledialog, ttk

import cv2
import mss
import numpy as np
import pytesseract
from PIL import Image, ImageDraw, ImageTk

# Fork: farm analytics (sessions in farm.db, real EXP/hr, mesos accounting, HP/MP, monsters).
from farmkit.gauges import GaugeConfirm
from farmkit.overlay import FarmOverlay, farm_config, read_status_gauges

# Use physical pixels so window positions, tkinter and mss screenshots all agree on
# scaled displays.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

VERSION = "0.9.6-farm.1"     # fork of 0.9.6-beta: farm analytics (see FORK.md)
WINDOW_TITLE = "Maple Classic Exp Overlay"    # the version is shown in Settings
OLD_TITLES = ("Maple EXP/hr",)               # what versions before 0.9.3 were called

# Files that come with the program (inside the .exe when packaged), the folder it runs from,
# and the folder for your settings, log and learned digits (MAPLE_EXP_DATA can override it).
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ASSETS = APP_DIR / "assets"
HOME_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("MAPLE_EXP_DATA") or Path(os.environ.get("APPDATA") or Path.home()) / "MapleEXP")

CONFIG_PATH = DATA_DIR / "config.json"
ERROR_LOG_PATH = DATA_DIR / "errors.log"
TEMPLATE_PATH = ASSETS / "digits.npz"
ICON_PATH = ASSETS / "icon.ico"
POLL_SECONDS = 1.5          # how often to read the EXP bar
ROLLING_WINDOW = 300        # seconds used for the "last 5 min" rate
SEARCH_STRIP = 0.22         # bottom part of the game window searched for the EXP text
LOG_PATH = DATA_DIR / "exp_log.json"
LOG_TEXT_PATH = DATA_DIR / "exp_log.txt"
LOG_BLOCK = 300             # seconds of non-stop EXP gain behind each log update
MESOS_PATH = DATA_DIR / "mesos_digits.npz"             # learned as you play...
MESOS_START_PATH = ASSETS / "mesos_digits.npz"          # ...starting from these
MESOS_BOX_PATH = ASSETS / "mesos_box.npz"
SCAN_SECONDS = 4            # how often the whole window is checked for the inventory and party
DEFAULTS = {"gap_seconds": 15, "transparency": 0, "topmost": True,
            "check_updates": True, "skip_version": "", "ui_scale": 100, "compact": False, "hotkeys_on": True}
# Settings > Size, in %: only these, so the window can't get too small to read or use.
UI_SCALES = (80, 90, 100, 110, 125, 150, 175, 200)
STATS_SITE = "https://maple-classic-exp-overlay.pages.dev"       # shared 15-minute runs (server/)
GITHUB_REPO = "xoere2/Maple-Classic-Exp-Overlay"      # the fork (upstream: beanguri/Maple-Classic-Exp-Overlay)
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# EXP needed to finish each level in MapleStory Classic World (index 0 = level 1),
# from meowdb.com/msclassic/guides/exp-table-level-1-to-100.
EXP_TABLE = [
    15, 34, 57, 92, 135, 372, 560, 840, 1242, 1716,
    2360, 3216, 4200, 5460, 7050, 8840, 11040, 13716, 16680, 20216,
    24402, 28980, 34320, 40512, 47216, 54900, 63666, 73080, 83720, 95700,
    108480, 122760, 138666, 155540, 174216, 194832, 216600, 240500, 266682, 294216,
    324240, 356916, 391160, 428280, 468450, 510420, 555680, 604416, 655200, 709716,
    748608, 789631, 832902, 878545, 926689, 977471, 1031036, 1087536, 1147132, 1209994,
    1276301, 1346242, 1420016, 1497832, 1579913, 1666492, 1757815, 1854143, 1955750, 2062925,
    2175973, 2295216, 2420993, 2553663, 2693603, 2841212, 2996910, 3161140, 3334370, 3517093,
    3709829, 3913127, 4127566, 4353756, 4592341, 4844001, 5109452, 5389449, 5684790, 11938059,
    25069923, 52646838, 110558359, 232172553, 487562361, 1023880958, 2150150011, 4515315023, 9482161548,
]

tesseract_ok = False        # set in main(); Tesseract is only a fallback


# --------------------------------------------------------------------------- files

_error_lock = threading.Lock()
_last_errors = {}


def log_error(what, exc=None):
    """Adds an error, with details, to errors.log in the data folder (the same one at most
    once a minute), so players can send it in when something goes wrong."""
    key = (what, repr(exc))
    with _error_lock:
        now = time.monotonic()
        if now - _last_errors.get(key, -1e9) < 60:
            return
        _last_errors[key] = now
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if ERROR_LOG_PATH.exists() and ERROR_LOG_PATH.stat().st_size > 1_000_000:
            os.replace(ERROR_LOG_PATH, ERROR_LOG_PATH.with_suffix(".old.log"))
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc else ""
        with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] v{VERSION}: {what}\n{details}\n")
    except Exception:
        pass


def load_config():
    """The saved settings, with anything missing or damaged replaced by the defaults."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        cfg = {}
    except Exception as e:
        log_error("reading settings (using the defaults)", e)
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    for key, default in DEFAULTS.items():
        value = cfg.get(key)
        if type(value) is not type(default) or (key == "gap_seconds" and not 1 <= value <= 3600):
            cfg[key] = default
    cfg["transparency"] = min(90, max(0, cfg["transparency"]))
    if cfg["ui_scale"] not in UI_SCALES:
        cfg["ui_scale"] = 100
    keys = cfg.get("hotkeys") if isinstance(cfg.get("hotkeys"), dict) else {}
    cfg["hotkeys"] = {a: keys[a] if a in keys and (keys[a] == "" or parse_hotkey(keys[a])) else k
                      for a, k in DEFAULT_HOTKEYS.items()}            # "" is a hotkey turned off
    ints = lambda v, n: isinstance(v, list) and len(v) == n and all(type(x) is int for x in v)
    checks = {"pos": lambda v: ints(v, 2), "log_size": lambda v: ints(v, 2) and min(v) >= 100,
              "mesos_box": lambda v: ints(v, 6), "mode": lambda v: v in ("window", "region"),
              "region": lambda v: isinstance(v, dict) and all(type(v.get(k)) is int and (
                  k in ("left", "top") or v[k] > 0) for k in ("left", "top", "width", "height")),
              "log_columns": lambda v: isinstance(v, dict), "log_columns_pt": lambda v: isinstance(v, dict),
              "log_open": lambda v: type(v) is bool,
              "share_runs": lambda v: type(v) is bool}       # missing: not asked yet
    for key, ok in checks.items():
        if key in cfg and cfg[key] is not None and not ok(cfg[key]):
            del cfg[key]
    if cfg.get("mode") == "region" and not cfg.get("region"):
        cfg["mode"] = "window"
    return cfg


def save_config(cfg):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)                    # never leaves a half-written file
    except Exception as e:
        log_error("saving settings", e)


def move_old_data():
    """Earlier versions kept settings and the log next to the program: copy them over once."""
    for name in ("config.json", "exp_log.json", "exp_log.txt", "mesos_digits.npz"):
        old, new = HOME_DIR / name, DATA_DIR / name
        if old.exists() and not new.exists():
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, new)
            except Exception as e:
                log_error(f"copying {name} from the program's folder", e)


def find_tesseract():
    """The Tesseract that comes with the download, else an installed one. Never one in the
    folder the overlay happens to be started from (Windows' usual search looks there first,
    so a tesseract.exe someone left in, say, Downloads could be run)."""
    on_path = [Path(d) / "tesseract.exe" for d in os.environ.get("PATH", "").split(os.pathsep)
               if d.strip() and os.path.isabs(d.strip())]
    candidates = [
        HOME_DIR / "tesseract" / "tesseract.exe",            # comes with the download
        APP_DIR / "tesseract" / "tesseract.exe",
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        *on_path,
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


# --------------------------------------------------------------------------- updates
#
# At start (unless turned off in Settings) it asks GitHub for the latest release. The only
# other time it goes online is to share finished 15-minute runs, if the player agreed to that.

def version_key(version):
    """'v0.9.1-beta' -> something to compare versions by. A full release beats its betas."""
    main, _, pre = version.strip().lstrip("vV").partition("-")
    nums = [int(x) for x in re.findall(r"\d+", main)[:3]]
    return tuple(nums + [0] * (3 - len(nums))), 0 if pre else 1, pre


def _plain_text(markdown):
    """Release notes from GitHub, readable as plain text."""
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", markdown)          # [text](link) -> text
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)                       # **bold** (keeps exp_log.txt)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"^#+\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*]\s+", "• ", t, flags=re.M)
    t = re.sub(r"\n{3,}", "\n\n", t.replace("\r", ""))
    return t.strip()[:5000]


def check_for_update(timeout=8):
    """(newer version, its release notes) if GitHub has a newer release, else None.
    Raises if GitHub can't be reached."""
    request = urllib.request.Request(LATEST_RELEASE_API, headers={
        "User-Agent": f"MapleClassicExpOverlay/{VERSION}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read(1_000_000).decode("utf-8"))
    tag = str(data.get("tag_name", ""))
    if not re.fullmatch(r"v?\d+(\.\d+){0,2}(-[\w.]+)?", tag):
        raise ValueError(f"unexpected release name {tag[:40]!r}")
    if version_key(tag) <= version_key(VERSION):
        return None
    return tag.lstrip("vV"), _plain_text(str(data.get("body") or ""))


def post_runs(install, runs, timeout=15):
    """Sends finished runs to the stats site (only if the player agreed to share).
    Returns the start times of the runs it's done with: the ones the site has (now or from
    before), and ones it turned down for good. Runs turned down only for today ("retry", too
    many sent today) are left to be sent again later."""
    shared = ("date", "started", "map_read", "class", "level_start", "level_end", "party", "exp",
              "seconds", "mesos", "mesos_seconds", "version", "seal")
    body = json.dumps({"install": install, "runs": [{k: r.get(k) for k in shared} for r in runs]}).encode()
    request = urllib.request.Request(f"{STATS_SITE}/api/runs", data=body, headers={
        "content-type": "application/json", "User-Agent": f"MapleClassicExpOverlay/{VERSION}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read(1_000_000).decode("utf-8"))
    return [r.get("started") for r in data.get("results", []) if r.get("ok") or not r.get("retry")]


# --------------------------------------------------------------------------- hotkeys
#
# Shift+1 to 4 by default (changeable in Settings). They're Windows hotkeys, as Discord and
# OBS use: they work while you play even though the game runs as administrator, and they
# never touch the game. They're only on while MapleStory or the overlay is the window in
# front, so other programs can still use those keys (e.g. to type "!").

HOTKEY_ACTIONS = {"start": "Start / Pause", "reset": "Reset", "skip": "Don't log", "compact": "Compact",
                  "afk": "AFK"}
DEFAULT_HOTKEYS = {"start": "Shift+1", "reset": "Shift+2", "skip": "Shift+3", "compact": "Shift+4",
                   "afk": "Shift+5"}
_HOTKEY_MODS = {"Ctrl": 0x2, "Alt": 0x1, "Shift": 0x4}          # MOD_CONTROL, MOD_ALT, MOD_SHIFT


def _key_name(vk):
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x70 <= vk <= 0x7B:
        return f"F{vk - 0x6F}"
    return None


def parse_hotkey(text):
    """'Shift+1' -> (modifiers, virtual key), or None if it isn't a hotkey the overlay takes:
    Ctrl, Alt and/or Shift with a number or letter, or an F key (with or without them)."""
    if not isinstance(text, str) or not text:
        return None
    *mods, key = text.split("+")
    if len(set(mods)) != len(mods) or any(m not in _HOTKEY_MODS for m in mods):
        return None
    vk = next((v for v in list(range(0x30, 0x3A)) + list(range(0x41, 0x5B)) + list(range(0x70, 0x7C))
               if _key_name(v) == key), None)
    if vk is None or (not mods and not key.startswith("F")):     # a plain letter would block typing
        return None
    return sum(_HOTKEY_MODS[m] for m in mods), vk


def hotkey_text(ctrl, alt, shift, vk):
    """The name of a key pressed with these modifiers (e.g. 'Shift+1'), or None if it can't be one."""
    key = _key_name(vk)
    text = "+".join([m for m, on in (("Ctrl", ctrl), ("Alt", alt), ("Shift", shift)) if on] + [key or "?"])
    return text if key and parse_hotkey(text) else None


class Hotkeys:
    """Windows hotkeys on their own thread. They're registered only while MapleStory or the
    overlay is the window in front; on_press(action) is called (from that thread) for each press."""

    def __init__(self, on_press):
        self.on_press = on_press
        self.problems = {}                   # action -> why it couldn't be used (e.g. taken)
        self._wanted, self._paused, self._changed = {}, False, True
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def set(self, keys, on=True):
        """keys: action -> 'Shift+1' (or '' for off)."""
        with self._lock:
            self._wanted = {a: parse_hotkey(k) for a, k in keys.items() if on and parse_hotkey(k)}
            self._changed = True

    def pause(self, paused):
        """Off for a moment (e.g. while a new hotkey is being chosen), then back on."""
        with self._lock:
            self._paused, self._changed = paused, True

    def in_front(self):
        hwnd = _GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return True
        cls = ctypes.create_unicode_buffer(64)
        _GetClassName(hwnd, cls, 64)
        return cls.value == "MapleStoryClass" or _process_name(hwnd) == "maplestory.exe"

    def _run(self):
        msg, active, was_on = wintypes.MSG(), {}, None    # active: hotkey id -> action
        self.thread_id = _kernel32.GetCurrentThreadId()
        _SetTimer(None, 0, 250, None)                     # a WM_TIMER every 250 ms, to check what's in front
        while _GetMessage(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:                     # WM_HOTKEY
                action = active.get(msg.wParam)
                if action:
                    self.on_press(action)
            elif msg.message == 0x0113:                   # WM_TIMER
                try:
                    on = self.in_front()
                except Exception:
                    on = False
                with self._lock:
                    changed, wanted, paused, self._changed = self._changed, dict(self._wanted), self._paused, False
                on = on and not paused
                if not changed and on == was_on:
                    continue
                for hid in active:
                    _UnregisterHotKey(None, hid)
                active, was_on = {}, on
                if on:
                    for hid, (action, (mods, vk)) in enumerate(wanted.items(), start=1):
                        if _RegisterHotKey(None, hid, mods | 0x4000, vk):     # MOD_NOREPEAT
                            active[hid] = action
                            self.problems.pop(action, None)
                        else:
                            self.problems[action] = "used by another program"


# --------------------------------------------------------------------------- game window

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _api(fn, restype, *argtypes):
    fn.restype, fn.argtypes = restype, list(argtypes)
    return fn


_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_EnumWindows = _api(_user32.EnumWindows, wintypes.BOOL, _EnumWindowsProc, wintypes.LPARAM)
_IsWindow = _api(_user32.IsWindow, wintypes.BOOL, wintypes.HWND)
_IsWindowVisible = _api(_user32.IsWindowVisible, wintypes.BOOL, wintypes.HWND)
_IsIconic = _api(_user32.IsIconic, wintypes.BOOL, wintypes.HWND)
_GetClassName = _api(_user32.GetClassNameW, ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_GetWindowText = _api(_user32.GetWindowTextW, ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_GetClientRect = _api(_user32.GetClientRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_ClientToScreen = _api(_user32.ClientToScreen, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.POINT))
_GetWindowThreadProcessId = _api(_user32.GetWindowThreadProcessId, wintypes.DWORD,
                                 wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_OpenProcess = _api(_kernel32.OpenProcess, wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_QueryFullProcessImageName = _api(_kernel32.QueryFullProcessImageNameW, wintypes.BOOL, wintypes.HANDLE,
                                  wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
_CloseHandle = _api(_kernel32.CloseHandle, wintypes.BOOL, wintypes.HANDLE)
_GetAncestor = _api(_user32.GetAncestor, wintypes.HWND, wintypes.HWND, wintypes.UINT)
_GetWindowRect = _api(_user32.GetWindowRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_ShowWindow = _api(_user32.ShowWindow, wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_SetForegroundWindow = _api(_user32.SetForegroundWindow, wintypes.BOOL, wintypes.HWND)
_CreateMutex = _api(_kernel32.CreateMutexW, wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
_MonitorFromPoint = _api(_user32.MonitorFromPoint, wintypes.HANDLE, wintypes.POINT, wintypes.DWORD)
_GetForegroundWindow = _api(_user32.GetForegroundWindow, wintypes.HWND)
_RegisterHotKey = _api(_user32.RegisterHotKey, wintypes.BOOL, wintypes.HWND, ctypes.c_int, wintypes.UINT,
                       wintypes.UINT)
_UnregisterHotKey = _api(_user32.UnregisterHotKey, wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_SetTimer = _api(_user32.SetTimer, ctypes.c_size_t, wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p)
_GetMessage = _api(_user32.GetMessageW, wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                   wintypes.UINT, wintypes.UINT)


def screen_dpi(x, y):
    """The DPI of the screen at (x, y), or None. Screens can have different Windows scaling
    (e.g. 200% on a 5K screen and 125% on a 1440p one), so text is sized for each."""
    try:
        dx, dy = ctypes.c_uint(), ctypes.c_uint()
        monitor = _MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)      # MONITOR_DEFAULTTONEAREST
        if ctypes.windll.shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
            return dx.value
    except Exception:
        pass
    return None


def _process_name(hwnd):
    pid = wintypes.DWORD()
    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = _OpenProcess(0x1000, False, pid.value)       # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        ok = _QueryFullProcessImageName(handle, 0, buf, ctypes.byref(size))
        return os.path.basename(buf.value).lower() if ok else ""
    finally:
        _CloseHandle(handle)


def client_rect(hwnd):
    """(left, top, width, height) of the window's game area in screen pixels, or None."""
    if not _IsWindow(hwnd) or _IsIconic(hwnd):
        return None
    r, pt = wintypes.RECT(), wintypes.POINT(0, 0)
    if not _GetClientRect(hwnd, ctypes.byref(r)) or not _ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    if r.right < 100 or r.bottom < 100:
        return None
    return pt.x, pt.y, r.right, r.bottom


def find_game_window():
    """Handle of the MapleStory game window (the biggest, if there are several), or None."""
    found = []

    def check(hwnd, _):
        if _IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            title = ctypes.create_unicode_buffer(256)
            _GetClassName(hwnd, cls, 256)
            _GetWindowText(hwnd, title, 256)
            if cls.value == "MapleStoryClass" or (
                    "maple" in title.value.lower() and _process_name(hwnd) == "maplestory.exe"):
                rect = client_rect(hwnd)
                found.append((rect[2] * rect[3] if rect else 0, hwnd))
        return True

    _EnumWindows(_EnumWindowsProc(check), 0)
    return max(found)[1] if found else None


def grab(sct, left, top, width, height):
    """Screenshot of a screen rectangle, clipped to the monitors: (bgr, left, top) or None."""
    v = sct.monitors[0]
    x0, y0 = max(left, v["left"]), max(top, v["top"])
    x1 = min(left + width, v["left"] + v["width"])
    y1 = min(top + height, v["top"] + v["height"])
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    shot = np.array(sct.grab({"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}))
    return np.ascontiguousarray(shot[:, :, :3]), x0, y0


# --------------------------------------------------------------------------- reading the bar
#
# The status bar shows "EXP. 38,849 [61.02%]": white digits and green brackets. The game
# draws it in a small pixel font scaled up to the window size, so once a digit is rescaled
# to a standard height it can be recognised by comparing it pixel by pixel with saved
# examples of each digit (digits.npz).

Blob = namedtuple("Blob", "i x y w h")
# cells are digit positions (centre x, width, "digit" or "wide"); V is the brightness image
Layout = namedtuple("Layout", "gh cy exp pct box V")

CANON = 24                  # digits are compared at this height
PH, PW = 28, 19             # size of one digit patch at that height
SHIFT = 3                   # px of wiggle room when lining a digit up with an example
MIN_SCORE = 0.75            # correlation the best digit needs (true matches score ~0.9+)
MIN_LEAD = 0.03             # ...and how far ahead of the runner-up it must be


def _blobs(mask):
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return labels, [Blob(i, *map(int, stats[i][:4])) for i in range(1, n)]


def _left_heavy(labels, b):
    """True for '[' (stroke on the left), False for ']'."""
    m = labels[b.y:b.y + b.h, b.x:b.x + b.w] == b.i
    half = max(1, b.w // 2)
    return m[:, :half].sum() > m[:, b.w - half:].sum()


def _cells(blobs, gh):
    """Digit positions. A blob as wide as several digits (touching digits) is split evenly."""
    pitch = 0.84 * gh
    out = []
    for b in blobs:
        if 0.95 * b.h < b.w < 1.25 * gh:
            out.append((b.x + b.w / 2, b.w, "wide"))          # the '%' sign
            continue
        n = max(1, int(round((b.w + 0.1 * gh) / pitch)))
        out.extend((b.x + (k + 0.5) * b.w / n, b.w / n, "digit") for k in range(n))
    return out


def find_exp(bgr):
    """Locate the EXP text in a screenshot. Returns a Layout or None."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)
    green = (sat >= 90) & (val >= 110) & (hue >= 26) & (hue <= 50)
    white = (sat < 70) & (val >= 140)
    glabels, gblobs = _blobs(green)
    _, wblobs = _blobs(white)
    brackets = sorted((b for b in gblobs if b.h >= 5 and b.w <= 0.6 * b.h), key=lambda b: b.x)
    V = val.astype(np.float32)
    found = []
    for o in brackets:
        bh, cy = o.h, o.y + o.h / 2
        on_line = lambda b: abs(b.y + b.h / 2 - cy) <= 0.3 * bh
        if o.w >= 6 and not _left_heavy(glabels, o):
            continue                                          # clearly a ']'
        right = [c for c in brackets if c.x > o.x + o.w and c.x - o.x <= 9 * bh
                 and on_line(c) and abs(c.h - bh) <= 0.25 * bh]
        if not right:
            continue
        c = right[0]
        if c.w >= 6 and _left_heavy(glabels, c):
            continue                                          # clearly another '['
        line = [b for b in wblobs if on_line(b) and b.h >= 0.6 * bh]
        inside = sorted((b for b in line if b.x >= o.x + o.w and b.x + b.w <= c.x), key=lambda b: b.x)
        if len(inside) < 2 or any(b.h > 1.25 * bh for b in inside):
            continue
        # The EXP number ends right before '['; walk left until a gap or the taller label.
        # Digit height comes from the % digits (same font): a single digit can be shorter
        # when the game scales its font by an odd amount (a '1' was 9 px next to 11 px digits).
        dh = float(np.median([b.h for b in inside]))
        run, edge = [], o.x
        for b in sorted((b for b in line if b.x + b.w <= o.x), key=lambda b: -b.x):
            if edge - (b.x + b.w) > 2.0 * bh or b.h > 1.25 * bh or b.h > 1.15 * dh:
                break
            run.append(b)
            edge = b.x
        if not run:
            continue                                          # HP/MP: label before '['
        run.reverse()
        heights = [b.h for b in run + inside if b.w <= 0.85 * b.h]
        gh = float(np.median(heights)) if heights else float(bh)
        found.append(Layout(gh, cy, _cells(run, gh), _cells(inside, gh),
                            (run[0].x, int(cy - bh), c.x + c.w, int(cy + bh)), V))
    if not found:
        return None
    # The status bar is the lowest line found; EXP is the right-most value on it.
    low = max(f.cy for f in found)
    return max((f for f in found if f.cy >= low - 0.5 * f.gh), key=lambda f: f.box[0])


def digit_patch(V, cx, cy, gh, shift):
    """Brightness patch around one digit, rescaled so the digit is CANON px tall."""
    k = gh / CANON
    x0 = cx - (PW / 2 + shift) * k
    y0 = cy - (PH / 2 + shift) * k
    M = np.float32([[1 / k, 0, -x0 / k], [0, 1 / k, -y0 / k]])
    return cv2.warpAffine(V, M, (PW + 2 * shift, PH + 2 * shift), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def _unit_rows(a):
    a = a - a.mean(axis=1, keepdims=True)
    return a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-6)


class DigitTemplates:
    """Example patches of each digit; scores a patch against all of them at once."""

    def __init__(self, examples):
        labels, rows = [], []
        for digit, patches in sorted(examples.items()):
            for p in patches:
                labels.append(digit)
                rows.append(np.asarray(p, np.float32).reshape(-1))
        self.labels = np.array(labels)
        self.digits = sorted(set(labels))
        self.mat = _unit_rows(np.stack(rows))

    @classmethod
    def load(cls, path=TEMPLATE_PATH):
        try:
            data = np.load(path)
            if int(data["canon"]) != CANON:
                return None
            return cls({k[1:]: data[k] for k in data.files if k.startswith("d")})
        except Exception:
            return None

    def scores(self, patch):
        """Best correlation per digit, trying every small shift of the patch."""
        windows = np.lib.stride_tricks.sliding_window_view(patch, (PH, PW)).reshape(-1, PH * PW)
        s = (_unit_rows(windows) @ self.mat.T).max(axis=0)
        return {d: float(s[self.labels == d].max()) for d in self.digits}

    def match(self, lay, cell):
        """(digit, score, lead over the runner-up) for one digit position."""
        best = {}
        for f in (0.95, 1.0, 1.05):         # allow for small errors in the size estimate
            for d, s in self.scores(digit_patch(lay.V, cell[0], lay.cy, lay.gh * f, SHIFT)).items():
                best[d] = max(best.get(d, -1.0), s)
        ranked = sorted(best.values(), reverse=True)
        lead = ranked[0] - ranked[1] if len(ranked) > 1 else 1.0
        return max(best, key=best.get), ranked[0], lead


def _pct_cells(lay):
    """The digits inside the brackets: drops the '%' sign right before the ']'."""
    return [c for c in lay.pct if lay.box[2] - c[0] > 1.5 * lay.gh]


def _read_digits(tpl, lay, cells):
    s = ""
    for cell in cells:
        if cell[2] != "digit":
            return None
        digit, score, lead = tpl.match(lay, cell)
        if score < MIN_SCORE or lead < MIN_LEAD:
            return None
        s += digit
    return s


def read_layout(tpl, lay):
    """(exp, pct) read with the digit templates, or None if any digit is unclear."""
    exp_s = _read_digits(tpl, lay, lay.exp)
    pct_s = _read_digits(tpl, lay, _pct_cells(lay))
    if not exp_s or not pct_s or not 3 <= len(pct_s) <= 5:
        return None
    if (len(exp_s) > 1 and exp_s[0] == "0") or (len(pct_s) > 3 and pct_s[0] == "0"):
        return None                                # a leading 0: some digits were cut off
    return int(exp_s), int(pct_s) / 100          # the game always shows 2 decimals


def _ocr_cells(bgr, lay, cells):
    """Tesseract on the area covering cells, two ways. Returns digit strings."""
    if not cells:
        return []
    g = lay.gh
    x0 = int(min(c[0] - c[1] / 2 for c in cells) - 0.1 * g)
    x1 = int(max(c[0] + c[1] / 2 for c in cells) + 0.1 * g)
    crop = bgr[max(0, int(lay.cy - 0.75 * g)):int(lay.cy + 0.75 * g), max(0, x0):x1]
    if crop.size == 0:
        return []
    big = cv2.resize(crop, None, fx=40 / g, fy=40 / g, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(big, cv2.COLOR_BGR2HSV)
    white = (((hsv[..., 1] < 70) & (hsv[..., 2] >= 140)) * 255).astype(np.uint8)
    white = cv2.dilate(white, np.ones((3, 3), np.uint8))
    _, bright = cv2.threshold(hsv[..., 2], 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out = []
    for mask in (white, bright):
        img = cv2.copyMakeBorder(255 - mask, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
        text = pytesseract.image_to_string(img, config="--psm 7 -c tessedit_char_whitelist=0123456789,.")
        out.append(re.sub(r"\D", "", text))
    return out


def tesseract_candidates(bgr, lay):
    """Fallback: (exp, pct) guesses from Tesseract. The tracker picks one that adds up."""
    if not tesseract_ok:
        return []
    exps = {int(s) for s in _ocr_cells(bgr, lay, lay.exp) if s}
    pcts = {int(s) / 100 for s in _ocr_cells(bgr, lay, _pct_cells(lay))
            if 3 <= len(s) <= 5}
    return [(e, p) for e in sorted(exps) for p in sorted(pcts)]


def build_templates(samples, scales=(1.0, 0.75, 0.55, 0.4, 0.3), path=TEMPLATE_PATH):
    """Make digits.npz from screenshots of the bar: [(bgr, "38,849", "61.02"), ...].

    Each screenshot is also shrunk to several sizes so small windows are covered too.
    """
    examples = {}
    for bgr, exp_s, pct_s in samples:
        for f in scales:
            img = bgr if f == 1.0 else cv2.resize(bgr, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            lay = find_exp(img)
            if not lay:
                continue
            exp_cells, pct_cells = lay.exp, _pct_cells(lay)
            exp_t, pct_t = re.sub(r"\D", "", exp_s), re.sub(r"\D", "", pct_s)
            if len(exp_cells) != len(exp_t) or len(pct_cells) != len(pct_t):
                continue
            for cell, ch in zip(exp_cells + pct_cells, exp_t + pct_t):
                examples.setdefault(ch, []).append(digit_patch(lay.V, cell[0], lay.cy, lay.gh, 0))
    np.savez(path, canon=CANON, **{f"d{k}": np.stack(v) for k, v in examples.items()})
    return {k: len(v) for k, v in sorted(examples.items())}


# --------------------------------------------------------------------------- map name and level
#
# The minimap's light-blue info box shows the region and, under it, the map name in white
# text. It's read with Tesseract a few different ways, and the most common answer wins.
# The level comes from the EXP table: each level needs a different amount of EXP.

def level_for_req(req):
    """The level whose EXP requirement matches req, from the Classic World EXP table."""
    if not req:
        return None
    for level, need in enumerate(EXP_TABLE, start=1):
        if abs(req - need) <= 0.005 * need:
            return level
    return None


def levels_for(exp, pct):
    """The levels whose EXP requirement fits exp at pct% (% is shown with 2 decimals)."""
    req, err = exp / (pct / 100), 0.002 + 0.01 / pct
    return [level for level, need in enumerate(EXP_TABLE, start=1)
            if abs(req - need) <= err * need and exp < need]


def fits_exp_table(exp, pct):
    """True if exp at pct% matches some level's EXP requirement. A misread almost never does
    (e.g. '1 [71.68%]' for '68,601 [71.68%]'), so a reading that doesn't isn't trusted."""
    return pct < 0.2 or bool(levels_for(exp, pct))      # under 0.2%: too little EXP to tell


# Job names as the status bar shows them (old-style, as in Classic World). A reading is
# matched to the closest of these; a clear reading that matches none is kept as read.
JOBS = ["Beginner",
        "Swordman", "Fighter", "Crusader", "Hero", "Page", "White Knight", "Paladin",
        "Spearman", "Dragon Knight", "Dark Knight",
        "Magician", "Wizard (Fire,Poison)", "Mage (Fire,Poison)", "Arch Mage (Fire,Poison)",
        "Wizard (Ice,Lightning)", "Mage (Ice,Lightning)", "Arch Mage (Ice,Lightning)",
        "Cleric", "Priest", "Bishop",
        "Archer", "Hunter", "Ranger", "Bowmaster", "Crossbowman", "Sniper", "Marksman",
        "Rogue", "Assassin", "Hermit", "Night Lord", "Bandit", "Chief Bandit", "Shadower",
        "Pirate", "Brawler", "Marauder", "Buccaneer", "Gunslinger", "Outlaw", "Corsair",
        "Warrior", "Bowman", "Thief"]


def _clean_job(text):
    t = re.sub(r"[^A-Za-z(),/ ]", "", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) >= 3 else ""


def read_job(bgr, x0, cy, g):
    """The job name in the status bar (e.g. 'Bandit'), from a shot where the EXP number's
    first digit is at x0 and its middle at cy, or None if it can't be read clearly.

    The job sits on the line of the HP bar's label ("HP"), right before it; the character
    name is on the line under it and is never read."""
    reg = bgr[max(0, int(cy - 2.5 * g)):int(cy + 3 * g), max(0, int(x0 - 64 * g)):max(0, int(x0 - 20 * g))]
    if reg.size == 0:
        return None
    white = _whiteness(reg)
    _, blobs = _blobs(white > 0.3)                    # thin letters: a gentle white check
    # The HP bar: 13-16 EXP digits wide, its left end 31-35 of them before the EXP number
    # (the region starts 64 before it). Not to be mixed up with the panels' light borders.
    bars = [b for b in blobs if 9 * g <= b.w <= 20 * g and 1.4 * g <= b.h <= 2.8 * g
            and 24 * g <= b.x <= 39 * g]
    if not bars:
        return None
    hp = min(bars, key=lambda b: b.x)                 # the HP bar (the MP bar is right of it)
    label = [b for b in blobs if hp.x - 0.5 * g <= b.x <= hp.x + 3 * g
             and b.y + b.h <= hp.y + 0.2 * g and 0.6 * g <= b.h <= 1.6 * g]
    if not label:
        return None
    top, bottom = min(b.y for b in label), max(b.y + b.h for b in label)
    row = sorted((b for b in blobs if b.x + b.w <= hp.x - g and b.y >= top - 0.3 * g
                  and b.y + b.h <= bottom + 0.4 * g and b.h >= 0.3 * g), key=lambda b: b.x)
    if len(row) < 2:
        return None
    words = [[row[0]]]
    for b in row[1:]:
        if b.x - max(c.x + c.w for c in words[-1]) > 2.0 * g:
            words.append([b])
        else:
            words[-1].append(b)
    word = words[-1]                                  # the letters right before "HP"
    x_a, x_b = max(0, min(b.x for b in word) - int(0.4 * g)), max(b.x + b.w for b in word) + int(0.4 * g)
    crop = white[max(0, top - int(0.4 * g)):bottom + int(0.4 * g), x_a:x_b]
    glyph_h = max(b.h for b in word)
    if glyph_h > 10:                                  # big screens: read at small-window size,
        k = 10.0 / glyph_h                            # where Tesseract reads this font best
        crop, glyph_h = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_AREA), 10.0
    votes = _ocr_votes(crop.astype(np.float32), glyph_h, "--psm 7", _clean_job)
    text, n = votes.most_common(1)[0] if votes else ("", 0)
    if n < 2:
        return None
    score, best = max((SequenceMatcher(None, text.lower(), j.lower()).ratio(), j) for j in JOBS)
    if score >= 0.72:
        return best
    return text.title() if n >= 3 else None


def _whiteness(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    return np.clip((hsv[..., 2] - 150) / 90, 0, 1) * np.clip((80 - hsv[..., 1]) / 50, 0, 1)


def _ocr_votes(whiteness, glyph_h, config, clean):
    """Tesseract on white text at a few sizes, with and without smoothing. Counts answers."""
    votes = Counter()
    for target in (36, 48, 60):
        s = target / glyph_h
        big = cv2.resize(whiteness, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
        for img in ((big > 0.5) * 255.0, np.clip(big, 0, 1) * 255):
            img = cv2.copyMakeBorder((255 - img).astype(np.uint8), 24, 24, 24, 24,
                                     cv2.BORDER_CONSTANT, value=255)
            text = clean(pytesseract.image_to_string(img, config=config))
            if text:
                votes[text] += 1
    return votes


def find_map_box(bgr, gh):
    """(x, y, w, h) of the minimap's info box in a shot of the window's top-left, or None."""
    hue, sat, val = [c.astype(np.int16) for c in cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV))]
    box = (abs(hue - 100) <= 8) & (abs(sat - 64) <= 30) & (abs(val - 204) <= 30)
    n, _, stats, _ = cv2.connectedComponentsWithStats(box.astype(np.uint8), connectivity=8)
    if n < 2:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = map(int, stats[i][:4])
    h = min(h, int(round(7.3 * gh)))            # just the info box, not the whole minimap
    return (x, y, w, h) if w >= 10 * gh and h >= 4 * gh else None


def _clean_map_name(text):
    t = text.replace("|", "I")                         # a lone "I" (as in "... I") reads as "|"
    t = re.sub(r"(?<=\s)[!\[\]](?=\s*$)", "I", t)
    t = re.sub(r"[^A-Za-z0-9 '<>:.&()\-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .:-")
    t = re.sub(r"(?<= )[Il1]{3}$", "III", t)         # Roman numerals read as l's and 1's
    t = re.sub(r"(?<= )[Il1]{2}$", "II", t)
    t = re.sub(r"(?<= )l$", "I", t)
    return t if len(t) >= 3 else ""


def read_map_name(bgr, gh):
    """The map name (the lower line of white text) from a shot of the minimap's info box."""
    white = _whiteness(bgr[:, int(0.85 * bgr.shape[0]):])      # skip the map's icon
    _, blobs = _blobs(white > 0.5)
    glyphs = sorted((b for b in blobs if 0.3 * gh <= b.h <= 1.6 * gh and b.w <= 2.5 * gh),
                    key=lambda b: b.y + b.h / 2)
    lines = []
    for b in glyphs:
        last = lines[-1][-1] if lines else None
        if last and abs(b.y + b.h / 2 - (last.y + last.h / 2)) <= 0.5 * gh:
            lines[-1].append(b)
        else:
            lines.append([b])
    lines = [ln for ln in lines if len(ln) >= 3]
    if not lines:
        return None
    ln = lines[-1]
    x0, x1 = max(0, min(b.x for b in ln) - 2), max(b.x + b.w for b in ln) + 2
    y0, y1 = max(0, min(b.y for b in ln) - 2), max(b.y + b.h for b in ln) + 2
    votes = _ocr_votes(white[y0:y1, x0:x1], max(b.h for b in ln), "--psm 7", _clean_map_name)
    name, n = votes.most_common(1)[0] if votes else (None, 0)
    if name and _looks_like_inventory_tabs(name):
        return None             # fork: with the minimap collapsed, the inventory's tabs can be read
    return name if n >= 2 else None


_INVENTORY_TABS = {"equip", "use", "setup", "set", "up", "etc", "cash", "deco"}


def _looks_like_inventory_tabs(name):
    """'Use I Set Up Cash Deco': the item inventory's tab row, not a map name."""
    words = [w.strip(".:'").lower() for w in name.replace("Up", " Up").split()]
    hits = sum(1 for w in words if w in _INVENTORY_TABS or w.replace("up", "") in ("set", "") and w)
    return hits >= 2


# --------------------------------------------------------------------------- mesos and party
#
# The inventory's mesos box (white, gold coin on its left) is found while it's solid, then
# read where it is, even while the window is see-through during fights: the digits stay
# about as much darker than the box as when it's solid. The digits are a different font
# from the EXP bar's, so examples of them are learned from your own screen
# (mesos_digits.npz), each checked by Tesseract first. Until all ten are learned the EXP
# font's digits are used, which match these closely enough.
# Party: the small party list shows one HP bar per member, in an evenly spaced column.

def find_mesos_field(bgr, gh=None):
    """(x, y, w, h) of the solid white mesos box (gold coin on its left), or None.

    With gh (the EXP digit height), the box must also be the mesos box's size at that scale:
    about 19.1 x 1.82 EXP digits."""
    hue, sat, val = cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV))
    n, _, stats, _ = cv2.connectedComponentsWithStats(((val > 225) & (sat < 30)).astype(np.uint8),
                                                      connectivity=8)
    coin = (hue >= 12) & (hue <= 35) & (sat >= 100) & (val >= 140)
    best = None
    for i in range(1, n):
        x, y, w, h, area = map(int, stats[i])
        if h < 8 or not 7 * h <= w <= 16 * h or area < 0.6 * w * h:
            continue
        if gh and not (16.2 * gh <= w <= 22 * gh and 1.45 * gh <= h <= 2.2 * gh):
            continue
        left = coin[y:y + h, max(0, x - int(1.6 * h)):x]
        if left.size == 0 or left.mean() < 0.15:
            continue
        if best is None or area > best[4]:
            best = (x, y, w, h, area)
    return best[:4] if best else None


def mesos_layout(bgr, field):
    """Digit cells of the number in the mesos box, or None if they can't be picked out cleanly.

    Works on how much darker each pixel is than the box right around it, so a see-through
    box over a changing background still works. Anything doubtful returns None: a wrong
    number is worse than waiting for a clean look.
    """
    x, y, w, h = field
    inner = bgr[y + 2:y + h - 2, x + 2:x + w - 2]
    if inner.size == 0:
        return None
    V = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)[..., 2].astype(np.float32)
    dh0 = 0.61 * h                                   # digits are ~61% as tall as the box
    k = max(3, int(round(0.35 * dh0)) | 1)
    strokes = cv2.morphologyEx(V, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8)) - V
    peak = float(np.percentile(strokes, 99.7))
    if peak < 40:
        return None
    band = np.zeros(strokes.shape, bool)             # the row the digits sit on
    mid = inner.shape[0] / 2
    b0, b1 = max(0, int(mid - 0.65 * dh0)), int(mid + 0.65 * dh0)
    band[b0:b1] = strokes[b0:b1] > 0.4 * peak
    c = max(1, int(round(0.06 * dh0)))
    band = cv2.morphologyEx(band.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((c, c), np.uint8)) > 0
    _, blobs = _blobs(band)
    digits = sorted((b for b in blobs if 0.6 * dh0 <= b.h <= 1.25 * dh0), key=lambda b: -b.x)
    if not digits or inner.shape[1] - (digits[0].x + digits[0].w) > 0.8 * dh0:
        return None                                  # the number is right-aligned
    run, edge = [], None
    for b in digits:
        if edge is not None and edge - (b.x + b.w) > 0.9 * dh0:
            break
        run.append(b)
        edge = b.x if edge is None else min(edge, b.x)
    run.reverse()
    dh = float(np.median([b.h for b in run]))
    pitch = 0.74 * dh
    cells = []
    for b in run:
        n = max(1, int(round((b.w + 0.12 * dh) / pitch)))
        cells.extend((b.x + (j + 0.5) * b.w / n, b.w / n, "digit") for j in range(n))
    # Nothing digit-like may be left over to the left of the number (a dropped digit).
    left = band[:, :max(0, int(cells[0][0] - cells[0][1] / 2 - 0.25 * dh))]
    if left.sum() > 0.25 * band[:, int(cells[-1][0] - cells[-1][1] / 2):].sum():
        return None
    # A comma after every 3 digits from the right makes that gap wider; anything else is off.
    centers = [c[0] for c in cells][::-1]
    for i in range(len(centers) - 1):
        want = 1.11 * dh if (i + 1) % 3 == 0 else 0.74 * dh
        if abs(centers[i] - centers[i + 1] - want) > 0.2 * dh:
            return None
    cy = float(np.median([b.y + b.h / 2 for b in run]))
    return Layout(dh, cy, cells, [], (0, 0, 0, 0), strokes)


# The game right-aligns the number: digit centres sit at these distances from the box's right
# edge, in box heights: first digit, each further digit, and extra room for every comma.
MESOS_FIRST, MESOS_PITCH, MESOS_COMMA = 0.36, 0.447, 0.30


def _mesos_spot(field, i):
    """x of the i-th digit from the right in the mesos box (i = -1: just right of them)."""
    x, y, w, h = field
    return x + w - (MESOS_FIRST + i * MESOS_PITCH + (max(i, 0) // 3) * MESOS_COMMA) * h


def _mesos_strokes(bgr, field):
    """A Layout for matching digits in the mesos box: how much darker each pixel is than
    the box around it (works solid or see-through)."""
    x, y, w, h = field
    V = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[..., 2].astype(np.float32)
    k = max(3, int(round(0.35 * 0.615 * h)) | 1)
    strokes = cv2.morphologyEx(V, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8)) - V
    return Layout(0.615 * h, y + 0.525 * h, [], [], (0, 0, 0, 0), strokes)


def mesos_box_lines_up(bgr, field, templates):
    """For a box found by its outline: does its right edge line up with the number? The
    first digit must match very clearly (in the wrong place the box's border can pass for
    a '1'), and just right of it there must be no digit (else the box is a digit too far
    left and the number would be read short)."""
    lay, h = _mesos_strokes(bgr, field), field[3]
    _, first, _ = templates.match(lay, (_mesos_spot(field, 0), 0.4 * h, "digit"))
    _, after, _ = templates.match(lay, (_mesos_spot(field, -1), 0.4 * h, "digit"))
    return first >= 0.85 and after < 0.84


def read_mesos_positions(bgr, field, templates, digit_count=None):
    """Reads the mesos box digit by digit at the spots the game draws them.

    Nothing has to be separated from the background first, so it also works when the
    inventory is see-through over a busy background. Every spot must clearly be a digit
    or clearly not one, and once the number ends none of the next few spots to its left
    may be a clear digit, so a digit lost in a busy background can't silently shorten the
    number from the middle. If digit_count is given, the number must have exactly that many digits (which
    also catches a lost first digit). Returns (number, Layout, weakest score, weakest lead),
    or None.
    """
    x, y, w, h = field
    lay = _mesos_strokes(bgr, field)
    digits, cells, scores, leads, ended = "", [], [], [], 0
    for i in range(16):
        cx = _mesos_spot(field, i)
        if cx - 0.3 * h < x:
            break
        digit, score, lead = templates.match(lay, (cx, 0.4 * h, "digit"))
        empty = bool(digits) and score < 0.65
        if ended:
            # A clear digit after the end means one was missed in between. (Only a clear
            # one: a see-through box's background often looks a little digit-like.)
            if score >= 0.75 and lead >= 0.05:
                return None
            ended += 1
            if ended > 3:
                break
        elif score >= 0.75 and lead >= 0.05:          # clearly a digit
            digits, cells = digit + digits, [(cx, 0.4 * h, "digit")] + cells
            scores.append(score)
            leads.append(lead)
        elif empty:
            ended = 1                                 # the number ends here
        else:
            return None                               # unclear: don't guess
    if not digits or (len(digits) > 1 and digits[0] == "0"):
        return None
    if scores[-1] < 0.85:
        return None          # the first digit must be very clear: a see-through box's background
                             # can pass for an extra '1' in front (real first digits score ~0.95)
    if digit_count and len(digits) != digit_count:
        return None
    return int(digits), lay._replace(exp=cells), min(scores), min(leads)


def read_mesos_digits(lay, templates):
    """The number in the mesos box, or None if any digit is unclear."""
    s = ""
    for cell in lay.exp:
        digit, score, lead = templates.match(lay, cell)
        if score < 0.70 or lead < 0.04:
            return None
        s += digit
    return int(s)


def tesseract_mesos(lay):
    """(number, how many of 6 Tesseract tries agreed) for the mesos box, or (None, 0)."""
    if not tesseract_ok:
        return None, 0
    first, last = lay.exp[0], lay.exp[-1]
    x0, x1 = max(0, int(first[0] - first[1] / 2 - 0.5 * lay.gh)), int(last[0] + last[1] / 2 + 0.5 * lay.gh)
    y0, y1 = max(0, int(lay.cy - 0.8 * lay.gh)), int(lay.cy + 0.8 * lay.gh)
    img = np.clip(lay.V[y0:y1, x0:x1] / max(1.0, float(np.percentile(lay.V, 99.7))), 0, 1)

    def clean(text):
        return re.sub(r"\D", "", text) if re.fullmatch(r"[\d,.\s]+", text.strip() or "x") else ""

    votes = _ocr_votes(img.astype(np.float32), lay.gh,
                       "--psm 7 -c tessedit_char_whitelist=0123456789,", clean)
    digits, n = votes.most_common(1)[0] if votes else ("", 0)
    return (int(digits), n) if n >= 4 else (None, n)


class MesosDigits:
    """Examples of the mesos box's digits, learned from your own screen as you play."""

    PER_DIGIT = 12

    def __init__(self, path=MESOS_PATH, start=MESOS_START_PATH):
        self.path = path
        self.examples = {}
        self._templates = None
        for source in (path, start):                 # what you've learned, else what comes with it
            try:
                data = np.load(source)
                if int(data["canon"]) == CANON:
                    self.examples = {k[1:]: list(data[k]) for k in data.files if k.startswith("d")}
                    break
            except Exception:
                continue

    @property
    def complete(self):
        return all(str(d) in self.examples for d in range(10))

    def templates(self):
        if self._templates is None and self.examples:
            self._templates = DigitTemplates({d: np.stack(v) for d, v in self.examples.items()})
        return self._templates

    def learn(self, lay, value):
        digits = str(value)
        if len(lay.exp) != len(digits):
            return
        added = False
        for cell, ch in zip(lay.exp, digits):
            examples = self.examples.setdefault(ch, [])
            if len(examples) < self.PER_DIGIT:
                examples.append(digit_patch(lay.V, cell[0], lay.cy, lay.gh, 0))
                added = True
        if added:
            self._templates = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                np.savez(self.path, canon=CANON, **{f"d{k}": np.stack(v) for k, v in self.examples.items()})
            except Exception as e:
                log_error("saving the learned mesos digits", e)


def _edges(bgr):
    v = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[..., 2].astype(np.float32)
    return cv2.magnitude(cv2.Sobel(v, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(v, cv2.CV_32F, 0, 1, ksize=3))


class MesosBoxFinder:
    """Finds the mesos box even while the inventory is see-through.

    It looks for the outline of the whole row (gold coin, box, MESOS label) at the size the
    UI is drawn at, which stays visible when the window is see-through. The outline is kept
    in mesos_box.npz. Work is done at a small scale (EXP digits 12 px tall) to be quick.
    """

    GH = 12.0

    def __init__(self, path=MESOS_BOX_PATH):
        self.edges = None
        try:
            data = np.load(path)
            self.edges, self.mask = data["edges"], data["mask"]
            self.ox, self.oy, self.bw, self.bh = map(float, data["box"])
        except Exception:
            pass

    @classmethod
    def build(cls, bgr, field, gh, path=MESOS_BOX_PATH):
        """Make mesos_box.npz from a screenshot showing the solid box at `field`."""
        x, y, w, h = field
        x0, x1 = int(x - 1.45 * h), int(x + w + 2.7 * h)
        y0, y1 = int(y - 0.35 * h), int(y + 1.35 * h)
        s = cls.GH / gh
        edges = cv2.resize(_edges(bgr[y0:y1, x0:x1]), None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        mask = np.ones(edges.shape, np.float32)       # leave out the inside of the box
        mask[int((y - y0 + 0.12 * h) * s):int((y - y0 + 0.88 * h) * s),
             int((x - x0 + 0.12 * h) * s):int((x - x0 + w - 0.12 * h) * s)] = 0
        np.savez(path, edges=edges, mask=mask, box=np.float32([(x - x0) * s, (y - y0) * s, w * s, h * s]))

    def find(self, bgr, gh):
        """(x, y, w, h) of the best-matching mesos box in a screenshot, or None.

        Compared with the EXP digits the box is drawn bigger in small game windows (in real
        screenshots: x1.12 at EXP digits 7 px tall, x1.07 at 11 px, x1.0 at 28 px), so sizes
        around that are tried and the clearest wins. Its left and right edges are then
        lined up with the box's border lines, because the digits are read at fixed
        distances from the right edge."""
        if self.edges is None:
            return None
        edges0 = _edges(bgr)
        expect = 0.961 + 1.092 / gh
        found = None
        for size in (expect, expect * 1.025, expect / 1.025):
            s = self.GH / (gh * size)
            edges = cv2.resize(edges0, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            if edges.shape[0] < self.edges.shape[0] or edges.shape[1] < self.edges.shape[1]:
                continue
            r = np.nan_to_num(cv2.matchTemplate(edges, self.edges, cv2.TM_CCORR_NORMED, mask=self.mask),
                              nan=0.0, posinf=0.0, neginf=0.0)
            r[np.abs(r) > 1.0] = 0.0                   # flat areas (e.g. all black) give junk values
            _, best, _, (px, py) = cv2.minMaxLoc(r)
            r[max(0, py - 3):py + 4, max(0, px - 12):px + 13] = 0
            _, second, _, _ = cv2.minMaxLoc(r)
            enough = 0.72 if gh >= 10 else 0.64       # tiny windows match less sharply
            if best >= enough and best - second >= 0.05 and (found is None or best > found[0]):
                found = (best, (int((px + self.ox) / s), int((py + self.oy) / s),
                                int(self.bw / s), int(self.bh / s)))
        if not found:
            return None                                # not clearly there
        x, y, w, h = found[1]
        # Line up the right edge (the digits are read from there) with the inside of the
        # box's border: leaving the box it gets darker, solid or see-through.
        gx = cv2.Sobel(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[..., 2].astype(np.float32),
                       cv2.CV_32F, 1, 0, ksize=3)
        rows = gx[max(0, y + h // 4):y + 3 * h // 4 + 1].sum(axis=0)
        reach = max(2, int(0.25 * h))
        lo, hi = max(0, x + w - reach), min(len(rows), x + w + reach + 1)
        right = lo + int(np.argmin(rows[lo:hi])) if hi - lo >= 3 else x + w
        return right - w, y, w, h


def _agrees(value, last):
    """Two mesos readings that could both be right: same number of digits and close together."""
    return last is not None and len(str(value)) == len(str(last)) and abs(value - last) <= max(1000, 0.02 * last)


def _second_mesos_box(bgr, field, gh):
    """Fork: is there another solid mesos box besides `field` (storage or trade window)?"""
    x, y, w, h = field
    other = bgr.copy()
    other[y:y + h, x:x + w] = 0
    return find_mesos_field(other, gh) is not None


def find_party_bars(bgr, gh):
    """HP bars of the party list's members: the longest evenly spaced column of identical
    HP-bar outlines. A single bar (or none) means you're on your own."""
    val = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[..., 2]
    n, _, stats, _ = cv2.connectedComponentsWithStats((val < 70).astype(np.uint8), connectivity=8)
    bars = sorted((tuple(map(int, stats[i][:4])) for i in range(1, n)
                   if 7.5 * gh <= stats[i][2] <= 10.5 * gh and 1.3 * gh <= stats[i][3] <= 1.85 * gh),
                  key=lambda b: b[1])
    best = []
    for i, first in enumerate(bars):
        same = [b for b in bars[i:] if abs(b[0] - first[0]) <= 0.5 * gh
                and abs(b[2] - first[2]) <= 0.5 * gh and abs(b[3] - first[3]) <= 0.3 * gh]
        column, pitch = [first], None
        for b in same[1:]:
            step = b[1] - column[-1][1]
            if pitch is None and 1.8 * gh <= step <= 3.0 * gh:
                pitch = step
            if pitch is None or abs(step - pitch) > 0.3 * gh:
                break
            column.append(b)
        if len(column) > len(best):
            best = column
    return best if len(best) >= 2 else best[:1]


# --------------------------------------------------------------------------- finding it on screen

Reading = namedtuple("Reading", "where candidates confident text image boxes map note "
                                 "mesos mesos_image mesos_boxes party mesos_at gauges",
                     defaults=((), False, "", None, (), None, None, None, None, (), None, None, None))


def _view(img, lay):
    """Crop around the EXP text and the digit boxes in it, for View capture."""
    g = lay.gh
    x0, x1 = max(0, int(lay.box[0] - 2 * g)), min(img.shape[1], int(lay.box[2] + g))
    y0, y1 = max(0, int(lay.cy - 1.2 * g)), min(img.shape[0], int(lay.cy + 1.2 * g))
    boxes = [(c[0] - c[1] / 2 - x0, lay.cy - g / 2 - y0, c[0] + c[1] / 2 - x0, lay.cy + g / 2 - y0, kind)
             for kind, cells in (("exp", lay.exp), ("pct", _pct_cells(lay))) for c in cells]
    return img[y0:y1, x0:x1].copy(), boxes


def _problem(where, text):
    """A status message about reading the EXP bar ("where" only matters for a selected area)."""
    return f"Selected area: {text}" if where == "Selected area" else text


class ScreenReader:
    """Finds the EXP text on screen and reads it, following the game window around.

    It also watches the minimap so the map name is read again whenever it changes, reads
    the inventory's mesos and, every few seconds, counts the members in the party list.
    """

    def __init__(self, templates, mode="window", region=None):
        self.templates = templates
        self.mode, self.region = mode, region
        self.hwnd = None
        self.size = None
        self.box = None             # where the EXP text is, relative to the game window
        self.misses = 0
        self.last_full_search = 0.0
        self.gh = None              # EXP digit height: how big the game's UI is drawn
        self.map_box = None         # the minimap's info box, relative to the game window
        self.map_print = None       # rough picture of its text, to notice map changes
        self.map_unread = False
        self.next_map_search = 0.0
        self.next_map_read = 0.0
        self.mesos_box = None       # the inventory's mesos box, relative to the game window
        self.saved_mesos = None     # (window w, h, x, y, w, h) remembered from last time
        self.box_finder = MesosBoxFinder()
        self.mesos_digits = MesosDigits()
        self.mesos_checked = None   # last mesos number Tesseract confirmed
        self.mesos_count = None     # how many digits the mesos number has
        self.mesos_pending = (None, 0)
        self.mesos_fails = 0
        self.next_mesos_check = 0.0
        self.mesos_covered = False  # this app's window is in the way of the mesos
        self.party_area = None      # where the party list was last seen, on screen
        self.next_scan = 0.0
        self.own_rect = None        # this app's window, which screenshots can't see through
        self.look_again_requested = False
        self.exp_anchor = None      # where the EXP number starts, relative to the game window
        self.job = None             # the class shown in the status bar
        self.next_job_read = 0.0
        self.read_hpmp = True       # fork: HP/MP from the status line
        self.gauge_confirm = GaugeConfirm()

    def use_window(self):
        self.mode, self.hwnd, self.box = "window", None, None

    def use_region(self, region):
        self.mode, self.region = "region", region

    def request_map(self):
        """Read the map name on the next look, even if it doesn't seem to have changed."""
        self.next_map_search, self.next_map_read, self.map_unread = 0.0, 0.0, True

    def look_again(self):
        """Forget where everything was (EXP bar, minimap, inventory, party list) and look
        for it all again. Done by the reading thread itself, at its next look."""
        self.look_again_requested = True

    def _forget_places(self):
        self.box, self.misses = None, 0
        self.map_box, self.map_print = None, None
        self.mesos_box = self.saved_mesos = None
        self.mesos_fails, self.mesos_count, self.mesos_pending = 0, None, (None, 0)
        self.mesos_covered, self.party_area, self.next_scan = False, None, 0.0
        self.exp_anchor, self.next_job_read = None, 0.0
        self.request_map()

    def read(self, sct):
        if self.look_again_requested:
            self.look_again_requested = False
            self._forget_places()
        rect = self._game_rect()
        if self.mode == "region":
            reading = self._read_region(sct, rect)
        elif not self.hwnd:
            return Reading("MapleStory window not found. Is the game running?")
        elif not rect:
            return Reading("MapleStory is minimized. Bring the game back up.")
        else:
            reading = self._read_window(sct, rect)
        if rect and self.gh:
            name, note = self._read_info(sct, rect)
            reading = self._read_extras(sct, rect, reading._replace(map=name, note=note))
            if self.exp_anchor and self.mode == "window":
                self._read_job(sct, rect)
                if self.read_hpmp and self.templates:
                    reading = reading._replace(gauges=read_status_gauges(
                        sct, rect, self.exp_anchor, self.gh, self.templates, self.gauge_confirm,
                        self._covered, grab))
        return reading

    def _read_job(self, sct, rect):
        """Reads the class from the status bar now and then (it only changes at a job advance)."""
        now = time.monotonic()
        if not tesseract_ok or now < self.next_job_read:
            return
        wx, wy, _, _ = rect
        ax, ay = self.exp_anchor
        g = self.gh
        x0, y0, w, h = wx + int(ax - 65 * g), wy + int(ay - 3 * g), int(46 * g), int(6.5 * g)
        if self._covered(x0, y0, w, h):
            self.next_job_read = now + 5
            return
        shot = grab(sct, x0, y0, w, h)
        job = read_job(shot[0], wx + ax - shot[1], wy + ay - shot[2], g) if shot else None
        if job:
            self.job = job
        self.next_job_read = now + (60 if job else 10)

    def _game_rect(self):
        if not (self.hwnd and _IsWindow(self.hwnd)):
            self.hwnd, self.box, self.map_box = find_game_window(), None, None
        rect = client_rect(self.hwnd) if self.hwnd else None
        if rect and rect[2:] != self.size:                  # resized: find everything again
            self.size, self.box, self.map_box, self.gh = rect[2:], None, None, None
            self.exp_anchor, self.next_job_read = None, 0.0
            self.mesos_box, self.party_area, self.next_scan = None, None, 0.0
            saved = self.saved_mesos
            if saved and tuple(saved[:2]) == tuple(rect[2:]):
                self.mesos_box = tuple(saved[2:])           # where it was last time
        return rect

    def _hide_own(self, shot):
        """The screenshot with this app's window blacked out, so it can never read itself."""
        r = self.own_rect
        if not shot or not r:
            return shot
        img, sx, sy = shot
        x0, y0 = max(0, r[0] - sx), max(0, r[1] - sy)
        x1, y1 = min(img.shape[1], r[0] + r[2] - sx), min(img.shape[0], r[1] + r[3] - sy)
        if x0 < x1 and y0 < y1:
            img = img.copy()
            img[y0:y1, x0:x1] = 0
        return img, sx, sy

    def _covered(self, x, y, w, h, margin=0):
        """Does this app's window overlap (or come within margin px of) the given area?"""
        r = self.own_rect
        return bool(r) and (x - margin < r[0] + r[2] and r[0] < x + w + margin
                            and y - margin < r[1] + r[3] and r[1] < y + h + margin)

    def _remember(self, lay, rect, shot=None):
        if lay and rect:
            self.gh = lay.gh                # tells the minimap reader how big the UI is
            if shot:                        # where the EXP number starts: the class is left of it
                self.exp_anchor = (shot[1] - rect[0] + lay.box[0], shot[2] - rect[1] + lay.cy)

    def _read_region(self, sct, rect):
        r = self.region
        if not r:
            return Reading("No area selected. Click Select area.")
        shot = self._hide_own(grab(sct, r["left"], r["top"], r["width"], r["height"]))
        reading, lay = self._read(shot, "Selected area")
        if reading.confident:
            self._remember(lay, rect)
        return reading

    def _read_window(self, sct, rect):
        wx, wy, ww, wh = rect
        where = f"MapleStory window {ww}x{wh}"
        if self.box:
            bx, by, bw, bh = self.box
            if self._covered(wx + bx, wy + by, bw, bh):
                return Reading("This window is covering the EXP bar. Move it.")
            shot = grab(sct, wx + bx, wy + by, bw, bh)
            reading, lay = self._read(shot, where)
            if reading.confident:
                self.misses = 0
                self._remember(lay, rect, shot)
                return reading
            # Not reading cleanly here (a dialog in the way, or this was the wrong spot):
            # after a few tries, look for the EXP text again.
            self.misses += 1
            if self.misses < 3:
                return reading
            self.box = None

        # Search the bottom of the window (and now and then the whole window).
        top = int(wh * (1 - SEARCH_STRIP))
        shot = self._hide_own(grab(sct, wx, wy + top, ww, wh - top))
        lay = find_exp(shot[0]) if shot else None
        if not lay and time.monotonic() - self.last_full_search > 10:
            self.last_full_search = time.monotonic()
            shot = self._hide_own(grab(sct, wx, wy, ww, wh))
            lay = find_exp(shot[0]) if shot else None
        if not lay:
            if self._covered(wx, wy + top, ww, wh - top):
                return Reading("Can't find the EXP bar. Is this window covering it?")
            return Reading("Can't find the EXP bar at the bottom of the game. Is something covering it?")
        img, sx, sy = shot
        g = lay.gh
        x0, x1 = max(0, int(lay.box[0] - g)), min(img.shape[1], int(lay.box[2] + 6 * g))
        y0, y1 = max(0, int(lay.cy - 1.4 * g)), min(img.shape[0], int(lay.cy + 1.4 * g))
        reading, lay = self._read(shot, where, lay)
        if reading.confident:             # only remember a spot whose digits match the game font
            self.box = (sx - wx + x0, sy - wy + y0, x1 - x0, y1 - y0)
            self.misses = 0
            self._remember(lay, rect, shot)
        return reading

    def _read(self, shot, where, lay=None):
        """Returns (Reading, Layout or None)."""
        if not shot:
            return Reading(_problem(where, "The EXP bar is off screen.")), None
        img = shot[0]
        lay = lay or find_exp(img)
        if not lay:
            return Reading(_problem(where, "Can't find the EXP bar.")), None
        view, boxes = _view(img, lay)
        got = read_layout(self.templates, lay) if self.templates else None
        if got and fits_exp_table(*got):
            return Reading(where, [got], True, f"{got[0]:,} [{got[1]:.2f}%]", view, boxes), lay
        # Unclear digits, or a reading that fits no level: not trusted. Readings that do fit
        # a level go first, so the tracker tries those.
        cands = ([got] if got else []) + [c for c in tesseract_candidates(img, lay) if c != got]
        cands.sort(key=lambda c: not fits_exp_table(*c))
        if cands:
            e, p = cands[0]
            how = "" if (e, p) == got else " (Tesseract)"
            return Reading(where, cands, False, f"{e:,} [{p:.2f}%]{how}", view, boxes), lay
        return Reading(_problem(where, "Found the EXP bar, but can't read its numbers clearly."),
                       image=view, boxes=boxes), lay

    def _read_extras(self, sct, rect, reading):
        """Adds the mesos count and, every few seconds, the party size to a reading."""
        wx, wy, ww, wh = rect
        now, g = time.monotonic(), self.gh
        notes = [reading.note] if reading.note else []
        party = moved = None
        if now >= self.next_scan:                     # look over the whole window
            self.next_scan = now + (SCAN_SECONDS if self.mesos_box else 2)
            shot = self._hide_own(grab(sct, wx, wy, ww, wh))
            if shot:
                img, sx, sy = shot
                field = find_mesos_field(img, g)      # the solid box is easy to spot
                if field and not self._readable(img, field):
                    field = None                      # a white patch in the scene, not the box
                if field and _second_mesos_box(img, field, g):
                    # Fork: storage and trade windows show two mesos boxes (storage's and
                    # yours): don't (re)locate the box from such a look.
                    field = "skip"
                # Look for it by its outline too if it's lost, or this window is in the way of
                # where it was (it may have been moved from there).
                if field is None and (self.mesos_box is None or self.mesos_fails >= 10 or self.mesos_covered):
                    field = self._find_seethrough_box(img, g)
                if field == "skip":
                    field, self.mesos_box, self.in_storage = None, None, True
                else:
                    self.in_storage = False
                if field:
                    box = (sx - wx + field[0], sy - wy + field[1], field[2], field[3])
                    old = self.mesos_box
                    if old is None or max(abs(a - b) for a, b in zip(box, old)) > 3:
                        self.mesos_box, self.mesos_fails = box, 0
                        moved = (ww, wh) + box
                area = self.party_area
                if area and self._covered(*area):
                    notes.append("This window is covering the party list.")
                else:
                    bars = find_party_bars(img, g)
                    party = max(1, len(bars))
                    if len(bars) >= 2:
                        x0, y0 = min(b[0] for b in bars), min(b[1] for b in bars)
                        x1, y1 = max(b[0] + b[2] for b in bars), max(b[1] + b[3] for b in bars)
                        # Only the HP bars are counted, so only they (and a little room
                        # around them) must be clear of this window.
                        self.party_area = (sx + x0 - int(g), sy + y0 - int(g),
                                           x1 - x0 + int(2 * g), y1 - y0 + int(2 * g))
        mesos, image, boxes = None, None, ()
        self.mesos_covered = False
        if self.mesos_box and not getattr(self, "in_storage", False):
            bx, by, bw, bh = self.mesos_box
            # (the digits only need the box and a sliver around it to be seen)
            if self._covered(wx + bx, wy + by, bw, bh, margin=max(2, int(0.3 * bh))):
                notes.append("This window is covering the inventory's mesos.")
                self.mesos_covered = True
            else:
                mesos, image, boxes = self._read_mesos(sct, wx + bx, wy + by, bw, bh)
        return reading._replace(mesos=mesos, mesos_image=image, mesos_boxes=boxes, party=party,
                                mesos_at=moved, note=" ".join(notes) or None)

    def _readable(self, img, field):
        """Clear mesos digits at field? (Always yes while the mesos font is still being
        learned: then Tesseract checks every new number instead.)"""
        learned = self.mesos_digits
        return not learned.complete or read_mesos_positions(img, field, learned.templates()) is not None

    def _find_seethrough_box(self, img, g):
        """The mesos box by its outline, checked by reading clear digits there.

        The digits are read at fixed spots from the box's right edge, so a box found a bit
        off could read a wrong number: it must also line up with the number."""
        field = self.box_finder.find(img, g)
        if not field:
            return None
        learned = self.mesos_digits
        templates = learned.templates() if learned.complete else self.templates
        if not templates or not mesos_box_lines_up(img, field, templates):
            return None
        return field if read_mesos_positions(img, field, templates) else None

    def _read_mesos(self, sct, x, y, w, h):
        """Reads the mesos box at (x, y, w, h) on screen. Returns (number, image, boxes)."""
        shot = grab(sct, x - 2 * h, y - h, w + 4 * h, 3 * h)
        if not shot:
            return None, None, ()
        img, sx, sy = shot
        fx, fy = x - sx, y - sy
        inner = cv2.cvtColor(img[fy + 2:fy + h - 2, fx + 2:fx + w - 2], cv2.COLOR_BGR2HSV)
        solid = ((inner[..., 2] > 225) & (inner[..., 1] < 30)).mean() > 0.6

        def boxes_of(lay, ox=0, oy=0):
            return [(ox + c[0] - c[1] / 2, oy + lay.cy - lay.gh / 2,
                     ox + c[0] + c[1] / 2, oy + lay.cy + lay.gh / 2, "mesos") for c in lay.exp]

        learned = self.mesos_digits
        if learned.complete:                          # all ten mesos digits learned
            got = read_mesos_positions(img, (fx, fy, w, h), learned.templates())
            if not got:
                self.mesos_fails += 1
                return None, img, ()
            self.mesos_fails = 0
            value, lay, score, lead = got
            if not solid and len(str(value)) != self.mesos_count:
                # The first reading, or a different number of digits than before (that only
                # happens when crossing 10,000, 100,000...): on a see-through box, only
                # believe it once 3 readings in a row agree.
                last, n = self.mesos_pending
                self.mesos_pending = (value, n + 1 if _agrees(value, last) else 1)
                if self.mesos_pending[1] < 3:
                    return None, img, boxes_of(lay)
            self.mesos_count, self.mesos_pending = len(str(value)), (None, 0)
            if solid and score >= 0.95 and lead >= 0.15:
                learned.learn(lay, value)             # more examples, from very clean looks
            return value, img, boxes_of(lay)
        # Still learning the mesos font: a new number needs Tesseract's OK. On a solid box
        # Tesseract alone is enough; on a see-through box both readers must agree.
        lay = mesos_layout(img, (fx, fy, w, h))
        if not lay:
            self.mesos_fails += 1
            return None, img, ()
        self.mesos_fails = 0
        boxes = boxes_of(lay, fx + 2, fy + 2)
        value = read_mesos_digits(lay, self.templates) if self.templates else None
        if value is not None and value == self.mesos_checked:
            return value, img, boxes
        now = time.monotonic()
        if now < self.next_mesos_check or (not solid and value is None):
            return None, img, boxes
        self.next_mesos_check = now + (2 if value is not None else 5)
        checked, votes = tesseract_mesos(lay)
        if checked is None or (not solid and checked != value):
            return None, img, boxes
        self.mesos_checked = checked
        if checked == value or votes == 6:            # two readers agree: learn these digits
            learned.learn(lay, checked)
        return checked, img, boxes

    def _read_info(self, sct, rect):
        """Returns (map name if it was just read, note)."""
        if not tesseract_ok:
            return None, "Map names can't be read: Tesseract OCR is missing (see the README)."
        if self.own_rect is None:
            return None, None                   # wait until we know where this app's window is
        wx, wy, ww, wh = rect
        g, now = self.gh, time.monotonic()
        name = note = None

        if self.map_box is None and now >= self.next_map_search:
            self.next_map_search = now + 3
            shot = self._hide_own(grab(sct, wx, wy, int(ww * 0.4), int(wh * 0.3)))
            box = find_map_box(shot[0], g) if shot else None
            if box:
                self.map_box = (shot[1] - wx + box[0], shot[2] - wy + box[1], box[2], box[3])
                self.map_print, self.map_unread = None, True
        if self.map_box is None:
            if self._covered(wx, wy, int(ww * 0.4), int(wh * 0.3)):
                note = "This window may be covering the minimap. Move it so the map name can be read."
            else:
                note = "Can't see the map name. Is the minimap open?"
        else:
            bx, by, bw, bh = self.map_box
            if self._covered(wx + bx, wy + by, bw, bh, margin=max(3, int(0.1 * g))):
                # Even a box that only touches this window may be cut off by it (it was
                # found with this window blacked out). Find the whole box again (and read
                # the name) once this window is out of the way.
                note = "This window is covering the minimap. Move it so the map name can be read."
                self.map_box, self.map_unread = None, True
            else:
                shot = grab(sct, wx + bx, wy + by, bw, bh)
                if shot:
                    picture = cv2.resize(_whiteness(shot[0]), (64, 16), interpolation=cv2.INTER_AREA) > 0.3
                    if self.map_print is not None and (picture != self.map_print).mean() > 0.04:
                        # Changed map: find the box again (its size can change), then read it.
                        self.map_box, self.next_map_search = None, now + 1
                        self.map_unread, self.next_map_read = True, 0.0
                    elif self.map_unread and now >= self.next_map_read:
                        name = read_map_name(shot[0], g)
                        self.map_unread = name is None
                        self.next_map_read = now + 5          # if unreadable, try again later
                    self.map_print = picture
        return name, note


# --------------------------------------------------------------------------- tracking

class ExpTracker:
    """Turns noisy readings into trustworthy EXP gains.

    Every level needs a fixed amount of EXP (req), and the bar shows exp = pct% * req.
    req comes from the EXP table as soon as two clean readings point at the same level
    (otherwise it's learned from several *different* readings that agree with each other),
    then a reading only counts if its EXP and % agree with req. Misread digits break that,
    so they are ignored instead of being counted as gains.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.running = False
        self._accum = 0.0
        self._resumed = None
        self.exp = None                       # last trusted EXP
        self.pct = None
        self.last_read = None                 # latest reading, trusted or not (for display)
        self.req = None                       # EXP required for the current level
        self._req_exact = False               # req came straight from the EXP table
        self._table_level = None              # level the last clean reading pointed at
        self._seen = {}                       # distinct (exp, pct) readings this level -> weight
        self._pending = None                  # big change waiting for confirmation (e, p, reads)
        self._recent = deque(maxlen=40)       # (when, EXP) of the latest trusted readings
        self._reads = 0                       # readings seen, as a clock for _recent
        self._misfits = 0                     # readings in a row that didn't fit req
        self._pending_lvl = None              # level-up waiting for confirmation
        self._level_start = None              # (exp, pct) right after a level-up, unverified
        self._prev_req = None                 # previous level's req, bounds the new one
        self._before_death = None             # (reads, EXP) just before the last death
        self.deaths = 0
        self.last_loss = None                 # % of the level's EXP the last death cost
        self.gained = 0
        self.levels = 0
        self.samples = deque()                # (elapsed, gained)
        self.raw_gained = 0                   # fork: every trusted gain, paused or not (real EXP/hr)

    def start(self):
        if not self.running:
            self.running = True
            self._resumed = time.monotonic()

    def pause(self, at=None):
        """Stops the clock (as of `at`, e.g. the last EXP gain, so idle time doesn't count)."""
        if self.running:
            end = time.monotonic() if at is None else min(time.monotonic(), at)
            self._accum += max(0.0, end - self._resumed)
            self.running = False

    def new_session(self):
        """Numbers from zero again, keeping what's known about the level and current EXP."""
        self.running = False
        self._accum, self._resumed = 0.0, None
        self.gained, self.levels, self.deaths = 0, 0, 0
        self.samples = deque()

    def elapsed(self):
        if self.running:
            return self._accum + time.monotonic() - self._resumed
        return self._accum

    # ---- level requirement
    @staticmethod
    def _rel_err(pct):
        # % is shown with 2 decimals, so exp / pct is only this precise
        return 0.002 + 0.01 / pct

    def _learn_req(self, candidates, confident):
        weight = 2 if confident else 1        # font-matched readings are rarely wrong
        for e, p in candidates:
            if p >= 0.2 and e > 0:                # (a misread 0 says nothing about req)
                self._seen[(e, p)] = max(self._seen.get((e, p), 0), weight)
        while len(self._seen) > 60:
            del self._seen[next(iter(self._seen))]
        ests = [(e / (p / 100), self._rel_err(p), w) for (e, p), w in self._seen.items()]
        if self._prev_req:                    # the next level needs about as much or more
            ests = [x for x in ests if 0.98 <= x[0] / self._prev_req <= 2.5]
        group_of = lambda c, ce: [x for x in ests if abs(x[0] - c) / c <= ce + x[1]]
        weight_of = lambda g: sum(x[2] for x in g)
        center = lambda g: sum(x[0] * x[2] / x[1] ** 2 for x in g) / sum(x[2] / x[1] ** 2 for x in g)
        groups = sorted((group_of(c, ce) for c, ce, _ in ests), key=weight_of, reverse=True)
        if not groups or weight_of(groups[0]) < 3:
            return
        best = groups[0]
        mid = center(best)
        if self.req is None:
            # Lock in only once one value clearly beats any other (misreads can agree too).
            rivals = [g for g in groups if not 0.99 <= center(g) / mid <= 1.01]
            if not rivals or weight_of(best) >= 2 * weight_of(rivals[0]):
                self.req = mid
            return
        if self._req_exact:
            return                            # straight from the EXP table: misreads can't move it
        current = group_of(self.req, 0.003)
        if 0.99 <= mid / self.req <= 1.01 or weight_of(best) < 1.5 * weight_of(current) + 1:
            if current:
                self.req = center(current)
            return
        # A different value now clearly has more support: the old one came from misreads.
        # Switch, and restart the baseline so the change isn't counted as a gain.
        self.req, self._req_exact = mid, False
        self.exp = self.pct = None
        self._pending = None

    def _req_from_table(self, reading):
        """Takes the level's EXP straight from the EXP table once two clean readings in a
        row point at the same single level, so there's no wait for EXP gains."""
        e, p = reading
        levels = levels_for(e, p) if p >= 0.5 else []
        level = levels[0] if len(levels) == 1 else None
        if level and self._prev_req and EXP_TABLE[level - 1] < 0.98 * self._prev_req:
            level = None                      # after a level-up, the next level needs more
        if level and level == self._table_level:
            self.req, self._req_exact = EXP_TABLE[level - 1], True
        self._table_level = level

    def _consistent(self, e, p):
        return abs(e / self.req * 100 - p) <= 0.02 + 0.003 * p

    def _add_gain(self, amount):
        self.raw_gained += amount             # fork: farmkit counts idle-time gains too
        if self.running:
            self.gained += amount
            now = self.elapsed()
            self.samples.append((now, self.gained))
            while self.samples and now - self.samples[0][0] > ROLLING_WINDOW:
                self.samples.popleft()

    # ---- main entry
    def observe(self, candidates, confident=False):
        """Feed the (exp, pct) guesses for one screenshot. Returns (accepted, note)."""
        candidates = [(e, p) for e, p in candidates if p is not None and 0 <= p < 100]
        if not candidates:
            return False, "unreadable"
        self._reads += 1
        self.last_read = candidates[0]

        if self._check_level_up(candidates):
            return True, "LEVEL UP!"
        if self._pending_lvl:
            # Looks like a level-up, not confirmed yet: don't also take it as EXP lost.
            return False, "level up? waiting to confirm"

        if self.req is None and confident:
            self._req_from_table(candidates[0])
        self._learn_req(candidates, confident)
        if self.req is None:
            return False, "calibrating: learns your level's EXP after your next EXP gain"

        good = [c for c in candidates if self._consistent(*c)]
        if not good:
            self._misfits += 1
            if self._req_exact and self._misfits >= 30:
                # Nothing has fitted this level for a long while: the level itself may be
                # wrong. Work it out again.
                self.req, self._req_exact, self._table_level, self._misfits = None, False, None, 0
                self.exp = self.pct = self._pending = None      # and start counting afresh
            return False, "misread, ignored"
        self._misfits = 0
        e, p = good[0]
        self.last_read = good[0]

        if self._level_start:
            # First trusted reading after a level-up: fix the start value if it was misread.
            e0, p0 = self._level_start
            self._level_start = None
            if not self._consistent(e0, p0):
                fixed = round(p0 / 100 * self.req)
                self._add_gain(fixed - e0)
                self.exp = fixed

        if self.exp is None:
            self.exp, self.pct = e, p
            self._recent.append((self._reads, e))
            return True, "baseline"

        delta = e - self.exp
        if (0.01 * self.req <= -delta or (e == 0 and delta < 0)) and -delta <= 0.15 * self.req                 and not self._was_near(e):
            # EXP went down a few % of the level (or to 0): dying (it costs up to about 10%). Once a
            # second reading agrees, carry on from the new number without counting the loss,
            # so a death doesn't drag EXP/hr down (or below zero).
            last = self._pending
            n = last[2] + 1 if last and abs(e - last[0]) <= 0.005 * self.req else 1
            self._pending = (e, p, n)
            if n < 2:
                return False, "EXP went down, waiting to confirm"
            self._pending = None
            self._before_death = (self._reads, self.exp)
            self.last_loss = -delta / self.req * 100
            self.deaths += 1
            self.exp, self.pct = e, p
            self._recent.append((self._reads, e))
            return True, "died"
        if self._before_death and self._reads - self._before_death[0] <= 40 and delta > 0 \
                and e >= self._before_death[1] - 0.005 * self.req:
            # Right back to where it was before "dying", in one go: those low readings were a
            # misread, not a death (EXP lost on dying comes back only slowly, from monsters).
            self._before_death, self.deaths = None, max(0, self.deaths - 1)
            self._pending = None
            self.exp, self.pct = e, p
            self._recent.append((self._reads, e))
            return True, "baseline"
        if abs(delta) > 0.15 * self.req:
            # Unusually big change in one tick: only believe it if the next read agrees.
            last = self._pending
            n = last[2] + 1 if last and abs(e - last[0]) <= 0.15 * self.req else 1
            self._pending = (e, p, n)
            if n < 2:
                return False, "big jump, waiting to confirm"
            if delta < 0 and not self._undoes_jump(e):
                # EXP never drops this much (dying costs at most 10%; level-ups are checked
                # separately), so this is a misread, even twice in a row. If it keeps
                # showing up, carry on from it, without counting the drop.
                if n < 8:
                    return False, "misread, ignored"
                self._pending, self.exp, self.pct = None, e, p
                self._recent.append((self._reads, e))
                return True, "baseline"
        self._pending = None
        self._add_gain(delta)
        self.exp, self.pct = e, p
        self._recent.append((self._reads, e))
        return True, ""

    def _was_near(self, e):
        """Was EXP about e a moment ago? Then going down to it is the right number coming
        back after a misread that was a bit too high (and counts, to cancel that out)."""
        return any(self._reads - r <= 40 and abs(e - x) <= 0.005 * self.req for r, x in self._recent)

    def _undoes_jump(self, e):
        """Was EXP around e a moment ago? Then a big drop to it is the right number coming
        back after a misread that was too high, and must count, to cancel that out."""
        return any(self._reads - r <= 40 and abs(e - x) <= 0.15 * self.req for r, x in self._recent)

    def _check_level_up(self, candidates):
        """% dropped a lot and EXP reset low: a level-up, once readings in a row agree on it.

        If the level is known, readings that fit the next level's EXP from the table count,
        and two in a row are enough. Otherwise a reading that still fits this level's EXP is
        never a level-up (e.g. the right number coming back after a misread that was too
        high), and as the next level's EXP is only known to be bigger, it takes five.
        """
        if self.exp is None or self.pct is None or self.req is None:
            return False
        level = level_for_req(self.req)
        nxt = EXP_TABLE[level] if level and level < len(EXP_TABLE) else None
        hits = []
        for e, p in candidates:
            if not (self.pct - p > 30 and e < self.exp):
                continue
            if nxt and abs(e / nxt * 100 - p) <= 0.02 + 0.003 * p:
                hits.append((e, p, 2))        # fits the next level exactly
            elif self._consistent(e, p):
                continue                      # still this level: not a level-up
            elif p >= 0.2 and 0.98 * self.req <= e / (p / 100) <= 2.5 * self.req:
                hits.append((e, p, 5))        # next level's EXP is similar or bigger
        if not hits:
            self._pending_lvl = None
            return False
        e, p, need = hits[0]
        last = self._pending_lvl
        # Each reading must carry on from the one before (EXP only goes up from there).
        n = last[2] + 1 if last and 0 <= e - last[0] <= 0.15 * (nxt or self.req) else 1
        self._pending_lvl = (e, p, n)
        if n < need:
            return False
        self._pending_lvl = self._pending = None
        if self.running:
            self.levels += 1
        self._add_gain(self.req - self.exp + e)
        self.exp, self.pct = e, p
        self._prev_req = self.req
        if need == 2:                          # the new level's EXP is known: no relearning
            self.req, self._req_exact = nxt, True
        else:
            self.req, self._req_exact = None, False
        self._table_level = None
        self._seen.clear()
        self._recent.clear()                   # the old level's numbers mean nothing now
        self._recent.append((self._reads, e))
        self._level_start = (e, p)
        return True

    def rate(self):
        t = self.elapsed()
        return self.gained / t * 3600 if t >= 10 else None

    def rolling_rate(self):
        if len(self.samples) < 2:
            return None
        (t0, g0), (t1, g1) = self.samples[0], self.samples[-1]
        return (g1 - g0) / (t1 - t0) * 3600 if t1 - t0 >= 30 else None


class MesoTracker:
    """Turns mesos readings into mesos gained per hour and per 5 minutes.

    A new count only counts once two readings in a row agree on it (three for a big jump),
    so a one-off misread never shows up as mesos gained. A change by more than half,
    or a digit-count change bigger than 10%, starts counting afresh from the new number
    instead: that's a shop visit or a corrected misread, not mesos picked up. Small digit
    rollovers count after three identical readings.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.value = None                     # confirmed mesos count
        self.seen_at = None                   # when it was last confirmed
        self._candidate = (None, 0, 0)
        self.gained = 0
        self.samples = deque()                # (elapsed, gained)
        self.start = None                     # elapsed time when counting began
        self.counting = False

    def pause(self):
        self.counting = False                 # don't count what changes while paused

    def observe(self, value, now, elapsed, running):
        jump = False
        if value == self.value:
            self._candidate = (None, 0, 0)        # unchanged: nothing new to confirm
        else:
            # How many readings in a row back the new number: close to it, and exactly it.
            last, n, same = self._candidate
            n = n + 1 if _agrees(value, last) else 1
            same = same + 1 if value == last else 1
            self._candidate = (value, n, same)
            old = self.value
            # Measured against the bigger of the two, so a misread and its correction are
            # always judged the same way (both counted, or both not), and cancel out.
            top = max(value, old) if old is not None else 0
            digits_changed = old is not None and len(str(value)) != len(str(old))
            jump = old is not None and abs(value - old) > (0.1 if digits_changed else 0.5) * top
            big = old is not None and abs(value - old) > max(10000, 0.05 * top)
            # The first count, a digit change and a jump need the very same number 3 times.
            # First counts and jumps aren't gained, so a misread there would never cancel
            # out; digit changes can also be different, nearby misreads of an extra digit.
            # About 10x (or a tenth): almost always a digit read too many (or lost).
            tenfold = old is not None and old > 0 and (9 <= value / old <= 11 or 0.09 <= value / old <= 0.11)
            if old is None:
                # Fork: the very first count needs 3 readings in a row that agree (same digit
                # count, close together), not 3 identical ones: while farming, pickups change
                # the number between readings, so identical ones could take minutes.
                if n < 3:
                    return
            elif jump or digits_changed:
                if same < (8 if tenfold else 3):
                    return
            elif n < (3 if big else 2):
                return
            self._candidate = (None, 0, 0)
        self.seen_at = now
        if running and not self.counting:
            self.counting = True                  # count changes from this reading on
            self.start = elapsed if self.start is None else self.start
            self.samples.append((elapsed, self.gained))
        elif running and value != self.value and not jump:
            self.gained += value - self.value
            self.samples.append((elapsed, self.gained))
            while len(self.samples) > 2 and self.samples[1][0] <= elapsed - ROLLING_WINDOW:
                self.samples.popleft()
        self.value = value

    def per_hour(self, elapsed):
        if self.start is None or elapsed - self.start < 30:
            return None
        return self.gained / (elapsed - self.start) * 3600

    def per_5min(self, elapsed):
        if self.start is None:
            return None
        t0 = max(self.start, elapsed - ROLLING_WINDOW)
        if elapsed - t0 < 60:
            return None
        before = 0
        for t, g in self.samples:
            if t > t0:
                break
            before = g
        return (self.gained - before) / (elapsed - t0) * ROLLING_WINDOW


class Session:
    """What's on screen: EXP and mesos since Start, and when tracking pauses by itself.

    After Start it tracks until Pause is pressed, except that it pauses by itself when no
    EXP comes in for `gap` seconds or you leave the map, and carries on at the next EXP
    gain: from zero if that's on a different map, so the numbers are always for the map
    you're on. Time spent paused never counts, and an idle pause rewinds the clock to the
    last EXP gain. The gain that ends a pause starts the clock again rather than counting,
    just like a log stretch's first gain.
    """

    def __init__(self, gap):
        self.exp, self.meso = ExpTracker(), MesoTracker()
        self.gap = gap
        self.on = False             # Start was pressed (and Pause wasn't)
        self.waiting = None         # paused by itself: "idle" or "map"
        self.map = None             # the map these numbers are for
        self.here = None            # the map you're on now
        self.last_gain = None
        self.auto_pause = True      # fork: Settings can switch the idle pause off

    def start(self, now):
        self.on, self.waiting, self.last_gain = True, None, now
        self.map = self.map or self.here
        self.exp.start()

    def pause(self):
        self.on, self.waiting = False, None
        self.exp.pause()
        self.meso.pause()

    def reset(self):
        self.exp.reset()
        self.meso.reset()
        self.on, self.waiting, self.map, self.last_gain = False, None, None, None

    def _wait(self, why):
        self.exp.pause(at=self.last_gain)
        self.meso.pause()
        self.waiting = why

    def observe(self, candidates, confident, now):
        """Feeds an EXP reading to the tracker. Returns the tracker's (accepted, note)."""
        t = self.exp
        exp_before, gained_before = t.exp, t.gained
        result = t.observe(candidates, confident)
        rose = exp_before is not None and t.exp is not None and (
            t.exp > exp_before or result[1] == "LEVEL UP!")
        if t.running and t.gained > gained_before:
            self.last_gain = now
        elif self.on and self.waiting and rose:
            if self.map and self.here and self.here != self.map:
                t.new_session()             # a different map: its own numbers
                self.meso.reset()
                self.map = self.here
            self.waiting = None
            self.last_gain = now
            t.start()
        return result

    def map_seen(self, name):
        """The map name was read (already matched to known names, see AutoLog.canonical)."""
        self.here = name
        if not self.on:
            return
        if self.map is None:
            self.map = name
        elif name != self.map:              # left the map
            if self.exp.running:
                self._wait("map")
            elif self.waiting:
                self.waiting = "map"

    def tick(self, now):
        """Call regularly: pauses after `gap` seconds without EXP."""
        if self.auto_pause and self.on and self.exp.running and now - self.last_gain > self.gap:
            self._wait("idle")


# --------------------------------------------------------------------------- auto log

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def party_label(n):
    return "Solo" if not n or n <= 1 else f"{n}ppl party"


def fmt_logged(seconds, short=False):
    """How long a log line was measured over: '25 min' / '1 h 05 min', or short '25m' / '1h 05m'."""
    hours, minutes = divmod(int(round((seconds or 0) / 60)), 60)
    if short:
        return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def format_entry(e):
    """e.g. 'Oct 8th, 2026 - Rocky Road II - Lv 27 - Bandit - Solo - 15,429exp/hr - 3,210mesos/hr - 25 min'"""
    d = date.fromisoformat(e["date"])
    level = e["level"] if e["level"] is not None else "?"
    parts = [f"{MONTHS[d.month - 1]} {_ordinal(d.day)}, {d.year}", e["map"], f"Lv {level}"]
    if e.get("class"):
        parts.append(e["class"])
    tracked = "party" in e                    # entries from before party/mesos tracking lack these
    if tracked:
        parts.append(party_label(e["party"]))
    parts.append(f"{e['exp'] / e['seconds'] * 3600 if e['seconds'] else 0:,.0f}exp/hr")
    if tracked:
        ms = e.get("mesos_seconds", 0)
        parts.append(f"{e['mesos'] / ms * 3600:,.0f}mesos/hr" if ms else "?mesos/hr")
    parts.append(fmt_logged(e["seconds"]))
    return " - ".join(parts)


def log_row(e, part=False):
    """The log table's cells for an entry: Date, Map, Lv, Class, Party, Time, EXP/hr, Mesos/hr.
    `part`: one party size under a merged party line, so just its own numbers."""
    d = date.fromisoformat(e["date"])
    day = f"{MONTHS[d.month - 1]} {d.day}" + ("" if d.year == date.today().year else f" '{d.year % 100:02d}")
    level = e["level"] if e["level"] is not None else "?"
    tracked = "party" in e                    # entries from before party/mesos tracking lack these
    party = ("Solo" if not e["party"] or e["party"] <= 1 else f"{e['party']}ppl") if tracked else ""
    exp = f"{e['exp'] / e['seconds'] * 3600:,.0f}" if e["seconds"] else "—"
    ms = e.get("mesos_seconds", 0)
    mesos = f"{e['mesos'] / ms * 3600:,.0f}" if ms else ("?" if tracked else "")
    if part:
        return "", "", "", "", party, fmt_logged(e["seconds"], short=True), exp, mesos
    return day, e["map"], level, e.get("class") or "", party, fmt_logged(e["seconds"], short=True), exp, mesos


def party_groups(entries):
    """Log lines as the log shows them, newest first: the party sizes (2 to 6) of one map on
    one day, at one level and class, together (smallest party first); everything else, solo
    play included, on its own. (Party sizes come and go as people join and leave.)"""
    groups = {}
    for e in entries:
        party = e.get("party")
        key = (e["date"], e["map"], e["level"], e.get("class"), "party") if party and party >= 2 else id(e)
        groups.setdefault(key, []).append(e)
    return [(key, sorted(g, key=lambda e: e["party"]) if len(g) > 1 else g) for key, g in groups.items()]


def merged(entries):
    """Log lines added together, so EXP/hr and mesos/hr are over all of their time."""
    m = dict(entries[0])
    for k in ("exp", "seconds", "mesos", "mesos_seconds"):
        m[k] = sum(e.get(k) or 0 for e in entries)
    return m


def group_row(entries, is_open=False):
    """The log table's cells for a map's party sizes together, e.g. Party '4–5ppl ▸'."""
    row = list(log_row(merged(entries)))
    sizes = [e["party"] for e in entries]
    row[4] = f"{sizes[0]}–{sizes[-1]}ppl {'▾' if is_open else '▸'}"
    return tuple(row)


def _numbers(name):
    """Roman numerals and numbers in a map name. Maps that differ in these are different maps."""
    return re.findall(r"\b(?:[IVX]+|\d+)\b", name)


LOG_VERSION = 3     # 2: mesos/hr from the mesos tracker; 3: class, seals and runs
RUN_SECONDS = 900   # a run: 15 minutes of non-stop farming on one map, one party size
                    # (until 0.9.4 a run was an hour; those are still shared as they are)

# Each line the tracker writes is sealed: a code worked out from its numbers. A line edited
# (or added) by hand doesn't match its seal any more, so it's known not to be the tracker's
# own. (Someone who digs into the program could still forge one, which is why anything
# shared will also be checked against what other players get.)
# (The old name stays in the key: changing it would unseal every log written so far.)
_SEAL_KEY = hashlib.sha256(b"Maple EXP/hr log seal: keep this log honest").digest()
_SEALED_LINE = ("date", "map_read", "level", "party", "class", "exp", "seconds", "mesos", "mesos_seconds")
_SEALED_RUN = ("date", "started", "map_read", "class", "level_start", "level_end", "party", "exp",
               "seconds", "mesos", "mesos_seconds", "version")


class AutoLog:
    """EXP/hr and mesos/hr per day, map, level, class and party size, measured over 5-minute
    stretches of non-stop EXP gain, plus 15-minute runs (shared with the stats site, if agreed).

    A stretch starts with an EXP gain and is thrown away if no EXP comes in for `gap`
    seconds, or the map or party size changes. Each finished stretch is added to that day's
    entry for the map, level, class and party size, so the entry gets more accurate the
    longer you play there. Mesos are counted for a stretch when the mesos were read during it.

    Alongside, a run goes on as long as the stretches do (a level-up doesn't stop it, a new
    map or party size does). Every full 15 minutes is saved as a run of its own.

    Every change re-reads the log file first and saves straight away, so lines written by
    anyone else (another copy of the app, or by hand) are never overwritten.
    """

    def __init__(self, gap_seconds, path=LOG_PATH, text_path=LOG_TEXT_PATH):
        self.gap = gap_seconds
        self.path, self.text_path = path, text_path
        self.entries, self.aliases, self.runs = [], {}, []   # aliases: misread/renamed map -> name
        self.install = secrets.token_hex(8)           # this log's own id, part of its seals
        self._kept = set()                            # problems a copy of the file was kept for
        version = self._load()
        if version < 2:
            def clear_old_mesos():                    # logged by the old, unreliable mesos counting
                for e in self.entries:
                    if "party" in e:
                        e["mesos"] = e["mesos_seconds"] = 0
            self._change(clear_old_mesos)
        elif version < LOG_VERSION:
            self._change(lambda: None)                # saves this log's id with it
        self.reset_clock()

    @staticmethod
    def key(e):
        return e["date"], e["map"], e["level"], e.get("party"), e.get("class")

    # ---- seals
    def _seal(self, record, fields):
        data = json.dumps([record.get(k) for k in fields], separators=(",", ":"))
        return hmac.new(_SEAL_KEY + self.install.encode(), data.encode(), hashlib.sha256).hexdigest()[:24]

    def sealed(self, e, fields=_SEALED_LINE):
        """True for a line exactly as the tracker wrote it."""
        return isinstance(e.get("seal"), str) and hmac.compare_digest(e["seal"], self._seal(e, fields))

    def edited(self, e):
        """True for a line that was changed outside the tracker (older lines have no seal)."""
        return "seal" in e and not self.sealed(e)

    def _reseal(self, e, ok):
        e["seal"] = self._seal(e, _SEALED_LINE) if ok else "edited"

    # ---- the file
    def _load(self):
        """Reads the log file into memory (keeps what's there if it can't be read).
        Returns the file's format version."""
        if not self.path.exists():
            return LOG_VERSION
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entries, aliases = data.get("entries", []), data.get("aliases", {})
            runs = data.get("runs", [])
            if not isinstance(entries, list) or not isinstance(aliases, dict) or not isinstance(runs, list):
                raise ValueError("unexpected contents")
        except Exception as e:
            self._keep_copy("reading the EXP log", e)        # damaged, e.g. edited by hand
            return LOG_VERSION
        good = [e for e in entries if self._valid(e)]
        if len(good) < len(entries):
            self._keep_copy(f"reading the EXP log: left out {len(entries) - len(good)} damaged lines")
        self.entries = good
        self.aliases = {k: v for k, v in aliases.items() if isinstance(k, str) and isinstance(v, str)}
        self.runs = [r for r in runs if isinstance(r, dict)]   # (only sealed ones are ever shared)
        if isinstance(data.get("install"), str) and re.fullmatch(r"[0-9a-f]{16}", data["install"]):
            self.install = data["install"]
        version = data.get("version", 1)
        return version if type(version) is int else LOG_VERSION

    def _keep_copy(self, what, exc=None):
        """Keeps a copy of the log file before it's saved over (once per problem)."""
        backup = self.path.with_name(f"exp_log.damaged-{time.strftime('%Y%m%d-%H%M%S')}.json")
        if what not in self._kept:
            self._kept.add(what)
            try:
                shutil.copy2(self.path, backup)
            except Exception:
                pass
            log_error(f"{what} (the file was copied to {backup.name})", exc)

    @staticmethod
    def _valid(e):
        """An entry with everything the log needs, each of the right kind, as the overlay writes
        them. Anything else is left out, not crashed on: the file may have been edited by hand."""
        def number(x):
            return type(x) in (int, float) and math.isfinite(x)

        def optional(key, ok, empty=False):           # missing (or empty), or of the right kind
            return key not in e or (empty and e[key] is None) or ok(e[key])
        try:
            date.fromisoformat(e["date"])
            return (isinstance(e["map"], str) and e["map"].strip() != "" and len(e["map"]) <= 200
                    and number(e["exp"]) and number(e["seconds"]) and e["seconds"] >= 0
                    and (e["level"] is None or (type(e["level"]) is int and 1 <= e["level"] <= 300))
                    and optional("party", lambda v: type(v) is int and 1 <= v <= 6, empty=True)
                    and optional("class", lambda v: isinstance(v, str), empty=True)
                    and optional("map_read", lambda v: isinstance(v, str), empty=True)
                    and optional("seal", lambda v: isinstance(v, str))
                    and optional("updated", number)
                    and optional("mesos", number) and optional("mesos_seconds", number)
                    and (not e.get("mesos_seconds") or number(e.get("mesos"))))
        except Exception:
            return False

    def _change(self, change):
        """Applies change() to the latest log on disk and saves it."""
        self._load()
        result = change()
        self.save()
        return result

    # ---- tracking
    def reset_clock(self):
        self.start = self.last_gain = self.block_map = self.block_party = self.meso0 = None
        self.block_exp = 0
        self.run = None                                  # the 15-minute run going on, if any

    def gain(self, now, amount):
        if amount > 0:
            if self.start is None:
                self.start, self.block_exp = now, 0          # this gain starts the clock
                self.run = self.run or {"start": now, "exp": 0, "map": None}
            else:
                self.block_exp += amount
                if self.run:
                    self.run["exp"] += amount
            self.last_gain = now
        elif self.start is not None:
            self.block_exp += amount                         # a misread too high, taken back
            if self.run:
                self.run["exp"] += amount

    def tick(self, now, map_name, level, party=1, mesos=None, job=None, map_read=None):
        """Call regularly while tracking. mesos is (mesos gained so far, when last read) from
        the MesoTracker, or None; job is the class; map_read the map name exactly as read.
        Returns the entry when one was logged."""
        if self.start is None:
            return None
        if now - self.last_gain > self.gap:
            self.reset_clock()                               # no EXP for too long: start over
            return None
        if map_name:
            if self.block_map and map_name != self.block_map:
                self.reset_clock()                           # changed maps: start over
                return None
            self.block_map = map_name
        if self.block_party is not None and party != self.block_party:
            self.reset_clock()                               # party size changed: start over
            return None
        self.block_party = party
        if self.meso0 is None and mesos and mesos[1] >= self.start - 2:
            self.meso0 = mesos                               # mesos as the stretch began
        self._run_tick(now, map_name, level, party, mesos, job, map_read)
        if now - self.start < LOG_BLOCK:
            return None
        if self.block_exp <= 0:                              # nothing gained after all
            self.start, self.block_exp = now, 0
            return None
        meso_gain = meso_seconds = 0
        if self.meso0 and mesos and mesos[1] > self.meso0[1]:
            meso_gain, meso_seconds = mesos[0] - self.meso0[0], mesos[1] - self.meso0[1]
        entry = self._add(self.block_map or "Unknown map", level, party, self.block_exp,
                          now - self.start, meso_gain, meso_seconds, job, map_read or self.block_map)
        self.start, self.block_exp = now, 0                  # the next 5 minutes start now
        self.meso0 = mesos if mesos and now - mesos[1] < 10 else None
        return entry

    def _run_tick(self, now, map_name, level, party, mesos, job, map_read):
        r = self.run
        if r is None:
            return
        if r["map"] is None:                                 # what the run is about, once known
            if not map_name:
                r["start"] = now                             # no map yet: the run can't start
                r["exp"] = 0
                return
            r.update(map=map_name, map_read=map_read or map_name, party=party, job=job,
                     level_start=level, wall=time.time() - (now - r["start"]), meso0=None)
        if job and r["job"] and job != r["job"]:
            self.run = {"start": now, "exp": 0, "map": None}  # a different class: a new run
            return
        r["job"] = r["job"] or job
        r["level"] = level
        if r["meso0"] is None and mesos and mesos[1] >= r["start"] - 2:
            r["meso0"] = mesos
        if now - r["start"] < RUN_SECONDS:
            return
        meso_gain = meso_seconds = 0
        if r["meso0"] and mesos and mesos[1] > r["meso0"][1]:
            meso_gain, meso_seconds = mesos[0] - r["meso0"][0], mesos[1] - r["meso0"][1]
        if r["exp"] > 0:
            self._add_run(r, now - r["start"], meso_gain, meso_seconds)
        self.run = {"start": now, "exp": 0, "map": None}     # straight on into the next run
        self._run_tick(now, map_name, level, party, mesos, job, map_read)

    def run_progress(self, now):
        """Seconds into the 15-minute run going on, or None."""
        r = self.run
        return None if r is None or r["map"] is None else max(0.0, now - r["start"])

    def runs_done(self, map_name, party, job, day=None):
        """How many runs were finished (sealed) on a day for a map, party size and class."""
        day = day or date.today().isoformat()
        return sum(1 for r in self.runs if r.get("date") == day and r.get("map") == map_name
                   and r.get("party") == party and r.get("class") == job and self.sealed(r, _SEALED_RUN))

    def unshared_runs(self):
        """Finished runs (exactly as the overlay saved them) not shared yet."""
        return [r for r in self.runs if not r.get("shared") and self.sealed(r, _SEALED_RUN)][:50]

    def mark_shared(self, started):
        def apply():
            for r in self.runs:
                if r.get("started") in started:
                    r["shared"] = True
        self._change(apply)

    def seconds_left(self, now):
        return None if self.start is None else max(0.0, LOG_BLOCK - (now - self.start))

    def _add(self, map_name, level, party, exp, seconds, mesos, meso_seconds, job=None, map_read=None):
        def add():
            key = (date.today().isoformat(), map_name, level, party, job)
            for e in self.entries:
                if self.key(e) == key:
                    ok = self.sealed(e)                      # still exactly the tracker's own?
                    e["exp"] += exp
                    e["seconds"] += seconds
                    e["mesos"] = e.get("mesos", 0) + mesos
                    e["mesos_seconds"] = e.get("mesos_seconds", 0) + meso_seconds
                    if "seal" in e:
                        self._reseal(e, ok)
                    break
            else:
                e = {"date": key[0], "map": map_name, "map_read": map_read or map_name, "level": level,
                     "party": party, "class": job, "exp": exp, "seconds": seconds, "mesos": mesos,
                     "mesos_seconds": meso_seconds}
                self._reseal(e, True)
                self.entries.append(e)
            e["updated"] = time.time()
            return e
        return self._change(add)

    def _add_run(self, r, seconds, mesos, meso_seconds):
        run = {"date": date.fromtimestamp(r["wall"]).isoformat(),
               "started": time.strftime("%Y-%m-%dT%H:%M", time.localtime(r["wall"])),
               "map": r["map"], "map_read": r["map_read"], "class": r["job"],
               "level_start": r["level_start"], "level_end": r["level"], "party": r["party"],
               "exp": r["exp"], "seconds": round(seconds, 1), "mesos": mesos,
               "mesos_seconds": round(meso_seconds, 1), "version": VERSION}
        run["seal"] = self._seal(run, _SEALED_RUN)
        self._change(lambda: self.runs.append(run))
        return run

    def canonical(self, name, also=()):
        """The name to log a map under: applies renames and fixes small misreads (matching
        the maps in the log, and any names in `also`, e.g. the map you were just on)."""
        name = self.aliases.get(name, name)
        best, score = name, 0.0
        for known in {e["map"] for e in self.entries} | set(self.aliases.values()) | {n for n in also if n}:
            if _numbers(known) == _numbers(name):
                s = SequenceMatcher(None, known.lower(), name.lower()).ratio()
                if s > score:
                    best, score = known, s
        return best if score >= 0.85 else name

    def rename(self, entry, new_name):
        """Renames a map (and remembers it for future reads). The map name as read stays on
        record (map_read), so renamed lines keep their seal."""
        old, key = entry["map"], self.key(entry)

        def apply():
            if old == "Unknown map":
                for e in self.entries:                       # just this one
                    if self.key(e) == key:
                        e.setdefault("map_read", old)
                        e["map"] = new_name
            else:
                self.aliases[old] = new_name                 # and future reads of the old name
                for k, v in self.aliases.items():
                    if v == old:
                        self.aliases[k] = new_name
                for e in self.entries:
                    if e["map"] == old:
                        e.setdefault("map_read", old)
                        e["map"] = new_name
                for r in self.runs:
                    if r.get("map") == old:
                        r["map"] = new_name                  # (runs are sealed by map_read)
            merged = {}
            for e in self.entries:                           # combine entries that now match
                if self.key(e) in merged:
                    m = merged[self.key(e)]
                    ok = self.sealed(m) and self.sealed(e)
                    for field in ("exp", "seconds", "mesos", "mesos_seconds"):
                        if field in e:
                            m[field] = m.get(field, 0) + e[field]
                    m["updated"] = max(m.get("updated", 0), e.get("updated", 0))
                    if "seal" in m or "seal" in e:
                        self._reseal(m, ok)
                else:
                    merged[self.key(e)] = e
            self.entries = list(merged.values())

        self._change(apply)
        if old != "Unknown map" and self.block_map == old:
            self.block_map = new_name
        if old != "Unknown map" and self.run and self.run.get("map") == old:
            self.run["map"] = new_name

    def delete(self, entry):
        key = self.key(entry)

        def apply():
            self.entries = [e for e in self.entries if self.key(e) != key]

        self._change(apply)

    def newest_first(self):
        return sorted(self.entries, key=lambda e: e.get("updated") or 0, reverse=True)

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": LOG_VERSION, "install": self.install, "entries": self.entries,
                                       "aliases": self.aliases, "runs": self.runs}, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            self.text_path.write_text("".join(format_entry(e) + "\n" for e in self.newest_first()),
                                      encoding="utf-8")
        except Exception as e:
            log_error("saving the EXP log", e)


# --------------------------------------------------------------------------- UI helpers

def fmt_num(n):
    return "—" if n is None else f"{n:,.0f}"


def fmt_short(n):
    if n is None:
        return "—"
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return f"{n / div:.2f}{suffix}"
    return f"{n:,.0f}"


def fmt_eta(seconds):
    """A time to wait, the way people say it: '36 min', '2 h 05 min'. None if too far off."""
    if seconds is None or seconds < 0 or seconds >= 100 * 3600:
        return None
    minutes = int(seconds // 60)
    if minutes < 1:
        return "under 1 min"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def fmt_dur(seconds):
    if seconds is None or seconds < 0 or seconds > 99 * 3600:
        return "—"
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


class RegionSelector:
    """Full-screen translucent overlay; drag a box around the EXP bar text."""

    def __init__(self, root, on_done):
        self.on_done = on_done
        with mss.mss() as sct:
            v = sct.monitors[0]
        self.origin = (v["left"], v["top"])
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.geometry(f"{v['width']}x{v['height']}+{v['left']}+{v['top']}")
        self.win.attributes("-alpha", 0.35)
        self.win.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.win, bg="black", cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(v["width"] // 2, 60, fill="white", font=("Segoe UI", 20, "bold"),
                                text="Drag a box around the EXP numbers  (Esc to cancel)")
        self.start = None
        self.rect = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<Escape>", lambda e: self._finish(None))
        self.win.focus_force()

    def _press(self, e):
        self.start = (e.x, e.y)
        self.rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#ffcc00", width=2)

    def _drag(self, e):
        if self.rect:
            self.canvas.coords(self.rect, *self.start, e.x, e.y)

    def _release(self, e):
        if not self.start:
            return
        x0, x1 = sorted((self.start[0], e.x))
        y0, y1 = sorted((self.start[1], e.y))
        if x1 - x0 < 10 or y1 - y0 < 5:
            self._finish(None)
            return
        self._finish({"left": self.origin[0] + x0, "top": self.origin[1] + y0,
                      "width": x1 - x0, "height": y1 - y0})

    def _finish(self, region):
        self.win.destroy()
        self.on_done(region)


# --------------------------------------------------------------------------- app

BG, FG, DIM, ACCENT, FIELD = "#1b1d23", "#e8e8e8", "#8a8f98", "#ffcc33", "#2c3038"
ACCENT_SOFT = "#d9b04a"     # a quieter gold, for text that goes with the big number
# The window's fonts: (family, size at 100%, weight, underline). Settings > Size scales them all.
FONTS = {"small": ("Segoe UI", 8, "normal", False), "text": ("Segoe UI", 9, "normal", False),
         "link": ("Segoe UI", 9, "normal", True), "row": ("Segoe UI", 10, "normal", False),
         "icon": ("Segoe UI", 11, "normal", False), "value": ("Consolas", 11, "normal", False),
         "next": ("Segoe UI Semibold", 11, "normal", False), "title": ("Segoe UI Semibold", 12, "normal", False),
         "big": ("Segoe UI", 24, "bold", False)}
LIST_BG, LIST_BG2, LIST_PART = "#23262d", "#282b33", "#1c1e23"
LOG_HINT = "Right-click a line to copy, rename or delete it."


class App:
    def __init__(self, root, templates):
        self.root = root
        self.cfg = farm_config({**DEFAULTS, **load_config()})
        mode = self.cfg.get("mode", "window")
        self.reader = ScreenReader(templates, mode, self.cfg.get("region"))
        saved = self.cfg.get("mesos_box")
        if isinstance(saved, list) and len(saved) == 6:
            self.reader.saved_mesos = tuple(saved)
        self.session = Session(self.cfg["gap_seconds"])
        self.tracker, self.meso = self.session.exp, self.session.meso
        self.party = 1
        self._party_seen = (None, 0)
        self.last_mesos = None
        self.rejected = 0
        self.autolog = AutoLog(self.cfg["gap_seconds"])
        self.session.auto_pause = self.cfg["auto_pause"]
        self.reader.read_hpmp = self.cfg["read_hpmp"]
        self.farm = FarmOverlay(self, DATA_DIR, VERSION, LOG_PATH)
        self.map_name = None
        self.level = None
        self.note = None
        self.last_logged = None
        self.skip_log = False               # "Don't log" is on: this run isn't logged
        self._sharing, self._next_share = False, 0.0       # sending runs to the site
        self.job = None                     # the class, from the status bar
        self.map_read = None                # the map name exactly as read
        self.found_at = None                # when the EXP bar was first read for this level
        self.level_up_at = None
        self.died_at = None
        self.log_open = False
        self.settings_win = None
        self.q = queue.Queue()
        self.stop_evt = threading.Event()
        self.last_capture = None

        self.fonts = {name: tkfont.Font(root, family=family, weight=weight, underline=underline)
                      for name, (family, _, weight, underline) in FONTS.items()}
        pos = self.cfg.get("pos")
        self._dpi = (screen_dpi(pos[0] + 40, pos[1] + 15) if pos else None) or root.winfo_fpixels("1i")
        self._apply_ui_scale()
        self.hotkey_note = None
        self.hotkeys = Hotkeys(lambda action: self.q.put(("hotkey", action)))
        self.hotkeys.set(self.cfg["hotkeys"], self.cfg["hotkeys_on"])
        self.compact = False

        root.title(WINDOW_TITLE)
        try:
            root.iconbitmap(default=str(ICON_PATH))
        except Exception:
            pass
        root.report_callback_exception = lambda kind, exc, tb: self._oops("a button or menu", exc)
        root.configure(bg=BG)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._apply_window_settings()
        self._restore_position()

        main = self.main = tk.Frame(root, bg=BG)
        main.grid(row=0, column=0, sticky="n")
        root.columnconfigure(1, weight=1)                 # the log gets any extra room
        root.rowconfigure(0, weight=1)
        self.vars = {}
        self.big_title = tk.Label(main, text=self.farm.headline_title(), bg=BG, fg=DIM, font=self.fonts["text"])
        self.big_title.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 0))
        corner = tk.Frame(main, bg=BG)
        corner.grid(row=0, column=1, sticky="ne", padx=(0, 6), pady=(4, 0))
        small = dict(bg=BG, fg=DIM, activebackground=FIELD, activeforeground=FG, relief="flat",
                     bd=0, padx=6, pady=0, font=self.fonts["icon"], cursor="hand2")
        self.best_btn = tk.Button(corner, text="Best maps", command=self.open_best_maps,
                  **{**small, "fg": ACCENT_SOFT, "activebackground": BG, "activeforeground": ACCENT,
                     "font": self.fonts["link"]})
        self.best_btn.pack(side="left", padx=(0, 4))
        tk.Button(corner, text="⚙", command=self.open_settings, **small).pack(side="left")
        self.log_btn = tk.Button(corner, text=">", command=self.toggle_log, **small)
        self.log_btn.pack(side="left")
        self.compact_btn = tk.Button(corner, text="Compact", command=lambda: self.set_compact(not self.compact),
                                     bg=FIELD, fg=FG, activebackground="#3a3f49", activeforeground=FG,
                                     relief="flat", bd=0, padx=8, pady=1, font=self.fonts["small"], cursor="hand2")
        self.compact_btn.pack(side="left", padx=(6, 0))
        self.big = tk.Label(main, text="—", bg=BG, fg=ACCENT, font=self.fonts["big"])
        self.big.grid(row=1, column=0, columnspan=2, sticky="w", padx=12)
        self.big.bind("<Double-Button-1>", lambda _: self.set_compact(not self.compact))
        # Under the big number, like a caption: when you'll reach the next level.
        self.next_level = tk.Label(main, text="", bg=BG, fg=ACCENT_SOFT, font=self.fonts["next"])
        self.next_level.grid(row=2, column=0, columnspan=2, sticky="nw", padx=(14, 12), pady=(0, 8))

        rows = ["Last 5 min", "% / hr", "Gained", "Current",
                "Mesos", "Mesos / hr", "Mesos / 5 min", "Elapsed"]
        self.detail = []                                  # the rows, hidden in compact mode
        for i, name in enumerate(rows, start=3):
            label = tk.Label(main, text=name, bg=BG, fg=DIM, font=self.fonts["row"])
            label.grid(row=i, column=0, sticky="w", padx=(12, 8))
            v = tk.StringVar(value="—")
            value = tk.Label(main, textvariable=v, bg=BG, fg=FG, font=self.fonts["value"])
            value.grid(row=i, column=1, sticky="e", padx=(8, 12))
            self.vars[name] = v
            self.detail += [label, value]
        # Compact mode's stand-in for the rows: the last 5 minutes and mesos/hr, on one line.
        self.compact_line = tk.Label(main, text="", bg=BG, fg=DIM, font=self.fonts["text"])
        self.compact_line.grid(row=3, column=0, columnspan=2, sticky="w", padx=12)
        self.compact_line.grid_remove()

        self.farm.build(main, self.fonts, {"BG": BG, "FG": FG, "DIM": DIM, "FIELD": FIELD, "ACCENT": ACCENT,
                                           "ACCENT_SOFT": ACCENT_SOFT}, len(rows) + 3)
        bar = tk.Frame(main, bg=BG)
        bar.grid(row=len(rows) + 4, column=0, columnspan=2, pady=(10, 4), padx=8)
        btn = dict(bg=FIELD, fg=FG, activebackground="#3a3f49", activeforeground=FG,
                   relief="flat", padx=8, pady=3, font=self.fonts["text"])
        self.start_btn = tk.Button(bar, text="Start", command=self.toggle, **btn)
        self.start_btn.pack(side="left", padx=2)
        reset_btn = tk.Button(bar, text="Reset", command=self.reset, **btn)
        reset_btn.pack(side="left", padx=2)
        # Keeps this run out of the log (everything is still shown). Turns itself off when
        # the run ends: when you leave the map or stop getting EXP.
        self._btn_style = btn
        self.skip_btn = tk.Button(bar, command=self.toggle_skip_log, **btn)
        self.skip_btn.pack(side="left", padx=2)
        self._show_skip_log()
        # Normally the EXP bar is found by itself; this switches to an area you drag out
        # yourself (if it ever can't find it) and back.
        self.area_btn = tk.Button(bar, command=self.toggle_area, **btn)
        self.area_btn.pack(side="left", padx=2)
        capture_btn = tk.Button(bar, text="View capture", command=self.view_capture, **btn)
        capture_btn.pack(side="left", padx=2)
        self._show_area_mode()
        self.more_buttons = [reset_btn, self.skip_btn, self.area_btn, capture_btn]   # not in compact mode

        self.status = tk.StringVar()
        self.status_label = tk.Label(main, textvariable=self.status, bg=BG, fg=DIM, font=self.fonts["small"],
                                     justify="left")
        self.status_label.grid(row=len(rows) + 5, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))
        self._fit_status()

        self._build_log_panel()
        if self.cfg["compact"]:
            self.set_compact(True)
        elif self.cfg.get("log_open"):
            self.toggle_log()

        if not templates:
            self.status.set("Some of the program's files are missing, so EXP reading is less "
                            "reliable. Please download it again.")
        else:
            self.status.set("Looking for the MapleStory window..." if mode == "window"
                            else "Using your selected area.")

        threading.Thread(target=self._worker, daemon=True).start()
        root.after(200, self._pump)
        root.after(500, self._refresh)
        if self.cfg["check_updates"]:
            self.check_updates()                       # only now, when it starts
        root.after(1500, self._ask_to_share)           # (asked once, the first time)

    # ---- log panel
    def _build_log_panel(self):
        p = self.log_panel = tk.Frame(self.root, bg=BG)
        tk.Label(p, text="EXP log", bg=BG, fg=DIM, font=self.fonts["text"]).grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.log_status = tk.StringVar()
        tk.Label(p, textvariable=self.log_status, bg=BG, fg=FG, font=self.fonts["text"],
                 justify="left", anchor="w").grid(row=1, column=0, columnspan=2, sticky="we", pady=(2, 6))

        # A compact table: one row per day, map, level and class, solo or in a party (click a
        # party row for a row per party size). Drag the lines between the column titles to
        # resize a column; widths are remembered.
        cell, head = self.fonts["text"], self.fonts["small"]
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.layout("Log.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Log.Treeview", background=LIST_BG, fieldbackground=LIST_BG, foreground=FG,
                        font=cell, rowheight=int(cell.metrics("linespace") * 1.45), borderwidth=0)
        style.configure("Log.Treeview.Heading", background=FIELD, foreground=DIM, relief="flat",
                        borderwidth=0, font=head)
        style.map("Log.Treeview", background=[("selected", "#3a3f49")], foreground=[("selected", FG)])
        style.map("Log.Treeview.Heading", background=[("active", "#3a3f49")])
        # (key, title, widest usual value, alignment): starting widths are measured in the
        # real font, so nothing is cut off at any screen scaling.
        columns = (("date", "Date", "Sep 30", "w"), ("map", "Map", "The Swamp of Despair II", "w"),
                   ("lv", "Lv", "99", "center"), ("class", "Class", "Crossbowman", "w"),
                   ("party", "Party", "2–6ppl ▸", "center"),
                   ("time", "Time", "12h 30m", "center"),
                   ("exp", "EXP/hr", "888,888", "e"), ("mesos", "Mesos/hr", "888,888", "e"))
        # Saved widths are in points, so they look the same on screens with different scaling
        # (older versions saved pixels: those are used as they are, once).
        saved = {k: round(v * self._px_per_pt) for k, v in (self.cfg.get("log_columns_pt") or {}).items()
                 if type(v) in (int, float)} or self.cfg.get("log_columns") or {}
        pad = cell.measure("00")
        tree = self.log_tree = ttk.Treeview(p, columns=[c[0] for c in columns], show="headings",
                                            style="Log.Treeview", height=11, selectmode="browse")
        for key, title, sample, anchor in columns:
            width = max(cell.measure(sample), head.measure(title)) + pad
            if isinstance(saved.get(key), int) and 10 <= saved[key] <= 2000:
                # (Party is never narrower than a merged party row: older versions' Party
                # column was sized for "6ppl".)
                width = max(saved[key], width) if key == "party" else saved[key]
            tree.heading(key, text=title, anchor=anchor)
            tree.column(key, width=width, minwidth=pad, anchor=anchor, stretch=False)
        tree.tag_configure("odd", background=LIST_BG2)
        tree.tag_configure("part", background=LIST_PART, foreground="#b3b8c0")   # one party size
        tree.tag_configure("edited", foreground="#6b7079")      # changed outside the tracker
        tree.grid(row=2, column=0, sticky="nsew")
        scroll = tk.Scrollbar(p, orient="vertical", command=tree.yview)
        scroll.grid(row=2, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        self.log_hint = tk.StringVar(value=LOG_HINT)
        hint = tk.Label(p, textvariable=self.log_hint, bg=BG, fg=DIM, font=head, justify="left", anchor="w")
        hint.grid(row=3, column=0, columnspan=2, sticky="we", pady=(4, 0))
        p.bind("<Configure>", lambda e: hint.config(wraplength=max(120, e.width - 8)))
        p.columnconfigure(0, weight=1)
        p.rowconfigure(2, weight=1)
        tree.bind("<Button-3>", self._log_menu)
        tree.bind("<<TreeviewSelect>>", self._log_selected)
        tree.bind("<Button-1>", self._log_click)
        tree.bind("<<TreeviewOpen>>", lambda e: tree.after_idle(self._sync_group, tree.focus()))
        tree.bind("<<TreeviewClose>>", lambda e: tree.after_idle(self._sync_group, tree.focus()))
        self.shown, self.groups, self.open_groups = {}, {}, set()
        self._fill_log()

    def toggle_log(self, remember=True):
        if self.log_open:
            self._remember_log_size()                   # before it closes
        self.log_open = not self.log_open
        root = self.root
        if self.log_open:
            # With the log open the window can be resized (e.g. for long map names).
            self.log_panel.grid(row=0, column=1, sticky="nsew", padx=(0, 12), pady=(10, 8))
            root.update_idletasks()
            root.minsize(self.main.winfo_reqwidth() + 150, self.main.winfo_reqheight())
            root.resizable(True, True)
            size = self.cfg.get("log_size")
            root.geometry(f"{size[0]}x{size[1]}" if size else "")
        else:
            self.log_panel.grid_remove()
            root.resizable(False, False)
            root.minsize(1, 1)
            root.geometry("")                           # back to just the tracker's size
        self.log_btn.config(text="<" if self.log_open else ">")
        if remember:
            self.cfg["log_open"] = self.log_open
            save_config(self.cfg)

    # ---- compact mode and size
    def set_compact(self, on):
        """Compact mode: just EXP/hr, when you'll level, the last 5 minutes and mesos/hr on one
        line, and Start/Pause, in a much smaller window. The Compact button (it says Full in
        compact mode), Shift+4, or double-clicking the big number switches it."""
        self.compact = self.cfg["compact"] = on
        if on and self.log_open:
            self.toggle_log(remember=False)                 # out of the way, but remembered
        for w in self.detail:
            w.grid_remove() if on else w.grid()
        self.compact_line.grid() if on else self.compact_line.grid_remove()
        for b in self.more_buttons:
            b.pack_forget()
            if not on:
                b.pack(side="left", padx=2)
        if on:
            self.log_btn.pack_forget()
        else:
            self.log_btn.pack(side="left", before=self.compact_btn)
        self.compact_btn.config(text="Full" if on else "Compact")
        self.farm.set_compact(on)
        self._fit_status()
        if not on and self.cfg.get("log_open"):
            self.toggle_log(remember=False)
        else:
            self.root.geometry("")
        save_config(self.cfg)

    def _apply_ui_scale(self):
        """Sizes every font by Settings > Size and for the screen the window is on (screens can
        have different Windows scaling), and the log's rows and columns with them."""
        old, self._px_per_pt = getattr(self, "_px_per_pt", None), self.cfg["ui_scale"] / 100 * self._dpi / 72
        for name, (_, size, _, _) in FONTS.items():
            # In pixels (negative), never under 9 so the smallest text stays readable.
            self.fonts[name].configure(size=-max(9, round(size * self._px_per_pt)))
        if old is None or not hasattr(self, "log_tree"):    # at start, before the window is built
            return
        self._fit_settings()
        ttk.Style(self.root).configure("Log.Treeview", rowheight=int(self.fonts["text"].metrics("linespace") * 1.45))
        for c in self.log_tree["columns"]:
            width = round(self.log_tree.column(c, "width") * self._px_per_pt / old)
            self.log_tree.column(c, width=max(10, width))
        self.cfg["log_size"] = None                         # a size from before may not fit any more
        self._fit_status()
        if self.log_open:
            self.root.update_idletasks()
            self.root.minsize(self.main.winfo_reqwidth() + 150, self.main.winfo_reqheight())
        self.root.geometry("")

    def _fit_settings(self):
        """Keeps the Settings window as big as what's in it. (Moving a window to a screen with
        different scaling can leave it at a fixed size, which would cut its contents off.)"""
        w = self.settings_win
        if w and w.winfo_exists() and (w.winfo_width() < w.winfo_reqwidth() or w.winfo_height() < w.winfo_reqheight()):
            w.geometry("")

    def _check_screen(self):
        """Resizes the text when the window is moved to a screen with different Windows scaling
        (judged by its title bar, which stays put when the window changes size)."""
        r = self._window_rect()
        dpi = screen_dpi(r[0] + 40, r[1] + 15) if r else None
        if dpi and dpi != self._dpi:
            self._dpi = dpi
            self._apply_ui_scale()

    def _fit_status(self):
        """Wraps the status line at the width the rest of the window needs."""
        self.status_label.config(wraplength=1)
        self.main.update_idletasks()
        self.status_label.config(wraplength=max(160, self.main.winfo_reqwidth() - 24))

    def _fill_log(self):
        """The log, newest first. A map's party sizes on a day share a row (smallest to biggest
        party, all their time together); clicking it shows a row for each party size."""
        tree = self.log_tree
        tree.delete(*tree.get_children())
        self.shown, self.groups = {}, {}
        edited = lambda e: ("edited",) if self.autolog.edited(e) else ()
        for i, (key, entries) in enumerate(party_groups(self.autolog.newest_first())):
            stripe = ("odd",) if i % 2 else ()
            if len(entries) == 1:
                iid = tree.insert("", "end", values=log_row(entries[0]), tags=stripe + edited(entries[0]))
                self.shown[iid] = entries[0]
                continue
            is_open = key in self.open_groups
            all_edited = all(self.autolog.edited(e) for e in entries)
            iid = tree.insert("", "end", values=group_row(entries, is_open), open=is_open,
                              tags=stripe + (("edited",) if all_edited else ()))
            self.groups[iid] = (key, entries)
            for e in entries:
                self.shown[tree.insert(iid, "end", values=log_row(e, part=True), tags=("part",) + edited(e))] = e
        self.log_hint.set(LOG_HINT if self.shown else "No logs yet. Press Start and gain EXP for 5 minutes.")

    def _log_click(self, event):
        """Clicking a merged party row opens it (a row per party size) or closes it."""
        iid = self.log_tree.identify_row(event.y)
        if iid in self.groups and self.log_tree.identify_region(event.x, event.y) == "cell":
            self.log_tree.item(iid, open=not self.log_tree.item(iid, "open"))
            self._sync_group(iid)

    def _sync_group(self, iid):
        """After a party row opens or closes: its arrow, and remembering it for next time."""
        if iid not in self.groups:
            return
        key, entries = self.groups[iid]
        is_open = bool(self.log_tree.item(iid, "open"))
        self.open_groups.add(key) if is_open else self.open_groups.discard(key)
        self.log_tree.item(iid, values=group_row(entries, is_open))
        if self.log_tree.selection() == (iid,):
            self._log_selected(None)

    def _log_selected(self, _event):
        sel = self.log_tree.selection()
        group = self.groups.get(sel[0]) if sel else None
        if group:
            entries = group[1]
            sizes = ", ".join(str(e["party"]) for e in entries[:-1]) + f" and {entries[-1]['party']}"
            runs = sum(self.autolog.runs_done(e["map"], e["party"], e.get("class"), e["date"]) for e in entries)
            hours = (f" Includes {runs} full {RUN_SECONDS // 60}-minute run{'s' * (runs != 1)} of non-stop farming."
                     if runs else "")
            what = "hide" if self.log_tree.item(sel[0], "open") else "see"
            self.log_hint.set(f"{entries[0]['map']}: parties of {sizes}, over {fmt_logged(sum(e['seconds'] for e in entries))} "
                              f"of non-stop EXP gain in all.{hours} Click it to {what} each party size. "
                              f"Right-click to copy, rename or delete.")
            return
        e = self.shown.get(sel[0]) if sel else None
        if e:
            if self.autolog.edited(e):
                self.log_hint.set("This line was changed outside the overlay (it no longer matches "
                                  "the overlay's seal), so it won't count toward the best-maps site.")
                return
            runs = self.autolog.runs_done(e["map"], e.get("party"), e.get("class"), e["date"])
            hours = (f" Includes {runs} full {RUN_SECONDS // 60}-minute run{'s' * (runs != 1)} of non-stop farming."
                     if runs else "")
            self.log_hint.set(f"{e['map']}: measured over {fmt_logged(e['seconds'])} of non-stop EXP gain."
                              f"{hours} Right-click to copy, rename or delete.")

    def _log_menu(self, event):
        """Right-click: copy, rename or delete a line (a merged party row: all its party sizes)."""
        iid = self.log_tree.identify_row(event.y)
        entries = self.groups[iid][1] if iid in self.groups else [self.shown[iid]] if iid in self.shown else None
        if not entries:
            return
        self.log_tree.selection_set(iid)
        many = len(entries) > 1
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Copy lines" if many else "Copy line", command=lambda: self._copy(entries))
        menu.add_command(label="Rename map...", command=lambda: self._rename(entries))
        menu.add_command(label=f"Delete all {len(entries)} party sizes" if many else "Delete entry",
                         command=lambda: self._delete(entries))
        menu.tk_popup(event.x_root, event.y_root)

    def _copy(self, entries):
        text = "\n".join(format_entry(e) for e in entries)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log_hint.set("Copied: " + text if len(entries) == 1 else f"Copied {len(entries)} lines, one per party size.")

    def _rename(self, entries):
        name = simpledialog.askstring("Rename map", "Map name:", initialvalue=entries[0]["map"],
                                      parent=self.root)
        if name and name.strip():
            for entry in entries:             # (one rename covers them all, unless it's "Unknown map")
                if entry["map"] != name.strip():
                    self.autolog.rename(entry, name.strip())
            if self.map_name:
                self.map_name = self.autolog.canonical(self.map_name)
            s = self.session                    # so a renamed map isn't taken as a new one
            s.map, s.here = (self.autolog.canonical(n) if n else n for n in (s.map, s.here))
            self._fill_log()

    def _delete(self, entries):
        lines = "\n".join(format_entry(e) for e in entries)
        question = f"Delete these {len(entries)} log entries?" if len(entries) > 1 else "Delete this log entry?"
        if messagebox.askyesno("Delete entry", f"{question}\n\n{lines}", parent=self.root):
            for entry in entries:
                self.autolog.delete(entry)
            self._fill_log()

    def _log_status_text(self, now):
        where = " · ".join(x for x in (self.map_name or "Map not read yet", f"Lv {self.level or '?'}",
                                       self.job, party_label(self.party)) if x)
        if self.skip_log:
            return f"{where}\nNot logging this run."
        if not self.session.on:
            return f"{where}\nPress Start to log EXP/hr here."
        if self.session.waiting:
            return f"{where}\nPaused. Logging carries on at your next EXP gain."
        left = self.autolog.seconds_left(now)
        if left is None:
            text = f"{where}\nWaiting for EXP gain..."
        else:
            text = f"{where}\nNext log in {int(left) // 60}:{int(left) % 60:02d} of non-stop EXP gain"
        if self.last_logged and now - self.last_logged < 8:
            text += "  (just logged)"
        run = self.autolog.run_progress(now)
        done = self.autolog.runs_done(self.map_name, self.party, self.job) if self.map_name else 0
        if run is not None or done:
            line = f"{RUN_SECONDS // 60}-min run: {int(run // 60) if run is not None else 0} of {RUN_SECONDS // 60} min"
            text += f"\n{line}" + (f" · {done} done here today" if done else "")
        return text

    # ---- settings
    def _apply_window_settings(self):
        self.root.attributes("-topmost", bool(self.cfg["topmost"]))
        self.root.attributes("-alpha", 1 - min(90, max(0, int(self.cfg["transparency"]))) / 100)

    def open_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.lift()
            return
        w = self.settings_win = tk.Toplevel(self.root)
        w.title("Settings")
        w.configure(bg=BG)
        w.resizable(False, False)
        w.transient(self.root)
        r = self._window_rect()
        if r:                                    # over the overlay, so on the same screen (and scaling)
            w.geometry(f"+{r[0] + 40}+{r[1] + 40}")
        w.attributes("-topmost", bool(self.cfg["topmost"]))
        text = dict(bg=BG, fg=FG, font=self.fonts["text"])

        gap = tk.StringVar(value=str(self.cfg["gap_seconds"]))
        tk.Label(w, text="Pause tracking (and restart the 5-minute log timer)\nafter no EXP for",
                 justify="left", **text).grid(
            row=0, column=0, sticky="w", padx=14, pady=(14, 4))
        line = tk.Frame(w, bg=BG)
        line.grid(row=1, column=0, sticky="w", padx=14)
        tk.Spinbox(line, from_=5, to=300, width=5, textvariable=gap, bg=FIELD, fg=FG,
                   buttonbackground=FIELD, insertbackground=FG, relief="flat",
                   font=self.fonts["text"]).pack(side="left")
        tk.Label(line, text="seconds", **text).pack(side="left", padx=(6, 0))

        transparency = tk.IntVar(value=int(self.cfg["transparency"]))
        tk.Label(w, text="Window transparency (%)", **text).grid(
            row=2, column=0, sticky="w", padx=14, pady=(14, 0))
        tk.Scale(w, from_=0, to=90, orient="horizontal", variable=transparency, length=240,
                 bg=BG, fg=FG, troughcolor=FIELD, highlightthickness=0, bd=0,
                 font=self.fonts["small"]).grid(row=3, column=0, sticky="w", padx=10)

        size_line = tk.Frame(w, bg=BG)
        size_line.grid(row=4, column=0, sticky="w", padx=14, pady=(10, 0))
        tk.Label(size_line, text="Size", **text).pack(side="left")
        # − 100% +: steps through UI_SCALES only, so it stops at the smallest and biggest sizes.
        size = tk.StringVar(value=f"{self.cfg['ui_scale']}%")
        step = dict(bg=FIELD, fg=FG, activebackground="#3a3f49", activeforeground=FG, relief="flat", bd=0,
                    padx=8, pady=0, font=self.fonts["text"], cursor="hand2", disabledforeground="#555a63")

        def nudge(by):
            i = UI_SCALES.index(int(size.get().rstrip("%"))) + by
            if 0 <= i < len(UI_SCALES):
                size.set(f"{UI_SCALES[i]}%")
            smaller.config(state="normal" if i > 0 else "disabled")
            bigger.config(state="normal" if i < len(UI_SCALES) - 1 else "disabled")
        smaller = tk.Button(size_line, text="−", command=lambda: nudge(-1), **step)
        smaller.pack(side="left", padx=(8, 0))
        tk.Label(size_line, textvariable=size, width=5, **text).pack(side="left")
        bigger = tk.Button(size_line, text="+", command=lambda: nudge(1), **step)
        bigger.pack(side="left")
        nudge(0)
        topmost = tk.BooleanVar(value=bool(self.cfg["topmost"]))
        tk.Checkbutton(w, text="Always on top of other windows", variable=topmost, bg=BG, fg=FG,
                       selectcolor=FIELD, activebackground=BG, activeforeground=FG,
                       font=self.fonts["text"]).grid(row=5, column=0, sticky="w", padx=10, pady=(8, 0))
        updates = tk.BooleanVar(value=bool(self.cfg["check_updates"]))
        tk.Checkbutton(w, text="Check for updates when it starts", variable=updates, bg=BG, fg=FG,
                       selectcolor=FIELD, activebackground=BG, activeforeground=FG,
                       font=self.fonts["text"]).grid(row=6, column=0, sticky="w", padx=10, pady=(2, 0))
        sharing = tk.BooleanVar(value=bool(self.cfg.get("share_runs")))
        share_line = tk.Frame(w, bg=BG)
        share_line.grid(row=7, column=0, sticky="w", padx=10, pady=(2, 0))
        tk.Checkbutton(share_line, text=f"Share my {RUN_SECONDS // 60}-minute runs (anonymously) on the best-maps site",
                       variable=sharing, bg=BG, fg=FG, selectcolor=FIELD, activebackground=BG,
                       activeforeground=FG, font=self.fonts["text"]).pack(side="left")
        tk.Button(share_line, text="Open it", command=self.open_best_maps, bg=BG,
                  fg=ACCENT_SOFT, activebackground=BG, activeforeground=ACCENT, relief="flat", bd=0,
                  cursor="hand2", font=self.fonts["link"]).pack(side="left", padx=(4, 0))

        def apply(*_):
            try:
                seconds = int(gap.get())
            except ValueError:
                seconds = 0
            if 1 <= seconds <= 3600:
                self.cfg["gap_seconds"] = self.autolog.gap = self.session.gap = seconds
            self.cfg["transparency"] = int(transparency.get())
            self.cfg["topmost"] = bool(topmost.get())
            self.cfg["check_updates"] = bool(updates.get())
            if bool(sharing.get()) != bool(self.cfg.get("share_runs")):
                self.cfg["share_runs"] = bool(sharing.get())
                self.share_runs()
            self._apply_window_settings()
            w.attributes("-topmost", self.cfg["topmost"])
            scale = int(size.get().rstrip("%"))
            if scale in UI_SCALES and scale != self.cfg["ui_scale"]:
                self.cfg["ui_scale"] = scale
                self._apply_ui_scale()
            save_config(self.cfg)

        for var in (gap, transparency, topmost, updates, sharing, size):
            var.trace_add("write", apply)
        self._hotkey_settings(w, row=8)
        buttons = tk.Frame(w, bg=BG)
        buttons.grid(row=9, column=0, sticky="we", padx=14, pady=(14, 6))
        style = dict(bg=FIELD, fg=FG, activebackground="#3a3f49", activeforeground=FG, relief="flat",
                     padx=12, pady=3, font=self.fonts["text"])
        tk.Button(buttons, text="Open data folder", command=self.open_data_folder, **style).pack(side="left")
        tk.Button(buttons, text="Farm settings", command=lambda: self.farm.settings_dialog(w),
                  **style).pack(side="left", padx=(6, 0))
        tk.Button(buttons, text="Check for updates", command=lambda: self.check_updates(manual=True),
                  **style).pack(side="left", padx=(6, 0))
        tk.Button(buttons, text="Report a bug", command=lambda: self.report_bug(w),
                  **style).pack(side="left", padx=(6, 0))
        tk.Button(buttons, text="Close", command=w.destroy, **style).pack(side="right", padx=(6, 0))
        tk.Label(w, text=f"Your log and settings are saved in {DATA_DIR}", bg=BG, fg=DIM,
                 font=self.fonts["small"], wraplength=int(250 * self._px_per_pt), justify="left").grid(
            row=10, column=0, sticky="w", padx=14)
        tk.Label(w, text=f"{WINDOW_TITLE} v{VERSION}. This is a beta: it's still being improved.",
                 bg=BG, fg=DIM, font=self.fonts["small"]).grid(row=11, column=0, sticky="w", padx=14, pady=(2, 12))

    def _hotkey_settings(self, w, row):
        """Settings' Hotkeys section: on or off, and each one changeable (click it, then press
        the keys; Esc keeps it as it was, Backspace turns it off)."""
        box = tk.Frame(w, bg=BG)
        box.grid(row=row, column=0, sticky="w", padx=10, pady=(10, 0))
        on = tk.BooleanVar(value=self.cfg["hotkeys_on"])
        tk.Checkbutton(box, text="Hotkeys, while MapleStory or the overlay is in front:", variable=on, bg=BG,
                       fg=FG, selectcolor=FIELD, activebackground=BG, activeforeground=FG,
                       font=self.fonts["text"]).grid(row=0, column=0, columnspan=3, sticky="w")
        key_style = dict(bg=FIELD, fg=FG, activebackground="#3a3f49", activeforeground=FG, relief="flat", bd=0,
                         padx=8, pady=1, width=10, font=self.fonts["text"], cursor="hand2")
        buttons, notes, choosing = {}, {}, {"action": None}
        hint = tk.StringVar(value="Click one, then press the keys you want. Backspace turns it off.")

        def show():
            for action, b in buttons.items():
                key = self.cfg["hotkeys"][action]
                b.config(text=key or "Off", bg=FIELD, fg=FG if key else DIM)
                notes[action].config(text=self.hotkeys.problems.get(action, ""))

        def choose(action):
            choosing["action"] = action
            self.hotkeys.pause(True)                 # so the keys reach this window
            buttons[action].config(text="Press keys...", bg=ACCENT_SOFT, fg=BG)
            w.focus_force()

        def done(action, key):
            choosing["action"] = None
            if key:
                for other, k in self.cfg["hotkeys"].items():
                    if k == key and other != action:
                        self.cfg["hotkeys"][other] = ""      # one key, one job
            self.cfg["hotkeys"][action] = key
            save_config(self.cfg)
            self.hotkeys.set(self.cfg["hotkeys"], self.cfg["hotkeys_on"])
            self.hotkeys.pause(False)
            show()

        def pressed(e):
            action = choosing["action"]
            if not action or e.keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
                                          "Win_L", "Win_R", "Caps_Lock", "Num_Lock"):
                return None
            if e.keysym == "Escape":
                done(action, self.cfg["hotkeys"][action])
            elif e.keysym in ("BackSpace", "Delete"):
                done(action, "")
            else:
                key = hotkey_text(bool(e.state & 0x4), bool(e.state & 0x20000), bool(e.state & 0x1), e.keycode)
                if not key:
                    hint.set("Use Ctrl, Alt or Shift with a number or letter, or an F key.")
                    return "break"
                hint.set("Click one, then press the keys you want. Backspace turns it off.")
                done(action, key)
            return "break"

        for i, (action, name) in enumerate(HOTKEY_ACTIONS.items(), start=1):
            tk.Label(box, text=name, bg=BG, fg=FG, font=self.fonts["text"]).grid(row=i, column=0, sticky="w",
                                                                                  padx=(26, 10), pady=1)
            buttons[action] = tk.Button(box, command=lambda a=action: choose(a), **key_style)
            buttons[action].grid(row=i, column=1, sticky="w", pady=1)
            notes[action] = tk.Label(box, bg=BG, fg="#d97a6c", font=self.fonts["small"])
            notes[action].grid(row=i, column=2, sticky="w", padx=(8, 0))
        tk.Label(box, textvariable=hint, bg=BG, fg=DIM, font=self.fonts["small"]).grid(
            row=len(HOTKEY_ACTIONS) + 1, column=0, columnspan=3, sticky="w", padx=(26, 0), pady=(2, 0))
        w.bind("<KeyPress>", pressed)
        w.bind("<Destroy>", lambda e: e.widget is w and self.hotkeys.pause(False))

        def switch(*_):
            self.cfg["hotkeys_on"] = bool(on.get())
            save_config(self.cfg)
            self.hotkeys.set(self.cfg["hotkeys"], self.cfg["hotkeys_on"])
        on.trace_add("write", switch)
        show()

    def open_data_folder(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(DATA_DIR)
        except Exception as e:
            self._oops("opening the data folder", e)

    def _restore_position(self):
        pos = self.cfg.get("pos")
        if not pos:
            return
        with mss.mss() as sct:
            screens = sct.monitors[1:]
        # Only if its top-left is still on a screen (not one that was unplugged, nor the
        # gap between two screens of different sizes).
        if any(v["left"] <= pos[0] < v["left"] + v["width"] - 50
               and v["top"] <= pos[1] < v["top"] + v["height"] - 50 for v in screens):
            self.root.geometry(f"+{pos[0]}+{pos[1]}")

    def _window_rect(self):
        """This app's window on screen (physical pixels), or None."""
        try:
            hwnd = _GetAncestor(self.root.winfo_id(), 2)       # GA_ROOT: the framed window
            r = wintypes.RECT()
            if _GetWindowRect(hwnd, ctypes.byref(r)):
                return r.left, r.top, r.right - r.left, r.bottom - r.top
        except Exception:
            pass
        return None

    # ---- background capture loop
    def _worker(self):
        with mss.mss() as sct:
            while not self.stop_evt.is_set():
                t0 = time.monotonic()
                try:
                    self.q.put(("reading", self.reader.read(sct)))
                except Exception as e:
                    log_error("reading the screen", e)
                    self.q.put(("error", f"{type(e).__name__}: {e}"))
                self.stop_evt.wait(max(0.1, POLL_SECONDS - (time.monotonic() - t0)))

    # ---- sharing runs
    def open_best_maps(self):
        """The best-maps site, already showing the maps for this level, party size and class."""
        ask = {k: v for k, v in (("level", self.level), ("party", self.party), ("job", self.job)) if v}
        webbrowser.open(STATS_SITE + ("/?" + urllib.parse.urlencode(ask) if ask else ""))

    def _ask_to_share(self):
        if "share_runs" in self.cfg:
            return
        yes = messagebox.askyesno(
            "Help other players pick maps?",
            "Share your farming, so everyone can see which maps give the most EXP for their "
            "class and level?\n\n"
            f"Every {RUN_SECONDS // 60} minutes of non-stop farming is shared: the map, your level, "
            "class and party size, and the EXP and mesos you got. Never your character's name.\n\n"
            f"They're combined with other players' on {STATS_SITE.split('//')[1]}. You can change "
            "this any time in Settings.", parent=self.root)
        self.cfg["share_runs"] = bool(yes)
        save_config(self.cfg)
        self.share_runs()

    def share_runs(self):
        """Sends finished runs to the stats site in the background (if agreed to)."""
        self._next_share = time.monotonic() + 1800
        if not self.cfg.get("share_runs") or self._sharing:
            return
        runs = self.autolog.unshared_runs()
        if not runs:
            return
        self._sharing, install = True, self.autolog.install

        def work():
            try:
                self.q.put(("shared", post_runs(install, runs)))
            except Exception:
                self.q.put(("shared", None))           # offline or the site is down: later
        threading.Thread(target=work, daemon=True).start()

    # ---- updates
    def check_updates(self, manual=False):
        """Asks GitHub for a newer version in the background; the answer comes via _pump."""
        def work():
            try:
                self.q.put(("update", (check_for_update(), manual, None)))
            except Exception as e:
                self.q.put(("update", (None, manual, e)))
        threading.Thread(target=work, daemon=True).start()

    def _show_update(self, found, manual, error):
        parent = self.settings_win if manual and self.settings_win and self.settings_win.winfo_exists() \
            else self.root
        if error is not None:
            if manual:
                messagebox.showinfo("Updates", "Couldn't check for updates. Are you connected to the "
                                    "internet?", parent=parent)
            return                                      # at start: quietly try again next time
        if not found:
            if manual:
                messagebox.showinfo("Updates", f"You have the latest version (v{VERSION}).", parent=parent)
            return
        version, notes = found
        if not manual and self.cfg.get("skip_version") == version:
            return
        w = tk.Toplevel(self.root)
        w.title("Update available")
        w.configure(bg=BG, padx=16, pady=14)
        w.resizable(False, False)
        w.transient(self.root)
        w.attributes("-topmost", bool(self.cfg["topmost"]))
        tk.Label(w, text=f"{WINDOW_TITLE} v{version} is out", bg=BG, fg=ACCENT,
                 font=self.fonts["title"]).pack(anchor="w")
        tk.Label(w, text=f"You have v{VERSION}. Your log and settings are kept when you update.",
                 bg=BG, fg=DIM, font=self.fonts["text"]).pack(anchor="w", pady=(2, 10))
        tk.Label(w, text="What's new:", bg=BG, fg=FG, font=self.fonts["text"]).pack(anchor="w")
        box = tk.Frame(w, bg=BG)
        box.pack(fill="both", expand=True, pady=(4, 12))
        text = tk.Text(box, width=64, height=12, wrap="word", bg=LIST_BG, fg=FG, relief="flat",
                       font=self.fonts["text"], padx=8, pady=6, highlightthickness=0)
        scroll = tk.Scrollbar(box, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.insert("1.0", notes or "(No details were given for this version.)")
        text.configure(state="disabled")
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        buttons = tk.Frame(w, bg=BG)
        buttons.pack(fill="x")
        style = dict(bg=FIELD, fg=FG, activebackground="#3a3f49", activeforeground=FG, relief="flat",
                     padx=12, pady=3, font=self.fonts["text"])

        def get_it():
            webbrowser.open(RELEASES_PAGE)                # always your own releases page
            w.destroy()

        def skip():
            self.cfg["skip_version"] = version
            save_config(self.cfg)
            w.destroy()
        tk.Button(buttons, text="Get the update", command=get_it, **{**style, "bg": ACCENT_SOFT, "fg": BG,
                  "activebackground": ACCENT, "activeforeground": BG}).pack(side="left")
        tk.Button(buttons, text="Not now", command=w.destroy, **style).pack(side="left", padx=(6, 0))
        tk.Button(buttons, text="Skip this version", command=skip, **style).pack(side="right")

    def _oops(self, what, exc):
        """Something unexpected went wrong: note it in errors.log and keep going."""
        log_error(what, exc)
        self.status.set("Something went wrong, but the overlay keeps running. Details are in "
                        "errors.log (Settings > Open data folder).")

    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "error":
                    self.status.set("Something went wrong reading the screen. Trying again... "
                                    "(details are in errors.log: Settings > Open data folder)")
                elif kind == "update":
                    self._show_update(*payload)
                elif kind == "hotkey":
                    self._hotkey(payload)
                elif kind == "shared":
                    self._sharing = False
                    if payload:
                        self.autolog.mark_shared(payload)
                elif kind == "vision":
                    self.farm.on_vision(payload)
                elif kind == "vision_frame":
                    self.farm.on_vision_frame(payload)
                else:
                    self._handle(payload)
        except queue.Empty:
            pass
        except Exception as e:
            self._oops("handling a reading", e)
        finally:
            if not self.stop_evt.is_set():
                self.root.after(200, self._pump)

    def _handle(self, r):
        t, now = self.tracker, time.monotonic()
        if r.image is not None:
            self.last_capture = r
        if r.mesos_image is not None:
            self.last_mesos = r
        if r.mesos_at:                                  # remember where the mesos box is
            self.cfg["mesos_box"] = list(r.mesos_at)
            self.reader.saved_mesos = tuple(r.mesos_at)
            save_config(self.cfg)
        if r.mesos is not None:
            self.meso.observe(r.mesos, now, t.elapsed(), t.running)
        if r.party is not None:                         # believe a new party size once seen twice
            seen, n = self._party_seen
            n = n + 1 if r.party == seen else 1
            self._party_seen = (r.party, n)
            if n >= 2:
                self.party = r.party
        if self.reader.job:
            self.job = self.reader.job
        if r.map:
            self.map_read = r.map                       # as read (kept on record if renamed)
            # A slightly misread name of the map you're on still counts as that map.
            self.map_name = self.autolog.canonical(r.map, also=(self.map_name, self.session.map))
            waiting = self.session.waiting
            self.session.map_seen(self.map_name)
            self._after_pause_check(waiting)
        self.note = r.note
        if r.candidates:
            if self.found_at is None or t.req is not None:
                self.found_at = now
            before, before_raw = t.gained, t.raw_gained
            ok, note = self.session.observe(r.candidates, r.confident, now)
            self.rejected = 0 if ok else self.rejected + 1
            if self.rejected >= 10:                 # keeps not adding up: find the EXP bar again
                self.reader.box, self.rejected = None, 0
            if t.running and t.gained != before and not self.skip_log:
                self.autolog.gain(now, t.gained - before)
            if note == "died":
                self.died_at = now
            if note == "LEVEL UP!":
                self.level_up_at = now
                if self.level:
                    self.level += 1             # until the new level's EXP confirms it
            self.farm.on_exp(now, t.raw_gained - before_raw, note, t, level_for_req(t.req) or self.level)
            status = self._status_for(note)
        else:
            status = r.where
        if self.note:
            status += f"\n{self.note}"
        self.status.set(status)
        self.level = level_for_req(t.req) or self.level
        self.farm.on_reading(r, now)
        self._log_tick(now)

    def _status_for(self, note):
        """What the status line says while the EXP bar is being read (details: View capture)."""
        t = self.tracker
        if self.level_up_at and time.monotonic() - self.level_up_at < 6:
            return f"Level up! You're now Lv {self.level}." if self.level else "Level up!"
        if self.hotkey_note and time.monotonic() - self.hotkey_note[1] < 4:
            return self.hotkey_note[0]
        if self.died_at and time.monotonic() - self.died_at < 30 and t.last_loss:
            return f"You died and lost {t.last_loss:.1f}% EXP. That isn't taken off your EXP/hr."
        if self.rejected >= 5:
            return "Having trouble reading the EXP bar clearly. View capture shows what it sees."
        if t.req is None:
            # Normally the level is known within seconds; if not, it needs an EXP gain.
            if time.monotonic() - self.found_at < 8:
                return "Found your EXP bar. Working out your level..."
            return "Found your EXP bar. Your level shows after your next EXP gain."
        s = self.session
        if s.waiting == "idle":
            return (f"Paused: no EXP for {s.gap} seconds, so this time doesn't count. "
                    "It carries on at your next EXP gain.")
        if s.waiting == "map":
            return ("Paused: you left the map. It carries on at your next EXP gain "
                    "(from zero if that's on a different map).")
        if t.running:
            return "Tracking your EXP. Not logging this run." if self.skip_log else "Tracking your EXP."
        if t.elapsed() > 0:
            return "Paused. Press Resume to carry on."
        return "Ready. Press Start when you begin farming."

    def _log_tick(self, now):
        if not self.tracker.running or self.skip_log:
            return
        m = self.meso
        mesos = (m.gained, m.seen_at) if m.value is not None and now - m.seen_at <= 30 else None
        runs = len(self.autolog.runs)
        entry = self.autolog.tick(now, self.map_name, self.level, self.party, mesos, self.job, self.map_read)
        if len(self.autolog.runs) > runs:
            self.share_runs()                          # a run just finished
        if entry:
            self.last_logged = now
            self._fill_log()

    def _refresh(self):
        try:
            self._check_screen()
            self._fit_settings()
            self._update_display()
        except Exception as e:
            self._oops("updating the window", e)
        finally:
            if not self.stop_evt.is_set():
                self.root.after(500, self._refresh)

    def _update_display(self):
        t, now = self.tracker, time.monotonic()
        self.farm.refresh(now, t)
        rate = self.farm.headline_rate(t)
        self.big.config(text=fmt_num(rate) if rate is not None else "—",
                        fg=ACCENT if rate is not None else DIM)
        self.vars["Last 5 min"].set(fmt_num(t.rolling_rate()))
        self.vars["% / hr"].set(f"{rate / t.req * 100:.2f}%" if rate and t.req else "—")
        lvl = f"  (+{t.levels} lvl)" if t.levels else ""
        self.vars["Gained"].set(f"{fmt_short(t.gained)}{lvl}")
        shown = (t.exp, t.pct) if t.exp is not None else t.last_read
        if shown:
            self.vars["Current"].set(f"{fmt_num(shown[0])} [{shown[1]:.2f}%]")
        best_rate = t.rolling_rate() or rate
        level = level_for_req(t.req) or self.level
        eta = fmt_eta((t.req - t.exp) / best_rate * 3600) if (
            t.req and t.exp is not None and best_rate and best_rate > 0) else None
        if level and eta:
            self.next_level.config(text=f"Lv {level + 1} in {eta}")
        else:
            self.next_level.config(text=f"Lv {level}" if level else "")
        m, el = self.meso, t.elapsed()
        if m.value is None:
            self.vars["Mesos"].set("move this window" if self.reader.mesos_covered else "open inventory")
        else:
            self.vars["Mesos"].set(fmt_num(m.value) + (f"  (+{fmt_short(m.gained)})" if m.gained > 0 else ""))
        self.vars["Mesos / hr"].set(fmt_num(m.per_hour(el)))
        self.vars["Mesos / 5 min"].set(fmt_num(m.per_5min(el)))
        self.vars["Elapsed"].set(fmt_dur(el))
        if self.compact:
            farm = self.farm.compact_text()
            self.compact_line.config(text=f"Last 5 min  {self.vars['Last 5 min'].get()}    "
                                          + (farm or f"Mesos / hr  {self.vars['Mesos / hr'].get()}"))
        waiting = self.session.waiting
        self.session.tick(now)
        if self.session.waiting != waiting:             # just paused by itself: say why now
            self._after_pause_check(waiting)
            self.status.set(self._status_for(""))
        self._log_tick(now)
        self.log_status.set(self._log_status_text(now))
        self.reader.own_rect = self._window_rect()
        if now >= self._next_share:                   # retry runs that couldn't be sent
            self.share_runs()

    # ---- buttons
    def _hotkey(self, action):
        if action == "start":
            self.toggle()
            done = "started" if self.session.on else "paused"
        elif action == "reset":
            self.reset()
            done = "reset"
        elif action == "skip":
            self.toggle_skip_log()
            done = "not logging this run" if self.skip_log else "logging this run again"
        elif action == "afk":
            self.farm.toggle_afk()
            return
        else:
            self.set_compact(not self.compact)
            return
        self.hotkey_note = (f"{self.cfg['hotkeys'][action]}: {done}.", time.monotonic())
        self.status.set(self.hotkey_note[0])

    def toggle(self):
        if self.session.on:                     # (also while it's paused by itself)
            self.session.pause()
            self.farm.on_pause(time.monotonic())
            self.start_btn.config(text="Resume")
        else:
            self.session.start(time.monotonic())
            self.farm.on_start(time.monotonic())
            self.reader.request_map()           # read the map name now
            self.start_btn.config(text="Pause")
        self.autolog.reset_clock()
        if self.tracker.exp is not None:
            self.status.set(self._status_for(""))   # right away, not at the next read

    def reset(self):
        self.farm.on_reset(time.monotonic())
        self.session.reset()
        self.autolog.reset_clock()
        self.skip_log = False
        self._show_skip_log()
        self.start_btn.config(text="Start")
        # Also look for the minimap, inventory and party list again (e.g. after moving them).
        self.reader.look_again()
        self.map_name, self.party, self._party_seen = None, 1, (None, 0)
        self.cfg.pop("mesos_box", None)
        save_config(self.cfg)
        self.status.set("Reset. Looking for the map name, inventory and party list again...")

    def toggle_skip_log(self):
        self.skip_log = not self.skip_log
        self.autolog.reset_clock()              # nothing from before or during it is logged
        self._show_skip_log()
        self.status.set("This run won't be logged. Logging turns back on when you leave the map "
                        "or stop getting EXP." if self.skip_log else "Logging this run again.")

    def _show_skip_log(self):
        if self.skip_log:
            self.skip_btn.config(text="Not logging", bg=ACCENT_SOFT, fg=BG, activebackground=ACCENT,
                                 activeforeground=BG)
        else:
            style = self._btn_style
            self.skip_btn.config(text="Don't log", bg=style["bg"], fg=style["fg"],
                                 activebackground=style["activebackground"],
                                 activeforeground=style["activeforeground"])

    def _after_pause_check(self, waiting_before):
        """When the run ends (tracking pauses by itself), "Don't log" turns off again, so
        the next run is logged. (Pressed while already paused, it's for the next run.)"""
        if self.skip_log and waiting_before is None and self.session.waiting:
            self.skip_log = False
            self._show_skip_log()

    def _new_source(self):
        # New place to read from -> new baseline, but keep the session totals.
        self.tracker.exp = self.tracker.pct = None
        self.tracker._pending = self.tracker._pending_lvl = None
        self.autolog.reset_clock()
        save_config(self.cfg)

    def _show_area_mode(self):
        self.area_btn.config(text="Follow game" if self.reader.mode == "region" else "Select area")

    def toggle_area(self):
        if self.reader.mode == "region":
            self.follow_game()
        else:
            self.select_area()

    def follow_game(self):
        self.reader.use_window()
        self.cfg["mode"] = "window"
        self._new_source()
        self._show_area_mode()
        self.status.set("Following the MapleStory window...")

    def select_area(self):
        def done(region):
            if region:
                self.reader.use_region(region)
                self.cfg.update(mode="region", region=region)
                self._new_source()
                self._show_area_mode()
                self.status.set("Using your selected area. Click Follow game to go back to "
                                "finding it automatically.")
        RegionSelector(self.root, done)

    @staticmethod
    def _boxed_image(bgr, boxes):
        """A screenshot crop, enlarged, with a box drawn around each digit found."""
        img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        scale = min(4.0, max(1.0, 120 / img.height), 900 / img.width)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        draw = ImageDraw.Draw(img)
        colors = {"exp": "#ffcc33", "pct": "#33ccff", "mesos": "#66dd66"}
        for x0, y0, x1, y1, kind in boxes:
            draw.rectangle([x0 * scale, y0 * scale, x1 * scale, y1 * scale],
                           outline=colors[kind], width=2)
        return img

    def _boxed(self, bgr, boxes):
        return ImageTk.PhotoImage(self._boxed_image(bgr, boxes))

    def view_capture(self):
        r, mr = self.last_capture, self.last_mesos
        if not r and not mr:
            messagebox.showinfo("View capture", "Nothing has been read from the game yet.\n\n"
                                "Make sure MapleStory is open and its EXP bar is showing.",
                                parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("Last capture")
        win.configure(bg=BG)
        win.attributes("-topmost", bool(self.cfg["topmost"]))
        shots = []
        if r:
            shots.append((r.image, r.boxes, f"EXP bar. Read: {r.text or 'nothing'}   ({r.where})"))
        if mr:
            read = fmt_num(mr.mesos) if mr.mesos is not None else "nothing (waiting for a clear look)"
            shots.append((mr.mesos_image, mr.mesos_boxes, f"Mesos. Read: {read}"))
        win._imgs = []
        for shot, boxes, text in shots:
            win._imgs.append(self._boxed(shot, boxes))
            tk.Label(win, image=win._imgs[-1], bg=BG).pack(padx=8, pady=(8, 2))
            tk.Label(win, text=text, bg=BG, fg=FG).pack(anchor="w", padx=8)
        bottom = tk.Frame(win, bg=BG)
        bottom.pack(fill="x", padx=8, pady=(4, 8))
        tk.Label(bottom, text="Boxes show where each digit was found.", bg=BG, fg=DIM).pack(side="left")
        tk.Button(bottom, text="Something's wrong? Report a bug", command=lambda: self.report_bug(win),
                  bg=FIELD, fg=FG, activebackground="#3a3f49", activeforeground=FG, relief="flat",
                  padx=10, pady=2, font=self.fonts["text"]).pack(side="right")

    # ---- bug reports
    def report_bug(self, parent=None):
        """Opens a new GitHub issue in the browser, filled in with what the tracker knows, and
        saves a picture of what it last read for the player to drag in. Nothing is sent by
        the tracker itself: the player sees everything and clicks Submit (or doesn't)."""
        parent = parent or self.root
        if not messagebox.askokcancel(
                "Report a bug",
                "Your browser will open a bug report on GitHub, already filled in with your "
                "version and what the overlay was reading (nothing personal).\n\n"
                "1. Write a sentence about what went wrong.\n"
                "2. Drag in the picture from the folder that opens.\n"
                "3. Click Submit.\n\n"
                "You need a free GitHub account.", parent=parent):
            return
        picture = self._save_report_picture()
        query = urllib.parse.urlencode({"title": f"Bug: (describe it here) [v{VERSION}]",
                                        "body": self._report_text(picture)})
        webbrowser.open(f"https://github.com/{GITHUB_REPO}/issues/new?{query}")
        if picture:
            try:
                subprocess.Popen(["explorer", f"/select,{picture}"])
            except Exception as e:
                log_error("opening the bug report picture's folder", e)

    def _save_report_picture(self):
        """What the tracker last read (EXP bar and mesos, with the digit boxes), as one PNG."""
        parts = []
        if self.last_capture is not None:
            parts.append(self._boxed_image(self.last_capture.image, self.last_capture.boxes))
        if self.last_mesos is not None:
            parts.append(self._boxed_image(self.last_mesos.mesos_image, self.last_mesos.mesos_boxes))
        if not parts:
            return None
        sheet = Image.new("RGB", (max(p.width for p in parts), sum(p.height + 8 for p in parts)), BG)
        y = 0
        for p in parts:
            sheet.paste(p, (0, y))
            y += p.height + 8
        path = DATA_DIR / "bug-report.png"
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            sheet.save(path)
            return path
        except Exception as e:
            log_error("saving the bug report picture", e)
            return None

    def _report_text(self, picture):
        """The bug report's text: room to describe it, then the details, without anything
        personal (your Windows user name is taken out of file paths)."""
        def private(text):
            for secret in {str(Path.home()), os.environ.get("USERNAME", "")}:
                if len(secret) > 2:
                    text = text.replace(secret, "<you>")
            return text
        r, mr, t = self.last_capture, self.last_mesos, self.tracker
        errors = ""
        try:
            errors = private(ERROR_LOG_PATH.read_text(encoding="utf-8", errors="replace"))[-1500:]
        except Exception:
            pass
        lines = [
            "**What went wrong?**", "(Describe it here: what did you do, what did you expect, what did it show?)", "",
            "**Screenshot**",
            ("(Drag in `bug-report.png` from the folder that just opened: it shows what the overlay read.)"
             if picture else "(If you can, add a screenshot of the game and the overlay.)"), "",
            "---", "<details><summary>Details (filled in by the overlay)</summary>", "",
            f"- Version: {VERSION} ({'download' if getattr(sys, 'frozen', False) else 'from source'})",
            f"- Windows: {platform.version()}",
            f"- Game window: {'x'.join(map(str, self.reader.size)) if self.reader.size else 'not found'}"
            f" (EXP digits {fmt_num(self.reader.gh) if self.reader.gh else '?'} px)",
            f"- Last EXP read: {r.text if r else 'nothing'}",
            f"- Level: {self.level or '?'}, class: {self.job or 'not read'}, map: {self.map_name or 'not read'},"
            f" party: {party_label(self.party)}",
            f"- Mesos: {fmt_num(self.meso.value) if self.meso.value is not None else 'not read'}"
            f" (last read {fmt_num(mr.mesos) if mr and mr.mesos is not None else 'nothing'})",
            f"- Tracking: {'on' if self.session.on else 'off'}, paused: {self.session.waiting or 'no'},"
            f" gained {fmt_num(t.gained)} EXP in {fmt_dur(t.elapsed())}",
            f"- Status: {private(self.status.get())}",
            f"- Sharing runs: {'on' if self.cfg.get('share_runs') else 'off'}"
            f" ({len(self.autolog.runs)} runs, {len(self.autolog.unshared_runs())} not sent)",
            f"- Settings: pause after {self.cfg['gap_seconds']} s, transparency {self.cfg['transparency']}%,"
            f" map names {'on' if tesseract_ok else 'off (no Tesseract)'}",
        ]
        if errors:
            lines += ["", "Recent errors:", "```", errors.strip(), "```"]
        lines += ["", "</details>"]
        return "\n".join(lines)

    def _remember_log_size(self):
        """Keeps the log's column widths, and the window size if the user resized it."""
        tree, root = self.log_tree, self.root
        self.cfg["log_columns_pt"] = {c: round(int(tree.column(c, "width")) / self._px_per_pt, 1)
                                      for c in tree["columns"]}
        self.cfg.pop("log_columns", None)
        if self.log_open:
            size = [root.winfo_width(), root.winfo_height()]
            natural = [root.winfo_reqwidth(), root.winfo_reqheight()]
            self.cfg["log_size"] = size if size != natural else None

    def close(self):
        self.stop_evt.set()
        try:
            self.farm.on_close(time.monotonic())
        except Exception as e:
            log_error("saving the farm session", e)
        try:
            self._remember_log_size()
        except Exception as e:
            log_error("saving the log size", e)
        self.cfg["pos"] = [self.root.winfo_x(), self.root.winfo_y()]
        save_config(self.cfg)
        self.root.destroy()


def main():
    global tesseract_ok, _single_instance
    # Only one copy at a time (per data folder): two copies would each keep their own log.
    # If it's already running, bring that window to the front instead. (The lock keeps the old
    # name, so an older version and this one can't run at the same time either.)
    lock ="Local\\MapleEXP-" + hashlib.md5(str(DATA_DIR).lower().encode()).hexdigest()[:12]
    _single_instance = _CreateMutex(None, False, lock)
    if ctypes.get_last_error() == 183:                      # ERROR_ALREADY_EXISTS
        found = []

        def check(hwnd, _):                                 # any version's main window
            title = ctypes.create_unicode_buffer(256)
            _GetWindowText(hwnd, title, 256)
            if title.value.startswith((WINDOW_TITLE, *OLD_TITLES)) and _IsWindowVisible(hwnd):
                found.append(hwnd)
            return True
        _EnumWindows(_EnumWindowsProc(check), 0)
        if found:
            _ShowWindow(found[0], 9)                        # SW_RESTORE
            _SetForegroundWindow(found[0])
        else:
            ctypes.windll.user32.MessageBoxW(None, f"{WINDOW_TITLE} is already running.", WINDOW_TITLE, 0x40)
        return
    move_old_data()
    tess = find_tesseract()
    if tess:
        pytesseract.pytesseract.tesseract_cmd = tess
        tesseract_ok = True
    root = tk.Tk()
    App(root, DigitTemplates.load())
    root.mainloop()


if __name__ == "__main__":
    main()
