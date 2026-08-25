# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import time
import traceback
import uuid
import ctypes
import difflib
import re
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import psutil
except Exception as exc:  # pragma: no cover - shown in the GUI at startup.
    psutil = None
    PSUTIL_IMPORT_ERROR = exc
else:
    PSUTIL_IMPORT_ERROR = None


APP_DIR = Path(__file__).resolve().parent
RULES_PATH = APP_DIR / "rules.json"
STATE_PATH = APP_DIR / "state.json"
ERROR_LOG_PATH = APP_DIR / "error.log"
SCHEDULE_PATH = APP_DIR / "schedule.json"
POLL_INTERVAL_MS = 1000
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
VK_L = 0x4C
VK_MENU = 0x12
VK_LBUTTON = 0x01
GA_ROOT = 2
SHORTCUT_POLL_MS = 120
BLOCKED_TARGET_EXES = {
    "applicationframehost.exe",
    "audiodg.exe",
    "conhost.exe",
    "csrss.exe",
    "ctfmon.exe",
    "dwm.exe",
    "explorer.exe",
    "fontdrvhost.exe",
    "lockapp.exe",
    "logonui.exe",
    "runtimebroker.exe",
    "searchhost.exe",
    "securityhealthsystray.exe",
    "shellexperiencehost.exe",
    "sihost.exe",
    "smartscreen.exe",
    "smss.exe",
    "startmenuexperiencehost.exe",
    "svchost.exe",
    "systemsettings.exe",
    "taskhostw.exe",
    "textinputhost.exe",
    "wininit.exe",
    "winlogon.exe",
    "winstore.app.exe",
    "wudfhost.exe",
}
BLOCKED_TARGET_PATH_PARTS = (
    "\\windows\\system32\\",
    "\\windows\\syswow64\\",
    "\\windows\\systemapps\\",
)
PAUSE_LOCK_PROMPT = (
    "人类在很多领域都反复撞上同一个问题：知道应该怎么做和实际能做到之间存在一条系统性的鸿沟。\n"
    "这条鸿沟不是知识问题，不是态度问题，甚至不是能力问题：\n"
    "它是一个关于“在什么条件下人的行为会偏离人的意图”的结构性问题。\n"
    "每一个在这条鸿沟上栽过跟头的领域，最终都会走向同一个解决方案："
)
PAUSE_LOCK_TARGET = "不再试图让人变得更好，而是设计一个环境，让人不需要那么好。"
PAUSE_LOCK_FULL_TARGET = PAUSE_LOCK_PROMPT + "\n" + PAUSE_LOCK_TARGET
PAUSE_LOCK_THRESHOLD = 0.66


def now_ts() -> float:
    return time.time()


def normalize_path(path: str) -> str:
    if not path:
        return ""
    return os.path.normcase(os.path.abspath(path))


def clean_exe_name(value: str) -> str:
    if not value:
        return ""
    return Path(value).name.lower()


def blocked_target_reason(proc_or_name: ProcessSnapshot | str, exe_path: str = "") -> str:
    if isinstance(proc_or_name, ProcessSnapshot):
        exe_name = clean_exe_name(proc_or_name.name or proc_or_name.exe)
        exe_path = proc_or_name.exe or exe_path
    else:
        exe_name = clean_exe_name(proc_or_name)
    path = normalize_path(exe_path) if exe_path else ""
    if exe_name in BLOCKED_TARGET_EXES:
        return f"{exe_name} is a Windows system or host process and cannot be targeted safely."
    if path and any(part in path for part in BLOCKED_TARGET_PATH_PARTS):
        return f"{exe_name or path} is under a protected Windows system folder and cannot be targeted safely."
    return ""


def is_blocked_target(proc_or_name: ProcessSnapshot | str, exe_path: str = "") -> bool:
    return bool(blocked_target_reason(proc_or_name, exe_path))


def pretty_minutes(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def format_remaining(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = max(0, int(round(seconds)))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def normalize_pause_lock_text(value: str) -> str:
    value = value.lower()
    value = value.replace("“", "\"").replace("”", "\"").replace("‘", "'").replace("’", "'")
    return "".join(re.findall(r"[\w\u4e00-\u9fff]+", value, flags=re.UNICODE))


def pause_lock_similarity(candidate: str, expected_text: str = PAUSE_LOCK_TARGET) -> float:
    expected = normalize_pause_lock_text(expected_text)
    actual = normalize_pause_lock_text(candidate)
    if not actual:
        return 0.0
    if expected in actual or actual in expected:
        shorter = min(len(expected), len(actual))
        longer = max(len(expected), len(actual))
        return shorter / longer if longer else 0.0
    return difflib.SequenceMatcher(None, expected, actual).ratio()


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def write_json(path: Path, payload: Any) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def parse_hhmm(value: str) -> int | None:
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def format_hhmm(minutes: int) -> str:
    minutes = max(0, min(23 * 60 + 59, minutes))
    hour, minute = divmod(minutes, 60)
    return f"{hour:02d}:{minute:02d}"


def schedule_window_active(current_minutes: int, start_minutes: int, end_minutes: int) -> bool:
    if start_minutes == end_minutes:
        return True
    if start_minutes < end_minutes:
        return start_minutes <= current_minutes < end_minutes
    return current_minutes >= start_minutes or current_minutes < end_minutes


@dataclass
class Rule:
    id: str
    name: str
    exe_name: str
    exe_path: str = ""
    match_mode: str = "path"
    time_mode: str = "runtime"
    usage_limit_minutes: float = 10.0
    cooldown_minutes: float = 5.0
    action: str = "close"
    overlay_top_inset: int = 0
    enabled: bool = True
    created_at: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Rule":
        exe_path = str(data.get("exe_path") or "")
        exe_name = clean_exe_name(str(data.get("exe_name") or exe_path))
        match_mode = str(data.get("match_mode") or ("path" if exe_path else "name"))
        if match_mode not in {"path", "name"}:
            match_mode = "path" if exe_path else "name"
        if match_mode == "path" and not exe_path:
            match_mode = "name"
        time_mode = str(data.get("time_mode") or "runtime")
        if time_mode not in {"runtime", "foreground"}:
            time_mode = "runtime"
        action = str(data.get("action") or "close")
        if action not in {"close", "overlay"}:
            action = "close"

        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            name=str(data.get("name") or exe_name or "Unnamed app"),
            exe_name=exe_name,
            exe_path=exe_path,
            match_mode=match_mode,
            time_mode=time_mode,
            usage_limit_minutes=float(data.get("usage_limit_minutes") or 10.0),
            cooldown_minutes=float(data.get("cooldown_minutes") or 5.0),
            action=action,
            overlay_top_inset=max(0, int(data.get("overlay_top_inset") or 0)),
            enabled=bool(data.get("enabled", True)),
            created_at=float(data.get("created_at") or now_ts()),
        )

    @property
    def match_key(self) -> str:
        if self.match_mode == "path" and self.exe_path:
            return normalize_path(self.exe_path)
        return self.exe_name.lower()

    @property
    def display_match(self) -> str:
        if self.match_mode == "path" and self.exe_path:
            return self.exe_path
        return self.exe_name

    @property
    def match_mode_label(self) -> str:
        return "Path" if self.match_mode == "path" else "EXE"


@dataclass
class ProcessSnapshot:
    pid: int
    name: str
    exe: str
    create_time: float
    process: Any


@dataclass
class TemporaryTimer:
    id: str
    name: str
    exe_name: str
    exe_path: str = ""
    match_mode: str = "path"
    time_mode: str = "runtime"
    duration_minutes: float = 30.0
    action: str = "close"
    overlay_top_inset: int = 0
    created_at: float = 0.0
    elapsed_seconds: float = 0.0
    last_counted_at: float | None = None
    triggered: bool = False
    cooldown_until: float = 0.0

    @property
    def match_key(self) -> str:
        if self.match_mode == "path" and self.exe_path:
            return normalize_path(self.exe_path)
        return self.exe_name.lower()

    @property
    def display_match(self) -> str:
        if self.match_mode == "path" and self.exe_path:
            return self.exe_path
        return self.exe_name


if os.name == "nt":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.GetAncestor.restype = wintypes.HWND
else:
    user32 = None
    EnumWindowsProc = None


def get_visible_windows_for_pid(pid: int) -> list[int]:
    if user32 is None or EnumWindowsProc is None:
        return []

    hwnds: list[int] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value != pid:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.IsIconic(hwnd):
            return True
        if user32.GetWindowTextLengthW(hwnd) <= 0:
            return True
        hwnds.append(int(hwnd))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return hwnds


def get_foreground_window_info() -> tuple[int | None, int | None]:
    if user32 is None:
        return None, None
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(hwnd), int(pid.value) if pid.value else None


def get_foreground_pid() -> int | None:
    _hwnd, pid = get_foreground_window_info()
    return pid


def get_window_pid(hwnd: int) -> int | None:
    if user32 is None or not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) if pid.value else None


def get_window_under_cursor() -> tuple[int | None, int | None]:
    if user32 is None:
        return None, None
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return None, None
    hwnd = user32.WindowFromPoint(point)
    if not hwnd:
        return None, None
    root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    return int(root), get_window_pid(int(root))


