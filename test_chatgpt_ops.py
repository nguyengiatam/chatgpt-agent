#!/usr/bin/env python3
"""Tests for the read-only operation layer.

The path tests carry the weight here: everything the model is allowed to touch
is decided by `resolve_path`, in code, where no prompt can argue with it.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chatgpt_ops as ops


class TempRootTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.outside = os.path.realpath(tempfile.mkdtemp())
        os.makedirs(os.path.join(self.root, "src"))
        self._write("src/pay.py", "def fee(x):\n    return x * 2\n")
        self._write("README.md", "# title\n\nbody\n")
        self._write(".env", "SECRET=1\n")
        self._write("outside-secret.txt", "in root, fine\n")
        with open(os.path.join(self.outside, "stolen.txt"), "w") as handle:
            handle.write("not yours\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.outside, ignore_errors=True)

    def _write(self, rel, text):
        path = os.path.join(self.root, rel)
        with open(path, "w") as handle:
            handle.write(text)
        return path


class ResolvePathTest(TempRootTest):
    def test_allows_a_file_inside_the_root(self):
        self.assertEqual(
            ops.resolve_path(self.root, "src/pay.py"),
            os.path.join(self.root, "src", "pay.py"),
        )

    def test_rejects_dot_dot_escape(self):
        with self.assertRaises(ops.OpError):
            ops.resolve_path(self.root, "../" + os.path.basename(self.outside) + "/stolen.txt")

    def test_rejects_an_absolute_path_outside_the_root(self):
        with self.assertRaises(ops.OpError):
            ops.resolve_path(self.root, os.path.join(self.outside, "stolen.txt"))

    def test_rejects_a_symlink_pointing_out_of_the_root(self):
        link = os.path.join(self.root, "escape")
        os.symlink(self.outside, link)
        with self.assertRaises(ops.OpError):
            ops.resolve_path(self.root, "escape/stolen.txt")

    def test_accepts_an_absolute_path_inside_the_root(self):
        target = os.path.join(self.root, "README.md")
        self.assertEqual(ops.resolve_path(self.root, target), target)

    def test_root_itself_resolves(self):
        self.assertEqual(ops.resolve_path(self.root, "."), self.root)


class BlockedPathTest(TempRootTest):
    def test_dotenv_is_blocked(self):
        with self.assertRaises(ops.OpError):
            ops.resolve_path(self.root, ".env")

    def test_dotenv_variants_are_blocked(self):
        for name in (".env.local", ".env.production"):
            with self.assertRaises(ops.OpError):
                ops.resolve_path(self.root, name)

    def test_private_keys_are_blocked(self):
        for name in ("server.pem", "app.key", "id_rsa", "id_ed25519"):
            with self.assertRaises(ops.OpError):
                ops.resolve_path(self.root, name)

    def test_git_internals_are_blocked(self):
        with self.assertRaises(ops.OpError):
            ops.resolve_path(self.root, ".git/config")

    def test_credential_files_are_blocked(self):
        for name in (".netrc", ".npmrc", "credentials"):
            with self.assertRaises(ops.OpError):
                ops.resolve_path(self.root, name)

    def test_a_name_merely_containing_env_is_allowed(self):
        self._write("environment.md", "notes\n")
        self.assertTrue(ops.resolve_path(self.root, "environment.md"))

    def test_the_error_names_the_reason(self):
        try:
            ops.resolve_path(self.root, ".env")
        except ops.OpError as exc:
            self.assertIn("blocked", str(exc).lower())
        else:
            self.fail("expected OpError")


class NumberLinesTest(unittest.TestCase):
    def test_numbers_start_at_one(self):
        self.assertIn("1|", ops.number_lines("alpha\nbeta\n"))

    def test_numbering_can_start_elsewhere(self):
        out = ops.number_lines("alpha\n", start=40)
        self.assertIn("40|", out)

    def test_content_is_preserved(self):
        self.assertIn("beta", ops.number_lines("alpha\nbeta\n"))


class ReadOpTest(TempRootTest):
    def test_reads_a_file_with_line_numbers(self):
        result = ops.execute(self.root, {"op": "read", "path": "src/pay.py"})
        self.assertNotIn("error", result)
        self.assertIn("def fee", result["body"])
        self.assertIn("1|", result["body"])

    def test_language_is_inferred_from_the_extension(self):
        result = ops.execute(self.root, {"op": "read", "path": "src/pay.py"})
        self.assertEqual(result["lang"], "python")

    def test_a_line_range_narrows_the_output(self):
        self._write("many.txt", "".join(str(n) + "\n" for n in range(1, 51)))
        result = ops.execute(self.root, {"op": "read", "path": "many.txt", "start": 10, "end": 12})
        self.assertIn("10|", result["body"])
        self.assertNotIn("20|", result["body"])

    def test_a_missing_file_is_an_error_result_not_a_crash(self):
        result = ops.execute(self.root, {"op": "read", "path": "nope.py"})
        self.assertIn("error", result)

    def test_a_blocked_file_is_an_error_result(self):
        result = ops.execute(self.root, {"op": "read", "path": ".env"})
        self.assertIn("error", result)
        self.assertNotIn("SECRET", str(result))


class ListOpTest(TempRootTest):
    def test_lists_entries(self):
        result = ops.execute(self.root, {"op": "list", "path": "."})
        self.assertIn("src", result["body"])
        self.assertIn("README.md", result["body"])

    def test_blocked_entries_are_not_listed(self):
        result = ops.execute(self.root, {"op": "list", "path": "."})
        self.assertNotIn(".env", result["body"])


class SearchOpTest(TempRootTest):
    def test_finds_a_match(self):
        result = ops.execute(self.root, {"op": "search", "pattern": "def fee"})
        self.assertNotIn("error", result)
        self.assertIn("pay.py", result["body"])

    def test_reports_no_matches_plainly(self):
        result = ops.execute(self.root, {"op": "search", "pattern": "zzz-not-here-zzz"})
        self.assertIn("no matches", result["body"].lower())

    def test_a_missing_pattern_is_an_error(self):
        result = ops.execute(self.root, {"op": "search"})
        self.assertIn("error", result)


class DispatchTest(TempRootTest):
    def test_unknown_op_is_reported_with_the_allowed_set(self):
        result = ops.execute(self.root, {"op": "rm", "path": "/"})
        self.assertIn("error", result)
        self.assertIn("read", result["error"])

    def test_write_is_not_an_op(self):
        result = ops.execute(self.root, {"op": "write", "path": "x", "body": "y"})
        self.assertIn("error", result)

    def test_shell_is_not_an_op(self):
        result = ops.execute(self.root, {"op": "shell", "cmd": "ls"})
        self.assertIn("error", result)

    def test_every_result_carries_a_label(self):
        for op in ({"op": "read", "path": "README.md"}, {"op": "list", "path": "."}):
            self.assertTrue(ops.execute(self.root, op)["label"])



class LimitTest(TempRootTest):
    """The run budget shrinks individual results; it must never enlarge them."""

    def setUp(self):
        TempRootTest.setUp(self)
        self._write("big.txt", "".join("line " + str(n) + "\n" for n in range(1, 400)))

    def test_a_small_limit_truncates_the_body(self):
        result = ops.execute(self.root, {"op": "read", "path": "big.txt"}, limit=200)
        self.assertLessEqual(len(result["body"]), 200)

    def test_truncation_is_announced(self):
        result = ops.execute(self.root, {"op": "read", "path": "big.txt"}, limit=200)
        self.assertIn("truncated", result.get("note", ""))

    def test_a_huge_limit_cannot_exceed_the_per_op_ceiling(self):
        result = ops.execute(self.root, {"op": "read", "path": "big.txt"}, limit=10 ** 9)
        self.assertLessEqual(len(result["body"]), ops.MAX_CHARS)

    def test_default_limit_still_works(self):
        result = ops.execute(self.root, {"op": "read", "path": "src/pay.py"})
        self.assertIn("def fee", result["body"])


class SearchLimitRegressionTest(TempRootTest):
    """The character budget and the hit count are different numbers.

    They were briefly the same variable, which silently clipped search output
    to fifty characters - short enough that the existing tests still passed.
    """

    def setUp(self):
        TempRootTest.setUp(self)
        for n in range(12):
            self._write("hit%02d.py" % n, "def marker_target():\n    pass\n")

    def test_many_hits_are_not_clipped_to_the_hit_count_in_characters(self):
        result = ops.execute(self.root, {"op": "search", "pattern": "marker_target"})
        self.assertGreater(len(result["body"]), 200)

    def test_every_matching_file_is_listed(self):
        result = ops.execute(self.root, {"op": "search", "pattern": "marker_target"})
        for n in range(12):
            self.assertIn("hit%02d.py" % n, result["body"])

    def test_max_still_caps_the_number_of_hits(self):
        result = ops.execute(self.root, {"op": "search", "pattern": "marker_target", "max": 3})
        self.assertEqual(len([l for l in result["body"].split("\n") if l.strip()]), 3)


if __name__ == "__main__":
    unittest.main()
