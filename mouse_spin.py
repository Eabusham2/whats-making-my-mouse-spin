#!/usr/bin/env python3
"""
whats-making-my-mouse-spin
==========================

One program that finds out which process is making your mouse cursor "spin" on
Windows, and shows it in a small live GUI (a terminal mode is included too).

There are two spinning cursors on Windows:

  * FULL SPIN     -> IDC_WAIT (OCR_WAIT, res id 32514)
                     the whole pointer becomes a spinning ring ("busy").
  * POINTER SPIN  -> IDC_APPSTARTING (OCR_APPSTARTING, res id 32650)
                     a normal arrow with a little spinner ("working in background").

For each spin it reports: process name + PID + which kind of spin + whether the
owning window is hung. If it can't tell, it says so.

GUI (default)
-------------
    python mouse_spin.py

The window has three toggles:
  * Always on top
  * Hide in tray (the "top-arrow" notification area)
  * Show window when a spin is detected   (pairs with "Hide in tray": the app
    lives in the tray and pops up the instant something makes your mouse spin)

Terminal modes
--------------
    python mouse_spin.py --cli            # one-shot snapshot, then exit
    python mouse_spin.py --watch          # live, prints on every change
    python mouse_spin.py --watch -d 60    # watch 60s, then print a summary

No third-party packages required (pure ctypes + tkinter, both stdlib).
"""

import argparse
import collections
import csv
import os
import queue
import sys
import threading
import time

IS_WINDOWS = sys.platform == "win32"

POLL_MS = 100  # GUI poll interval (fast enough to catch brief spins)

# status kind -> (background colour, headline)
STYLES = {
    "full":    ("#aa1e1e", "FULL SPIN"),
    "pointer": ("#bb7300", "POINTER SPIN"),
    "hidden":  ("#464646", "CURSOR HIDDEN"),
    "none":    ("#196e37", "NO SPIN"),
}

# --------------------------------------------------------------------------- #
# Win32 bindings (only built on Windows; the file still imports elsewhere so we
# can print a friendly "not possible here" message instead of crashing).
# --------------------------------------------------------------------------- #
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    LPDWORD = ctypes.POINTER(wintypes.DWORD)
    LRESULT = ctypes.c_ssize_t

    CURSOR_SHOWING = 0x00000001
    GA_ROOT = 2
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    OCR_NORMAL = 32512
    OCR_WAIT = 32514
    OCR_APPSTARTING = 32650

    SPIN_CURSORS = {
        OCR_WAIT: ("full", "full spin (busy / wait cursor)"),
        OCR_APPSTARTING: ("pointer", "pointer spin (working-in-background cursor)"),
    }
    KNOWN_CURSORS = {
        OCR_NORMAL: "arrow (normal)",
        32513: "I-beam (text)",
        OCR_WAIT: "wait (full spin)",
        32515: "crosshair",
        32649: "hand",
        OCR_APPSTARTING: "app-starting (pointer spin)",
        32646: "move",
        32648: "no / unavailable",
    }

    # tray / window-message constants
    WS_POPUP = 0x80000000
    WM_DESTROY = 0x0002
    WM_USER = 0x0400
    WM_TRAYICON = WM_USER + 1
    WM_TRAY_QUIT = WM_USER + 2
    WM_LBUTTONUP = 0x0202
    WM_LBUTTONDBLCLK = 0x0203
    WM_RBUTTONUP = 0x0205
    NIM_ADD = 0
    NIM_MODIFY = 1
    NIM_DELETE = 2
    NIF_MESSAGE = 0x01
    NIF_ICON = 0x02
    NIF_TIP = 0x04
    NIF_INFO = 0x10
    TPM_RIGHTBUTTON = 0x0002
    TPM_NONOTIFY = 0x0080
    TPM_RETURNCMD = 0x0100
    MF_STRING = 0x0000
    IDI_APPLICATION = 32512
    ID_TRAY_SHOW = 1001
    ID_TRAY_EXIT = 1002

    # process enumeration (Toolhelp)
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class CURSORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("hCursor", ctypes.c_void_p),
                    ("ptScreenPos", POINT)]

    class ICONINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("fIcon", wintypes.BOOL),
                    ("xHotspot", wintypes.DWORD),
                    ("yHotspot", wintypes.DWORD),
                    ("hbmMask", ctypes.c_void_p),
                    ("hbmColor", ctypes.c_void_p),
                    ("wResID", wintypes.WORD),
                    ("szModName", wintypes.WCHAR * 260),
                    ("szResName", wintypes.WCHAR * 260)]

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("hwndActive", wintypes.HWND),
                    ("hwndFocus", wintypes.HWND),
                    ("hwndCapture", wintypes.HWND),
                    ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND),
                    ("hwndCaret", wintypes.HWND),
                    ("rcCaret", RECT)]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_byte * 8)]

    class NOTIFYICONDATA(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("hWnd", wintypes.HWND),
                    ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT),
                    ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128),
                    ("dwState", wintypes.DWORD),
                    ("dwStateMask", wintypes.DWORD),
                    ("szInfo", wintypes.WCHAR * 256),
                    ("uVersion", wintypes.UINT),
                    ("szInfoTitle", wintypes.WCHAR * 64),
                    ("dwInfoFlags", wintypes.DWORD),
                    ("guidItem", GUID),
                    ("hBalloonIcon", wintypes.HICON)]

    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    class WNDCLASS(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR)]

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD)]

    # --- detection bindings ------------------------------------------------- #
    user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
    user32.GetCursorInfo.restype = wintypes.BOOL
    user32.GetIconInfoExW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ICONINFOEXW)]
    user32.GetIconInfoExW.restype = wintypes.BOOL
    user32.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.LoadCursorW.restype = ctypes.c_void_p
    user32.WindowFromPoint.argtypes = [POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, LPDWORD]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
    user32.GetGUIThreadInfo.restype = wintypes.BOOL
    user32.IsHungAppWindow.argtypes = [wintypes.HWND]
    user32.IsHungAppWindow.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, LPDWORD]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteObject.restype = wintypes.BOOL

    # --- tray / window bindings --------------------------------------------- #
    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.GetMessageW.restype = ctypes.c_int
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.LoadIconW.restype = wintypes.HICON
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT,
                                   ctypes.c_size_t, wintypes.LPCWSTR]
    user32.AppendMenuW.restype = wintypes.BOOL
    user32.TrackPopupMenu.restype = ctypes.c_int
    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                      wintypes.LPVOID]
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATA)]

    # --- process enumeration bindings --------------------------------------- #
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)]
    kernel32.GetProcessTimes.restype = wintypes.BOOL


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
def _cursor_res_id(hcursor):
    """OCR_* resource id of a cursor handle, or None. Robust to custom schemes."""
    info = ICONINFOEXW()
    info.cbSize = ctypes.sizeof(ICONINFOEXW)
    if not user32.GetIconInfoExW(hcursor, ctypes.byref(info)):
        return None
    if info.hbmMask:
        gdi32.DeleteObject(info.hbmMask)
    if info.hbmColor:
        gdi32.DeleteObject(info.hbmColor)
    return int(info.wResID)