def is_key_down(vkey: int) -> bool:
    if user32 is None:
        return False
    return bool(user32.GetAsyncKeyState(vkey) & 0x8000)


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if user32 is None:
        return None
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        return None
    return int(rect.left), int(rect.top), width, height


def get_window_content_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if user32 is None:
        return None

    client = wintypes.RECT()
    if user32.GetClientRect(hwnd, ctypes.byref(client)):
        point = wintypes.POINT(0, 0)
        if user32.ClientToScreen(hwnd, ctypes.byref(point)):
            width = int(client.right - client.left)
            height = int(client.bottom - client.top)
            if width > 0 and height > 0:
                return int(point.x), int(point.y), width, height

    window_rect = get_window_rect(hwnd)
    if window_rect is None:
        return None
    x, y, width, height = window_rect
    top_safe_area = min(96, max(40, int(height * 0.08)))
    return x, y + top_safe_area, width, max(1, height - top_safe_area)


def get_rule_window_rects(
    matches: list[ProcessSnapshot],
    foreground_hwnd: int | None = None,
) -> dict[int, tuple[int, int, int, int]]:
    rects: dict[int, tuple[int, int, int, int]] = {}
    for proc in matches:
        for hwnd in get_visible_windows_for_pid(proc.pid):
            if foreground_hwnd is not None and hwnd != foreground_hwnd:
                continue
            rect = get_window_content_rect(hwnd)
            if rect is not None:
                rects[hwnd] = rect
    return rects


def make_window_no_activate(widget: tk.Toplevel) -> None:
    if user32 is None:
        return
    try:
        widget.update_idletasks()
        hwnd = int(widget.winfo_id())
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
    except Exception:
        pass


def enumerate_processes() -> list[ProcessSnapshot]:
    if psutil is None:
        return []

    snapshots: list[ProcessSnapshot] = []
    for proc in psutil.process_iter(["pid", "name", "exe", "create_time"]):
        try:
            info = proc.info
            pid = int(info.get("pid") or proc.pid)
            name = str(info.get("name") or "")
            exe = str(info.get("exe") or "")
            create_time = float(info.get("create_time") or 0.0)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception:
            continue

        if not name and exe:
            name = Path(exe).name
        if not name:
            continue
        snapshots.append(ProcessSnapshot(pid, name, exe, create_time, proc))

    return snapshots


def process_snapshot_from_pid(pid: int) -> ProcessSnapshot | None:
    if psutil is None or pid <= 0:
        return None
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        try:
            exe = proc.exe()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            exe = ""
        create_time = proc.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    except Exception:
        return None
    if not name and exe:
        name = Path(exe).name
    if not name:
        return None
    return ProcessSnapshot(pid, name, exe, create_time, proc)


def rule_matches_process(rule: Rule, proc: ProcessSnapshot, own_pid: int) -> bool:
    if proc.pid == own_pid:
        return False

    proc_name = clean_exe_name(proc.name or proc.exe)
    if rule.match_mode == "path" and rule.exe_path:
        proc_path = normalize_path(proc.exe)
        if proc_path:
            return proc_path == normalize_path(rule.exe_path)
        return proc_name == rule.exe_name.lower()

    return proc_name == rule.exe_name.lower()


def same_target(left: Rule | TemporaryTimer, right: Rule | TemporaryTimer) -> bool:
    left_path = normalize_path(left.exe_path) if left.exe_path else ""
    right_path = normalize_path(right.exe_path) if right.exe_path else ""
    if left_path and right_path:
        return left_path == right_path
    return left.exe_name.lower() == right.exe_name.lower()


def safe_process_label(proc: Any) -> str:
    try:
        return f"{proc.name()}({proc.pid})"
    except Exception:
        return f"PID {getattr(proc, 'pid', 'unknown')}"


def taskkill_process(pid: int) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception as exc:
        return False, str(exc)

    output = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode == 0, output


def wait_for_processes(procs: list[Any], timeout: float) -> tuple[list[Any], list[Any], list[str]]:
    if not procs:
        return [], [], []
    try:
        gone, alive = psutil.wait_procs(procs, timeout=timeout)
        return gone, alive, []
    except Exception as exc:
        alive = []
        for proc in procs:
            try:
                if proc.is_running():
                    alive.append(proc)
            except Exception:
                alive.append(proc)
        return [], alive, [str(exc)]


