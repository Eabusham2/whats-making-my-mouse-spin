# whats-making-my-mouse-spin

Find out **which process is making your mouse cursor spin** — and whether it's
the **full spin** (the whole pointer becomes a busy ring) or the **pointer
spin** (a normal arrow with a little spinner, "working in background").

For each spin it reports:

- **process name** + **PID**
- **which kind of spin** (`full` vs `pointer`)
- whether that window is **hung / not responding**
- and if it genuinely can't be determined, it tells you so.

> **Platform:** This is a **Windows** tool. The two spinning cursors are a
> Win32 concept (`IDC_WAIT` and `IDC_APPSTARTING`). On macOS/Linux the idea
> doesn't map cleanly — see [Other OSes](#other-oses-and-ideas) below. Run it on
> anything else and it just tells you it isn't possible there.

## What "spin" means here

| Spin type | Win32 cursor | Resource id | What you see |
|-----------|--------------|-------------|--------------|
| `full`    | `IDC_WAIT` (`OCR_WAIT`) | `32514` | The entire pointer is a spinning ring ("busy"). |
| `pointer` | `IDC_APPSTARTING` (`OCR_APPSTARTING`) | `32650` | Arrow **plus** a small spinner ("working in background"). |

## Pick how you want to run it

| File | Type | Needs | Best for |
|------|------|-------|----------|
| `mouse_spin_gui.c` | **GUI** (native Win32) | a C compiler once | a tiny standalone `.exe`, no runtime deps |
| `mouse_spin_gui.py` | **GUI** (tkinter) | Python | running a window immediately, no compile |
| `mouse_spin_detector.py` | CLI | Python | scripting, `--watch` summaries |
| `Find-MouseSpin.ps1` | CLI | nothing (PowerShell) | zero-install quick check |

All four report the same thing; they just differ in packaging.

## GUI versions

A small always-on-top window that live-updates: green = no spin, orange =
pointer spin, red = full spin, and it names the culprit process + PID + whether
it's hung.

### Native `.exe` — `mouse_spin_gui.c`

No runtime dependencies; compile once and run the `.exe`.

```powershell
# MSVC (from a "Developer Command Prompt for VS")
cl /W4 /O2 mouse_spin_gui.c /link user32.lib gdi32.lib /SUBSYSTEM:WINDOWS

# or MinGW-w64
gcc mouse_spin_gui.c -o mouse_spin_gui.exe -mwindows -luser32 -lgdi32

.\mouse_spin_gui.exe
```

### No-compile — `mouse_spin_gui.py`

Uses `tkinter`, which ships with the standard Windows Python installer.

```powershell
python mouse_spin_gui.py
```

## CLI versions

### Python (full-featured) — `mouse_spin_detector.py`

Pure standard library (just `ctypes`), no `pip install` needed.

```powershell
# one-shot snapshot of the cursor right now
python mouse_spin_detector.py

# live: print a line every time the spin state changes
python mouse_spin_detector.py --watch

# watch for 60s, then print a summary of which process spun the most
python mouse_spin_detector.py --watch -d 60

# also show every candidate window, not just the best guess
python mouse_spin_detector.py --watch --all
```

Example output:

```
SPINNING DETECTED -> pointer spin (working-in-background cursor)
  Culprit:  Code.exe  (PID 12345)   [via: under-cursor]
  Window:   "main.py - Visual Studio Code"  (class Chrome_WidgetWin_1)
  Path:     C:\Users\you\AppData\Local\Programs\Microsoft VS Code\Code.exe
  State:    NOT RESPONDING (window is hung)
```

### PowerShell (zero install) — `Find-MouseSpin.ps1`

No Python required; PowerShell ships with Windows.

```powershell
powershell -ExecutionPolicy Bypass -File .\Find-MouseSpin.ps1
powershell -ExecutionPolicy Bypass -File .\Find-MouseSpin.ps1 -Watch
```

## How it works

1. **Read the live cursor** with `GetCursorInfo`. `GetIconInfoExW` gives the
   cursor's resource id, so we identify the *wait* (32514) and *app-starting*
   (32650) cursors even if you use a custom cursor scheme.
2. **Attribute it to a window.** The process that owns the window controlling
   the cursor is the culprit. We pick the best candidate in this order:
   1. the window holding **mouse capture** (`GetGUIThreadInfo` → `hwndCapture`),
      which controls the cursor anywhere on screen;
   2. the window **directly under the pointer** (`WindowFromPoint`);
   3. the **foreground** window (`GetForegroundWindow`).
3. **Resolve the process**: walk up to the root window (`GetAncestor`), get the
   PID (`GetWindowThreadProcessId`), then the image path
   (`QueryFullProcessImageNameW`). `IsHungAppWindow` flags a frozen app.

`--watch` samples on an interval and, with `-d`, prints a summary ranking
processes by how long each kept the cursor spinning.

## Limitations / honesty

- Attribution is a strong heuristic, not a guarantee. A spinner is global UI
  state; Windows doesn't record "process X set the cursor." We infer it from who
  owns the relevant window, which is correct in the vast majority of cases.
- If the cursor is **hidden/suppressed** (full-screen games/video), there's
  nothing to attribute and the tool says so.
- If the owning process is higher-integrity (e.g. elevated/admin) you may see
  the PID but not the name unless you run the tool **as Administrator**.
- The wait cursor can be set by a child control belonging to the same process,
  so the reported process is right even when the exact window title is generic.

## Other OSes and ideas

- **macOS** has no busy/app-starting cursor; instead it shows the *spinning
  beach-ball* (SPOD) when an **application stops pumping its event loop**. The
  right signal there isn't the cursor — it's app responsiveness. You can list
  non-responsive apps (each `NSRunningApplication` /
  `CGSEventIsAppUnresponsive`, or simply `Activity Monitor` shows "(Not
  Responding)"). A future `spin_macos.py` could poll for not-responding apps and
  report PID + name the same way.
- **Linux (X11/Wayland)** has no single global "busy cursor" to query; each
  toolkit/app manages its own cursor, and Wayland clients render their own. The
  closest equivalent is detecting an X client that has stopped answering pings
  (`_NET_WM_PING` / `xdotool`-style liveness checks).
- **Bonus idea — "who's hogging the main thread":** combine this with a quick
  per-process check (CPU spike, or `IsHungAppWindow` across all top-level
  windows) to catch the culprit even in the instant before the cursor flips, and
  to distinguish "busy working" from "frozen/deadlocked."