def read_cursor():
    """Snapshot the cursor: (hcursor, res_id, showing, (x, y)) or None on failure."""
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(ci)):
        return None
    showing = bool(ci.flags & CURSOR_SHOWING)
    pos = (ci.ptScreenPos.x, ci.ptScreenPos.y)
    hcur = ci.hCursor if (showing and ci.hCursor) else None
    res_id = _cursor_res_id(ci.hCursor) if (showing and ci.hCursor) else None
    return hcur, res_id, showing, pos


def spin_kind(hcursor, res_id):
    """Return (short, description) if the cursor is a spinner, else None.

    Two independent checks, because the system busy cursors are animated:
      1. the OCR_* resource id (reliable for static cursors / custom schemes);
      2. comparing the live handle to the current IDC_WAIT / IDC_APPSTARTING
         handles (reliable for the animated .ani system cursors, where the
         resource id may come back as 0).
    """
    if res_id in SPIN_CURSORS:
        return SPIN_CURSORS[res_id]
    if hcursor:
        wait = user32.LoadCursorW(None, OCR_WAIT)
        appstarting = user32.LoadCursorW(None, OCR_APPSTARTING)
        if wait and int(hcursor) == int(wait):
            return SPIN_CURSORS[OCR_WAIT]
        if appstarting and int(hcursor) == int(appstarting):
            return SPIN_CURSORS[OCR_APPSTARTING]
    return None


def _window_text(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _process_image(pid):
    """Full image path for a PID, or None if we can't open the process."""
    if not pid:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        kernel32.CloseHandle(handle)
    return None


def _process_cpu_ticks(pid):
    """Total CPU time (kernel+user) for a PID in 100-ns units, or None."""
    if not pid:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        c, e, k, u = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if kernel32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(e),
                                    ctypes.byref(k), ctypes.byref(u)):
            kt = (k.dwHighDateTime << 32) | k.dwLowDateTime
            ut = (u.dwHighDateTime << 32) | u.dwLowDateTime
            return kt + ut
    finally:
        kernel32.CloseHandle(handle)
    return None


def _describe_window(label, hwnd):
    """Resolve a window handle into a culprit dict, or None."""
    if not hwnd:
        return None
    root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    pid = wintypes.DWORD(0)
    tid = user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
    pid = pid.value
    image = _process_image(pid)
    name = image.rsplit("\\", 1)[-1] if image else None
    return {
        "via": label,
        "hwnd": int(root) if root else 0,
        "pid": pid,
        "tid": tid,
        "name": name,
        "image": image,
        "title": _window_text(root),
        "class": _class_name(root),
        "hung": bool(user32.IsHungAppWindow(root)),
    }


