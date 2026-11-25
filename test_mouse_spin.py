#!/usr/bin/env python3
"""Unit tests for the platform-independent parts of mouse_spin.py.

These run on any OS (Linux CI included): they cover the launch-note
vocabulary, the cause ranking used by the GUI, and the --watch summary.
The Win32 detection itself can only be exercised on Windows.

    python -m unittest -v
"""
import io
import unittest
from unittest import mock

import mouse_spin as ms


class LaunchNotesTests(unittest.TestCase):
    def test_plain_app_has_no_notes(self):
        self.assertEqual(
            ms.launch_notes(r"C:\Program Files\App\app.exe", "app.exe", "explorer.exe"),
            [])

    def test_interpreter_is_flagged(self):
        notes = ms.launch_notes(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                                "powershell.exe", "explorer.exe")
        self.assertIn("script/interpreter", notes)

    def test_office_parent_of_interpreter(self):
        notes = ms.launch_notes(r"C:\Windows\System32\cmd.exe", "cmd.exe", "WINWORD.EXE")
        self.assertIn("started by an Office app (WINWORD.EXE)", notes)

    def test_temp_folder_is_noted_once(self):
        notes = ms.launch_notes(r"C:\Users\me\AppData\Local\Temp\setup.exe",
                                "setup.exe", "explorer.exe")
        self.assertEqual(notes, ["runs from appdata\\local\\temp"])

    def test_lolbin_outside_system32(self):
        notes = ms.launch_notes(r"C:\Users\me\Downloads\rundll32.exe",
                                "rundll32.exe", "explorer.exe")
        self.assertIn("rundll32.exe outside System32", notes)
        self.assertIn("runs from downloads", notes)

    def test_unreadable_path(self):
        self.assertEqual(ms.launch_notes(None, "thing.exe", None), ["path unreadable"])


class BestCauseTests(unittest.TestCase):
    def test_none_handling(self):
        c = {"via": "foreground"}
        self.assertIsNone(ms.best_cause(None, None))
        self.assertIs(ms.best_cause(None, c), c)
        self.assertIs(ms.best_cause(c, None), c)

    def test_new_process_beats_window_owner(self):
        owner = {"via": "under-cursor", "name": "explorer.exe"}
        launched = {"via": "new-process", "name": "setup.exe"}
        self.assertIs(ms.best_cause(owner, launched), launched)
        self.assertIs(ms.best_cause(launched, owner), launched)

    def test_equal_rank_keeps_first_seen(self):
        a = {"via": "under-cursor"}
        b = {"via": "mouse-capture"}
        self.assertIs(ms.best_cause(a, b), a)

    def test_unknown_via_ranks_lowest(self):
        weird = {"via": "???"}
        cpu = {"via": "high-cpu"}
        self.assertIs(ms.best_cause(weird, cpu), cpu)


class CauseKeyTests(unittest.TestCase):
    def test_unattributed(self):
        self.assertEqual(ms.cause_key(None), "(unattributed)")

    def test_name_and_pid(self):
        self.assertEqual(ms.cause_key({"name": "setup.exe", "pid": 42}), "setup.exe (PID 42)")

    def test_missing_name(self):
        self.assertEqual(ms.cause_key({"name": None, "pid": 7}), "(unknown) (PID 7)")


class WatchSummaryTests(unittest.TestCase):
    def _capture(self, stats):
        buf = io.StringIO()
        ms.print_watch_summary(stats, out=lambda line: buf.write(line + "\n"))
        return buf.getvalue()

    def test_empty(self):
        self.assertIn("no spinning cursor was seen", self._capture({}))

    def test_ranked_by_time(self):
        out = self._capture({
            ("setup.exe (PID 9123)", "pointer"): 0.6,
            ("Outlook.exe (PID 6789)", "full"): 4.2,
        })
        lines = [l for l in out.splitlines() if l.startswith("  ")]
        self.assertEqual(len(lines), 2)
        self.assertIn("Outlook.exe (PID 6789)  -  full spin  ~4.2s", lines[0])
        self.assertIn("setup.exe (PID 9123)  -  pointer spin  ~0.6s", lines[1])


class MetaTests(unittest.TestCase):
    def test_version_string(self):
        parts = ms.__version__.split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(p.isdigit() for p in parts))

    def test_styles_cover_all_kinds(self):
        for kind in ("full", "pointer", "hidden", "none"):
            self.assertIn(kind, ms.STYLES)


class LogFileTests(unittest.TestCase):
    def test_elevated_logging_uses_secure_opener(self):
        opened = (object(), False)
        with mock.patch.object(ms, "_open_elevated_log_file",
                               return_value=opened) as secure_open:
            self.assertIs(ms._open_log_file("protected.csv", True), opened)
        secure_open.assert_called_once_with("protected.csv")

    def test_elevated_open_failure_does_not_fallback(self):
        with mock.patch.object(ms, "_open_elevated_log_file",
                               side_effect=PermissionError("unsafe")), \
                mock.patch("builtins.open") as ordinary_open:
            with self.assertRaises(PermissionError):
                ms._open_log_file("protected.csv", True)
        ordinary_open.assert_not_called()

    def test_new_log_writes_csv_header(self):
        app = object.__new__(ms.SpinGuiApp)
        app.var_log = mock.Mock()
        app.var_log.get.return_value = True
        app.status_label = mock.Mock()
        app._log_path = "mouse_spin_log.csv"
        app._log_elevated = False
        stream = io.StringIO()
        with mock.patch.object(ms, "_open_log_file",
                               return_value=(stream, True)):
            app._toggle_log()
        self.assertTrue(stream.getvalue().startswith(
            "time,spin,duration_s,process,pid,ppid,parent,via,notes,path"))

    def test_log_open_error_disables_toggle(self):
        app = object.__new__(ms.SpinGuiApp)
        app.var_log = mock.Mock()
        app.var_log.get.return_value = True
        app.status_label = mock.Mock()
        app._log_path = "mouse_spin_log.csv"
        app._log_elevated = True
        with mock.patch.object(ms, "_open_log_file",
                               side_effect=PermissionError("unsafe")):
            app._toggle_log()
        app.var_log.set.assert_called_once_with(False)
        self.assertIsNone(app._log_fh)
        self.assertIsNone(app._log_writer)


if __name__ == "__main__":
    unittest.main()
