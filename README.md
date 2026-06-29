# whats-making-my-mouse-spin

Find out **which process is making your mouse cursor spin** — and whether it's
the **full spin** (the whole pointer becomes a busy ring) or the **pointer
spin** (a normal arrow with a little spinner, "working in background").

It answers **"why is my mouse spinning right now?"** by gathering *every* cause
it can find and showing them together — whatever the reason turns out to be:

- the **process that just launched** (name + PID + parent) — the classic
  "app-starting" trigger, whether that's a normal app, a background task, an
  installer, or a script;
- the **window/process that owns the cursor** at that moment;
- anything **not responding / hung**, since a frozen app freezes the cursor;
- neutral **notes** on each launch (script/interpreter, runs-from-Temp, started
  by an Office app, …) so you can tell what *kind* of thing it was;
- a **spin history** and an optional **CSV log** so you can catch things that
  happen while you're away;
- and if it genuinely can't be determined, it tells you so.

The notes can double as a heads-up for sketchy activity (a script firing from a
Temp folder, say), but that's just one of the things it surfaces — this is a
general "what's spinning my cursor" diagnostic, **not** an antivirus.

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
red = full spin**. When a spin happens it shows, all together: the process(es)
that just launched (with **parent**, **path**, and **notes**), the
**window/process that owns the cursor**, and anything **not responding**. It
also:

- shows **how long** the current spin has lasted (in the headline);
- keeps a **spin history** panel (newest first) so repeat offenders stand out;
- lets you **click the details to copy** them to the clipboard.

Four toggles:

- **Always on top** — keep the window above everything else.
- **Hide in tray (top-arrow area)** — tuck it into the Windows notification
  area (the `^` overflow by the clock). Left-click the tray icon, or
  right-click → *Show*, to bring it back; right-click → *Exit* to quit. If the
  tray can't be created for some reason, it falls back to minimizing so you're
  never stuck.
- **Show window when a spin is detected** — pair this with *Hide in tray* and
  the app lives quietly in the tray, then pops itself up the instant something
  makes your mouse spin, and tucks away again when it stops. (If you leave this
  off while hidden, you instead get a **balloon toast** naming the suspect.)
- **Log every spin to `mouse_spin_log.csv`** — append every spin event (time,
  spin type, process, PID, parent, notes, path) to a CSV in the working
  directory, so you can leave it running and review later.

### The terminal mode

`--watch` is the mode to use, because it tracks process launches over time (a
single `--cli` snapshot has no history to compare against).

```
[14:02:07] SPINNING -> pointer spin (working-in-background cursor)
  Just launched (most likely the trigger):
    - setup.exe  (PID 9123, 0.3s ago)  parent: explorer.exe  (runs from downloads)
          C:\Users\you\Downloads\setup.exe

  Cursor owned by: explorer.exe  (PID 4477) [under-cursor]
          window "Downloads"

  Not responding (can freeze the cursor):
    - Outlook.exe (PID 6789)
```

`--watch` samples on an interval and, with `-d`, prints a summary ranking what
kept the cursor spinning the longest.

## How it works

1. **Read the live cursor** with `GetCursorInfo`. We identify the *wait*
   (`32514`) and *app-starting* (`32650`) spinners two ways — by the cursor's
   `GetIconInfoExW` resource id, **and** by comparing the live handle against
   the current `IDC_WAIT` / `IDC_APPSTARTING` handles (the system spinners are
   animated `.ani` cursors, where the resource id alone can read back as `0`).
2. **Track process launches.** Every poll, `CreateToolhelp32Snapshot` lists all
   processes; we diff against the previous list to record what was **just
   created** (PID, name, parent PID) with a timestamp. A process created in the
   last few seconds is the most likely trigger — that's exactly what the
   "app-starting" cursor signals. Each is annotated with neutral **notes**
   (script/interpreter, runs-from-Temp, started-by-Office, outside-System32).
3. **Find who owns the cursor.** The window controlling the displayed cursor,
   best candidate first: capture window (`GetGUIThreadInfo` → `hwndCapture`) →
   under the pointer (`WindowFromPoint`) → foreground (`GetForegroundWindow`),
   resolved to a PID + image path (`GetAncestor` / `GetWindowThreadProcessId` /
   `QueryFullProcessImageNameW`).
4. **Find what's hung.** `EnumWindows` sweeps every visible, titled top-level
   window and lists any that are **not responding** (`IsHungAppWindow`), since a
   frozen app freezes the cursor.

All three are shown together. The tool excludes **its own** PID/window so it
never blames itself.

## Limitations / honesty

- Attribution is a strong heuristic, not proof. Windows doesn't record "process
  X caused this spin"; we infer it from launch timing, window ownership, and
  hung state.
- The **notes** are just context (plenty of legit software runs from `AppData`
  or uses PowerShell). They're a "what kind of thing was this / worth a look"
  hint, not a verdict — this is **not** antivirus.
- Very short-lived processes (created and gone between polls) can be missed; run
  `--watch` / leave the GUI open so the poll cadence catches more.
- If the cursor is **hidden/suppressed** (full-screen games/video), there's
  nothing to attribute and the tool says so.
- Higher-integrity processes (elevated/admin) may show a PID but not a name or
  path unless you run the tool **as Administrator**.

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
