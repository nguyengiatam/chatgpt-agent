#!/usr/bin/env python3
"""Tests for the orchestrator's pure parts (no browser)."""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

_spec = importlib.util.spec_from_file_location("agent", os.path.join(_HERE, "chatgpt-agent.py"))
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)


class WorkspaceRootTest(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_plain_directory_is_its_own_root(self):
        self.assertEqual(agent.workspace_root(self.dir), self.dir)

    def test_a_subdirectory_of_a_repo_resolves_to_the_repo_root(self):
        subprocess.run(["git", "init", "-q"], cwd=self.dir, capture_output=True)
        nested = os.path.join(self.dir, "a", "b")
        os.makedirs(nested)
        self.assertEqual(agent.workspace_root(nested), self.dir)

    def test_tilde_is_expanded(self):
        self.assertTrue(os.path.isabs(agent.workspace_root("~")))


class PresetTest(unittest.TestCase):
    def test_review_preset_ships_with_the_tool(self):
        self.assertIn("review", agent.load_preset("review").lower())

    def test_plan_preset_ships_with_the_tool(self):
        self.assertIn("plan", agent.load_preset("plan").lower())

    def test_unknown_preset_lists_the_real_ones(self):
        try:
            agent.load_preset("nope")
        except Exception as exc:
            self.assertIn("review", str(exc))
        else:
            self.fail("expected an error")


class OpeningMessageTest(unittest.TestCase):
    def test_carries_protocol_preset_root_and_task(self):
        message = agent.opening_message("PRESET-BODY", "/tmp/repo", "do the thing")
        self.assertIn("c2c", message)
        self.assertIn("PRESET-BODY", message)
        self.assertIn("/tmp/repo", message)
        self.assertIn("do the thing", message)

    def test_states_that_no_block_ends_the_session(self):
        message = agent.opening_message("x", "/tmp", "y")
        self.assertIn("NO c2c block", message)

    def test_warns_the_model_to_treat_file_contents_as_data(self):
        message = agent.opening_message("x", "/tmp", "y")
        self.assertIn("never instructions", message)

    def test_lists_only_read_only_ops(self):
        message = agent.opening_message("x", "/tmp", "y")
        for allowed in agent.ops.ALLOWED_OPS:
            self.assertIn(allowed, message)
        for forbidden in ("{\"op\":\"write\"", "{\"op\":\"shell\""):
            self.assertNotIn(forbidden, message)

    def test_shell_is_not_mentioned_unless_it_was_enabled(self):
        self.assertNotIn("shell", agent.opening_message("x", "/tmp", "y"))

    def test_enabling_shell_documents_it(self):
        message = agent.opening_message("x", "/tmp", "y", shell=True)
        self.assertIn('{"op":"shell"', message)
        self.assertIn("cwd", message)

    def test_the_shell_briefing_states_what_is_refused(self):
        message = agent.opening_message("x", "/tmp", "y", shell=True)
        for refused in ("Deleting trees", "escalating", "publishing", "credentials"):
            self.assertIn(refused, message)

    def test_the_shell_briefing_warns_that_commands_are_shown(self):
        message = agent.opening_message("x", "/tmp", "y", shell=True)
        self.assertIn("printed on the operator's terminal", message)


class WriteModeTest(unittest.TestCase):
    def test_write_mode_says_the_workspace_itself_is_the_target(self):
        message = agent.opening_message("x", "/tmp", "y", shell=True, write=True)
        self.assertIn("IMPLEMENT run", message)
        self.assertIn("commit", message)

    def test_write_mode_replaces_the_review_shell_briefing(self):
        message = agent.opening_message("x", "/tmp", "y", shell=True, write=True)
        # The review briefing tells the model to stay out of the workspace;
        # saying that in an implement run would contradict the task.
        self.assertNotIn("Work in the copy, not in the workspace itself", message)

    def test_write_mode_still_keeps_the_break_in_a_copy(self):
        message = agent.opening_message("x", "/tmp", "y", shell=True, write=True)
        self.assertIn("/tmp", message)
        self.assertIn("mutation", message)

    def test_write_mode_refuses_to_publish(self):
        message = agent.opening_message("x", "/tmp", "y", shell=True, write=True)
        for refused in ("pushing", "merging", "rewriting history"):
            self.assertIn(refused, message)

    def test_the_parser_leaves_the_preset_unset_so_write_can_choose_it(self):
        parsed = agent.build_parser().parse_args(["--write", "do it"])
        self.assertIsNone(parsed.preset)
        self.assertTrue(parsed.write)
        plain = agent.build_parser().parse_args(["do it"])
        self.assertIsNone(plain.preset)
        self.assertFalse(plain.write)

    def test_the_data_budget_is_unset_until_the_mode_is_known(self):
        # A review budget spent inside four rounds is how an implement run died
        # before it reached an edit, so --write has to be able to raise it.
        parsed = agent.build_parser().parse_args(["--write", "x"])
        self.assertIsNone(parsed.max_chars)
        self.assertIsNone(parsed.round_chars)
        self.assertGreater(agent.WRITE_RUN_CHARS, agent.RUN_CHARS)
        self.assertGreater(agent.WRITE_ROUND_CHARS, agent.ROUND_CHARS)

    def test_an_exhausted_write_run_is_told_to_describe_the_worktree(self):
        # "Conclude with what you have" is review language; an implement run
        # that concludes without saying what it left behind loses the work.
        self.assertIn("worktree", agent.DATA_SPENT_WRITE)
        self.assertIn("Do not claim the task is done", agent.DATA_SPENT_WRITE)

    def test_write_mode_gets_more_rounds_than_a_review(self):
        # A review converges in three to eight exchanges. An implement run that
        # reads, edits, builds, tests, mutates and commits needs each of those
        # as at least one exchange; a real task ran out at 24.
        parsed = agent.build_parser().parse_args(["--write", "x"])
        self.assertIsNone(parsed.max_rounds)
        self.assertGreater(agent.WRITE_MAX_ROUNDS, 8)

    def test_an_exhausted_write_run_is_given_a_way_to_commit(self):
        # BUDGET_SPENT forbids a c2c block, and committing needs one. Applied to
        # an implement run that is exactly the instruction that loses the work.
        self.assertIn("Do not emit a c2c block", agent.BUDGET_SPENT)
        self.assertIn("c2c block", agent.LANDING_ROUND)
        self.assertIn("commit", agent.LANDING_ROUND)
        self.assertNotIn("Do not emit a c2c block", agent.LANDING_ROUND)

    def test_the_landing_round_forbids_new_work(self):
        for banned in ("no new edits", "no further"):
            self.assertIn(banned, agent.LANDING_ROUND)

    def test_an_implement_preset_ships_with_the_plugin(self):
        body = agent.load_preset("implement")
        self.assertIn("commit", body.lower())


class WorkspaceFactsTest(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_bare_directory_yields_no_invented_facts(self):
        self.assertEqual(agent.workspace_facts(self.dir), [])

    def test_it_reports_the_branch_and_head_of_a_repo(self):
        subprocess.run(["git", "init", "-q"], cwd=self.dir, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=self.dir, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=self.dir, check=True)
        open(os.path.join(self.dir, "a.txt"), "w").write("hi\n")
        subprocess.run(["git", "add", "-A"], cwd=self.dir, check=True)
        subprocess.run(["git", "commit", "-qm", "one"], cwd=self.dir, check=True)
        facts = " | ".join(agent.workspace_facts(self.dir))
        self.assertIn("on branch", facts)
        self.assertIn("worktree clean", facts)

    def test_it_says_when_node_dependencies_are_missing(self):
        open(os.path.join(self.dir, "package.json"), "w").write(
            json.dumps({"scripts": {"test": "jest", "build": "tsc"}}))
        facts = " | ".join(agent.workspace_facts(self.dir))
        self.assertIn("NOT installed", facts)
        self.assertIn("test", facts)

    def test_it_says_where_node_dependencies_live_when_present(self):
        open(os.path.join(self.dir, "package.json"), "w").write("{}")
        os.makedirs(os.path.join(self.dir, "node_modules"))
        facts = " | ".join(agent.workspace_facts(self.dir))
        self.assertIn("node_modules", facts)
        self.assertNotIn("NOT installed", facts)

    def test_facts_reach_the_opening_message(self):
        message = agent.opening_message("x", "/tmp/repo", "y", facts=["on branch main at abc123"])
        self.assertIn("on branch main at abc123", message)

    def test_facts_do_not_leak_the_command_op_into_a_read_only_run(self):
        message = agent.opening_message("x", "/tmp/repo", "y", facts=["Go module"])
        self.assertNotIn("shell", message)


class SessionStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._saved = (agent.STATE_DIR, agent.SESSIONS)
        agent.STATE_DIR = self.dir
        agent.SESSIONS = os.path.join(self.dir, "sessions.json")

    def tearDown(self):
        agent.STATE_DIR, agent.SESSIONS = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write_raw(self, raw):
        with open(agent.SESSIONS, "w") as handle:
            json.dump(raw, handle)

    def test_missing_store_reads_as_empty(self):
        self.assertEqual(agent.load_sessions(), {})

    def test_a_saved_session_round_trips(self):
        agent.save_session("app", "https://chatgpt.com/c/abc")
        self.assertEqual(agent.session_url("app"), "https://chatgpt.com/c/abc")

    def test_a_saved_session_keeps_its_tab_binding(self):
        self._write_raw({"app": {"url": "https://chatgpt.com/c/abc",
                                 "tab_id": 707,
                                 "updated": "2026-09-17T00:00:00Z"}})
        self.assertEqual(agent.load_sessions()["app"]["tab_id"], 707)

    def test_saving_keeps_other_sessions(self):
        agent.save_session("one", "https://chatgpt.com/c/1")
        agent.save_session("two", "https://chatgpt.com/c/2")
        self.assertEqual(len(agent.load_sessions()), 2)

    def test_a_corrupt_store_does_not_crash_the_run(self):
        with open(agent.SESSIONS, "w") as handle:
            handle.write("{ not json")
        self.assertEqual(agent.load_sessions(), {})

    def test_an_absent_session_has_no_url(self):
        self.assertIsNone(agent.session_url("never-saved"))

    def test_saving_stamps_the_time_so_the_bookmark_can_age(self):
        agent.save_session("app", "https://chatgpt.com/c/abc")
        self.assertIsNotNone(agent.load_sessions()["app"]["updated"])

    def test_a_legacy_url_string_still_resolves(self):
        self._write_raw({"old": "https://chatgpt.com/c/old"})
        self.assertEqual(agent.session_url("old"), "https://chatgpt.com/c/old")

    def test_a_legacy_entry_is_dated_from_the_file_so_it_can_age_out(self):
        self._write_raw({"old": "https://chatgpt.com/c/old"})
        old = time.time() - 30 * 86400
        os.utime(agent.SESSIONS, (old, old))
        stamp = agent.load_sessions()["old"]["updated"]
        self.assertGreater(agent._age_days(agent._stamp_seconds(stamp)), 29)

    def test_reusing_a_session_refreshes_its_stamp(self):
        self._write_raw({"app": {"url": "https://chatgpt.com/c/abc",
                                 "updated": "2020-01-01T00:00:00Z"}})
        agent.touch_session("app")
        stamp = agent.load_sessions()["app"]["updated"]
        self.assertLess(agent._age_days(agent._stamp_seconds(stamp)), 1)

    def test_touching_an_absent_session_saves_nothing(self):
        agent.touch_session("never-saved")
        self.assertEqual(agent.load_sessions(), {})

    def test_an_entry_that_is_not_a_bookmark_is_dropped(self):
        self._write_raw({"junk": 5, "app": "https://chatgpt.com/c/abc"})
        self.assertEqual(sorted(agent.load_sessions()), ["app"])


class RunStateTest(unittest.TestCase):
    """A checkpoint is what turns a crash at round 5 into a resume, not a loss."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._saved = agent.RUNS_DIR
        agent.RUNS_DIR = self.dir

    def tearDown(self):
        agent.RUNS_DIR = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_nothing_saved_reads_as_none(self):
        self.assertIsNone(agent.load_run("absent"))

    def test_a_checkpoint_round_trips(self):
        agent.save_run("app", {"round": 5, "marker": "[c2c:abcd1234]", "spent": 900})
        state = agent.load_run("app")
        self.assertEqual(state["round"], 5)
        self.assertEqual(state["marker"], "[c2c:abcd1234]")
        self.assertEqual(state["spent"], 900)

    def test_clearing_removes_it(self):
        agent.save_run("app", {"round": 1})
        agent.clear_run("app")
        self.assertIsNone(agent.load_run("app"))

    def test_clearing_an_absent_run_is_not_an_error(self):
        agent.clear_run("never-existed")

    def test_runs_are_kept_apart_by_name(self):
        agent.save_run("one", {"round": 1})
        agent.save_run("two", {"round": 7})
        self.assertEqual(agent.load_run("two")["round"], 7)

    def test_a_corrupt_checkpoint_reads_as_none(self):
        os.makedirs(agent.RUNS_DIR, exist_ok=True)
        with open(os.path.join(agent.RUNS_DIR, "bad.json"), "w") as handle:
            handle.write("{ truncated")
        self.assertIsNone(agent.load_run("bad"))


class PruneTest(unittest.TestCase):
    """Neither store used to drop anything, so both grew for as long as the tool
    was used. Pruning is what bounds them - without deleting a checkpoint that a
    run is still depending on."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._saved = (agent.STATE_DIR, agent.SESSIONS, agent.RUNS_DIR)
        agent.STATE_DIR = self.dir
        agent.SESSIONS = os.path.join(self.dir, "sessions.json")
        agent.RUNS_DIR = os.path.join(self.dir, "runs")

    def tearDown(self):
        agent.STATE_DIR, agent.SESSIONS, agent.RUNS_DIR = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run_aged(self, name, days):
        agent.save_run(name, {"round": 1})
        when = time.time() - days * 86400
        os.utime(agent._run_path(name), (when, when))

    def _session_aged(self, name, days):
        sessions = agent.load_sessions()
        sessions[name] = {
            "url": "https://chatgpt.com/c/" + name,
            "updated": agent._stamp_from_epoch(time.time() - days * 86400),
        }
        agent.write_sessions(sessions)

    def _names(self, victims, kind):
        return sorted(name for a_kind, name, _ in victims if a_kind == kind)

    def test_a_checkpoint_past_the_ttl_is_listed(self):
        self._run_aged("dead", 30)
        self.assertEqual(self._names(agent.prune_plan(days=14), "run"), ["dead"])

    def test_a_fresh_checkpoint_is_left_alone(self):
        self._run_aged("today", 0)
        self.assertEqual(agent.prune_plan(days=14), [])

    def test_the_run_in_flight_is_never_listed(self):
        self._run_aged("app", 30)
        self.assertEqual(agent.prune_plan(days=14, keep=("app",)), [])

    def test_a_session_past_the_ttl_is_listed(self):
        self._session_aged("old", 30)
        self.assertEqual(self._names(agent.prune_plan(days=14), "session"), ["old"])

    def test_a_fresh_session_is_left_alone(self):
        self._session_aged("today", 1)
        self.assertEqual(agent.prune_plan(days=14), [])

    def test_the_session_in_flight_is_never_listed(self):
        self._session_aged("app", 30)
        self.assertEqual(agent.prune_plan(days=14, keep=("app",)), [])

    def test_planning_deletes_nothing(self):
        self._run_aged("dead", 30)
        self._session_aged("old", 30)
        agent.prune_plan(days=14)
        self.assertIsNotNone(agent.load_run("dead"))
        self.assertIsNotNone(agent.session_url("old"))

    def test_applying_removes_both_kinds(self):
        self._run_aged("dead", 30)
        self._session_aged("old", 30)
        agent.prune_apply(agent.prune_plan(days=14))
        self.assertIsNone(agent.load_run("dead"))
        self.assertIsNone(agent.session_url("old"))

    def test_applying_keeps_what_it_was_not_given(self):
        self._run_aged("dead", 30)
        self._run_aged("today", 0)
        self._session_aged("old", 30)
        self._session_aged("recent", 1)
        agent.prune_apply(agent.prune_plan(days=14))
        self.assertIsNotNone(agent.load_run("today"))
        self.assertEqual(sorted(agent.load_sessions()), ["recent"])

    def test_no_age_limit_takes_everything_not_kept(self):
        self._run_aged("today", 0)
        self._session_aged("today", 0)
        victims = agent.prune_plan(days=None)
        self.assertEqual(self._names(victims, "run"), ["today"])
        self.assertEqual(self._names(victims, "session"), ["today"])

    def test_an_unreadable_stamp_survives_a_ttl_prune(self):
        agent.write_sessions({"weird": {"url": "https://chatgpt.com/c/w",
                                        "updated": "whenever"}})
        self.assertEqual(agent.prune_plan(days=14), [])
        self.assertEqual(self._names(agent.prune_plan(days=None), "session"), ["weird"])

    def test_a_stray_file_in_the_runs_directory_is_ignored(self):
        os.makedirs(agent.RUNS_DIR, exist_ok=True)
        with open(os.path.join(agent.RUNS_DIR, "notes.txt"), "w") as handle:
            handle.write("x")
        self.assertEqual(agent.prune_plan(days=None), [])

    def test_an_empty_state_directory_prunes_nothing(self):
        self.assertEqual(agent.prune_plan(days=14), [])


class PruneCommandTest(unittest.TestCase):
    """--prune lists by default; deleting takes --yes. A cleanup that removes
    things before the operator has seen the list is how a resume gets lost."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._saved = (agent.STATE_DIR, agent.SESSIONS, agent.RUNS_DIR, agent.claims.CLAIM_DIR)
        agent.STATE_DIR = self.dir
        agent.SESSIONS = os.path.join(self.dir, "sessions.json")
        agent.RUNS_DIR = os.path.join(self.dir, "runs")
        agent.claims.CLAIM_DIR = os.path.join(self.dir, "claims")
        agent.save_run("dead", {"round": 1})
        when = time.time() - 30 * 86400
        os.utime(agent._run_path("dead"), (when, when))

    def tearDown(self):
        agent.STATE_DIR, agent.SESSIONS, agent.RUNS_DIR, agent.claims.CLAIM_DIR = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def _main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = agent.main(list(argv))
        return code, out.getvalue()

    def test_listing_names_the_checkpoint_and_removes_nothing(self):
        code, out = self._main("--prune")
        self.assertEqual(code, 0)
        self.assertIn("dead", out)
        self.assertIsNotNone(agent.load_run("dead"))

    def test_listing_says_how_to_go_through_with_it(self):
        _, out = self._main("--prune")
        self.assertIn("--yes", out)

    def test_yes_removes_it(self):
        code, out = self._main("--prune", "--yes")
        self.assertEqual(code, 0)
        self.assertIsNone(agent.load_run("dead"))

    def test_a_shorter_ttl_can_be_asked_for(self):
        agent.save_run("today", {"round": 1})
        _, out = self._main("--prune", "--days", "0")
        self.assertIn("today", out)

    def test_nothing_to_prune_says_so(self):
        agent.clear_run("dead")
        _, out = self._main("--prune")
        self.assertIn("nothing to prune", out)

    def test_pruning_needs_no_task(self):
        code, _ = self._main("--prune")
        self.assertEqual(code, 0)


class AttemptTest(unittest.TestCase):
    """Send and wait are retried separately; see the docstring on attempt()."""

    def setUp(self):
        self._pause = agent.RETRY_PAUSE
        agent.RETRY_PAUSE = 0
        self.logged = []

    def tearDown(self):
        agent.RETRY_PAUSE = self._pause

    def _log(self, line):
        self.logged.append(line)

    def test_a_working_action_runs_once(self):
        calls = []

        def action():
            calls.append(1)
            return "ok"

        self.assertEqual(agent.attempt("send", 3, self._log, action), "ok")
        self.assertEqual(len(calls), 1)

    def test_a_flaky_action_succeeds_on_a_later_try(self):
        calls = []

        def action():
            calls.append(1)
            if len(calls) < 3:
                raise agent.cgpt.CliError("transport wobble")
            return "ok"

        self.assertEqual(agent.attempt("wait", 3, self._log, action), "ok")
        self.assertEqual(len(calls), 3)

    def test_a_dead_action_raises_after_the_last_try(self):
        def action():
            raise agent.cgpt.CliError("still broken")

        with self.assertRaises(agent.cgpt.CliError):
            agent.attempt("send", 3, self._log, action)

    def test_exactly_one_attempt_means_no_retry(self):
        calls = []

        def action():
            calls.append(1)
            raise agent.cgpt.CliError("nope")

        with self.assertRaises(agent.cgpt.CliError):
            agent.attempt("send", 1, self._log, action)
        self.assertEqual(len(calls), 1)

    def test_each_retry_is_logged_so_a_slow_run_is_explainable(self):
        calls = []

        def action():
            calls.append(1)
            if len(calls) < 2:
                raise agent.cgpt.CliError("wobble")
            return "ok"

        agent.attempt("wait", 3, self._log, action)
        self.assertTrue(any("retrying" in line for line in self.logged))



class _FakeCgpt:
    """Just enough browser for the round loop: hand it the replies to give."""

    CliError = RuntimeError

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def ensure_tab(self, timeout):
        return 101

    def open_tab(self, url, timeout):
        return 101

    def bridge(self, *a):
        return "done" if a and a[0] == "loading" else ""

    def needs_attachment(self, message):
        return False

    def send(self, tab_id, message):
        self.sent.append(message)
        return "marker"

    def wait_for_reply(self, tab_id, marker, timeout, poll):
        if not self.replies:
            raise AssertionError("the loop asked for more replies than the test gave it")
        return self.replies.pop(0)

    def eval_js(self, tab_id, expr):
        if "probe" in expr:
            return {"ok": True}
        return "https://chatgpt.com/c/test"


class _FakeOps:
    def execute(self, root, op, limit, allow_shell, role):
        return {"label": str(op.get("op")), "body": "ok"}


class MalformedBlockTest(unittest.TestCase):
    """A bad c2c block must be re-asked, and must never end the run quietly."""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self._runs, agent.RUNS_DIR = agent.RUNS_DIR, os.path.join(self.dir, "runs")
        self._state, agent.STATE_DIR = agent.STATE_DIR, self.dir
        self._sessions, agent.SESSIONS = agent.SESSIONS, os.path.join(self.dir, "sessions.json")
        self._cgpt, self._ops, self._goto = agent.cgpt, agent.ops, agent.goto
        agent.ops = _FakeOps()
        agent.goto = lambda *a, **k: None

    def tearDown(self):
        agent.RUNS_DIR, agent.STATE_DIR, agent.SESSIONS = self._runs, self._state, self._sessions
        agent.cgpt, agent.ops, agent.goto = self._cgpt, self._ops, self._goto
        shutil.rmtree(self.dir, ignore_errors=True)

    def _args(self, **over):
        values = dict(
            workspace=self.dir, preset="review", task="t", session=None, new=True,
            resume=False, write=False, allow_shell=False, focus=False,
            max_rounds=10, max_chars=1000, round_chars=500, timeout=1, poll=0,
            retries=1,
        )
        values.update(over)
        return argparse.Namespace(**values)

    def _run(self, replies):
        agent.cgpt = _FakeCgpt(replies)
        lines = []
        answer = agent.run(self._args(), lines.append, {})
        return answer, lines, agent.cgpt.sent

    def test_a_bad_block_is_re_asked_instead_of_ending_the_run(self):
        answer, _, sent = self._run(['```c2c\n{"ops":[{"op":\n```', "GO - nothing found."])
        self.assertEqual(answer, "GO - nothing found.")
        self.assertIn("Send the c2c block again", sent[-1])

    def test_the_correction_never_offers_to_finish(self):
        # The old wording said "or omit it to finish" and the model took it.
        self._run(['```c2c\n{"ops":[{"op":\n```', "GO."])
        self.assertNotIn("omit it to finish", "\n".join(agent.cgpt.sent))

    def test_three_bad_blocks_in_a_row_stop_the_run(self):
        bad = '```c2c\n{"ops":[{"op":\n```'
        with self.assertRaises(Exception) as caught:
            self._run([bad, bad, bad])
        self.assertIn("3 times in a row", str(caught.exception))

    def test_two_bad_blocks_then_a_good_one_still_finishes(self):
        bad = '```c2c\n{"ops":[{"op":\n```'
        answer, _, _ = self._run([bad, bad, "NOT-GO: one finding."])
        self.assertEqual(answer, "NOT-GO: one finding.")

    def test_the_counter_resets_after_a_served_round(self):
        bad = '```c2c\n{"ops":[{"op":\n```'
        good = '```c2c\n{"ops":[{"op":"read","path":"x"}]}\n```'
        answer, _, _ = self._run([bad, good, bad, bad, "GO."])
        self.assertEqual(answer, "GO.")


class EmptyAnswerTest(unittest.TestCase):
    """An empty reply is not an answer - it must never be saved as the result."""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self._runs, agent.RUNS_DIR = agent.RUNS_DIR, os.path.join(self.dir, "runs")
        self._state, agent.STATE_DIR = agent.STATE_DIR, self.dir
        self._sessions, agent.SESSIONS = agent.SESSIONS, os.path.join(self.dir, "sessions.json")
        self._cgpt, self._ops, self._goto = agent.cgpt, agent.ops, agent.goto
        agent.ops = _FakeOps()
        agent.goto = lambda *a, **k: None

    def tearDown(self):
        agent.RUNS_DIR, agent.STATE_DIR, agent.SESSIONS = self._runs, self._state, self._sessions
        agent.cgpt, agent.ops, agent.goto = self._cgpt, self._ops, self._goto
        shutil.rmtree(self.dir, ignore_errors=True)

    def _args(self):
        return argparse.Namespace(
            workspace=self.dir, preset="review", task="t", session=None, new=True,
            resume=False, write=False, allow_shell=False, focus=False,
            max_rounds=10, max_chars=1000, round_chars=500, timeout=1, poll=0,
            retries=1,
        )

    def _run(self, replies):
        agent.cgpt = _FakeCgpt(replies)
        return agent.run(self._args(), lambda line: None, {})

    def test_an_empty_reply_is_asked_again_not_returned(self):
        answer = self._run(["```c\n\n```", "GO - nothing found."])
        self.assertEqual(answer, "GO - nothing found.")
        self.assertIn("empty", "\n".join(agent.cgpt.sent).lower())

    def test_three_empty_replies_stop_the_run(self):
        with self.assertRaises(Exception) as caught:
            self._run(["```c\n\n```", "   ", "```\n\n```"])
        self.assertIn("nothing", str(caught.exception).lower())

    def test_a_real_answer_is_still_returned_untouched(self):
        self.assertEqual(self._run(["GO - nothing found."]), "GO - nothing found.")

class HousekeepingTest(unittest.TestCase):
    """Pruning has to happen on the way past a real run - a cleanup nobody ever
    calls is the state of affairs this replaced."""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self._runs, agent.RUNS_DIR = agent.RUNS_DIR, os.path.join(self.dir, "runs")
        self._state, agent.STATE_DIR = agent.STATE_DIR, self.dir
        self._sessions, agent.SESSIONS = agent.SESSIONS, os.path.join(self.dir, "sessions.json")
        self._cgpt, self._ops, self._goto = agent.cgpt, agent.ops, agent.goto
        agent.ops = _FakeOps()
        agent.goto = lambda *a, **k: None
        agent.cgpt = _FakeCgpt(["GO - nothing found."])

    def tearDown(self):
        agent.RUNS_DIR, agent.STATE_DIR, agent.SESSIONS = self._runs, self._state, self._sessions
        agent.cgpt, agent.ops, agent.goto = self._cgpt, self._ops, self._goto
        shutil.rmtree(self.dir, ignore_errors=True)

    def _aged_run(self, name, days):
        agent.save_run(name, {"round": 1})
        when = time.time() - days * 86400
        os.utime(agent._run_path(name), (when, when))

    def _run(self):
        self.lines = []
        args = argparse.Namespace(
            workspace=self.dir, preset="review", task="t", session=None, new=True,
            resume=False, write=False, allow_shell=False, focus=False,
            max_rounds=10, max_chars=1000, round_chars=500, timeout=1, poll=0,
            retries=1,
        )
        return agent.run(args, self.lines.append, {})

    def test_a_run_drops_a_dead_checkpoint_on_its_way_past(self):
        self._aged_run("zombie", 30)
        self._run()
        self.assertIsNone(agent.load_run("zombie"))

    def test_it_says_what_it_pruned(self):
        self._aged_run("zombie", 30)
        self._run()
        self.assertIn("pruned", "\n".join(self.lines))

    def test_a_fresh_checkpoint_survives_a_run(self):
        self._aged_run("yesterday", 1)
        self._run()
        self.assertIsNotNone(agent.load_run("yesterday"))

    def test_a_stale_bookmark_is_dropped_too(self):
        agent.write_sessions({"old": {"url": "https://chatgpt.com/c/old",
                                      "updated": agent._stamp_from_epoch(
                                          time.time() - 30 * 86400)}})
        self._run()
        self.assertIsNone(agent.session_url("old"))

    def test_a_clean_store_is_pruned_silently(self):
        self._run()
        self.assertNotIn("pruned", "\n".join(self.lines))


if __name__ == "__main__":
    unittest.main()


class ClaimSpansTheRunTest(unittest.TestCase):
    """The tab claim must cover the whole run, not each browser call.

    A claim taken and dropped around individual calls passes any contention
    test that happens to collide during one of them, and still leaves the tab
    free between rounds - which is when a rival run takes it and starts writing
    into the same conversation.
    """

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self._runs, agent.RUNS_DIR = agent.RUNS_DIR, os.path.join(self.dir, "runs")
        self._state, agent.STATE_DIR = agent.STATE_DIR, self.dir
        self._sessions, agent.SESSIONS = agent.SESSIONS, os.path.join(self.dir, "sessions.json")
        self._lock, agent.SESSIONS_LOCK = agent.SESSIONS_LOCK, os.path.join(self.dir, "sessions.lock")
        self._claims, agent.claims.CLAIM_DIR = agent.claims.CLAIM_DIR, os.path.join(self.dir, "claims")
        self._cgpt, self._ops, self._goto = agent.cgpt, agent.ops, agent.goto
        agent.ops = _FakeOps()
        agent.goto = lambda *a, **k: None

    def tearDown(self):
        agent.RUNS_DIR, agent.STATE_DIR, agent.SESSIONS = self._runs, self._state, self._sessions
        agent.SESSIONS_LOCK = self._lock
        agent.claims.CLAIM_DIR = self._claims
        agent.cgpt, agent.ops, agent.goto = self._cgpt, self._ops, self._goto
        shutil.rmtree(self.dir, ignore_errors=True)

    def _rival_verdict(self, tab):
        """What a separate process gets when it reaches for the same tab."""
        code = (
            "import sys\n"
            "import chatgpt_claims as claims\n"
            "try:\n"
            "    claims.ClaimSet('rival').claim_tab(int(sys.argv[1]))\n"
            "except claims.ClaimBusy as exc:\n"
            "    print(str(exc)); raise SystemExit(23)\n"
            "print('acquired')\n"
        )
        env = os.environ.copy()
        env["CHATGPT_AGENT_CLAIM_DIR"] = agent.claims.CLAIM_DIR
        proc = subprocess.Popen([sys.executable, "-c", code, str(tab)], cwd=_HERE,
                                env=env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        out, err = proc.communicate(timeout=30)
        return proc.returncode, out, err

    def test_a_rival_is_refused_while_the_owner_waits_between_rounds(self):
        verdicts = []
        outer = self

        class Cgpt(_FakeCgpt):
            def wait_for_reply(self, tab_id, marker, timeout, poll):
                # A run spends most of its life in here, and the gap between
                # two rounds sits inside it. A per-call claim leaves the tab
                # free at exactly this moment.
                verdicts.append(outer._rival_verdict(101))
                return _FakeCgpt.wait_for_reply(self, tab_id, marker, timeout, poll)

        agent.cgpt = Cgpt(["nothing to report."])
        args = argparse.Namespace(
            workspace=self.dir, preset="review", task="t", session=None, new=True,
            resume=False, write=False, allow_shell=False, focus=False,
            max_rounds=4, max_chars=1000, round_chars=500, timeout=1, poll=0,
            retries=1,
        )
        agent.run(args, lambda line: None, {})

        self.assertTrue(verdicts, "the loop never waited for a reply")
        code, out, err = verdicts[0]
        self.assertEqual(code, 23, "a rival took the tab between rounds: " + out + err)
        self.assertIn("101", out)


class PageErrorReplyTest(unittest.TestCase):
    """The page's own failure notice must not be reported as the run's result."""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self._runs, agent.RUNS_DIR = agent.RUNS_DIR, os.path.join(self.dir, "runs")
        self._state, agent.STATE_DIR = agent.STATE_DIR, self.dir
        self._sessions, agent.SESSIONS = agent.SESSIONS, os.path.join(self.dir, "sessions.json")
        self._lock, agent.SESSIONS_LOCK = agent.SESSIONS_LOCK, os.path.join(self.dir, "sessions.lock")
        self._claims, agent.claims.CLAIM_DIR = agent.claims.CLAIM_DIR, os.path.join(self.dir, "claims")
        self._cgpt, self._ops, self._goto = agent.cgpt, agent.ops, agent.goto
        agent.ops = _FakeOps()
        agent.goto = lambda *a, **k: None

    def tearDown(self):
        agent.RUNS_DIR, agent.STATE_DIR, agent.SESSIONS = self._runs, self._state, self._sessions
        agent.SESSIONS_LOCK, agent.claims.CLAIM_DIR = self._lock, self._claims
        agent.cgpt, agent.ops, agent.goto = self._cgpt, self._ops, self._goto
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, replies):
        agent.cgpt = _FakeCgpt(replies)
        args = argparse.Namespace(
            workspace=self.dir, preset="review", task="t", session=None, new=True,
            resume=False, write=False, allow_shell=False, focus=False,
            max_rounds=10, max_chars=1000, round_chars=500, timeout=1, poll=0,
            retries=1,
        )
        return agent.run(args, lambda line: None, {})

    def test_a_page_error_is_asked_again_not_returned(self):
        answer = self._run(["Đã hết thời gian chờ gửi tin nhắn. Vui lòng thử lại.",
                            "GO - nothing found."])
        self.assertEqual(answer, "GO - nothing found.")

    def test_three_page_errors_in_a_row_fail_the_run(self):
        with self.assertRaises(Exception) as caught:
            self._run(["Something went wrong.", "Network error",
                       "There was an error generating a response."])
        self.assertIn("page reported a failure", str(caught.exception))

class SessionBindingSelectionTest(unittest.TestCase):
    def setUp(self):
        self._cgpt = agent.cgpt

    def tearDown(self):
        agent.cgpt = self._cgpt

    def _browser(self, rows, opened=909):
        class Browser:
            def __init__(self):
                self.opened = []
            def bridge(self, *args):
                if args[0] == "list":
                    return rows
                raise AssertionError(args)
            def parse_tabs(self, raw):
                return self._cgpt.parse_tabs(raw)
            def open_tab(self, url, timeout):
                self.opened.append((url, timeout))
                return opened
        browser = Browser()
        browser._cgpt = self._cgpt
        return browser

    def test_binding_driven_to_another_conversation_is_reopened(self):
        browser = self._browser("1\t1\t707\thttps://chatgpt.com/c/other")
        agent.cgpt = browser
        entry = {"url": "https://chatgpt.com/c/wanted", "tab_id": 707}
        self.assertEqual(agent.resolve_session_tab(entry, 12), 909)
        self.assertEqual(browser.opened, [("https://chatgpt.com/c/wanted", 12)])

    def test_closed_binding_is_reopened_at_saved_conversation(self):
        browser = self._browser("1\t1\t808\thttps://chatgpt.com/c/unrelated", opened=910)
        agent.cgpt = browser
        entry = {"url": "https://chatgpt.com/c/wanted", "tab_id": 707}
        self.assertEqual(agent.resolve_session_tab(entry, 12), 910)
        self.assertEqual(browser.opened[0][0], "https://chatgpt.com/c/wanted")

    def test_legacy_session_without_tab_binding_reopens_its_conversation(self):
        browser = self._browser("", opened=911)
        agent.cgpt = browser
        self.assertEqual(agent.resolve_session_tab({"url": "https://chatgpt.com/c/legacy"}, 12), 911)
        self.assertEqual(browser.opened[0][0], "https://chatgpt.com/c/legacy")


class TabGoneRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.saved = (agent.STATE_DIR, agent.SESSIONS, agent.SESSIONS_LOCK, agent.RUNS_DIR,
                      agent.cgpt, agent.goto, agent.claims.ClaimSet)
        agent.STATE_DIR = self.dir
        agent.SESSIONS = os.path.join(self.dir, "sessions.json")
        agent.SESSIONS_LOCK = os.path.join(self.dir, "sessions.lock")
        agent.RUNS_DIR = os.path.join(self.dir, "runs")
        agent.goto = lambda *a, **k: None
        self.events = []

        outer = self
        class RecordingClaims:
            def __init__(self, owner):
                outer.events.append(("new", owner))
            def claim_tab(self, tab_id):
                outer.events.append(("claim_tab", tab_id))
            def release_tab(self, tab_id):
                outer.events.append(("release_tab", tab_id))
            def claim_conversation(self, identity):
                outer.events.append(("claim_conversation", identity))
            def close(self):
                pass
        agent.claims.ClaimSet = RecordingClaims

    def tearDown(self):
        (agent.STATE_DIR, agent.SESSIONS, agent.SESSIONS_LOCK, agent.RUNS_DIR,
         agent.cgpt, agent.goto, agent.claims.ClaimSet) = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def _args(self):
        return argparse.Namespace(workspace=self.dir, preset="review", task="t", session="app",
                                  new=False, resume=False, write=False, allow_shell=False,
                                  focus=False, max_rounds=2, max_chars=1000, round_chars=500,
                                  timeout=1, poll=0, retries=1)

    def _install_browser(self, vanish_twice=False):
        outer = self
        class Browser:
            class CliError(RuntimeError): pass
            class TabGone(CliError): pass
            def __init__(self):
                self.sent = []
                self.wait_tabs = []
                self.opened = []
            def bridge(self, *args):
                if args[0] == "list":
                    return "1\t1\t101\thttps://chatgpt.com/c/original"
                if args[0] == "focus":
                    return "ok"
                raise AssertionError(args)
            def parse_tabs(self, raw):
                return [(1, 1, 101, "https://chatgpt.com/c/original")]
            def open_tab(self, url, timeout):
                self.opened.append(url)
                return 202
            def needs_attachment(self, message): return False
            def send(self, tab_id, message):
                self.sent.append((tab_id, message))
                return "marker-1"
            def wait_for_reply(self, tab_id, marker, timeout, poll):
                self.wait_tabs.append(tab_id)
                if tab_id == 101:
                    raise self.TabGone("gone")
                if vanish_twice:
                    raise self.TabGone("gone again")
                return "done"
            def eval_js(self, tab_id, expr):
                return "https://chatgpt.com/c/original"
            def ensure_tab(self, timeout):
                return 101
            def find_chatgpt_tab(self, tabs):
                return 101
        agent.cgpt = Browser()
        agent.write_sessions({"app": {"url": "https://chatgpt.com/c/original", "tab_id": 101,
                                      "updated": agent._now_stamp()}})
        return agent.cgpt

    def test_tab_gone_recovers_once_without_resending_and_rebinds_claim(self):
        browser = self._install_browser()
        answer = agent.run(self._args(), lambda line: None, {})
        self.assertEqual(answer, "done")
        self.assertEqual(len(browser.sent), 1)
        self.assertEqual(browser.wait_tabs, [101, 202])
        self.assertIn(("release_tab", 101), self.events)
        self.assertIn(("claim_tab", 202), self.events)
        self.assertEqual(agent.load_sessions()["app"]["tab_id"], 202)

    def test_second_tab_gone_stops_and_names_conversation(self):
        self._install_browser(vanish_twice=True)
        with self.assertRaises(Exception) as caught:
            agent.run(self._args(), lambda line: None, {})
        self.assertIn("https://chatgpt.com/c/original", str(caught.exception))

class WaitRecoveryHelperTest(unittest.TestCase):
    def setUp(self):
        self.saved = (agent.cgpt, agent.goto)
        agent.goto = lambda *a, **k: None

    def tearDown(self):
        agent.cgpt, agent.goto = self.saved

    def test_helper_recovers_once_and_second_disappearance_names_url(self):
        class Browser:
            class CliError(RuntimeError): pass
            class TabGone(CliError): pass
            def __init__(self):
                self.waits = []
            def open_tab(self, url, timeout): return 22
            def wait_for_reply(self, tab_id, marker, timeout, poll):
                self.waits.append(tab_id)
                raise self.TabGone("gone")
            def bridge(self, *args): return "ok"
        class Held:
            def __init__(self): self.events=[]
            def release_tab(self, tab_id): self.events.append(("release", tab_id))
            def claim_tab(self, tab_id): self.events.append(("claim", tab_id))
        agent.cgpt = Browser()
        args = argparse.Namespace(timeout=1, poll=0, retries=1, focus=False, session=None)
        recovery = {"used": False}
        held = Held()
        with self.assertRaises(agent.cgpt.CliError) as caught:
            agent.wait_with_tab_recovery(11, "m", "https://chatgpt.com/c/abc",
                                         held, args, recovery, lambda line: None)
        self.assertIn("https://chatgpt.com/c/abc", str(caught.exception))
        self.assertEqual(agent.cgpt.waits, [11, 22])
        self.assertEqual(held.events, [("release", 11), ("claim", 22)])
        self.assertTrue(recovery["used"])


class ConversationIdSettlesLateTest(unittest.TestCase):
    """The first URL a new chat shows is not the one it keeps.

    Measured against the real page on 2026-09-17: the first read gave
    /c/WEB:849fd09c-5574-4c1a-95b7-4068782180f1 and the same tab was on
    /c/6aabe307-d930-83ec-bf38-9fa2bbfa51f6 shortly after. Binding to the first
    one left a session that could never reopen its conversation, and put the
    conversation claim on an id nothing else would ever ask for - a claim that
    protected nothing.
    """

    PLACEHOLDER = "https://chatgpt.com/c/WEB:849fd09c-5574-4c1a-95b7-4068782180f1"
    SETTLED = "https://chatgpt.com/c/6aabe307-d930-83ec-bf38-9fa2bbfa51f6"

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self._runs, agent.RUNS_DIR = agent.RUNS_DIR, os.path.join(self.dir, "runs")
        self._state, agent.STATE_DIR = agent.STATE_DIR, self.dir
        self._sessions, agent.SESSIONS = agent.SESSIONS, os.path.join(self.dir, "sessions.json")
        self._lock, agent.SESSIONS_LOCK = agent.SESSIONS_LOCK, os.path.join(self.dir, "sessions.lock")
        self._claims, agent.claims.CLAIM_DIR = agent.claims.CLAIM_DIR, os.path.join(self.dir, "claims")
        self._cgpt, self._ops, self._goto = agent.cgpt, agent.ops, agent.goto
        agent.ops = _FakeOps()
        agent.goto = lambda *a, **k: None

    def tearDown(self):
        agent.RUNS_DIR, agent.STATE_DIR, agent.SESSIONS = self._runs, self._state, self._sessions
        agent.SESSIONS_LOCK, agent.claims.CLAIM_DIR = self._lock, self._claims
        agent.cgpt, agent.ops, agent.goto = self._cgpt, self._ops, self._goto
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_binding_follows_the_id_the_conversation_settles_on(self):
        outer = self
        seen = []

        class Cgpt(_FakeCgpt):
            def eval_js(self, tab_id, expr):
                if "probe" in expr:
                    return {"ok": True}
                seen.append(expr)
                return outer.PLACEHOLDER if len(seen) == 1 else outer.SETTLED

        agent.cgpt = Cgpt(['```c2c\n{"ops":[{"op":"list","path":"."}]}\n```',
                           "GO - nothing found."])
        args = argparse.Namespace(
            workspace=self.dir, preset="review", task="t", session="bound", new=True,
            resume=False, write=False, allow_shell=False, focus=False,
            max_rounds=4, max_chars=10000, round_chars=5000, timeout=1, poll=0,
            retries=1,
        )
        agent.run(args, lambda line: None, {})

        entry = agent.load_sessions()["bound"]
        self.assertEqual(entry["url"], self.SETTLED,
                         "the session stayed bound to the placeholder id")

    def test_the_claim_lands_on_the_settled_conversation(self):
        outer = self
        seen = []

        class Cgpt(_FakeCgpt):
            def eval_js(self, tab_id, expr):
                if "probe" in expr:
                    return {"ok": True}
                seen.append(expr)
                return outer.PLACEHOLDER if len(seen) == 1 else outer.SETTLED

        agent.cgpt = Cgpt(['```c2c\n{"ops":[{"op":"list","path":"."}]}\n```',
                           "GO - nothing found."])
        args = argparse.Namespace(
            workspace=self.dir, preset="review", task="t", session=None, new=True,
            resume=False, write=False, allow_shell=False, focus=False,
            max_rounds=4, max_chars=10000, round_chars=5000, timeout=1, poll=0,
            retries=1,
        )
        agent.run(args, lambda line: None, {})

        # Claims are registered per process, so the registry - not the claim
        # directory - is where a claim taken in this process is observable.
        settled = ("conversation", agent.claims.conversation_id(self.SETTLED))
        placeholder = ("conversation", agent.claims.conversation_id(self.PLACEHOLDER))
        self.assertIn(settled, agent.claims._HELD,
                      "the settled conversation was never claimed")
        self.assertIn(placeholder, agent.claims._HELD,
                      "the placeholder was expected to have been claimed first")
