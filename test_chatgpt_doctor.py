"""doctor.py had no test, so a tab-shape change left it crashing with the suite green."""

import importlib.util
import os
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("doctor", os.path.join(_HERE, "scripts", "doctor.py"))
doctor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(doctor)


class CheckChatgptTabTest(unittest.TestCase):
    def _run(self, listing):
        with mock.patch.object(doctor.cgpt, "bridge", return_value=listing):
            return doctor.check_chatgpt_tab()

    def test_reports_the_stable_id_of_the_first_chatgpt_tab(self):
        rows = [(1, 1, 11, "https://example.com/?q=chatgpt.com"),
                (1, 2, 22, "https://chatgpt.com/c/abc")]
        with mock.patch.object(doctor.cgpt, "parse_tabs", return_value=rows):
            check, found = self._run("ignored")
        self.assertTrue(check.ok)
        self.assertEqual(found, 22)
        self.assertEqual(check.detail, "tab 22")

    def test_no_chatgpt_tab_is_reported_not_raised(self):
        rows = [(1, 1, 11, "https://example.com/")]
        with mock.patch.object(doctor.cgpt, "parse_tabs", return_value=rows):
            check, found = self._run("ignored")
        self.assertFalse(check.ok)
        self.assertIsNone(found)
        self.assertEqual(check.detail, "none")


if __name__ == "__main__":
    unittest.main()