def find_candidates(pos, exclude_hwnds=None, exclude_pids=None):
    """Ordered list of windows that could own the cursor, best guess first.

    exclude_hwnds / exclude_pids let a caller (e.g. our own GUI) avoid being
    blamed for the spin it is reporting on.
    """
    exclude_hwnds = exclude_hwnds or set()
    exclude_pids = exclude_pids or set()
    candidates = []
    seen = set()

    def add(label, hwnd):
        if not hwnd or hwnd in seen:
            return
        seen.add(hwnd)
        described = _describe_window(label, hwnd)
        if not described:
            return
        if described["hwnd"] in exclude_hwnds or described["pid"] in exclude_pids:
            return
        candidates.append(described)

    # 1) A window with mouse capture controls the cursor anywhere on screen.
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    if user32.GetGUIThreadInfo(0, ctypes.byref(gti)) and gti.hwndCapture:
        add("mouse-capture", gti.hwndCapture)

    # 2) Otherwise the window directly under the pointer sets the cursor.
    add("under-cursor", user32.WindowFromPoint(POINT(pos[0], pos[1])))

    # 3) Fall back to whatever app is in the foreground.
    add("foreground", user32.GetForegroundWindow())

    return candidates


def snapshot_processes():
    """Map of pid -> (exe_name, parent_pid) for every running process."""
    out = {}
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return out
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            out[int(pe.th32ProcessID)] = (pe.szExeFile, int(pe.th32ParentProcessID))
            ok = kernel32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        kernel32.CloseHandle(snap)
    return out


# Vocabulary used only to *annotate why* a launch might spin the cursor - script
# ran, installer in a Temp folder, Office spawned a helper, etc. Not a verdict.
SUSPECT_DIRS = (
    "\\appdata\\local\\temp\\", "\\windows\\temp\\", "\\temp\\", "\\tmp\\",
    "\\downloads\\", "\\users\\public\\", "\\programdata\\",
    "\\appdata\\roaming\\", "\\$recycle.bin\\",
)
LOLBINS = {
    "regsvr32.exe", "rundll32.exe", "msbuild.exe", "installutil.exe",
    "certutil.exe", "bitsadmin.exe", "wmic.exe", "schtasks.exe",
}
INTERPRETERS = {
    "powershell.exe", "pwsh.exe", "cmd.exe", "wscript.exe", "cscript.exe",
    "mshta.exe", "python.exe", "pythonw.exe", "node.exe", "ruby.exe", "perl.exe",
    "php.exe", "java.exe", "javaw.exe", "dotnet.exe",
}
OFFICE_PARENTS = {
    "winword.exe", "excel.exe", "powerpnt.exe", "outlook.exe", "onenote.exe",
    "msaccess.exe", "mspub.exe", "visio.exe",
}


def launch_notes(image, name, parent):
    """Plain-language context for why a process might spin the cursor.

    Covers everything - a normal app, a background task, a script/interpreter,
    an installer in Temp - not just sketchy stuff. Returns a list of short notes.
    """
    notes = []
    low = (image or "").lower()
    nm = (name or "").lower()
    par = (parent or "").lower()

    if nm in INTERPRETERS or nm in LOLBINS:
        notes.append("script/interpreter")
    if par in OFFICE_PARENTS and (nm in INTERPRETERS or nm in LOLBINS):
        notes.append("started by an Office app (%s)" % parent)
    for d in SUSPECT_DIRS:
        if d in low:
            notes.append("runs from %s" % d.strip("\\"))
            break
    if nm in LOLBINS and low and "\\system32\\" not in low and "\\syswow64\\" not in low:
        notes.append("%s outside System32" % name)
    if not image:
        notes.append("path unreadable")
    return notes


def find_hung_windows(exclude_pids=None, limit=6):
    """Visible, titled top-level windows that are not responding (system-wide)."""
    exclude_pids = set(exclude_pids or set())
    found, seen = [], set()

    def _cb(hwnd, _lparam):
        try:
            if (not user32.IsWindowVisible(hwnd)
                    or user32.GetWindowTextLengthW(hwnd) == 0
                    or not user32.IsHungAppWindow(hwnd)):
                return True
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid = pid.value
            if pid in exclude_pids or pid in seen:
                return True
            seen.add(pid)
            image = _process_image(pid)
            found.append({"pid": pid,
                          "name": image.rsplit("\\", 1)[-1] if image else None,
                          "title": _window_text(hwnd)})
        except Exception:
            pass
        return True  # keep enumerating

    cb = WNDENUMPROC(_cb)
    try:
        user32.EnumWindows(cb, 0)
    except Exception:
        pass
    return found[:limit]


