#!/usr/bin/env python3
"""
whats-making-my-mouse-spin
==========================

Find out which process is making your mouse cursor "spin" on Windows.

There are two spinning cursors on Windows:

  * FULL SPIN     -> IDC_WAIT (OCR_WAIT, res id 32514)
                     the whole pointer becomes a spinning ring ("busy").
  * POINTER SPIN  -> IDC_APPSTARTING (OCR_APPSTARTING, res id 32650)
                     a normal arrow with a little spinner next to it
                     ("working in background").

This tool reads the *currently displayed* system cursor, decides whether it is
one of the two spinners, and then attributes it to the process that owns the
window responsible for the cursor (the window with mouse-capture, otherwise the
window under the pointer, otherwise the foreground window). For each it reports:

    process name + PID + which kind of spin + whether that window is hung.

If it can't figure it out, it says so plainly.

Usage
-----
    python mouse_spin_detector.py                 # one-shot snapshot
    python mouse_spin_detector.py --watch         # live, prints on every change
    python mouse_spin_detector.py --watch -d 60   # watch 60s, then print summary
    python mouse_spin_detector.py --watch --all   # also show every candidate window

No third-party packages required (pure ctypes + stdlib).
"""

import argparse
import sys
import time

IS_WINDOWS = sys.platform == "win32"

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

    LPDWORD = ctypes.POINTER(wintypes.DWORD)

    CURSOR_SHOWING = 0x00000001
    GA_ROOT = 2
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    # OCR_* resource ids returned by GetIconInfoExW for the shared system cursors.
    OCR_NORMAL = 32512
    OCR_WAIT = 32514
    OCR_APPSTARTING = 32650

    # res id -> (short label, human description)
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


# --------------------------------------------------------------------------- #
# Win32 helpers
# --------------------------------------------------------------------------- #
def _cursor_res_id(hcursor):
    """Return the OCR_* resource id of a cursor handle, or None.

    Using the resource id (rather than comparing handles) is robust to custom
    cursor *schemes*: even if the user installed a fancy wait cursor, the wait
    cursor still reports res id 32514.
    """
    info = ICONINFOEXW()
    info.cbSize = ctypes.sizeof(ICONINFOEXW)
    if not user32.GetIconInfoExW(hcursor, ctypes.byref(info)):
        return None
    # GetIconInfoExW creates bitmaps we own; free them so we don't leak GDI.
    if info.hbmMask:
        gdi32.DeleteObject(info.hbmMask)
    if info.hbmColor:
        gdi32.DeleteObject(info.hbmColor)
    return int(info.wResID)


def read_cursor():
    """Snapshot the current cursor: (res_id, showing, (x, y)) or None on failure."""
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(ci)):
        return None
    showing = bool(ci.flags & CURSOR_SHOWING)
    pos = (ci.ptScreenPos.x, ci.ptScreenPos.y)
    res_id = _cursor_res_id(ci.hCursor) if (showing and ci.hCursor) else None
    return res_id, showing, pos


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
        "hwnd": root,
        "pid": pid,
        "tid": tid,
        "name": name,
        "image": image,
        "title": _window_text(root),
        "class": _class_name(root),
        "hung": bool(user32.IsHungAppWindow(root)),
    }


def find_candidates(pos):
    """Ordered list of windows that could own the cursor, best guess first."""
    candidates = []
    seen = set()

    def add(label, hwnd):
        if hwnd and hwnd not in seen:
            seen.add(hwnd)
            described = _describe_window(label, hwnd)
            if described:
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


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def classify(res_id):
    """Return (short, description) if res_id is a spinner, else None."""
    return SPIN_CURSORS.get(res_id)


def format_culprit(culprit, indent="  "):
    pid = culprit["pid"]
    name = culprit["name"] or "(name unavailable - access denied?)"
    lines = [
        f"{indent}Culprit:  {name}  (PID {pid})   [via: {culprit['via']}]",
    ]
    title = culprit["title"] or "(no title)"
    lines.append(f'{indent}Window:   "{title}"  (class {culprit["class"]})')
    if culprit["image"]:
        lines.append(f"{indent}Path:     {culprit['image']}")
    if culprit["hung"]:
        lines.append(f"{indent}State:    NOT RESPONDING (window is hung)")
    return "\n".join(lines)


