#!/usr/bin/env python3
"""Tests for the orchestrator's pure parts (no browser)."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
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

    def test_missing_store_reads_as_empty(self):
        self.assertEqual(agent.load_sessions(), {})

    def test_a_saved_session_round_trips(self):
        agent.save_session("app", "https://chatgpt.com/c/abc")
        self.assertEqual(agent.load_sessions()["app"], "https://chatgpt.com/c/abc")

    def test_saving_keeps_other_sessions(self):
        agent.save_session("one", "https://chatgpt.com/c/1")
        agent.save_session("two", "https://chatgpt.com/c/2")
        self.assertEqual(len(agent.load_sessions()), 2)

    def test_a_corrupt_store_does_not_crash_the_run(self):
        with open(agent.SESSIONS, "w") as handle:
            handle.write("{ not json")
        self.assertEqual(agent.load_sessions(), {})


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


if __name__ == "__main__":
    unittest.main()
