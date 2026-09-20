#!/usr/bin/env python3
"""Tests for the pure-Python logic in chatgpt-cli.py (no browser needed)."""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("cgpt", os.path.join(_HERE, "chatgpt-cli.py"))
cgpt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cgpt)

NODE = shutil.which("node")


def _edge_tab_count():
    """Number of tabs Edge reports, or None when it cannot be asked."""
    try:
        proc = subprocess.run(
            ["osascript", "-e", 'tell application "Microsoft Edge" to return (count of tabs of window 1)'],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


EDGE_TABS = _edge_tab_count()


class JsStringTest(unittest.TestCase):
    """js_string() must turn any Python str into one safe JS string literal."""

    def test_wraps_plain_text_in_quotes(self):
        self.assertEqual(cgpt.js_string("hello"), '"hello"')

    def test_escapes_double_quotes(self):
        self.assertNotIn('"a"b"', cgpt.js_string('a"b'))

    def test_escapes_backslash(self):
        self.assertEqual(cgpt.js_string("a\\b"), '"a\\\\b"')

    def test_newline_never_appears_literally(self):
        # A raw newline inside a JS literal is a syntax error.
        self.assertNotIn("\n", cgpt.js_string("line one\nline two"))

    def test_non_ascii_is_escaped(self):
        # osascript mangles non-ASCII bytes; force \uXXXX so it survives.
        out = cgpt.js_string("cà phê \U0001f600")
        self.assertTrue(all(ord(c) < 128 for c in out), out)

    @unittest.skipUnless(NODE, "node not installed")
    def test_round_trips_through_real_javascript(self):
        nasty = 'He said "hi"\\ then\nnew line\ttab \'quote\' cà phê \U0001f600 ${x} `tick`'
        literal = cgpt.js_string(nasty)
        proc = subprocess.run(
            [NODE, "-e", "process.stdout.write(JSON.stringify(" + literal + "))"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), nasty)


class ParseTabsTest(unittest.TestCase):
    """parse_tabs() reads the tab-separated rows the AppleScript bridge prints."""

    def test_parses_window_tab_id_and_url(self):
        self.assertEqual(
            cgpt.parse_tabs("1\t2\t314\thttps://example.com/"),
            [(1, 2, 314, "https://example.com/")],
        )

    def test_ignores_blank_lines(self):
        raw = "1\t1\t101\thttps://a.com/\n\n2\t3\t202\thttps://b.com/\n"
        self.assertEqual(len(cgpt.parse_tabs(raw)), 2)

    def test_keeps_url_containing_tab_separator_characters(self):
        raw = "1\t1\t314\thttps://a.com/?x=1&y=2"
        self.assertEqual(cgpt.parse_tabs(raw)[0][3], "https://a.com/?x=1&y=2")

    def test_rejects_the_old_three_column_format(self):
        self.assertEqual(cgpt.parse_tabs("1\t2\thttps://example.com/"), [])

    def test_rejects_non_integer_tab_ids(self):
        self.assertEqual(cgpt.parse_tabs("1\t2\tnot-an-id\thttps://example.com/"), [])

    def test_returns_empty_list_for_empty_output(self):
        self.assertEqual(cgpt.parse_tabs(""), [])


class FindChatgptTabTest(unittest.TestCase):
    """find_chatgpt_tab() must match on host, not on a substring of the URL."""

    def test_finds_chatgpt_com(self):
        tabs = [(1, 1, 101, "https://news.example.com/"), (1, 2, 202, "https://chatgpt.com/c/abc")]
        self.assertEqual(cgpt.find_chatgpt_tab(tabs), 202)

    def test_finds_legacy_chat_openai_com(self):
        tabs = [(2, 5, 505, "https://chat.openai.com/c/xyz")]
        self.assertEqual(cgpt.find_chatgpt_tab(tabs), 505)

    def test_returns_first_match_so_conversation_is_reused(self):
        tabs = [(1, 1, 101, "https://chatgpt.com/c/first"), (1, 2, 202, "https://chatgpt.com/c/second")]
        self.assertEqual(cgpt.find_chatgpt_tab(tabs), 101)

    def test_returns_none_when_no_chatgpt_tab(self):
        self.assertIsNone(cgpt.find_chatgpt_tab([(1, 1, 101, "https://example.com/")]))

    def test_does_not_match_other_host_mentioning_chatgpt_in_query(self):
        tabs = [(1, 1, 101, "https://evil.example.com/?redirect=https://chatgpt.com/")]
        self.assertIsNone(cgpt.find_chatgpt_tab(tabs))

    def test_does_not_match_lookalike_host(self):
        self.assertIsNone(cgpt.find_chatgpt_tab([(1, 1, 101, "https://notchatgpt.com/")]))


class StabilityTrackerTest(unittest.TestCase):
    """Completion needs our reply to exist and to have stopped growing.

    `streaming` is a page-wide signal, so it only counts against us while our
    reply is still the newest one - otherwise a second CLI run streaming into
    the same tab would keep us waiting forever.
    """

    def tracker(self):
        return cgpt.StabilityTracker(stable_polls=2)

    def settle(self, tracker, text, found=True, streaming=False, is_last=True):
        return tracker.update(found=found, streaming=streaming, is_last=is_last, text=text)

    def test_not_done_while_our_reply_is_still_streaming(self):
        t = self.tracker()
        self.assertFalse(self.settle(t, "partial", streaming=True))
        self.assertFalse(self.settle(t, "partial", streaming=True))

    def test_not_done_before_our_reply_appears(self):
        t = self.tracker()
        self.assertFalse(self.settle(t, "", found=False))
        self.assertFalse(self.settle(t, "", found=False))

    def test_not_done_on_first_stable_observation(self):
        t = self.tracker()
        self.settle(t, "answer")
        self.assertFalse(self.settle(t, "answer"))

    def test_done_after_two_consecutive_identical_texts(self):
        t = self.tracker()
        self.settle(t, "answer")
        self.settle(t, "answer")
        self.assertTrue(self.settle(t, "answer"))

    def test_growing_text_resets_stability(self):
        t = self.tracker()
        self.settle(t, "ans")
        self.settle(t, "ans")
        self.settle(t, "answer")
        self.assertFalse(self.settle(t, "answer"))

    def test_never_completes_on_empty_text(self):
        t = self.tracker()
        for _ in range(5):
            self.assertFalse(self.settle(t, ""))

    def test_streaming_midway_resets_stability(self):
        t = self.tracker()
        self.settle(t, "answer")
        self.settle(t, "answer", streaming=True)
        self.assertFalse(self.settle(t, "answer"))

    def test_someone_elses_reply_streaming_after_ours_does_not_block_us(self):
        t = self.tracker()
        for _ in range(2):
            self.settle(t, "our finished answer", streaming=True, is_last=False)
        self.assertTrue(self.settle(t, "our finished answer", streaming=True, is_last=False))


class ExplainErrorTest(unittest.TestCase):
    """Bridge errors must stay actionable in any macOS display language.

    Regression: matching the English phrase "turned off" made the
    JavaScript-is-disabled hint vanish on a Vietnamese system.
    """

    VI_JS_OFF = (
        "execution error: Microsoft Edge g\u1eb7p l\u1ed7i: Th\u1ef1c thi JavaScript "
        "th\u00f4ng qua AppleScript \u0111\u00e3 b\u1ecb t\u1eaft. \u0110\u1ec3 b\u1eadt, "
        "t\u1eeb thanh menu, h\u00e3y v\u00e0o m\u1ee5c Xem > Nh\u00e0 ph\u00e1t tri\u1ec3n. (12)"
    )
    EN_JS_OFF = (
        "execution error: Microsoft Edge got an error: Executing JavaScript through "
        "AppleScript is turned off. (12)"
    )

    def test_vietnamese_javascript_disabled_error_gives_the_enable_hint(self):
        self.assertEqual(cgpt._explain(self.VI_JS_OFF), cgpt.ENABLE_HINT)

    def test_english_javascript_disabled_error_gives_the_enable_hint(self):
        self.assertEqual(cgpt._explain(self.EN_JS_OFF), cgpt.ENABLE_HINT)

    def test_automation_denied_error_code_gives_the_automation_hint(self):
        localised = "execution error: kh\u00f4ng \u0111\u01b0\u1ee3c ph\u00e9p. (-1743)"
        self.assertEqual(cgpt._explain(localised), cgpt.AUTOMATION_HINT)

    def test_app_not_running_error_code_is_recognised(self):
        self.assertIn("not running", cgpt._explain("execution error: ... (-600)").lower())

    def test_unknown_error_is_passed_through_verbatim(self):
        self.assertIn("something else broke", cgpt._explain("something else broke (-42)"))


@unittest.skipUnless(EDGE_TABS, "Microsoft Edge is not running with an open window")
class BridgeIntegrationTest(unittest.TestCase):
    """Runs the real AppleScript against the real Edge.

    Unit tests cannot catch AppleScript name collisions: inside a `tell
    application` block, a bare word is resolved against the app's dictionary
    first. `mode` silently became an Edge property (error -1728) and `tab`
    silently became Edge's tab CLASS, emitting the literal word "tab" as the
    column separator. Both compiled cleanly. Only a real run exposes them.
    """

    def test_list_returns_one_parsable_row_per_open_tab(self):
        tabs = cgpt.parse_tabs(cgpt.bridge("list"))
        self.assertGreaterEqual(len(tabs), EDGE_TABS)

    def test_rows_are_separated_by_real_tab_characters(self):
        raw = cgpt.bridge("list")
        self.assertIn("\t", raw)
        self.assertNotRegex(raw, r"^\d+tab\d+tab")

    def test_every_row_carries_a_plausible_url(self):
        for _, _, _, url in cgpt.parse_tabs(cgpt.bridge("list")):
            self.assertRegex(url, r"^[a-z][a-z0-9+.-]*:")

    def test_loading_reports_a_known_state(self):
        tabs = cgpt.parse_tabs(cgpt.bridge("list"))
        _, _, tab_id, _ = tabs[0]
        self.assertIn(cgpt.bridge("loading", tab_id), ("loading", "done"))

    def test_stable_id_survives_an_earlier_tab_closing(self):
        first = int(cgpt.bridge("open", "about:blank"))
        second = int(cgpt.bridge("open", "about:blank"))
        try:
            cgpt.bridge("close", first)
            self.assertEqual(cgpt.bridge("eval", "String(40 + 2)", second), "42")
        finally:
            try:
                cgpt.bridge("close", second)
            except cgpt.TabGone:
                pass

    def test_unknown_tab_id_fails_loudly(self):
        ids = [tab_id for _, _, tab_id, _ in cgpt.parse_tabs(cgpt.bridge("list"))]
        missing = (max(ids) + 1000000) if ids else 1000000
        with self.assertRaises(cgpt.TabGone) as caught:
            cgpt.bridge("loading", missing)
        self.assertIn(str(missing), str(caught.exception))


class ReadPromptTest(unittest.TestCase):
    """Argument words and piped stdin are both prompt material.

    Regression: passing an instruction as an argument while piping content
    silently discarded the pipe, so `git diff | chatgpt-cli.py "review this"`
    asked ChatGPT to review nothing.
    """

    class Stdin(io.StringIO):
        def __init__(self, data="", tty=False):
            io.StringIO.__init__(self, data)
            self._tty = tty

        def isatty(self):
            return self._tty

    def test_words_only(self):
        self.assertEqual(cgpt.read_prompt(["hello", "world"], self.Stdin(tty=True)), "hello world")

    def test_piped_input_only(self):
        self.assertEqual(cgpt.read_prompt([], self.Stdin("piped body")), "piped body")

    def test_words_and_pipe_are_combined_with_the_instruction_first(self):
        result = cgpt.read_prompt(["review", "this"], self.Stdin("diff --git a/x b/x"))
        self.assertEqual(result, "review this\n\ndiff --git a/x b/x")

    def test_nothing_given_on_a_terminal_yields_empty(self):
        self.assertEqual(cgpt.read_prompt([], self.Stdin(tty=True)), "")

    def test_empty_pipe_falls_back_to_the_words(self):
        self.assertEqual(cgpt.read_prompt(["just", "this"], self.Stdin("   \n")), "just this")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class NeedsAttachmentTest(unittest.TestCase):
    """Typing cost is quadratic in NEWLINES, not in characters.

    Measured against the live composer, same 40k characters throughout:
    1 line 0.1s, 50 lines 0.5s, 200 lines 2.4s, 700 lines 16.7s,
    2000 lines 116.3s. Each newline is another ProseMirror block node in a
    single transaction. So the threshold that matters is the line count, and a
    rule written against length alone would have let the worst case straight
    through.
    """

    def test_a_short_prompt_is_typed(self):
        self.assertFalse(cgpt.needs_attachment("review this diff"))

    def test_a_long_single_line_is_still_typed(self):
        # 60k on one line inserted in 0.1s; length alone is not the problem.
        self.assertFalse(cgpt.needs_attachment("x" * 60000))

    def test_many_short_lines_are_attached(self):
        self.assertTrue(cgpt.needs_attachment("\n".join("line" for _ in range(2000))))

    def test_the_threshold_sits_below_the_painful_range(self):
        self.assertLess(cgpt.ATTACH_LINES, 200)

    def test_just_under_the_line_threshold_is_typed(self):
        self.assertFalse(cgpt.needs_attachment("\n".join("a" for _ in range(cgpt.ATTACH_LINES - 1))))

    def test_just_over_the_line_threshold_is_attached(self):
        self.assertTrue(cgpt.needs_attachment("\n".join("a" for _ in range(cgpt.ATTACH_LINES + 2))))

    def test_an_enormous_single_line_still_attaches_on_size(self):
        self.assertTrue(cgpt.needs_attachment("x" * (cgpt.ATTACH_CHARS + 1)))

    def test_empty_text_is_typed(self):
        self.assertFalse(cgpt.needs_attachment(""))
        self.assertFalse(cgpt.needs_attachment(None))


class AttachPayloadTest(unittest.TestCase):
    """The first attach on a freshly navigated chat is the one that fails.

    React has not bound its handler to the file input yet, so the change event
    is dropped - and nothing downstream notices, because input.files still
    holds a file nobody consumed. Success is therefore the chip appearing, not
    the assignment returning ok.
    """

    def setUp(self):
        self.attaches = 0
        self.chip_after = 1          # attach number from which the chip appears
        self._real_eval = cgpt.eval_js
        self._real_settle = cgpt.ATTACH_SETTLE
        cgpt.eval_js = self._fake_eval
        cgpt.ATTACH_SETTLE = 0.9
        self.stderr = io.StringIO()
        self._real_stderr, sys.stderr = sys.stderr, self.stderr

    def tearDown(self):
        cgpt.eval_js = self._real_eval
        cgpt.ATTACH_SETTLE = self._real_settle
        sys.stderr = self._real_stderr

    def _fake_eval(self, tab_id, expression):
        if expression.startswith("CGPT.attach("):
            self.attaches += 1
            return {"ok": True, "error": None}
        if expression.startswith("CGPT.hasChip("):
            return self.attaches >= self.chip_after
        return {"ok": True, "error": None}

    def test_a_warm_page_attaches_exactly_once(self):
        cgpt.attach_payload(101, "payload", "c2c-aaaa1111.txt")
        self.assertEqual(self.attaches, 1)

    def test_a_warm_page_says_nothing(self):
        cgpt.attach_payload(101, "payload", "c2c-aaaa1111.txt")
        self.assertEqual(self.stderr.getvalue(), "")

    def test_a_cold_page_is_re_attached_until_the_chip_appears(self):
        self.chip_after = 2
        cgpt.attach_payload(101, "payload", "c2c-aaaa1111.txt")
        self.assertEqual(self.attaches, 2)

    def test_every_retry_is_announced(self):
        # Silence here would hide the only signal that says the tab was cold.
        self.chip_after = 2
        cgpt.attach_payload(101, "payload", "c2c-aaaa1111.txt")
        said = self.stderr.getvalue()
        self.assertIn("c2c-aaaa1111.txt", said)
        self.assertIn("try 2", said)

    def test_a_page_that_never_takes_the_file_raises(self):
        self.chip_after = 99
        with self.assertRaises(cgpt.CliError):
            cgpt.attach_payload(101, "payload", "c2c-aaaa1111.txt", tries=2)

    def test_it_gives_up_after_the_allotted_tries(self):
        self.chip_after = 99
        try:
            cgpt.attach_payload(101, "payload", "c2c-aaaa1111.txt", tries=2)
        except cgpt.CliError:
            pass
        self.assertEqual(self.attaches, 2)

    def test_a_refused_attachment_is_not_retried_into_the_ground(self):
        def refuses(tab_id, expression):
            if expression.startswith("CGPT.attach("):
                self.attaches += 1
                return {"ok": False, "error": "file-input-not-found"}
            return {"ok": True}

        cgpt.eval_js = refuses
        with self.assertRaises(cgpt.CliError):
            cgpt.attach_payload(101, "payload", "c2c-aaaa1111.txt")
        self.assertEqual(self.attaches, 1)


class SendTypedTextTest(unittest.TestCase):
    """Guard what send() actually types, not the constant it starts from.

    A mutation at the concatenation site - appending " The file is <name>." when
    building `typed` - leaves ATTACHED_NOTE untouched. A test asserting on the
    constant stayed green while the filename was back in the turn, which is
    exactly where it makes the post-send check a tautology again. So the
    assertion has to read the string that left the function.
    """

    BIG = "\n".join("line %d" % n for n in range(300))

    def setUp(self):
        self.calls = []
        self._real_eval = cgpt.eval_js
        cgpt.eval_js = self._fake_eval

    def tearDown(self):
        cgpt.eval_js = self._real_eval

    def _fake_eval(self, tab_id, expression):
        self.calls.append(expression)
        if expression.startswith("CGPT.attachmentReady("):
            return {"ok": True, "ready": True}
        if expression.startswith("CGPT.sentWithAttachment("):
            return {"ok": True, "sent": True, "carried": True}
        return {"ok": True, "error": None}

    def _call(self, prefix):
        for expression in self.calls:
            if expression.startswith(prefix):
                return expression
        return None

    def _typed(self):
        call = self._call("CGPT.insert(")
        return json.loads(call[len("CGPT.insert("):-1]) if call else None

    def test_the_typed_text_never_carries_the_attachment_name(self):
        marker = cgpt.send(101, self.BIG)
        self.assertNotIn(cgpt.attachment_name(marker), self._typed())

    def test_the_typed_text_still_carries_the_marker(self):
        marker = cgpt.send(101, self.BIG)
        self.assertIn(marker, self._typed())

    def test_the_payload_itself_is_not_typed(self):
        cgpt.send(101, self.BIG)
        self.assertNotIn("line 299", self._typed())

    def test_the_attachment_call_does_carry_the_name(self):
        # Proves this test exercises the attach path at all, so the assertion
        # above is about a name that genuinely exists somewhere in the run.
        marker = cgpt.send(101, self.BIG)
        self.assertIn(cgpt.attachment_name(marker), self._call("CGPT.attach("))

    def test_a_small_prompt_is_typed_whole_and_never_attached(self):
        marker = cgpt.send(101, "review this")
        self.assertIn("review this", self._typed())
        self.assertIsNone(self._call("CGPT.attach("))

    def test_send_refuses_when_the_turn_arrived_without_the_file(self):
        def carried_nothing(tab_id, expression):
            self.calls.append(expression)
            if expression.startswith("CGPT.attachmentReady("):
                return {"ok": True, "ready": True}
            if expression.startswith("CGPT.sentWithAttachment("):
                return {"ok": True, "sent": True, "carried": False}
            return {"ok": True, "error": None}

        cgpt.eval_js = carried_nothing
        with self.assertRaises(cgpt.CliError):
            cgpt.send(101, self.BIG)


class AttachedNoteTest(unittest.TestCase):
    """The typed note must not name the file.

    Both attachment guards search the sent turn for the filename. When our own
    sentence supplied it, neither could ever fail - three fixes in a row were
    accepted on evidence the tool wrote itself.
    """

    def test_the_note_never_names_the_file(self):
        self.assertNotIn("{name}", cgpt.ATTACHED_NOTE)
        self.assertNotIn(".txt", cgpt.ATTACHED_NOTE)

    def test_the_note_takes_no_format_arguments(self):
        self.assertEqual(cgpt.ATTACHED_NOTE.format(), cgpt.ATTACHED_NOTE)

    def test_the_note_still_tells_the_model_there_is_a_file(self):
        self.assertIn("attached", cgpt.ATTACHED_NOTE.lower())


class AttachmentNameTest(unittest.TestCase):
    def test_the_name_is_derived_from_the_marker(self):
        name = cgpt.attachment_name("[c2c:abcd1234]")
        self.assertIn("abcd1234", name)

    def test_the_name_ends_in_txt(self):
        self.assertTrue(cgpt.attachment_name("[c2c:abcd1234]").endswith(".txt"))

    def test_the_name_has_no_path_separators(self):
        self.assertNotIn("/", cgpt.attachment_name("[c2c:../../etc]"))


class MakeMarkerTest(unittest.TestCase):
    """The marker is what lets us find our own reply in a shared tab."""

    def test_markers_are_unique(self):
        self.assertNotEqual(cgpt.make_marker(), cgpt.make_marker())

    def test_marker_survives_markdown_rendering(self):
        # No backtick, asterisk or underscore: nothing the renderer would eat.
        marker = cgpt.make_marker()
        for char in "`*_#[]()":
            if char in "[]":
                continue
            self.assertNotIn(char, marker)

    def test_marker_is_short_enough_to_stay_out_of_the_way(self):
        self.assertLess(len(cgpt.make_marker()), 20)


class MainWiringTest(unittest.TestCase):
    """main() must pass the browser plumbing whatever ensure_tab hands back.

    Regression: when tab addressing moved from a (window, tab) pair to a single
    id, every *use* of the id inside main() was updated and the two lines that
    *produce* it were not. The module imported, compiled and unit-tested clean;
    the CLI died on its first line of real work with a ValueError. Nothing in
    the suite ran main(), so nothing noticed.
    """

    def _run_main(self, argv=("hello",)):
        calls = {}

        def ensure_tab(timeout):
            calls["ensure_tab"] = timeout
            return 4242

        def bridge(*args):
            calls.setdefault("bridge", []).append(args)
            return "ok"

        def eval_js(tab_id, expression):
            calls.setdefault("eval_js", []).append((tab_id, expression))
            return {"ok": True}

        def send(tab_id, text):
            calls["send"] = (tab_id, text)
            return "[c2c:deadbeef]"

        def wait_for_reply(tab_id, marker, timeout, poll):
            calls["wait_for_reply"] = (tab_id, marker)
            return "the answer"

        patched = {
            "ensure_tab": ensure_tab, "bridge": bridge, "eval_js": eval_js,
            "send": send, "wait_for_reply": wait_for_reply,
        }
        saved = {name: getattr(cgpt, name) for name in patched}
        for name, fake in patched.items():
            setattr(cgpt, name, fake)
        stdout, sys.stdout = sys.stdout, io.StringIO()
        try:
            code = cgpt.main(argv)
        finally:
            sys.stdout = stdout
            for name, original in saved.items():
                setattr(cgpt, name, original)
        return code, calls

    def test_main_completes_and_carries_one_tab_handle_throughout(self):
        code, calls = self._run_main()
        self.assertEqual(code, 0)
        self.assertEqual(calls["send"][0], 4242)
        self.assertEqual(calls["wait_for_reply"][0], 4242)
        self.assertEqual(calls["eval_js"][0][0], 4242)

    def test_focus_addresses_the_same_tab(self):
        code, calls = self._run_main(argv=("--focus", "hello"))
        self.assertEqual(code, 0)
        self.assertEqual(calls["bridge"], [("focus", 4242)])

    def test_no_bridge_call_when_focus_is_not_asked_for(self):
        _, calls = self._run_main()
        self.assertNotIn("bridge", calls)

class OneShotSelectionTest(unittest.TestCase):
    def test_claimed_first_candidate_uses_second_chatgpt_tab(self):
        rows = "1\t1\t101\thttps://chatgpt.com/c/a\n1\t2\t202\thttps://chatgpt.com/c/b"
        attempted = []
        class Held:
            def claim_tab(self, tab_id):
                attempted.append(tab_id)
                if tab_id == 101:
                    raise cgpt.claims.ClaimBusy("busy")
            def claim_conversation(self, identity):
                pass
            def release_tab(self, tab_id):
                pass
        real_bridge = cgpt.bridge
        cgpt.bridge = lambda *args: rows if args[0] == "list" else (_ for _ in ()).throw(AssertionError(args))
        try:
            self.assertEqual(cgpt.claim_one_shot_tab(Held(), 3), 202)
        finally:
            cgpt.bridge = real_bridge
        self.assertEqual(attempted, [101, 202])

class OneShotConversationSelectionTest(unittest.TestCase):
    def test_claimed_conversation_fails_immediately_and_names_holder(self):
        rows = "1\t1\t101\thttps://chatgpt.com/c/a\n1\t2\t202\thttps://chatgpt.com/c/b"
        attempted_tabs = []
        released_tabs = []
        class Held:
            def claim_tab(self, tab_id):
                attempted_tabs.append(tab_id)
            def release_tab(self, tab_id):
                released_tabs.append(tab_id)
            def claim_conversation(self, identity):
                if identity == "a":
                    raise cgpt.claims.ClaimBusy("conversation a is already claimed by rival")
        real_bridge, real_eval = cgpt.bridge, cgpt.eval_js
        cgpt.bridge = lambda *args: rows if args[0] == "list" else (_ for _ in ()).throw(AssertionError(args))
        cgpt.eval_js = lambda tab_id, expr: "https://chatgpt.com/c/a" if tab_id == 101 else "https://chatgpt.com/c/b"
        try:
            with self.assertRaises(cgpt.claims.ClaimBusy) as caught:
                cgpt.claim_one_shot_tab(Held(), 3)
        finally:
            cgpt.bridge, cgpt.eval_js = real_bridge, real_eval
        self.assertIn("rival", str(caught.exception))
        self.assertEqual(attempted_tabs, [101])
        self.assertEqual(released_tabs, [101])

class OneShotPostSendConversationClaimTest(unittest.TestCase):
    def test_main_claims_conversation_created_by_send_before_waiting(self):
        events = []
        class Held:
            def __init__(self, owner): events.append(("new", owner))
            def claim_tab(self, tab_id): events.append(("claim_tab", tab_id))
            def release_tab(self, tab_id): events.append(("release_tab", tab_id))
            def claim_conversation(self, identity): events.append(("claim_conversation", identity))
            def close(self): pass

        saved = {name: getattr(cgpt, name) for name in
                 ("ensure_tab", "eval_js", "send", "wait_for_reply")}
        real_claim_set = cgpt.claims.ClaimSet
        calls = {"location": 0}
        cgpt.ensure_tab = lambda timeout: 4242
        def eval_js(tab_id, expression):
            if expression == "location.href":
                calls["location"] += 1
                return ("https://chatgpt.com/" if calls["location"] == 1
                        else "https://chatgpt.com/c/newly-created")
            return {"ok": True}
        cgpt.eval_js = eval_js
        cgpt.send = lambda tab_id, text: "[c2c:marker]"
        def wait_for_reply(tab_id, marker, timeout, poll):
            self.assertIn(("claim_conversation", "newly-created"), events)
            return "OK"
        cgpt.wait_for_reply = wait_for_reply
        cgpt.claims.ClaimSet = Held
        stdout, sys.stdout = sys.stdout, io.StringIO()
        try:
            self.assertEqual(cgpt.main(["hello"]), 0)
        finally:
            sys.stdout = stdout
            cgpt.claims.ClaimSet = real_claim_set
            for name, value in saved.items():
                setattr(cgpt, name, value)


class ToolWindowTest(unittest.TestCase):
    """P7: every run works in a small corner window it owns and records.

    The registry is what tells a later run - or the same run at cleanup time -
    which windows the tool opened and which belong to the person using Edge. A
    window missing from it is a user window forever after, so recording it is
    part of opening it, and forgetting it is part of closing it.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (cgpt.WINDOW_STATE, cgpt.WINDOW_STATE_LOCK, cgpt.bridge)
        cgpt.WINDOW_STATE = os.path.join(self.dir, "windows.json")
        cgpt.WINDOW_STATE_LOCK = cgpt.WINDOW_STATE + ".lock"
        self.calls = []

    def tearDown(self):
        cgpt.WINDOW_STATE, cgpt.WINDOW_STATE_LOCK, cgpt.bridge = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def _bridge(self, replies):
        def bridge(*args):
            self.calls.append(args)
            reply = replies.get(args[0])
            if isinstance(reply, Exception):
                raise reply
            if reply is None:
                raise AssertionError("unexpected bridge call: " + repr(args))
            return reply
        cgpt.bridge = bridge

    def _windows(self):
        with open(cgpt.WINDOW_STATE, encoding="utf-8") as handle:
            return json.load(handle)


class WindowRegistryTest(ToolWindowTest):
    def test_an_absent_registry_reads_as_empty(self):
        self.assertEqual(cgpt._load_windows(), [])

    def test_a_corrupt_registry_does_not_crash_a_run(self):
        with open(cgpt.WINDOW_STATE, "w", encoding="utf-8") as handle:
            handle.write("{ truncated")
        self.assertEqual(cgpt._load_windows(), [])

    def test_a_recorded_window_round_trips_as_numbers(self):
        cgpt._record_window("707", "12")
        stored = self._windows()
        self.assertEqual(stored[0]["tab_id"], 707)
        self.assertEqual(stored[0]["window_id"], 12)

    def test_recording_keeps_the_windows_already_there(self):
        cgpt._record_window(707, 12)
        cgpt._record_window(808, 13)
        self.assertEqual([item["tab_id"] for item in self._windows()], [707, 808])


class CascadeIndexTest(ToolWindowTest):
    """The cascade step counts the tool's own live windows, not Edge's.

    Counting every Edge window walks a run's window off the screen as soon as
    the person has a few of their own open; counting records that are already
    closed does the same over a long day. Only live tool windows may shift it.
    """

    def test_only_recorded_windows_whose_tab_is_still_open_are_counted(self):
        cgpt._record_window(707, 12)
        cgpt._record_window(808, 13)
        self._bridge({"list": "1\t1\t707\thttps://chatgpt.com/c/one"})
        self.assertEqual(cgpt._live_window_count(), 1)

    def test_the_persons_own_windows_never_shift_the_cascade(self):
        self._bridge({"list": "1\t1\t900\thttps://example.com\n1\t2\t901\thttps://chatgpt.com/"})
        self.assertEqual(cgpt._live_window_count(), 0)

    def test_a_browser_that_cannot_be_asked_means_no_cascade(self):
        cgpt._record_window(707, 12)
        self._bridge({"list": cgpt.CliError("Edge is not running")})
        self.assertEqual(cgpt._live_window_count(), 0)


class OpenToolWindowTest(ToolWindowTest):
    def test_the_window_is_asked_for_small_and_cascaded(self):
        self._bridge({"list": "", "open_window": "707\t12", "loading": "done"})
        self.assertEqual(cgpt.open_tab("https://chatgpt.com/", 5), 707)
        opened = [call for call in self.calls if call[0] == "open_window"]
        self.assertEqual(
            opened,
            [("open_window", "https://chatgpt.com/", "480", "360", "0", "40")],
        )

    def test_the_second_window_of_a_run_is_cascaded_one_step(self):
        cgpt._record_window(707, 12)
        self._bridge({"list": "1\t1\t707\thttps://chatgpt.com/c/one",
                      "open_window": "808\t13", "loading": "done"})
        cgpt.open_tab("https://chatgpt.com/", 5)
        opened = [call for call in self.calls if call[0] == "open_window"][0]
        self.assertEqual(opened[4], "1")

    def test_the_window_is_claimed_and_recorded(self):
        events = []

        class Held:
            def claim_window(self, window_id):
                events.append(window_id)

        self._bridge({"list": "", "open_window": "707\t12", "loading": "done"})
        cgpt.open_tab("https://chatgpt.com/", 5, Held())
        self.assertEqual(events, [12])
        self.assertEqual(self._windows()[0]["window_id"], 12)

    def test_a_window_that_cannot_be_claimed_is_never_recorded(self):
        """W0: lose the race, leave nothing behind for a later run to close."""
        class Held:
            def claim_window(self, window_id):
                raise cgpt.claims.ClaimBusy("window 12 is already claimed")

        self._bridge({"list": "", "open_window": "707\t12", "loading": "done"})
        with self.assertRaises(cgpt.claims.ClaimBusy):
            cgpt.open_tab("https://chatgpt.com/", 5, Held())
        self.assertEqual(cgpt._load_windows(), [])

    def test_a_bridge_that_reports_no_window_records_nothing(self):
        self._bridge({"list": "", "open_window": "707", "loading": "done"})
        self.assertEqual(cgpt.open_tab("https://chatgpt.com/", 5), 707)
        self.assertEqual(cgpt._load_windows(), [])


class CleanupWindowTest(ToolWindowTest):
    """W4: close only a window the tool still proves is its own, single tab.

    Everything here is one half of a pair with the AppleScript `close_window`,
    which re-checks the window holds exactly that one tab on that conversation
    at the instant of closing. Python decides *whether to ask*; AppleScript
    decides whether it is still true.
    """

    def test_a_closed_window_is_forgotten(self):
        cgpt._record_window(707, 12)
        self._bridge({"close_window": "ok"})
        cgpt.cleanup_window(707, "https://chatgpt.com/c/abc123")
        self.assertEqual(self.calls, [("close_window", 12, 707, "abc123")])
        self.assertEqual(cgpt._load_windows(), [])

    def test_a_refused_close_keeps_the_record_so_resume_can_reclaim_it(self):
        cgpt._record_window(707, 12)
        self._bridge({"close_window": "no"})
        cgpt.cleanup_window(707, "https://chatgpt.com/c/abc123")
        self.assertEqual([item["tab_id"] for item in self._windows()], [707])

    def test_without_a_conversation_id_nothing_is_closed(self):
        cgpt._record_window(707, 12)
        self._bridge({})
        cgpt.cleanup_window(707, "")
        self.assertEqual(self.calls, [])
        self.assertEqual([item["tab_id"] for item in self._windows()], [707])

    def test_a_tab_the_tool_never_opened_is_left_alone(self):
        self._bridge({})
        cgpt.cleanup_window(707, "https://chatgpt.com/c/abc123")
        self.assertEqual(self.calls, [])

    def test_another_runs_window_is_neither_closed_nor_forgotten(self):
        cgpt._record_window(707, 12)
        cgpt._record_window(808, 13)
        self._bridge({"close_window": "ok"})
        cgpt.cleanup_window(707, "https://chatgpt.com/c/abc123")
        self.assertEqual([call[1] for call in self.calls], [12])
        self.assertEqual([item["tab_id"] for item in self._windows()], [808])


class WindowRegistryConcurrencyTest(unittest.TestCase):
    """Parallel runs each record a window. All of them must survive.

    Read-modify-write on one shared file loses every update but the last, and
    a lost record is a window nobody will ever close - it stays on the person's
    screen after the run that opened it is gone.
    """

    def test_parallel_writers_all_survive(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        env = os.environ.copy()
        env["HOME"] = home.name

        writer = (
            "import importlib.util, os, sys\n"
            "spec = importlib.util.spec_from_file_location('cgpt',"
            " os.path.join(sys.argv[1], 'chatgpt-cli.py'))\n"
            "cgpt = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(cgpt)\n"
            "cgpt._record_window(int(sys.argv[2]), int(sys.argv[2]) + 1000)\n"
        )
        writers = [
            subprocess.Popen([sys.executable, "-c", writer, _HERE, str(700 + i)],
                             env=env, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for i in range(12)
        ]
        for proc in writers:
            _, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)

        with open(os.path.join(home.name, ".chatgpt-agent", "windows.json")) as handle:
            stored = json.load(handle)
        self.assertEqual(sorted(item["tab_id"] for item in stored),
                         [700 + i for i in range(12)])