class SpinForensics:
    """Explains *why* the cursor is spinning, using every signal at once:

      * what process just launched (the "app-starting" trigger),
      * which window/process actually owns the cursor right now,
      * anything that is not responding (a hung app freezes the cursor).

    Each launched process is annotated with neutral notes (script/interpreter,
    runs-from-Temp, started-by-Office, ...) so you can tell a normal app from a
    background task from a script - whatever the reason turns out to be.
    """

    NEW_WINDOW_S = 6.0   # a process this freshly created is a likely trigger
    HISTORY_S = 90.0

    BUSY_PCT = 12.0      # a process this busy is a plausible "why"
    CPU_EVERY_S = 0.5    # how often to resample CPU (it is the costly part)

    def __init__(self, own_pid):
        self.own_pid = own_pid
        self._prev_pids = None
        self._snapshot = {}
        self._creations = collections.deque()  # (monotonic_ts, pid, name, ppid)
        self._ncpu = max(1, os.cpu_count() or 1)
        self._cpu = {}            # pid -> cpu percent (last computed)
        self._cpu_prev = {}       # pid -> cpu ticks (previous sample)
        self._cpu_t = 0.0         # perf_counter of last sample

    def poll(self):
        """Refresh the process list, record new processes, resample CPU."""
        snap = snapshot_processes()
        if not snap:
            return self._snapshot
        now = time.monotonic()
        if self._prev_pids is not None:
            for pid in set(snap.keys()) - self._prev_pids:
                if pid == self.own_pid:
                    continue
                name, ppid = snap[pid]
                self._creations.append((now, pid, name, ppid))
        cutoff = now - self.HISTORY_S
        while self._creations and self._creations[0][0] < cutoff:
            self._creations.popleft()
        self._prev_pids = set(snap.keys())
        self._snapshot = snap
        self._sample_cpu()
        return snap

    def _sample_cpu(self):
        """Throttled per-process CPU%, so a busy background task can be named."""
        now = time.perf_counter()
        if self._cpu_t and (now - self._cpu_t) < self.CPU_EVERY_S:
            return
        dt = (now - self._cpu_t) if self._cpu_t else 0.0
        pct, newprev = {}, {}
        for pid in list(self._snapshot.keys()):
            if pid == self.own_pid:
                continue
            ticks = _process_cpu_ticks(pid)
            if ticks is None:
                continue
            newprev[pid] = ticks
            if dt and pid in self._cpu_prev:
                cpu = ((ticks - self._cpu_prev[pid]) / 1e7) / (dt * self._ncpu) * 100.0
                if cpu >= 1.0:
                    pct[pid] = cpu
        self._cpu_prev = newprev
        self._cpu = pct
        self._cpu_t = now

    def _busy(self, exclude_pids, limit=3):
        items = sorted(((c, p) for p, c in self._cpu.items()
                        if c >= self.BUSY_PCT and p not in exclude_pids),
                       reverse=True)
        out = []
        for cpu, pid in items[:limit]:
            name, ppid = self._snapshot.get(pid, (None, None))
            out.append({"pid": pid, "name": name, "ppid": ppid, "cpu": cpu})
        return out

    def _enrich(self, pid, name, ppid, via="new-process"):
        image = _process_image(pid)
        parent = self._snapshot.get(ppid, (None, None))[0] or "(parent already exited)"
        return {
            "name": name or (image.rsplit("\\", 1)[-1] if image else "(unknown)"),
            "pid": pid, "ppid": ppid, "parent": parent, "image": image,
            "notes": "; ".join(launch_notes(image, name, parent)),
            "via": via, "hung": False,
        }

    def describe(self, exclude_hwnds=None):
        """Return (kind, detail_text, primary_cause_or_None).

        The text gathers *every* reason it can find, so you see the whole
        picture rather than a single guess.
        """
        cur = read_cursor()
        if cur is None:
            return "none", "Could not read the cursor (GetCursorInfo failed).", None
        hcur, res_id, showing, pos = cur

        if not showing:
            return "hidden", ("Cursor is hidden/suppressed (full-screen app or "
                              "game) - nothing to attribute."), None

        spin = spin_kind(hcur, res_id)
        if spin is None:
            known = KNOWN_CURSORS.get(res_id)
            if known is None:
                known = "custom cursor" if not res_id else "non-spinning (res id %s)" % res_id
            return "none", "Your cursor is normal: %s.\nNothing is spinning." % known, None

        short, _desc = spin
        now = time.monotonic()
        sections = []
        primary = None

        # 1) What just launched - the classic "app starting" trigger.
        launched = [(now - ts, pid, nm, ppid)
                    for (ts, pid, nm, ppid) in reversed(self._creations)
                    if now - ts <= self.NEW_WINDOW_S]
        if launched:
            lines = ["Just launched (most likely the trigger):"]
            for i, (age, pid, nm, ppid) in enumerate(launched[:6]):
                info = self._enrich(pid, nm, ppid)
                tail = "  (%s)" % info["notes"] if info["notes"] else ""
                lines.append("  - %s  (PID %s, %.1fs ago)  parent: %s%s"
                             % (info["name"], pid, age, info["parent"], tail))
                if info["image"]:
                    lines.append("        %s" % info["image"])
                if i == 0:
                    primary = info
            sections.append("\n".join(lines))

        # 2) Who actually owns the displayed cursor right now.
        cands = find_candidates(pos, exclude_hwnds, {self.own_pid})
        if cands:
            c = dict(cands[0])
            ppid = self._snapshot.get(c["pid"], (None, None))[1]
            parent = self._snapshot.get(ppid, (None, None))[0] if ppid else None
            c.update({"ppid": ppid or "", "parent": parent or "(unknown)",
                      "notes": "; ".join(launch_notes(c["image"], c["name"], parent))})
            owner = ("Cursor owned by: %s  (PID %s) [%s]%s"
                     % (c["name"] or "(unknown)", c["pid"], c["via"],
                        "   - NOT RESPONDING" if c["hung"] else ""))
            extra = []
            if c["title"]:
                extra.append('window "%s"' % c["title"])
            if c["notes"]:
                extra.append(c["notes"])
            if extra:
                owner += "\n        " + "; ".join(extra)
            sections.append(owner)
            if primary is None:
                primary = c

        # 3) Anything not responding anywhere - a hung app freezes the cursor.
        excl = {self.own_pid} | ({primary["pid"]} if primary else set())
        hung = find_hung_windows(exclude_pids=excl)
        if hung:
            lines = ["Not responding (can freeze the cursor):"]
            for h in hung:
                lines.append("  - %s (PID %s)" % (h["name"] or "(unknown)", h["pid"]))
            sections.append("\n".join(lines))
            if primary is None:
                h0 = hung[0]
                primary = {"name": h0["name"], "pid": h0["pid"], "ppid": "",
                           "parent": "", "image": None, "notes": "not responding",
                           "via": "hung", "hung": True}

        # 4) Busy in the background - catches headless work with no new process,
        #    no owning window, and no hang (e.g. an indexer or an updater).
        seen_pids = {self.own_pid}
        if primary:
            seen_pids.add(primary["pid"])
        busy = self._busy(seen_pids)
        if busy:
            lines = ["Working hard right now (high CPU):"]
            for b in busy:
                lines.append("  - %s (PID %s)  %.0f%% CPU"
                             % (b["name"] or "(unknown)", b["pid"], b["cpu"]))
            sections.append("\n".join(lines))
            if primary is None:
                b0 = busy[0]
                parent = self._snapshot.get(b0["ppid"], (None, None))[0]
                primary = {"name": b0["name"], "pid": b0["pid"],
                           "ppid": b0["ppid"] or "", "parent": parent or "(unknown)",
                           "image": _process_image(b0["pid"]),
                           "notes": "high CPU (%.0f%%)" % b0["cpu"],
                           "via": "high-cpu", "hung": False}

        if not sections:
            sections.append("Spinning, but I couldn't pin down a cause - possibly a "
                            "hidden background process. Try running as Administrator "
                            "so more processes are visible.")

        return short, "\n\n".join(sections), primary