class RuleEditor(tk.Toplevel):
    def __init__(self, parent: tk.Widget, rule: Rule) -> None:
        super().__init__(parent)
        self.title("Edit rule")
        self.resizable(False, False)
        self.result: dict[str, Any] | None = None

        self.name_var = tk.StringVar(value=rule.name)
        self.usage_var = tk.StringVar(value=pretty_minutes(rule.usage_limit_minutes))
        self.cooldown_var = tk.StringVar(value=pretty_minutes(rule.cooldown_minutes))
        self.time_mode_var = tk.StringVar(value=rule.time_mode)
        self.action_var = tk.StringVar(value=rule.action)
        self.overlay_top_inset_var = tk.StringVar(value=str(rule.overlay_top_inset))
        self.enabled_var = tk.BooleanVar(value=rule.enabled)

        body = ttk.Frame(self, padding=16)
        body.grid(row=0, column=0, sticky="nsew")

        ttk.Label(body, text="Name").grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Entry(body, textvariable=self.name_var, width=44).grid(
            row=0,
            column=1,
            sticky="ew",
            pady=(0, 8),
        )

        ttk.Label(body, text="Match mode").grid(row=1, column=0, sticky="w", pady=(0, 8))
        ttk.Label(body, text=rule.match_mode_label).grid(
            row=1,
            column=1,
            sticky="w",
            pady=(0, 8),
        )

        ttk.Label(body, text="Target").grid(row=2, column=0, sticky="nw", pady=(0, 8))
        target = ttk.Label(body, text=rule.display_match, wraplength=420)
        target.grid(row=2, column=1, sticky="w", pady=(0, 8))

        ttk.Label(body, text="Usage minutes").grid(row=3, column=0, sticky="w", pady=(0, 8))
        ttk.Entry(body, textvariable=self.usage_var, width=12).grid(
            row=3,
            column=1,
            sticky="w",
            pady=(0, 8),
        )

        ttk.Label(body, text="Cooldown minutes").grid(row=4, column=0, sticky="w", pady=(0, 8))
        ttk.Entry(body, textvariable=self.cooldown_var, width=12).grid(
            row=4,
            column=1,
            sticky="w",
            pady=(0, 8),
        )

        ttk.Label(body, text="Time mode").grid(row=5, column=0, sticky="w", pady=(0, 8))
        ttk.Combobox(
            body,
            textvariable=self.time_mode_var,
            values=("runtime", "foreground"),
            state="readonly",
            width=14,
        ).grid(row=5, column=1, sticky="w", pady=(0, 8))

        ttk.Checkbutton(body, text="Enabled", variable=self.enabled_var).grid(
            row=6,
            column=1,
            sticky="w",
            pady=(4, 12),
        )

        ttk.Label(body, text="Action").grid(row=7, column=0, sticky="w", pady=(0, 8))
        ttk.Combobox(
            body,
            textvariable=self.action_var,
            values=("close", "overlay"),
            state="readonly",
            width=12,
        ).grid(row=7, column=1, sticky="w", pady=(0, 8))

        ttk.Label(body, text="Overlay top inset px").grid(row=8, column=0, sticky="w", pady=(0, 8))
        ttk.Entry(body, textvariable=self.overlay_top_inset_var, width=12).grid(
            row=8,
            column=1,
            sticky="w",
            pady=(0, 8),
        )

        buttons = ttk.Frame(body)
        buttons.grid(row=9, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", command=self.save).pack(side="right", padx=(0, 8))

        self.bind("<Return>", lambda _event: self.save())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.wait_visibility()
        self.focus_force()

    def save(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Name required", "Please enter a rule name.", parent=self)
            return

        try:
            usage = float(self.usage_var.get())
            cooldown = float(self.cooldown_var.get())
            overlay_top_inset = int(self.overlay_top_inset_var.get() or "0")
        except ValueError:
            messagebox.showwarning("Invalid time", "Minutes must be a number, such as 10 or 0.2.", parent=self)
            return

        if usage <= 0 or cooldown <= 0:
            messagebox.showwarning("Invalid time", "Usage and cooldown minutes must be greater than 0.", parent=self)
            return

        if overlay_top_inset < 0:
            messagebox.showwarning("Invalid inset", "Overlay top inset must be 0 or greater.", parent=self)
            return

        self.result = {
            "name": name,
            "usage_limit_minutes": usage,
            "cooldown_minutes": cooldown,
            "time_mode": self.time_mode_var.get(),
            "action": self.action_var.get(),
            "overlay_top_inset": overlay_top_inset,
            "enabled": bool(self.enabled_var.get()),
        }
        self.destroy()


class ProcessPicker(tk.Toplevel):
    def __init__(self, parent: "AppUsageLimiter") -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.title("Add from running processes")
        self.geometry("900x520")
        self.minsize(760, 420)
        self.result: ProcessSnapshot | None = None
        self.processes: list[ProcessSnapshot] = []
        self.filtered: dict[str, ProcessSnapshot] = {}
        self.search_var = tk.StringVar()

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        top = ttk.Frame(body)
        top.pack(fill="x", pady=(0, 10))
        ttk.Label(top, text="Search").pack(side="left")
        search = ttk.Entry(top, textvariable=self.search_var)
        search.pack(side="left", fill="x", expand=True, padx=(8, 8))
        ttk.Button(top, text="Refresh", command=self.reload_processes).pack(side="left")

        columns = ("pid", "name", "path")
        self.tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("pid", text="PID")
        self.tree.heading("name", text="Process")
        self.tree.heading("path", text="Path")
        self.tree.column("pid", width=80, anchor="e", stretch=False)
        self.tree.column("name", width=180, stretch=False)
        self.tree.column("path", width=620, stretch=True)

        yscroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(body, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="left", fill="y")
        xscroll.pack(fill="x", side="bottom")

        bottom = ttk.Frame(self, padding=(12, 0, 12, 12))
        bottom.pack(fill="x")
        self.info_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.info_var).pack(side="left", fill="x", expand=True)
        ttk.Button(bottom, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text="Add selected", command=self.add_selected).pack(
            side="right",
            padx=(0, 8),
        )

        self.search_var.trace_add("write", lambda *_args: self.apply_filter())
        self.tree.bind("<Double-1>", lambda _event: self.add_selected())
        self.bind("<Escape>", lambda _event: self.destroy())

        self.transient(parent)
        self.reload_processes()
        search.focus_set()

    def reload_processes(self) -> None:
        self.processes = sorted(
            [proc for proc in enumerate_processes() if not is_blocked_target(proc)],
            key=lambda item: (item.name.lower(), item.exe.lower(), item.pid),
        )
        self.apply_filter()

    def apply_filter(self) -> None:
        query = self.search_var.get().strip().lower()
        for item_id in self.tree.get_children():
            self.tree.delete(item_id)
        self.filtered.clear()

        count = 0
        for index, proc in enumerate(self.processes):
            haystack = f"{proc.pid} {proc.name} {proc.exe}".lower()
            if query and query not in haystack:
                continue
            item_id = str(index)
            self.filtered[item_id] = proc
            self.tree.insert("", "end", iid=item_id, values=(proc.pid, proc.name, proc.exe))
            count += 1

        self.info_var.set(f"Showing {count} processes. Full path is preferred; EXE name is used when path is unavailable.")

    def add_selected(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("No selection", "Please select a running process first.", parent=self)
            return

        proc = self.filtered.get(selected[0])
        if proc is None:
            return
        reason = blocked_target_reason(proc)
        if reason:
            messagebox.showwarning("Blocked target", reason, parent=self)
            return
        self.result = proc
        self.destroy()


class WindowPickDialog(tk.Toplevel):
    def __init__(self, parent: tk.Toplevel, own_pid: int) -> None:
        super().__init__(parent)
        self.title("Pick window")
        self.resizable(False, False)
        self.result: ProcessSnapshot | None = None
        self.own_pid = own_pid
        self.waiting_for_release = is_key_down(VK_LBUTTON)
        self.info_var = tk.StringVar(value="Click the visible window you want to limit. Press Esc to cancel.")

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Pick Target Window", font=("", 12, "bold")).pack(anchor="w")
        ttk.Label(body, textvariable=self.info_var, wraplength=360).pack(anchor="w", pady=(6, 0))

        self.attributes("-topmost", True)
        self.geometry("+60+60")
        self.bind("<Escape>", lambda _event: self.destroy())
        self.after(120, self.poll_click)

    def poll_click(self) -> None:
        if not self.winfo_exists():
            return
        down = is_key_down(VK_LBUTTON)
        if self.waiting_for_release:
            if not down:
                self.waiting_for_release = False
            self.after(40, self.poll_click)
            return
        if down:
            _hwnd, pid = get_window_under_cursor()
            if pid is None:
                self.info_var.set("Could not identify that window. Try again, or press Esc to cancel.")
                self.waiting_for_release = True
                self.after(120, self.poll_click)
                return
            if pid == self.own_pid:
                self.info_var.set("That was this tool window. Click the target app window instead.")
                self.waiting_for_release = True
                self.after(120, self.poll_click)
                return
            proc = process_snapshot_from_pid(pid)
            if proc is None:
                self.info_var.set("Found a window, but could not read its process. Try another visible part of it.")
                self.waiting_for_release = True
                self.after(120, self.poll_click)
                return
            reason = blocked_target_reason(proc)
            if reason:
                messagebox.showwarning("Blocked target", reason, parent=self)
                self.info_var.set("That window belongs to a protected Windows system or host process. Pick another app window.")
                self.waiting_for_release = True
                self.after(120, self.poll_click)
                return
            self.result = proc
            self.destroy()
            return
        self.after(40, self.poll_click)


class TemporaryTimerDialog(tk.Toplevel):
    def __init__(self, parent: "AppUsageLimiter", default_pid: int | None) -> None:
        super().__init__(parent)
        self.title("One-shot timer")
        self.resizable(True, False)
        self.result: TemporaryTimer | None = None
        self.processes: list[ProcessSnapshot] = []
        self.process_lookup: dict[str, ProcessSnapshot] = {}

        self.target_var = tk.StringVar(value="")
        self.duration_var = tk.StringVar(value="30")
        self.time_mode_var = tk.StringVar(value="runtime")
        self.action_var = tk.StringVar(value="close")
        self.inset_var = tk.StringVar(value="0")
        self.info_var = tk.StringVar(value="Alt+L detected. Pick a target and start a one-shot timer.")

        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="Target").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.target_combo = ttk.Combobox(body, textvariable=self.target_var, state="readonly")
        self.target_combo.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        ttk.Button(body, text="Refresh", command=lambda: self.reload_processes(default_pid=None)).grid(
            row=0,
            column=2,
            sticky="ew",
            padx=(8, 0),
            pady=(0, 8),
        )
        ttk.Button(body, text="Pick window", command=self.pick_window).grid(
            row=0,
            column=3,
            sticky="ew",
            padx=(8, 0),
            pady=(0, 8),
        )

        ttk.Label(body, text="Minutes").grid(row=1, column=0, sticky="w", pady=(0, 8))
        duration = ttk.Combobox(
            body,
            textvariable=self.duration_var,
            values=("3", "5", "10", "15", "30", "45", "60"),
        )
        duration.grid(row=1, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(body, text="Time mode").grid(row=2, column=0, sticky="w", pady=(0, 8))
        ttk.Combobox(
            body,
            textvariable=self.time_mode_var,
            values=("runtime", "foreground"),
            state="readonly",
        ).grid(row=2, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(body, text="Action").grid(row=3, column=0, sticky="w", pady=(0, 8))
        ttk.Combobox(
            body,
            textvariable=self.action_var,
            values=("close", "overlay"),
            state="readonly",
        ).grid(row=3, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(body, text="Overlay inset").grid(row=4, column=0, sticky="w", pady=(0, 8))
        ttk.Entry(body, textvariable=self.inset_var).grid(row=4, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(body, textvariable=self.info_var, wraplength=560).grid(
            row=5,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(4, 12),
        )

        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Start timer", command=self.start_timer).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Return>", lambda _event: self.start_timer())
        self.transient(parent)
        self.attributes("-topmost", True)
        self.reload_processes(default_pid=default_pid)
        self.target_combo.focus_set()
        self.after(100, self.lift)

    def process_label(self, proc: ProcessSnapshot) -> str:
        path = proc.exe or proc.name
        return f"{proc.name} | PID {proc.pid} | {path}"

    def select_process(self, proc: ProcessSnapshot, prefix: str = "Selected app") -> None:
        label = self.process_label(proc)
        self.process_lookup[label] = proc
        values = list(self.target_combo["values"])
        if label not in values:
            values.insert(0, label)
            self.target_combo["values"] = values
        self.target_var.set(label)
        self.info_var.set(f"{prefix}: {proc.name}. This timer will not be saved as a permanent rule.")

    def reload_processes(self, default_pid: int | None = None) -> None:
        processes = [
            proc
            for proc in enumerate_processes()
            if proc.pid != os.getpid() and clean_exe_name(proc.name or proc.exe) and not is_blocked_target(proc)
        ]
        processes.sort(key=lambda item: (0 if default_pid and item.pid == default_pid else 1, item.name.lower(), item.pid))
        self.processes = processes
        self.process_lookup.clear()
        values = []
        for proc in self.processes:
            label = self.process_label(proc)
            self.process_lookup[label] = proc
            values.append(label)
        self.target_combo["values"] = values
        if values:
            selected = self.process_lookup[values[0]]
            prefix = "Selected foreground app" if default_pid and selected.pid == default_pid else "Selected app"
            self.select_process(selected, prefix)
        else:
            self.target_var.set("")
            self.info_var.set("No usable running process was found.")

    def pick_window(self) -> None:
        self.withdraw()
        picker = WindowPickDialog(self, os.getpid())
        self.wait_window(picker)
        self.deiconify()
        self.lift()
        if picker.result is None:
            self.info_var.set("Window pick cancelled.")
            return
        self.select_process(picker.result, "Picked window")

    def start_timer(self) -> None:
        proc = self.process_lookup.get(self.target_var.get())
        if proc is None:
            messagebox.showwarning("No target", "Please choose a running process.", parent=self)
            return
        reason = blocked_target_reason(proc)
        if reason:
            messagebox.showwarning("Blocked target", reason, parent=self)
            return
        try:
            duration_minutes = float(self.duration_var.get().strip())
        except ValueError:
            messagebox.showwarning("Invalid minutes", "Minutes must be a number.", parent=self)
            return
        if duration_minutes <= 0:
            messagebox.showwarning("Invalid minutes", "Minutes must be greater than zero.", parent=self)
            return
        try:
            overlay_top_inset = max(0, int(float(self.inset_var.get().strip() or "0")))
        except ValueError:
            messagebox.showwarning("Invalid inset", "Overlay inset must be a number.", parent=self)
            return

        exe_path = proc.exe if proc.exe and Path(proc.exe).suffix.lower() == ".exe" else ""
        exe_name = clean_exe_name(proc.name or proc.exe)
        if not exe_name and exe_path:
            exe_name = clean_exe_name(exe_path)
        if not exe_name:
            messagebox.showwarning("Cannot start", "This process does not have a usable EXE name.", parent=self)
            return

        self.result = TemporaryTimer(
            id=f"temp-{uuid.uuid4()}",
            name=f"One-shot: {Path(exe_name).stem or exe_name}",
            exe_name=exe_name,
            exe_path=exe_path,
            match_mode="path" if exe_path else "name",
            time_mode=self.time_mode_var.get(),
            duration_minutes=duration_minutes,
            action=self.action_var.get(),
            overlay_top_inset=overlay_top_inset,
            created_at=now_ts(),
        )
        self.destroy()


class CountdownMiniWindow(tk.Toplevel):
    def __init__(self, parent: tk.Tk) -> None:
        super().__init__(parent)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#111827")
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._has_position = False

        self.name_var = tk.StringVar(value="")
        self.time_var = tk.StringVar(value="")

        frame = tk.Frame(self, bg="#111827", padx=14, pady=10)
        frame.pack(fill="both", expand=True)

        tk.Label(
            frame,
            text="Time left",
            bg="#111827",
            fg="#9ca3af",
            font=("Segoe UI", 9),
        ).pack(anchor="w")
        tk.Label(
            frame,
            textvariable=self.time_var,
            bg="#111827",
            fg="#ffffff",
            font=("Segoe UI", 20, "bold"),
        ).pack(anchor="w")
        tk.Label(
            frame,
            textvariable=self.name_var,
            bg="#111827",
            fg="#d1d5db",
            font=("Segoe UI", 10),
        ).pack(anchor="w")

        self.bind("<ButtonPress-1>", self.start_drag)
        self.bind("<B1-Motion>", self.drag)
        for child in frame.winfo_children():
            child.bind("<ButtonPress-1>", self.start_drag)
            child.bind("<B1-Motion>", self.drag)

    def start_drag(self, event: tk.Event) -> None:
        self._drag_start_x = event.x_root - self.winfo_x()
        self._drag_start_y = event.y_root - self.winfo_y()

    def drag(self, event: tk.Event) -> None:
        x = event.x_root - self._drag_start_x
        y = event.y_root - self._drag_start_y
        self._has_position = True
        self.geometry(f"+{x}+{y}")

    def show_countdown(self, rule_name: str, remaining_seconds: float) -> None:
        self.name_var.set(rule_name)
        self.time_var.set(format_remaining(remaining_seconds))
        self.attributes("-topmost", True)
        if not self.winfo_viewable():
            if not self._has_position:
                self.geometry("+80+80")
                self._has_position = True
            self.deiconify()
        self.lift()

    def hide_countdown(self) -> None:
        if self.winfo_exists() and self.winfo_viewable():
            self.withdraw()


class WindowOverlay(tk.Toplevel):
    def __init__(self, parent: tk.Tk) -> None:
        super().__init__(parent)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.88)
        except tk.TclError:
            pass
        self.configure(bg="#050505")

        self.rule_var = tk.StringVar(value="")
        self.time_var = tk.StringVar(value="")

        frame = tk.Frame(self, bg="#050505")
        frame.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(
            frame,
            text="Locked for a break",
            bg="#050505",
            fg="#ffffff",
            font=("Segoe UI", 24, "bold"),
        ).pack(pady=(0, 8))
        tk.Label(
            frame,
            textvariable=self.rule_var,
            bg="#050505",
            fg="#d1d5db",
            font=("Segoe UI", 14),
        ).pack(pady=(0, 8))
        tk.Label(
            frame,
            textvariable=self.time_var,
            bg="#050505",
            fg="#fbbf24",
            font=("Segoe UI", 28, "bold"),
        ).pack()
        make_window_no_activate(self)

    def update_overlay(self, rule_name: str, remaining_seconds: float, rect: tuple[int, int, int, int]) -> None:
        x, y, width, height = rect
        self.rule_var.set(rule_name)
        self.time_var.set(format_remaining(remaining_seconds))
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.attributes("-topmost", True)
        if not self.winfo_viewable():
            self.deiconify()
        self.lift()


class OverlayManager:
    def __init__(self, parent: tk.Tk) -> None:
        self.parent = parent
        self.overlays: dict[tuple[str, int], WindowOverlay] = {}

    def sync_rule(
        self,
        rule_id: str,
        rule_name: str,
        matches: list[ProcessSnapshot],
        remaining_seconds: float,
        top_inset: int = 0,
        foreground_hwnd: int | None = None,
    ) -> None:
        rects = get_rule_window_rects(matches, foreground_hwnd)
        active_keys: set[tuple[str, int]] = set()
        for hwnd, rect in rects.items():
            if top_inset > 0:
                x, y, width, height = rect
                inset = min(top_inset, max(0, height - 1))
                rect = (x, y + inset, width, height - inset)
            key = (rule_id, hwnd)
            active_keys.add(key)
            overlay = self.overlays.get(key)
            if overlay is None or not overlay.winfo_exists():
                overlay = WindowOverlay(self.parent)
                self.overlays[key] = overlay
            overlay.update_overlay(rule_name, remaining_seconds, rect)

        for key in list(self.overlays):
            if key[0] == rule_id and key not in active_keys:
                self.destroy_overlay(key)

    def hide_rule(self, rule_id: str) -> None:
        for key in list(self.overlays):
            if key[0] == rule_id:
                self.destroy_overlay(key)

    def hide_all(self) -> None:
        for key in list(self.overlays):
            self.destroy_overlay(key)

    def destroy_overlay(self, key: tuple[str, int]) -> None:
        overlay = self.overlays.pop(key, None)
        if overlay is not None and overlay.winfo_exists():
            overlay.destroy()


class PauseVerificationDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, require_full_text: bool = False, action_label: str = "pause") -> None:
        super().__init__(parent)
        self.title("Pause verification")
        self.resizable(True, False)
        self.allowed = False
        self.require_full_text = require_full_text
        self.expected_text = PAUSE_LOCK_FULL_TARGET if require_full_text else PAUSE_LOCK_TARGET
        self.feedback_var = tk.StringVar(value=f"Type the required text below to unlock {action_label}.")

        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)

        if not require_full_text:
            ttk.Label(body, text=PAUSE_LOCK_PROMPT, wraplength=760, justify="left").pack(
                fill="x",
                anchor="w",
                pady=(0, 12),
            )

        ttk.Label(
            body,
            text="Required full text:" if require_full_text else "Required sentence:",
            font=("", 9, "bold"),
        ).pack(anchor="w")
        ttk.Label(body, text=self.expected_text, wraplength=760, justify="left").pack(
            fill="x",
            anchor="w",
            pady=(2, 10),
        )

        self.input = tk.Text(body, height=8 if require_full_text else 4, width=92, wrap="word")
        self.input.pack(fill="x", expand=True)
        self.input.bind("<KeyRelease>", lambda _event: self.update_feedback())

        ttk.Label(body, textvariable=self.feedback_var).pack(anchor="w", pady=(8, 12))

        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Unlock", command=self.try_unlock).pack(
            side="right",
            padx=(0, 8),
        )

        self.bind("<Escape>", lambda _event: self.destroy())
        self.transient(parent)
        self.grab_set()
        self.wait_visibility()
        self.input.focus_set()

    def current_similarity(self) -> float:
        return pause_lock_similarity(self.input.get("1.0", "end"), self.expected_text)

    def update_feedback(self) -> None:
        score = self.current_similarity()
        self.feedback_var.set(f"Current match: {score:.0%}. Required: {PAUSE_LOCK_THRESHOLD:.0%}.")

    def try_unlock(self) -> None:
        score = self.current_similarity()
        if score >= PAUSE_LOCK_THRESHOLD:
            self.allowed = True
            self.destroy()
            return
        self.feedback_var.set(
            f"Not close enough yet: {score:.0%}. Punctuation does not need to be exact, but the sentence needs to be recognizable."
        )


