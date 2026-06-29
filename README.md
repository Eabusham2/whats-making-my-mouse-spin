# whats-making-my-mouse-spin

Find out **which process is making your mouse cursor spin** — and whether it's
the **full spin** (the whole pointer becomes a busy ring) or the **pointer
spin** (a normal arrow with a little spinner, "working in background").

The pointer spin is the system's *"a process is starting / doing work in the
background"* signal — which is exactly what fires when something launches behind
your back. So this tool is built to answer **"what just ran?"**: when a spin
happens it points at the **process that launched at that moment**, what
**spawned** it, and whether it looks **suspicious** (runs from a Temp/AppData
folder, a script host spawned by Office, etc.). That makes it handy for
spotting sketchy background/maybe-malware activity — not just hung apps.

For each spin it reports:

- the **process that just launched** (name + PID) and its **parent** process
- **which kind of spin** (`full` vs `pointer`)
- a **[SUSPICIOUS]** flag with the reason, for common malware tells
- a **spin history** and an optional **CSV log** so you can catch things that
  happen while you're away
- and if it genuinely can't be determined, it tells you so.

> **Not antivirus.** These are heuristics to help you *notice and investigate*
> background activity. A clean result doesn't mean safe, and a `[SUSPICIOUS]`
> flag doesn't mean infected — it means "worth a look."

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
red = full spin**. When a spin happens it shows the process(es) that just
launched, each with its **parent**, **path**, and a **[SUSPICIOUS]** note when a
malware heuristic matches. It also:

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
  spin type, process, PID, parent, suspicious flag + reasons, path) to a CSV in
  the working directory, so you can leave it running and review later.

### The terminal mode

`--watch` is the mode to use for hunting, because it tracks process launches
over time (a single `--cli` snapshot has no history to compare against).

```
[14:02:07] SPINNING -> pointer spin (working-in-background cursor)
  Process(es) that just launched (most likely cause):
    powershell.exe  (PID 9123, 0.3s ago)  [SUSPICIOUS]
        parent: winword.exe (PID 4477)
        path: C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
        why: script host spawned by an Office app (winword.exe -> powershell.exe)
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
   created** (PID, name, parent PID) with a timestamp.
3. **When a spin fires, blame what just launched.** Any process created in the
   last few seconds is the prime suspect — that's precisely what the
   "app-starting" cursor is signalling. For each we resolve the image path
   (`QueryFullProcessImageNameW`) and the **parent** process, then run malware
   heuristics:
   - runs from `Temp` / `AppData` / `Downloads` / `ProgramData` / `Public`;
   - a script host / LOLBin (`powershell`, `wscript`, `mshta`, `rundll32`, …)
     **spawned by an Office app**, or a LOLBin chain;
   - a LOLBin running from **outside `System32`**.
4. **Fallback.** If nothing new spawned (an already-running process did the
   work), it attributes to the active window instead — capture window →
   under-pointer → foreground (`GetGUIThreadInfo` / `WindowFromPoint` /
   `GetForegroundWindow`) — and still shows the parent + suspicious checks. The
   tool excludes **its own** PID/window so it never blames itself.

## Limitations / honesty

- This is an **investigation aid, not antivirus.** The `[SUSPICIOUS]` flag is a
  set of cheap heuristics — plenty of legit software runs from `AppData` or uses
  PowerShell. Treat it as "worth a look," and treat a clean result as "nothing
  obvious," not "definitely safe."
- Attribution is a strong heuristic, not proof. Windows doesn't record "process
  X caused this spin"; we infer it from launch timing and window ownership.
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