# --------------------------------------------------------------------------- #
# System tray (Windows notification area / "top-arrow" overflow)
# --------------------------------------------------------------------------- #
if IS_WINDOWS:

    class SystemTray:
        """A notification-area icon on its own thread; clicks arrive via a queue.

        events queue yields the strings "show" or "exit". Everything is wrapped
        defensively so a tray hiccup can never take the GUI down.
        """

        _counter = 0

        def __init__(self, tooltip="What's making my mouse spin?"):
            self.tooltip = tooltip
            self.events = queue.Queue()
            self.active = False
            self._hwnd = None
            self._thread = None
            self._ready = threading.Event()
            self._wndproc = None
            self._nid = None
            SystemTray._counter += 1
            self._classname = "MouseSpinTray_%d" % SystemTray._counter

        def start(self, timeout=3.0):
            if self.active:
                return True
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            if self._ready.wait(timeout) and self._hwnd:
                self.active = True
                return True
            return False

        def stop(self):
            if self._hwnd:
                try:
                    user32.PostMessageW(self._hwnd, WM_TRAY_QUIT, 0, 0)
                except Exception:
                    pass
            self.active = False

        def _run(self):
            try:
                hinst = kernel32.GetModuleHandleW(None)
                self._wndproc = WNDPROC(self._on_message)
                wc = WNDCLASS()
                wc.lpfnWndProc = self._wndproc
                wc.hInstance = hinst
                wc.lpszClassName = self._classname
                wc.hCursor = user32.LoadCursorW(None, OCR_NORMAL)
                user32.RegisterClassW(ctypes.byref(wc))
                self._hwnd = user32.CreateWindowExW(
                    0, self._classname, "MouseSpinTrayOwner", WS_POPUP,
                    0, 0, 0, 0, None, None, hinst, None)
                if not self._hwnd:
                    self._ready.set()
                    return
                self._add_icon()
                self._ready.set()
                msg = wintypes.MSG()
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            except Exception:
                self._ready.set()

        def _add_icon(self):
            nid = NOTIFYICONDATA()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
            nid.hWnd = self._hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAYICON
            nid.hIcon = user32.LoadIconW(None, IDI_APPLICATION)
            nid.szTip = self.tooltip[:127]
            shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
            self._nid = nid

        def _remove_icon(self):
            if self._nid is not None:
                try:
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
                except Exception:
                    pass
                self._nid = None

        def notify(self, title, message):
            """Pop a balloon/toast from the tray icon (used while hidden)."""
            if not self.active or self._nid is None:
                return
            try:
                self._nid.uFlags = NIF_INFO
                self._nid.szInfoTitle = title[:63]
                self._nid.szInfo = message[:255]
                self._nid.dwInfoFlags = 0
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))
                self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            except Exception:
                pass

        def _on_message(self, hwnd, msg, wparam, lparam):
            try:
                if msg == WM_TRAYICON:
                    ev = lparam & 0xFFFF
                    if ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                        self.events.put("show")
                    elif ev == WM_RBUTTONUP:
                        self._popup_menu(hwnd)
                    return 0
                if msg == WM_TRAY_QUIT:
                    self._remove_icon()
                    user32.DestroyWindow(hwnd)
                    return 0
                if msg == WM_DESTROY:
                    self._remove_icon()
                    user32.PostQuitMessage(0)
                    return 0
            except Exception:
                pass
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        def _popup_menu(self, hwnd):
            try:
                menu = user32.CreatePopupMenu()
                user32.AppendMenuW(menu, MF_STRING, ID_TRAY_SHOW, "Show window")
                user32.AppendMenuW(menu, MF_STRING, ID_TRAY_EXIT, "Exit")
                pt = POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                user32.SetForegroundWindow(hwnd)
                cmd = user32.TrackPopupMenu(
                    menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                    pt.x, pt.y, 0, hwnd, None)
                user32.PostMessageW(hwnd, 0, 0, 0)  # WM_NULL, per TrackPopupMenu docs
                user32.DestroyMenu(menu)
                if cmd == ID_TRAY_SHOW:
                    self.events.put("show")
                elif cmd == ID_TRAY_EXIT:
                    self.events.put("exit")
            except Exception:
                self.events.put("show")


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #
class SpinGuiApp:
    def __init__(self, root):
        import tkinter as tk
        self.tk = tk
        self.root = root
        self.tray = None
        self.window_shown = True
        self.auto_shown = False
        self._last_kind = "none"
        self._spin_started = None
        self._own_pid = os.getpid()
        self._own_hwnds = set()
        self.forensics = SpinForensics(self._own_pid)
        self._log_path = os.path.abspath("mouse_spin_log.csv")
        self._log_fh = None
        self._log_writer = None

        root.title("What's making my mouse spin?")
        root.geometry("560x460")
        root.minsize(420, 320)

        # status area (recoloured each tick)
        self.status_frame = tk.Frame(root)
        self.status_frame.pack(side="top", fill="both", expand=True)
        self.headline = tk.Label(self.status_frame, font=("Segoe UI", 20, "bold"),
                                 fg="white", anchor="w", padx=16, pady=8)
        self.headline.pack(fill="x")
        self.detail = tk.Label(self.status_frame, font=("Segoe UI", 11), fg="white",
                               justify="left", anchor="nw", padx=16, cursor="hand2")
        self.detail.pack(fill="both", expand=True)
        self.detail.bind("<Button-1>", self._copy_detail)  # click to copy

        # controls area (constant dark strip)
        ctrl = tk.Frame(root, bg="#202020")
        ctrl.pack(side="bottom", fill="x")
        self.var_ontop = tk.BooleanVar(value=True)
        self.var_tray = tk.BooleanVar(value=False)
        self.var_show = tk.BooleanVar(value=False)

        def mkcb(text, var, cmd):
            cb = tk.Checkbutton(ctrl, text=text, variable=var, command=cmd,
                                bg="#202020", fg="white", selectcolor="#444",
                                activebackground="#202020", activeforeground="white",
                                anchor="w", padx=12)
            cb.pack(fill="x")
            return cb

        self.var_log = tk.BooleanVar(value=False)
        mkcb("Always on top", self.var_ontop, self._apply_ontop)
        mkcb("Hide in tray (top-arrow area)", self.var_tray, self._apply_tray)
        mkcb("Show window when a spin is detected", self.var_show, lambda: None)
        mkcb("Log every spin to mouse_spin_log.csv", self.var_log, self._toggle_log)
        self.status_label = tk.Label(ctrl, text="", bg="#202020", fg="#9a9a9a",
                                     anchor="w", padx=12, font=("Segoe UI", 8))
        self.status_label.pack(fill="x")

        # event log (history of spins + suspects), sits above the controls
        logwrap = tk.Frame(root, bg="#151515")
        logwrap.pack(side="bottom", fill="x")
        tk.Label(logwrap, text="Spin history (newest first):", bg="#151515",
                 fg="#7fbf7f", anchor="w", padx=12, font=("Segoe UI", 8)).pack(fill="x")
        self.logbox = tk.Text(logwrap, height=7, bg="#151515", fg="#cfcfcf",
                              insertbackground="#cfcfcf", wrap="none", bd=0,
                              font=("Consolas", 9), padx=12, state="disabled")
        self.logbox.pack(fill="x")

        root.protocol("WM_DELETE_WINDOW", self.on_close)

        # learn our own top-level handle(s) so we never blame ourselves
        root.update_idletasks()
        try:
            child = int(root.winfo_id())
            root_hwnd = user32.GetAncestor(child, GA_ROOT) or child
            self._own_hwnds = {child, int(root_hwnd)}
        except Exception:
            self._own_hwnds = set()

        self._apply_ontop()
        self.tick()

    # -- toggles ------------------------------------------------------------ #
    def _apply_ontop(self):
        try:
            self.root.attributes("-topmost", bool(self.var_ontop.get()))
        except Exception:
            pass

    def _apply_tray(self):
        if self.var_tray.get():
            # a stopped tray can't be cleanly restarted, so start fresh.
            if self.tray is None or not self.tray.active:
                self.tray = SystemTray()
            ok = self.tray.start()
            if ok:
                self.status_label.config(
                    text="Tucked into the tray - click the icon (or right-click > Show) to return.")
            else:
                self.status_label.config(
                    text="System tray unavailable - minimizing to the taskbar instead.")
            spinning = self._last_kind in ("full", "pointer")
            if not (self.var_show.get() and spinning):
                self._set_visible(False)
        else:
            if self.tray is not None:
                self.tray.stop()
            self.status_label.config(text="")
            self._set_visible(True)

    # -- visibility --------------------------------------------------------- #
    def _set_visible(self, visible, auto=False):
        if visible:
            self.root.deiconify()
            self.root.lift()
            self._apply_ontop()
            self.window_shown = True
            self.auto_shown = auto
        else:
            if self.tray is not None and self.tray.active:
                self.root.withdraw()
            else:
                self.root.iconify()
            self.window_shown = False
            self.auto_shown = False

    # -- main loop ---------------------------------------------------------- #
    def tick(self):
        # handle tray clicks first
        if self.tray is not None:
            try:
                while True:
                    ev = self.tray.events.get_nowait()
                    if ev == "show":
                        self.var_tray.set(False)
                        self._apply_tray()
                    elif ev == "exit":
                        self.on_close()
                        return
            except queue.Empty:
                pass

        self.forensics.poll()  # keep the process-creation history fresh

        prev_kind = self._last_kind
        kind, text, primary = self.forensics.describe(self._own_hwnds)
        self._last_kind = kind
        spinning = kind in ("full", "pointer")

        bg, head = STYLES.get(kind, STYLES["none"])
        now = time.monotonic()
        if spinning:
            if self._spin_started is None:
                self._spin_started = now
            elapsed = int(now - self._spin_started)
            if elapsed >= 1:
                head = "%s   ·   %ds" % (head, elapsed)
        else:
            self._spin_started = None

        self.status_frame.config(bg=bg)
        self.headline.config(bg=bg, text=head)
        self.detail.config(bg=bg, text=text)

        # A spin just started -> record it in the history (and toast / log).
        if spinning and prev_kind not in ("full", "pointer"):
            self._on_spin_start(kind, primary)

        # Auto show/hide when paired with "Hide in tray".
        if self.var_tray.get() and self.var_show.get():
            if spinning and not self.window_shown:
                self._set_visible(True, auto=True)
            elif (not spinning) and self.window_shown and self.auto_shown:
                self._set_visible(False)
        elif self.var_show.get() and spinning and self.window_shown:
            self.root.lift()

        self.root.after(POLL_MS, self.tick)

    def _on_spin_start(self, kind, primary):
        stamp = time.strftime("%H:%M:%S")
        if primary:
            nm = primary.get("name", "?")
            pid = primary.get("pid", "?")
            via = primary.get("via", "?")
            notes = primary.get("notes", "")
            tag = "  (%s)" % notes if notes else ""
            line = "%s  %-7s %s (PID %s) via %s%s" % (stamp, kind, nm, pid, via, tag)
        else:
            nm, pid, notes = "(unattributed)", "?", ""
            line = "%s  %-7s (unattributed)" % (stamp, kind)
        self._log_line(line)

        if (self.tray is not None and self.tray.active and not self.window_shown
                and not self.var_show.get() and primary is not None):
            self.tray.notify("Mouse spin: %s" % kind,
                             "%s (PID %s)%s" % (nm, pid, ("  - " + notes) if notes else ""))

        if self.var_log.get() and self._log_writer is not None and primary is not None:
            try:
                self._log_writer.writerow([
                    time.strftime("%Y-%m-%d %H:%M:%S"), kind, nm, pid,
                    primary.get("ppid", ""), primary.get("parent", ""), via,
                    notes, primary.get("image", "") or ""])
                self._log_fh.flush()
            except Exception:
                pass

    def _log_line(self, line):
        try:
            self.logbox.config(state="normal")
            self.logbox.insert("1.0", line + "\n")          # newest on top
            self.logbox.delete("400.0", "end")              # cap the buffer
            self.logbox.config(state="disabled")
        except Exception:
            pass

    def _toggle_log(self):
        if self.var_log.get():
            try:
                fresh = not os.path.exists(self._log_path)
                self._log_fh = open(self._log_path, "a", newline="", encoding="utf-8")
                self._log_writer = csv.writer(self._log_fh)
                if fresh:
                    self._log_writer.writerow(
                        ["time", "spin", "process", "pid", "ppid", "parent",
                         "via", "notes", "path"])
                    self._log_fh.flush()
                self.status_label.config(text="Logging spins to %s" % self._log_path)
            except Exception:
                self.var_log.set(False)
                self._log_fh = self._log_writer = None
                self.status_label.config(text="Could not open the log file.")
        else:
            if self._log_fh is not None:
                try:
                    self._log_fh.close()
                except Exception:
                    pass
            self._log_fh = self._log_writer = None
            self.status_label.config(text="")

    def _copy_detail(self, _evt=None):
        try:
            txt = self.detail.cget("text")
            if txt:
                self.root.clipboard_clear()
                self.root.clipboard_append(txt)
                self.status_label.config(text="Copied the details to the clipboard.")
        except Exception:
            pass

    def on_close(self):
        if self.tray is not None:
            self.tray.stop()
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except Exception:
                pass
        self.root.destroy()