class AppUsageLimiter(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("App Usage Limiter")
        self.geometry("1040x640")
        self.minsize(900, 520)

        self.own_pid = os.getpid()
        self.rules: list[Rule] = []
        self.state: dict[str, dict[str, Any]] = {}
        self.display_state: dict[str, dict[str, str]] = {}
        self.temporary_timers: list[TemporaryTimer] = []
        self.monitoring = True
        self.state_dirty = False
        self.last_tick_error = ""
        self.hotkey_was_down = False
        self.timer_dialog_open = False
        self.countdown_window: CountdownMiniWindow | None = None
        self.overlay_manager: OverlayManager | None = None

        self.status_var = tk.StringVar(value="Monitoring")
        self.summary_var = tk.StringVar(value="")
        self.monitor_button_text = tk.StringVar(value="Pause daily rules")
        self.schedule_enabled_var = tk.BooleanVar(value=False)
        self.schedule_start_var = tk.StringVar(value="08:00")
        self.schedule_end_var = tk.StringVar(value="22:00")
        self.schedule_status_var = tk.StringVar(value="Schedule disabled")
        self.schedule_last_active: bool | None = None

        self.configure_styles()
        self.load_rules()
        self.load_state()
        self.load_schedule()
        self.build_ui()
        self.countdown_window = CountdownMiniWindow(self)
        self.overlay_manager = OverlayManager(self)
        self.refresh_table()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(200, self.monitor_tick)
        self.after(SHORTCUT_POLL_MS, self.poll_shortcut)

    def configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=26)
        style.configure("Primary.TButton", padding=(12, 6))
        style.configure("Toolbar.TButton", padding=(10, 5))

    def build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root)
        header.pack(fill="x", pady=(0, 12))

        title_group = ttk.Frame(header)
        title_group.pack(side="left", fill="x", expand=True)
        ttk.Label(title_group, text="Windows App Usage Limiter", font=("", 16, "bold")).pack(
            anchor="w",
        )
        ttk.Label(title_group, textvariable=self.summary_var).pack(anchor="w", pady=(4, 0))

        ttk.Button(
            header,
            textvariable=self.monitor_button_text,
            style="Primary.TButton",
            command=self.toggle_monitoring,
        ).pack(side="right")
        ttk.Button(
            header,
            text="Emergency stop all",
            style="Primary.TButton",
            command=self.emergency_stop_all,
        ).pack(side="right", padx=(0, 8))

        toolbar = ttk.Frame(root)
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Button(
            toolbar,
            text="Add from running",
            style="Toolbar.TButton",
            command=self.add_from_running,
        ).pack(side="left")
        ttk.Button(
            toolbar,
            text="Add EXE",
            style="Toolbar.TButton",
            command=self.add_from_file,
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            toolbar,
            text="One-shot timer (Alt+L)",
            style="Toolbar.TButton",
            command=self.open_temporary_timer_dialog,
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            toolbar,
            text="Stop all one-time",
            style="Toolbar.TButton",
            command=self.stop_all_temporary_timers,
        ).pack(side="left", padx=(8, 0))
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(toolbar, text="Edit", style="Toolbar.TButton", command=self.edit_selected).pack(
            side="left",
        )
        ttk.Button(
            toolbar,
            text="Enable/Disable",
            style="Toolbar.TButton",
            command=self.toggle_selected,
        ).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Delete", style="Toolbar.TButton", command=self.delete_selected).pack(
            side="left",
            padx=(8, 0),
        )
        ttk.Button(toolbar, text="Refresh", style="Toolbar.TButton", command=self.refresh_table).pack(
            side="left",
            padx=(8, 0),
        )

        table_frame = ttk.Frame(root)
        table_frame.pack(fill="both", expand=True)

        columns = (
            "kind",
            "name",
            "match_mode",
            "target",
            "usage",
            "cooldown",
            "time_mode",
            "action",
            "overlay_inset",
            "enabled",
            "status",
            "remaining",
        )
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "kind": "Kind",
            "name": "Name",
            "match_mode": "Match",
            "target": "Target",
            "usage": "Usage",
            "cooldown": "Cooldown",
            "time_mode": "Time",
            "action": "Action",
            "overlay_inset": "Inset",
            "enabled": "Enabled",
            "status": "Status",
            "remaining": "Remaining",
        }
        widths = {
            "kind": 74,
            "name": 150,
            "match_mode": 64,
            "target": 360,
            "usage": 78,
            "cooldown": 78,
            "enabled": 56,
            "status": 128,
            "remaining": 86,
        }
        headings.update({"action": "Action", "overlay_inset": "Inset"})
        widths.update({"time_mode": 92, "action": 76, "overlay_inset": 64})
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                stretch=column == "target",
                anchor="w",
            )

        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        schedule = ttk.LabelFrame(root, text="Daily schedule", padding=(10, 8))
        schedule.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(schedule, text="Enable schedule", variable=self.schedule_enabled_var).pack(side="left")
        ttk.Label(schedule, text="Start").pack(side="left", padx=(16, 4))
        ttk.Entry(schedule, textvariable=self.schedule_start_var, width=7).pack(side="left")
        ttk.Label(schedule, text="End").pack(side="left", padx=(10, 4))
        ttk.Entry(schedule, textvariable=self.schedule_end_var, width=7).pack(side="left")
        ttk.Button(schedule, text="Save schedule", command=self.save_schedule_from_ui).pack(side="left", padx=(10, 0))
        ttk.Label(schedule, textvariable=self.schedule_status_var).pack(side="left", padx=(16, 0), fill="x", expand=True)

        footer = ttk.Frame(root)
        footer.pack(fill="x", pady=(10, 0))
        ttk.Label(footer, textvariable=self.status_var).pack(side="left", fill="x", expand=True)

    def load_schedule(self) -> None:
        payload = read_json(SCHEDULE_PATH, {})
        if not isinstance(payload, dict):
            payload = {}
        self.schedule_enabled_var.set(bool(payload.get("enabled", False)))
        start = str(payload.get("start") or "08:00")
        end = str(payload.get("end") or "22:00")
        self.schedule_start_var.set(start if parse_hhmm(start) is not None else "08:00")
        self.schedule_end_var.set(end if parse_hhmm(end) is not None else "22:00")
        self.update_schedule_status()

    def save_schedule(self) -> None:
        write_json(
            SCHEDULE_PATH,
            {
                "version": 1,
                "updated_at": now_ts(),
                "enabled": bool(self.schedule_enabled_var.get()),
                "start": self.schedule_start_var.get().strip(),
                "end": self.schedule_end_var.get().strip(),
            },
        )

    def save_schedule_from_ui(self) -> None:
        start = parse_hhmm(self.schedule_start_var.get())
        end = parse_hhmm(self.schedule_end_var.get())
        if start is None or end is None:
            messagebox.showwarning("Invalid schedule", "Use 24-hour HH:MM times, such as 08:00 or 22:00.", parent=self)
            return

        will_pause_daily = False
        if self.schedule_enabled_var.get():
            current = time.localtime()
            current_minutes = current.tm_hour * 60 + current.tm_min
            will_pause_daily = self.monitoring and not schedule_window_active(current_minutes, start, end)
        if will_pause_daily:
            dialog = PauseVerificationDialog(self, require_full_text=True, action_label="enable a schedule that pauses daily rules now")
            self.wait_window(dialog)
            if not dialog.allowed:
                self.status_var.set("Schedule save cancelled.")
                return

        self.schedule_start_var.set(format_hhmm(start))
        self.schedule_end_var.set(format_hhmm(end))
        self.save_schedule()
        self.apply_schedule(force_status=True)
        self.refresh_table()

    def current_schedule_active(self) -> bool | None:
        if not self.schedule_enabled_var.get():
            return None
        start = parse_hhmm(self.schedule_start_var.get())
        end = parse_hhmm(self.schedule_end_var.get())
        if start is None or end is None:
            return None
        current = time.localtime()
        current_minutes = current.tm_hour * 60 + current.tm_min
        return schedule_window_active(current_minutes, start, end)

    def update_schedule_status(self) -> None:
        active = self.current_schedule_active()
        if active is None:
            if self.schedule_enabled_var.get():
                self.schedule_status_var.set("Schedule has invalid time; daily rules remain manual")
            else:
                self.schedule_status_var.set("Schedule disabled; daily rules are manual")
            return
        state = "active" if active else "inactive"
        self.schedule_status_var.set(
            f"Schedule {state}: {self.schedule_start_var.get().strip()} -> {self.schedule_end_var.get().strip()}"
        )

    def apply_schedule(self, force_status: bool = False) -> None:
        active = self.current_schedule_active()
        if active is None:
            self.schedule_last_active = None
            self.update_schedule_status()
            return
        if self.schedule_last_active is None or self.schedule_last_active != active or force_status:
            self.monitoring = active
            self.monitor_button_text.set("Pause daily rules" if self.monitoring else "Resume daily rules")
            self.status_var.set(
                "Schedule activated daily rules." if active else "Schedule paused daily rules; one-time rules still active."
            )
            self.schedule_last_active = active
        self.update_schedule_status()


    def load_rules(self) -> None:
        payload = read_json(RULES_PATH, {"rules": []})
        raw_rules = payload.get("rules", payload if isinstance(payload, list) else [])
        self.rules = []
        for item in raw_rules:
            if isinstance(item, dict):
                try:
                    self.rules.append(Rule.from_dict(item))
                except Exception:
                    continue

    def save_rules(self) -> None:
        write_json(
            RULES_PATH,
            {
                "version": 1,
                "updated_at": now_ts(),
                "rules": [asdict(rule) for rule in self.rules],
            },
        )

    def load_state(self) -> None:
        payload = read_json(STATE_PATH, {"rules": {}})
        raw_state = payload.get("rules", {}) if isinstance(payload, dict) else {}
        self.state = raw_state if isinstance(raw_state, dict) else {}
        self.prune_state()

    def prune_state(self) -> None:
        valid_ids = {rule.id for rule in self.rules}
        for key in list(self.state):
            if key not in valid_ids:
                del self.state[key]
                self.state_dirty = True
        for rule in self.rules:
            self.get_rule_state(rule.id)

    def save_state(self) -> None:
        self.prune_state()
        write_json(
            STATE_PATH,
            {
                "version": 1,
                "updated_at": now_ts(),
                "rules": self.state,
            },
        )
        self.state_dirty = False

    def get_rule_state(self, rule_id: str) -> dict[str, Any]:
        item = self.state.setdefault(rule_id, {})
        item.setdefault("session_started_at", None)
        item.setdefault("session_elapsed_seconds", 0.0)
        item.setdefault("last_counted_at", None)
        item.setdefault("cooldown_until", 0.0)
        item.setdefault("last_error", "")
        return item

    def selected_rule(self) -> Rule | None:
        selected = self.tree.selection()
        if not selected:
            return None
        selected_id = selected[0]
        for rule in self.rules:
            if rule.id == selected_id:
                return rule
        return None

    def selected_temporary_timer(self) -> TemporaryTimer | None:
        selected = self.tree.selection()
        if not selected:
            return None
        selected_id = selected[0]
        for timer in self.temporary_timers:
            if timer.id == selected_id:
                return timer
        return None

    def temporary_timer_status(self, timer: TemporaryTimer) -> tuple[str, str]:
        current_time = now_ts()
        if timer.triggered:
            remaining = max(0.0, timer.cooldown_until - current_time)
            return "One-time cooldown", format_remaining(remaining)
        remaining = max(0.0, timer.duration_minutes * 60.0 - timer.elapsed_seconds)
        return "One-time running", format_remaining(remaining)

    def toggle_monitoring(self) -> None:
        if self.schedule_enabled_var.get():
            messagebox.showinfo(
                "Schedule enabled",
                "Daily rules are currently controlled by the schedule. Disable the schedule to control them manually.",
                parent=self,
            )
            return
        if self.monitoring:
            dialog = PauseVerificationDialog(self, require_full_text=True, action_label="pause daily rules")
            self.wait_window(dialog)
            if not dialog.allowed:
                self.status_var.set("Pause daily rules cancelled.")
                return
        self.monitoring = not self.monitoring
        self.monitor_button_text.set("Pause daily rules" if self.monitoring else "Resume daily rules")
        self.status_var.set("Daily rules monitoring" if self.monitoring else "Daily rules paused; one-time rules still active")
        self.refresh_table()

    def clear_temporary_timers(self) -> None:
        for timer in list(self.temporary_timers):
            self.hide_overlay_rule(timer)
        self.temporary_timers.clear()
        self.update_countdown_window(None)

    def stop_all_temporary_timers(self) -> None:
        if not self.temporary_timers:
            self.status_var.set("No one-time rules to stop.")
            return
        dialog = PauseVerificationDialog(self, require_full_text=True, action_label="stop all one-time rules")
        self.wait_window(dialog)
        if not dialog.allowed:
            self.status_var.set("Stop all one-time rules cancelled.")
            return
        count = len(self.temporary_timers)
        self.clear_temporary_timers()
        self.status_var.set(f"Stopped {count} one-time rule(s).")
        self.refresh_table()

    def emergency_stop_all(self) -> None:
        dialog = PauseVerificationDialog(self, require_full_text=True, action_label="emergency stop all")
        self.wait_window(dialog)
        if not dialog.allowed:
            self.status_var.set("Emergency stop cancelled.")
            return
        self.monitoring = False
        self.schedule_enabled_var.set(False)
        self.schedule_last_active = None
        self.save_schedule()
        self.update_schedule_status()
        self.monitor_button_text.set("Resume daily rules")
        self.clear_temporary_timers()
        self.hide_all_overlays()
        self.status_var.set("Emergency stop: schedule disabled, daily rules paused, and all one-time rules stopped.")
        self.refresh_table()

    def add_from_running(self) -> None:
        picker = ProcessPicker(self)
        self.wait_window(picker)
        if picker.result is None:
            return
        self.add_rule_from_process(picker.result)

    def add_from_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Choose EXE to monitor",
            filetypes=[("Windows executables", "*.exe"), ("All files", "*.*")],
        )
        if not path:
            return
        reason = blocked_target_reason(Path(path).name, path)
        if reason:
            messagebox.showwarning("Blocked target", reason, parent=self)
            return
        name = Path(path).name
        proc = ProcessSnapshot(0, name, path, 0.0, None)
        self.add_rule_from_process(proc)

    def poll_shortcut(self) -> None:
        try:
            hotkey_down = is_key_down(VK_MENU) and is_key_down(VK_L)
            if hotkey_down and not self.hotkey_was_down:
                self.open_temporary_timer_dialog()
            self.hotkey_was_down = hotkey_down
        finally:
            self.after(SHORTCUT_POLL_MS, self.poll_shortcut)

    def open_temporary_timer_dialog(self) -> None:
        if self.timer_dialog_open:
            return
        _foreground_hwnd, foreground_pid = get_foreground_window_info()
        self.timer_dialog_open = True
        try:
            dialog = TemporaryTimerDialog(self, foreground_pid)
            self.deiconify()
            self.lift()
            self.wait_window(dialog)
            if dialog.result is None:
                return
            self.temporary_timers.append(dialog.result)
            self.update_countdown_window((dialog.result.name, dialog.result.duration_minutes * 60.0))
            self.status_var.set(
                f"Started one-shot timer for {dialog.result.name}: {pretty_minutes(dialog.result.duration_minutes)} min."
            )
        finally:
            self.timer_dialog_open = False

    def add_rule_from_process(self, proc: ProcessSnapshot) -> None:
        reason = blocked_target_reason(proc)
        if reason:
            messagebox.showwarning("Blocked target", reason, parent=self)
            return
        exe_path = proc.exe if proc.exe and Path(proc.exe).suffix.lower() == ".exe" else ""
        exe_name = clean_exe_name(proc.name or proc.exe)
        match_mode = "path" if exe_path else "name"
        if not exe_name and exe_path:
            exe_name = clean_exe_name(exe_path)
        if not exe_name:
            messagebox.showwarning("Cannot add", "This process does not have a usable EXE name.", parent=self)
            return

        candidate = Rule(
            id=str(uuid.uuid4()),
            name=Path(exe_name).stem or exe_name,
            exe_name=exe_name,
            exe_path=exe_path,
            match_mode=match_mode,
            time_mode="runtime",
            usage_limit_minutes=10.0,
            cooldown_minutes=5.0,
            action="close",
            overlay_top_inset=0,
            enabled=True,
            created_at=now_ts(),
        )

        if self.find_duplicate(candidate):
            messagebox.showinfo("Already exists", "The same target is already in the list.", parent=self)
            return

        self.rules.append(candidate)
        self.get_rule_state(candidate.id)
        self.save_rules()
        self.save_state()
        self.status_var.set(f"Added: {candidate.name}")
        self.refresh_table(select_id=candidate.id)

    def find_duplicate(self, candidate: Rule) -> Rule | None:
        for rule in self.rules:
            if rule.match_mode == candidate.match_mode and rule.match_key == candidate.match_key:
                return rule
        return None

    def edit_selected(self) -> None:
        rule = self.selected_rule()
        if rule is None:
            if self.selected_temporary_timer() is not None:
                messagebox.showinfo("One-time rule", "One-time rules cannot be edited after they are started.", parent=self)
                return
            messagebox.showinfo("No selection", "Please select a daily rule first.", parent=self)
            return

        editor = RuleEditor(self, rule)
        self.wait_window(editor)
        if editor.result is None:
            return

        rule.name = editor.result["name"]
        rule.usage_limit_minutes = editor.result["usage_limit_minutes"]
        rule.cooldown_minutes = editor.result["cooldown_minutes"]
        rule.time_mode = editor.result["time_mode"]
        rule.action = editor.result["action"]
        rule.overlay_top_inset = editor.result["overlay_top_inset"]
        rule.enabled = editor.result["enabled"]
        self.save_rules()
        self.status_var.set(f"Saved: {rule.name}")
        self.refresh_table(select_id=rule.id)

    def toggle_selected(self) -> None:
        timer = self.selected_temporary_timer()
        if timer is not None:
            dialog = PauseVerificationDialog(self, action_label="disable this one-time rule")
            self.wait_window(dialog)
            if not dialog.allowed:
                self.status_var.set(f"Disable cancelled: {timer.name}")
                return
            self.hide_overlay_rule(timer)
            self.temporary_timers.remove(timer)
            self.status_var.set(f"{timer.name} disabled")
            self.refresh_table()
            return

        rule = self.selected_rule()
        if rule is None:
            messagebox.showinfo("No selection", "Please select a rule first.", parent=self)
            return
        if rule.enabled:
            dialog = PauseVerificationDialog(self, action_label="disable this daily rule")
            self.wait_window(dialog)
            if not dialog.allowed:
                self.status_var.set(f"Disable cancelled: {rule.name}")
                return
        rule.enabled = not rule.enabled
        if not rule.enabled:
            state = self.get_rule_state(rule.id)
            state["session_started_at"] = None
            state["session_elapsed_seconds"] = 0.0
            state["last_counted_at"] = None
            self.hide_overlay_rule(rule)
            self.state_dirty = True
        self.save_rules()
        if self.state_dirty:
            self.save_state()
        self.status_var.set(f"{rule.name} {'enabled' if rule.enabled else 'disabled'}")
        self.refresh_table(select_id=rule.id)

    def delete_selected(self) -> None:
        timer = self.selected_temporary_timer()
        if timer is not None:
            dialog = PauseVerificationDialog(self, action_label="disable this one-time rule")
            self.wait_window(dialog)
            if not dialog.allowed:
                self.status_var.set(f"Delete cancelled: {timer.name}")
                return
            self.hide_overlay_rule(timer)
            self.temporary_timers.remove(timer)
            self.status_var.set(f"Deleted: {timer.name}")
            self.refresh_table()
            return

        rule = self.selected_rule()
        if rule is None:
            messagebox.showinfo("No selection", "Please select a rule first.", parent=self)
            return

        if not messagebox.askyesno("Delete rule", f"Delete {rule.name}?", parent=self):
            return

        self.rules = [item for item in self.rules if item.id != rule.id]
        self.state.pop(rule.id, None)
        self.display_state.pop(rule.id, None)
        self.save_rules()
        self.save_state()
        self.status_var.set(f"Deleted: {rule.name}")
        self.refresh_table()

    def refresh_table(self, select_id: str | None = None) -> None:
        selected = select_id or (self.tree.selection()[0] if self.tree.selection() else None)
        for item_id in self.tree.get_children():
            self.tree.delete(item_id)

        for rule in self.rules:
            status_info = self.display_state.get(rule.id, {})
            self.tree.insert(
                "",
                "end",
                iid=rule.id,
                values=(
                    "Daily",
                    rule.name,
                    rule.match_mode_label,
                    rule.display_match,
                    f"{pretty_minutes(rule.usage_limit_minutes)} min",
                    f"{pretty_minutes(rule.cooldown_minutes)} min",
                    rule.time_mode,
                    rule.action,
                    str(rule.overlay_top_inset),
                    "Yes" if rule.enabled else "No",
                    status_info.get("status", "Pending" if rule.enabled else "Disabled"),
                    status_info.get("remaining", ""),
                ),
            )

        for timer in self.temporary_timers:
            status, remaining = self.temporary_timer_status(timer)
            self.tree.insert(
                "",
                "end",
                iid=timer.id,
                values=(
                    "One-time",
                    timer.name,
                    "Path" if timer.match_mode == "path" else "EXE",
                    timer.display_match,
                    f"{pretty_minutes(timer.duration_minutes)} min",
                    f"{pretty_minutes(timer.duration_minutes)} min",
                    timer.time_mode,
                    timer.action,
                    str(timer.overlay_top_inset),
                    "Yes",
                    status,
                    remaining,
                ),
            )

        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
            self.tree.see(selected)

        enabled_count = sum(1 for rule in self.rules if rule.enabled)
        mode = "Daily monitoring" if self.monitoring else "Daily paused"
        temp_count = len(self.temporary_timers)
        temp_text = f" · {temp_count} one-time rule{'s' if temp_count != 1 else ''}" if temp_count else ""
        self.summary_var.set(
            f"{mode} · {enabled_count}/{len(self.rules)} rules enabled{temp_text} · config: {RULES_PATH.name}",
        )

    def update_countdown_window(self, countdown: tuple[str, float] | None) -> None:
        if self.countdown_window is None:
            return
        if countdown is None:
            self.countdown_window.hide_countdown()
            return
        rule_name, remaining_seconds = countdown
        self.countdown_window.show_countdown(rule_name, remaining_seconds)

    def sync_overlay_rule(
        self,
        rule: Rule,
        matches: list[ProcessSnapshot],
        remaining_seconds: float,
        foreground_hwnd: int | None = None,
    ) -> None:
        if self.overlay_manager is not None:
            self.overlay_manager.sync_rule(
                rule.id,
                rule.name,
                matches,
                remaining_seconds,
                rule.overlay_top_inset,
                foreground_hwnd,
            )

    def hide_overlay_rule(self, rule: Rule) -> None:
        if self.overlay_manager is not None:
            self.overlay_manager.hide_rule(rule.id)

    def hide_all_overlays(self) -> None:
        if self.overlay_manager is not None:
            self.overlay_manager.hide_all()

    def monitor_tick(self) -> None:
        try:
            self.apply_schedule()
            self.enforce_rules()
            self.refresh_table()
        except Exception as exc:
            self.update_countdown_window(None)
            self.hide_all_overlays()
            self.last_tick_error = str(exc)
            self.status_var.set(f"Monitor error: {exc}")
            self.write_error_log()
        finally:
            self.after(POLL_INTERVAL_MS, self.monitor_tick)

    def enforce_rules(self) -> None:
        processes = enumerate_processes()
        current_time = now_ts()
        foreground_hwnd, foreground_pid = get_foreground_window_info()
        active_countdowns: list[tuple[float, str]] = []

        for rule in self.rules:
            state = self.get_rule_state(rule.id)
            if not self.monitoring:
                state["session_started_at"] = None
                state["session_elapsed_seconds"] = 0.0
                state["last_counted_at"] = None
                self.hide_overlay_rule(rule)
                self.display_state[rule.id] = {"status": "Daily paused", "remaining": ""}
                continue

            if not rule.enabled:
                state["session_started_at"] = None
                state["session_elapsed_seconds"] = 0.0
                state["last_counted_at"] = None
                self.hide_overlay_rule(rule)
                self.display_state[rule.id] = {"status": "Disabled", "remaining": ""}
                continue

            overriding_timer = next((timer for timer in self.temporary_timers if same_target(rule, timer)), None)
            if overriding_timer is not None:
                state["session_started_at"] = None
                state["session_elapsed_seconds"] = 0.0
                state["last_counted_at"] = None
                state["cooldown_until"] = 0.0
                state["last_error"] = ""
                self.hide_overlay_rule(rule)
                self.display_state[rule.id] = {
                    "status": "One-shot override",
                    "remaining": format_remaining(
                        max(
                            0.0,
                            (overriding_timer.cooldown_until - current_time)
                            if overriding_timer.triggered
                            else (overriding_timer.duration_minutes * 60.0 - overriding_timer.elapsed_seconds),
                        )
                    ),
                }
                self.state_dirty = True
                continue

            matches = [
                proc
                for proc in processes
                if rule_matches_process(rule, proc, self.own_pid)
            ]
            active_matches = matches
            if rule.time_mode == "foreground":
                active_matches = [proc for proc in matches if proc.pid == foreground_pid]
            cooldown_until = float(state.get("cooldown_until") or 0.0)
            last_error = str(state.get("last_error") or "")

            if cooldown_until > current_time:
                if matches:
                    if rule.action == "overlay":
                        self.sync_overlay_rule(rule, matches, cooldown_until - current_time, foreground_hwnd)
                        state["last_error"] = ""
                        state["session_started_at"] = None
                        state["session_elapsed_seconds"] = 0.0
                        state["last_counted_at"] = None
                        self.display_state[rule.id] = {
                            "status": "Overlay active",
                            "remaining": format_remaining(cooldown_until - current_time),
                        }
                        self.state_dirty = True
                        continue
                    closed_count, errors = self.close_processes(matches)
                    if errors:
                        last_error = "; ".join(errors[:3])
                        state["last_error"] = last_error
                    else:
                        last_error = ""
                        state["last_error"] = ""
                    if closed_count:
                        self.status_var.set(f"{rule.name} blocked during cooldown.")
                    self.state_dirty = True
                if rule.action == "overlay" and not matches:
                    self.hide_overlay_rule(rule)
                state["session_started_at"] = None
                state["session_elapsed_seconds"] = 0.0
                state["last_counted_at"] = None
                self.display_state[rule.id] = {
                    "status": "Cooling down" if not last_error else "Cooldown error",
                    "remaining": format_remaining(cooldown_until - current_time),
                }
                continue

            if cooldown_until and cooldown_until <= current_time:
                state["cooldown_until"] = 0.0
                state["last_error"] = ""
                last_error = ""
                self.hide_overlay_rule(rule)
                self.state_dirty = True

            if not matches:
                if state.get("session_started_at") is not None:
                    state["session_started_at"] = None
                    state["session_elapsed_seconds"] = 0.0
                    state["last_counted_at"] = None
                    self.state_dirty = True
                self.hide_overlay_rule(rule)
                self.display_state[rule.id] = {"status": "Not running", "remaining": ""}
                continue

            usage_limit_seconds = max(1.0, rule.usage_limit_minutes * 60.0)
            elapsed = float(state.get("session_elapsed_seconds") or 0.0)
            if not active_matches:
                state["last_counted_at"] = None
                self.state_dirty = True
                remaining = usage_limit_seconds - elapsed
                self.hide_overlay_rule(rule)
                self.display_state[rule.id] = {
                    "status": "Background" if rule.time_mode == "foreground" else "Waiting",
                    "remaining": format_remaining(remaining),
                }
                continue

            session_started = state.get("session_started_at")
            if session_started is None:
                session_started = current_time
                state["session_started_at"] = session_started
                state["last_error"] = ""
                last_error = ""

            last_counted_at = state.get("last_counted_at")
            if last_counted_at is not None:
                elapsed += max(0.0, current_time - float(last_counted_at))
            state["session_elapsed_seconds"] = elapsed
            state["last_counted_at"] = current_time
            self.state_dirty = True

            if elapsed >= usage_limit_seconds:
                state["session_started_at"] = None
                state["session_elapsed_seconds"] = 0.0
                state["last_counted_at"] = None
                state["cooldown_until"] = current_time + max(1.0, rule.cooldown_minutes * 60.0)
                self.state_dirty = True
                self.save_state()

                if rule.action == "overlay":
                    self.sync_overlay_rule(rule, matches, state["cooldown_until"] - current_time, foreground_hwnd)
                    state["last_error"] = ""
                    self.display_state[rule.id] = {
                        "status": "Overlay active",
                        "remaining": format_remaining(state["cooldown_until"] - current_time),
                    }
                    continue

                closed_count, errors = self.close_processes(matches)
                state["last_error"] = "; ".join(errors[:3]) if errors else ""
                self.state_dirty = True

                if errors:
                    self.status_var.set(f"{rule.name} reached limit, but some processes failed to close.")
                    self.display_state[rule.id] = {
                        "status": "Close failed",
                        "remaining": format_remaining(state["cooldown_until"] - current_time),
                    }
                else:
                    suffix = f"closed {closed_count} process(es)" if closed_count else "no process was closed"
                    self.status_var.set(f"{rule.name} reached limit; {suffix}; cooldown started.")
                    self.display_state[rule.id] = {
                        "status": "Cooldown",
                        "remaining": format_remaining(state["cooldown_until"] - current_time),
                    }
            else:
                remaining = usage_limit_seconds - elapsed
                active_countdowns.append((remaining, rule.name))
                status = "Running" if not last_error else "Running error"
                self.display_state[rule.id] = {
                    "status": status,
                    "remaining": format_remaining(remaining),
                }

        self.enforce_temporary_timers(
            processes,
            current_time,
            foreground_hwnd,
            foreground_pid,
            active_countdowns,
        )

        if active_countdowns:
            remaining, rule_name = min(active_countdowns, key=lambda item: item[0])
            self.update_countdown_window((rule_name, remaining))
        else:
            self.update_countdown_window(None)

        if self.state_dirty:
            self.save_state()

    def enforce_temporary_timers(
        self,
        processes: list[ProcessSnapshot],
        current_time: float,
        foreground_hwnd: int | None,
        foreground_pid: int | None,
        active_countdowns: list[tuple[float, str]],
    ) -> None:
        for timer in list(self.temporary_timers):
            matches = [
                proc
                for proc in processes
                if rule_matches_process(timer, proc, self.own_pid)
            ]

            if timer.triggered:
                if timer.cooldown_until > current_time:
                    if timer.action == "overlay":
                        if matches:
                            self.sync_overlay_rule(timer, matches, timer.cooldown_until - current_time, foreground_hwnd)
                        else:
                            self.hide_overlay_rule(timer)
                        continue
                    if matches:
                        closed_count, errors = self.close_processes(matches)
                        if errors:
                            self.status_var.set(
                                f"{timer.name} one-shot cooldown active, but close failed: {'; '.join(errors[:2])}"
                            )
                        elif closed_count:
                            self.status_var.set(f"{timer.name} blocked during one-shot cooldown.")
                    continue
                self.hide_overlay_rule(timer)
                self.temporary_timers.remove(timer)
                self.status_var.set(f"{timer.name} one-shot cooldown finished.")
                continue

            if not matches:
                timer.last_counted_at = None
                continue

            active_matches = matches
            if timer.time_mode == "foreground":
                active_matches = [proc for proc in matches if proc.pid == foreground_pid]
            if not active_matches:
                timer.last_counted_at = None
                continue

            if timer.last_counted_at is not None:
                timer.elapsed_seconds += max(0.0, current_time - timer.last_counted_at)
            timer.last_counted_at = current_time

            duration_seconds = max(1.0, timer.duration_minutes * 60.0)
            remaining = duration_seconds - timer.elapsed_seconds
            if remaining > 0:
                active_countdowns.append((remaining, timer.name))
                continue

            timer.triggered = True
            timer.last_counted_at = None
            timer.cooldown_until = current_time + duration_seconds
            if timer.action == "overlay":
                self.sync_overlay_rule(timer, matches, duration_seconds, foreground_hwnd)
                self.status_var.set(
                    f"{timer.name} one-shot timer finished; overlay cooldown started for {pretty_minutes(timer.duration_minutes)} min."
                )
                continue

            closed_count, errors = self.close_processes(matches)
            self.hide_overlay_rule(timer)
            if errors:
                self.status_var.set(f"{timer.name} one-shot timer finished, but close failed: {'; '.join(errors[:2])}")
            else:
                self.status_var.set(
                    f"{timer.name} one-shot timer finished; closed {closed_count} process(es); cooldown started for {pretty_minutes(timer.duration_minutes)} min."
                )

    def close_processes(self, matches: list[ProcessSnapshot]) -> tuple[int, list[str]]:
        if psutil is None:
            return 0, ["psutil is not available"]

        targets = []
        errors: list[str] = []
        for snapshot in matches:
            if snapshot.pid == self.own_pid:
                continue
            try:
                proc = psutil.Process(snapshot.pid)
                if not proc.is_running():
                    continue
                proc.terminate()
                targets.append(proc)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                errors.append(f"{snapshot.name}({snapshot.pid}) access denied")
            except Exception as exc:
                errors.append(f"{snapshot.name}({snapshot.pid}) {exc}")

        closed_count = 0
        gone, alive, wait_errors = wait_for_processes(targets, timeout=2.0)
        closed_count += len(gone)
        errors.extend(f"wait failed: {error}" for error in wait_errors)

        still_alive = []
        for proc in alive:
            try:
                proc.kill()
                still_alive.append(proc)
            except psutil.NoSuchProcess:
                closed_count += 1
            except psutil.AccessDenied:
                errors.append(f"{safe_process_label(proc)} access denied")
            except Exception as exc:
                errors.append(f"{safe_process_label(proc)} {exc}")

        if still_alive:
            gone, alive, wait_errors = wait_for_processes(still_alive, timeout=1.0)
            closed_count += len(gone)
            errors.extend(f"wait failed: {error}" for error in wait_errors)
            for proc in alive:
                errors.append(f"{safe_process_label(proc)} is still running")

        return closed_count, errors

    def write_error_log(self) -> None:
        ERROR_LOG_PATH.write_text(traceback.format_exc(), encoding="utf-8")

    def on_close(self) -> None:
        try:
            self.save_rules()
            self.save_state()
            self.save_schedule()
        finally:
            self.hide_all_overlays()
            if self.countdown_window is not None and self.countdown_window.winfo_exists():
                self.countdown_window.destroy()
            self.destroy()


def show_startup_error(title: str, message: str) -> None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(title, message)
    root.destroy()


def main() -> None:
    if PSUTIL_IMPORT_ERROR is not None:
        show_startup_error(
            "Missing dependency",
            "This tool requires psutil, but psutil could not be loaded.\n\n"
            f"Error: {PSUTIL_IMPORT_ERROR}",
        )
        return

    try:
        app = AppUsageLimiter()
        app.mainloop()
    except Exception:
        ERROR_LOG_PATH.write_text(traceback.format_exc(), encoding="utf-8")
        show_startup_error(
            "Startup failed",
            f"The app failed to start. Details were written to:\n{ERROR_LOG_PATH}",
        )


if __name__ == "__main__":
    main()