def snapshot_report(show_all=False):
    cur = read_cursor()
    if cur is None:
        print("Could not read the cursor (GetCursorInfo failed).")
        return None
    res_id, showing, pos = cur

    if not showing:
        print("The cursor is currently hidden/suppressed (e.g. full-screen video"
              " or a game), so there is nothing to attribute right now.")
        return None

    spin = classify(res_id)
    if spin is None:
        known = KNOWN_CURSORS.get(res_id, f"non-spinning (res id {res_id})")
        print(f"No spinning cursor right now. Current cursor: {known}.")
        return None

    short, desc = spin
    candidates = find_candidates(pos)
    print(f"SPINNING DETECTED -> {desc}")
    if not candidates:
        print("  ...but I couldn't identify any owning window/process. "
              "Not possible to attribute this one.")
        return short

    print(format_culprit(candidates[0]))
    if show_all and len(candidates) > 1:
        print("  Other candidate windows:")
        for c in candidates[1:]:
            nm = c["name"] or "(unknown)"
            print(f"    - {nm} (PID {c['pid']}) via {c['via']}"
                  f"{'  [hung]' if c['hung'] else ''}")
    return short


# --------------------------------------------------------------------------- #
# Watch mode
# --------------------------------------------------------------------------- #
def watch(interval, duration, show_all):
    print(f"Watching for a spinning cursor (every {interval:g}s). Ctrl+C to stop.\n")
    stats = {}            # (name, pid, short) -> sample count
    last_signature = None
    start = time.monotonic()
    try:
        while True:
            cur = read_cursor()
            if cur is not None:
                res_id, showing, pos = cur
                spin = classify(res_id) if showing else None
                if spin is None:
                    signature = ("none",)
                    if last_signature is not None and last_signature[0] != "none":
                        print(f"[{time.strftime('%H:%M:%S')}] spin stopped.\n")
                    last_signature = signature
                else:
                    short, desc = spin
                    candidates = find_candidates(pos)
                    top = candidates[0] if candidates else None
                    key = (top["name"] if top else None,
                           top["pid"] if top else None, short)
                    stats[key] = stats.get(key, 0) + 1
                    signature = ("spin", key)
                    if signature != last_signature:
                        print(f"[{time.strftime('%H:%M:%S')}] SPINNING -> {desc}")
                        if top:
                            print(format_culprit(top))
                            if show_all and len(candidates) > 1:
                                for c in candidates[1:]:
                                    nm = c["name"] or "(unknown)"
                                    print(f"    - {nm} (PID {c['pid']}) via {c['via']}")
                        else:
                            print("  (could not attribute to a process)")
                        print()
                    last_signature = signature

            if duration and (time.monotonic() - start) >= duration:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")

    _print_summary(stats, interval)


def _print_summary(stats, interval):
    if not stats:
        print("\nSummary: no spinning cursor was seen.")
        return
    print("\n=== Summary (most spin time first) ===")
    rows = sorted(stats.items(), key=lambda kv: kv[1], reverse=True)
    for (name, pid, short), count in rows:
        seconds = count * interval
        nm = name or "(unknown process)"
        pid_s = pid if pid is not None else "?"
        print(f"  {nm}  (PID {pid_s})  -  {short} spin  ~{seconds:.1f}s")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(
        description="Find which process is making your mouse cursor spin (Windows).")
    parser.add_argument("--watch", action="store_true",
                        help="keep watching and report changes live")
    parser.add_argument("-i", "--interval", type=float, default=0.2,
                        help="poll interval in seconds for --watch (default 0.2)")
    parser.add_argument("-d", "--duration", type=float, default=0,
                        help="stop --watch after N seconds (0 = until Ctrl+C)")
    parser.add_argument("--all", action="store_true",
                        help="also show every candidate window, not just the best")
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
        watch(args.interval, args.duration, args.all)
    else:
        snapshot_report(show_all=args.all)
    return 0


if __name__ == "__main__":
    sys.exit(main())