# --------------------------------------------------------------------------- #
# Terminal modes
# --------------------------------------------------------------------------- #
def cli_snapshot():
    forensics = SpinForensics(os.getpid())
    forensics.poll()
    kind, text, _primary = forensics.describe()
    head = STYLES.get(kind, STYLES["none"])[1]
    if kind in ("full", "pointer"):
        print("SPINNING DETECTED -> %s\n  %s" % (head, text.replace("\n", "\n  ")))
    else:
        print("%s - %s" % (head, text.replace("\n", " ")))
    if kind in ("full", "pointer"):
        print("\n(tip: --watch tracks process launches over time, which finds the\n"
              "background/malware culprit far better than a single snapshot.)")


def cli_watch(interval, duration, _show_all):
    print("Watching for a spinning cursor (every %gs). Ctrl+C to stop.\n" % interval)
    forensics = SpinForensics(os.getpid())
    stats = {}
    last = None
    start = time.monotonic()
    try:
        while True:
            forensics.poll()
            kind, text, _primary = forensics.describe()
            spinning = kind in ("full", "pointer")
            if spinning:
                head = STYLES[kind][1]
                first = text.splitlines()[0] if text else ""
                key = (first, kind)
                stats[key] = stats.get(key, 0) + 1
                sig = ("spin", key)
                if sig != last:
                    stamp = time.strftime("%H:%M:%S")
                    print("[%s] SPINNING -> %s\n  %s\n"
                          % (stamp, head, text.replace("\n", "\n  ")))
                last = sig
            else:
                if last is not None and last[0] == "spin":
                    print("[%s] spin stopped.\n" % time.strftime("%H:%M:%S"))
                last = ("none",)

            if duration and (time.monotonic() - start) >= duration:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")

    if stats:
        print("\n=== Summary (most spin time first) ===")
        for (first, kind), count in sorted(stats.items(), key=lambda kv: kv[1], reverse=True):
            print("  %s  -  %s spin  ~%.1fs" % (first, kind, count * interval))
    else:
        print("\nSummary: no spinning cursor was seen.")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(
        description="Find which process is making your mouse cursor spin (Windows).")
    parser.add_argument("--cli", action="store_true",
                        help="print one snapshot to the terminal and exit")
    parser.add_argument("--watch", action="store_true",
                        help="terminal mode: watch and report changes live")
    parser.add_argument("-i", "--interval", type=float, default=0.15,
                        help="poll interval in seconds for --watch (default 0.15)")
    parser.add_argument("-d", "--duration", type=float, default=0,
                        help="stop --watch after N seconds (0 = until Ctrl+C)")
    args = parser.parse_args()

    if not IS_WINDOWS:
        print("Sorry, this isn't possible on this OS.\n"
              f"You're on '{sys.platform}'. The spinning mouse cursor (the busy ring "
              "and the\n'working in background' pointer) is a Windows-specific concept, "
              "and this tool\nuses the Win32 cursor APIs to detect it.\n\n"
              "- macOS shows a spinning beach-ball when an *app* stops responding; you'd\n"
              "  detect that differently (see the README for notes/ideas).\n"
              "- Linux/X11/Wayland don't have a single global 'busy cursor' to query.\n\n"
              "Run this on Windows to get process name + PID + spin type.")
        return 2

    if args.watch:
        cli_watch(args.interval, args.duration, False)
        return 0
    if args.cli:
        cli_snapshot()
        return 0

    import tkinter as tk
    root = tk.Tk()
    SpinGuiApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
