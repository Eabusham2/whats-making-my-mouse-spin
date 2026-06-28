#!/usr/bin/env python3
"""
whats-making-my-mouse-spin - GUI (Python / tkinter)
===================================================

A no-compile graphical version: a small always-on-top window that live-updates
to show whether your mouse cursor is spinning and, if so, which process owns it.

It reuses the detection logic from mouse_spin_detector.py, so it reports the
same thing the CLI does (process name + PID + spin type + hung state) - just in
a window instead of the terminal.

Run:
    python mouse_spin_gui.py

tkinter ships with the standard Windows Python installer, so there's nothing to
install. If you'd rather have a single native .exe with no Python at all, build
mouse_spin_gui.c instead.
"""

import sys

import mouse_spin_detector as msd

POLL_MS = 150

# state kind -> (background colour, headline)
STYLES = {
    "full":    ("#aa1e1e", "FULL SPIN"),
    "pointer": ("#bb7300", "POINTER SPIN"),
    "hidden":  ("#464646", "CURSOR HIDDEN"),
    "none":    ("#196e37", "NO SPIN"),
}


def current_state():
    """Return (kind, detail_text) using the shared detector helpers."""
    cur = msd.read_cursor()
    if cur is None:
        return "none", "Could not read the cursor (GetCursorInfo failed)."
    res_id, showing, pos = cur

    if not showing:
        return "hidden", ("Cursor is hidden/suppressed (full-screen app or game) "
                          "- nothing to attribute.")

    spin = msd.classify(res_id)
    if spin is None:
        known = msd.KNOWN_CURSORS.get(res_id, f"non-spinning (res id {res_id})")
        return "none", f"Your cursor is normal: {known}.\nNothing is spinning."

    short, _desc = spin
    candidates = msd.find_candidates(pos)
    if not candidates:
        return short, "Spinning, but I couldn't attribute it to any window/process."

    c = candidates[0]
    name = c["name"] or "(name unavailable - run as admin?)"
    lines = [
        f"Process:  {name}   (PID {c['pid']})",
        f'Window:  "{c["title"] or "(no title)"}"',
        f"Via:  {c['via']}",
    ]
    if c["hung"]:
        lines.append("State:  NOT RESPONDING (hung)")
    return short, "\n".join(lines)


def main():
    if not msd.IS_WINDOWS:
        print("This GUI only works on Windows (the spinning cursor is a Win32 "
              "concept). See the README for the why and for macOS/Linux notes.")
        return 2

    import tkinter as tk

    root = tk.Tk()
    root.title("What's making my mouse spin?")
    root.geometry("480x200")
    root.attributes("-topmost", True)

    headline = tk.Label(root, font=("Segoe UI", 22, "bold"),
                        fg="white", anchor="w", padx=16, pady=10)
    headline.pack(fill="x")

    detail = tk.Label(root, font=("Segoe UI", 11), fg="white",
                      justify="left", anchor="nw", padx=16)
    detail.pack(fill="both", expand=True)

    def tick():
        kind, text = current_state()
        bg, head = STYLES.get(kind, STYLES["none"])
        root.configure(bg=bg)
        for w in (headline, detail):
            w.configure(bg=bg)
        headline.configure(text=head)
        detail.configure(text=text)
        root.after(POLL_MS, tick)

    tick()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
