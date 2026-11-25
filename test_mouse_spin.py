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


class TerminalControlTests(unittest.TestCase):
    def test_c0_c1_and_delete_are_escaped(self):
        codes = list(range(0x20)) + [0x7f] + list(range(0x80, 0xa0))
        controls = "".join(chr(code) for code in codes)
        expected = "".join("\\x%02x" % code for code in codes)
        self.assertEqual(ms.escape_terminal_controls(controls), expected)

    def test_printable_window_title_is_preserved(self):
        title = 'Résumé — setup.exe "ready"'
        self.assertEqual(ms.escape_terminal_controls(title), title)

    def _describe_title(self, title, terminal_safe):
        forensics = ms.SpinForensics(999)
        candidate = {
            "via": "foreground", "hwnd": 1, "pid": 123, "tid": 456,
            "name": "app.exe", "image": r"C:\Program Files\App\app.exe",
            "title": title, "class": "AppWindow", "hung": False,
        }
        with mock.patch.object(ms, "read_cursor", return_value=(1, None, True, (0, 0))), \
                mock.patch.object(ms, "spin_kind", return_value=("full", "full spin")), \
                mock.patch.object(ms, "find_candidates", return_value=[candidate]), \
                mock.patch.object(ms, "find_hung_windows", return_value=[]), \
                mock.patch.object(forensics, "_busy", return_value=[]):
            return forensics.describe(terminal_safe=terminal_safe)[1]

    def test_terminal_report_escapes_title_controls(self):
        detail = self._describe_title("ready\x1b[2J\r\b\t\n\x9bspoof", True)
        self.assertIn(
            'window "ready\\x1b[2J\\x0d\\x08\\x09\\x0a\\x9bspoof"', detail)
        self.assertIn('\n        window "', detail)

    def test_gui_report_keeps_original_title(self):
        title = "first line\nsecond line"
        self.assertIn('window "%s"' % title, self._describe_title(title, False))

class SpreadsheetSafeCellTests(unittest.TestCase):
    def test_formula_prefixes_are_literalized(self):
        for value in ("=1+1", "+cmd", "-2+3", "@SUM(A1:A2)"):
            with self.subTest(value=value):
                self.assertEqual(ms.spreadsheet_safe_cell(value), "'" + value)

    def test_whitespace_bypasses_are_literalized(self):
        for value in ("  =1+1", "\t+cmd", "\r-2+3", "\n@SUM(A1:A2)",
                      "\tplain text", " \rplain text"):
            with self.subTest(value=value):
                self.assertEqual(ms.spreadsheet_safe_cell(value), "'" + value)

    def test_ordinary_values_are_preserved(self):
        values = ("setup.exe", r"C:\Program Files\App\app.exe", "", None, 42)
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(ms.spreadsheet_safe_cell(value), value)


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


if __name__ == "__main__":
    unittest.main()
