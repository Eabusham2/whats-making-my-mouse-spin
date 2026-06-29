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

## One program — `mouse_spin.py`

Everything is in a single file. It needs only Python (the standard Windows
installer already includes `ctypes` and `tkinter`) — **nothing to `pip install`,
nothing to compile.**

```powershell
python mouse_spin.py            # GUI (default)
python mouse_spin.py --cli      # one-shot snapshot in the terminal
python mouse_spin.py --watch    # live terminal mode, prints on every change
python mouse_spin.py --watch -d 60   # watch 60s, then print a summary
```

### The GUI

A small window that live-updates: **green = no spin, orange = pointer spin,
red = full spin**, naming the culprit process + PID + how it was attributed +
whether the window is hung. Three toggles:

- **Always on top** — keep the window above everything else.
- **Hide in tray (top-arrow area)** — tuck it into the Windows notification
  area (the `^` overflow by the clock). Left-click the tray icon, or
  right-click → *Show*, to bring it back; right-click → *Exit* to quit. If the
  tray can't be created for some reason, it falls back to minimizing so you're
  never stuck.
- **Show window when a spin is detected** — pair this with *Hide in tray* and
  the app lives quietly in the tray, then pops itself up the instant something
  makes your mouse spin, and tucks away again when it stops.

### The terminal mode

```
SPINNING DETECTED -> pointer spin (working-in-background cursor)
  Process:  Code.exe   (PID 12345)
  Window:  "main.py - Visual Studio Code"
  Via:  under-cursor
  State:  NOT RESPONDING (hung)
```

`--watch` samples on an interval and, with `-d`, prints a summary ranking what
kept the cursor spinning the longest.

## How it works

1. **Read the live cursor** with `GetCursorInfo`. We identify the *wait*
   (`32514`) and *app-starting* (`32650`) spinners two ways — by the cursor's
   `GetIconInfoExW` resource id, **and** by comparing the live handle against
   the current `IDC_WAIT` / `IDC_APPSTARTING` handles (the system spinners are
   animated `.ani` cursors, where the resource id alone can read back as `0`).
2. **Attribute it to a window.** The process that owns the window controlling
   the cursor is the culprit. We pick the best candidate in this order:
   1. the window holding **mouse capture** (`GetGUIThreadInfo` → `hwndCapture`),
      which controls the cursor anywhere on screen;
   2. the window **directly under the pointer** (`WindowFromPoint`);
   3. the **foreground** window (`GetForegroundWindow`).
   The GUI excludes **its own** window/PID so it never blames itself.
3. **Resolve the process**: walk up to the root window (`GetAncestor`), get the
   PID (`GetWindowThreadProcessId`), then the image path
   (`QueryFullProcessImageNameW`). `IsHungAppWindow` flags a frozen app.

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
