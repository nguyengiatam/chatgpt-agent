#!/usr/bin/env python3
"""Process-level tests for run ownership claims; no browser required."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import chatgpt_claims as claims
except ImportError:
    claims = None


@unittest.skipUnless(hasattr(os, "kill") and hasattr(signal, "SIGKILL"), "requires POSIX process signals")
class ClaimProcessTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = os.environ.copy()
        self.env["CHATGPT_AGENT_CLAIM_DIR"] = self.dir.name
        self.procs = []

    def tearDown(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        self.dir.cleanup()

    def _python(self, code, *args, **kwargs):
        proc = subprocess.Popen(
            [sys.executable, "-c", code, *map(str, args)],
            cwd=HERE,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs,
        )
        self.procs.append(proc)
        return proc

    def _owner(self, owner="owner-A", tab=101, conversation=None):
        code = r'''
import sys, time
import chatgpt_claims as claims
held = claims.ClaimSet(sys.argv[1])
held.claim_tab(int(sys.argv[2]))
if len(sys.argv) > 3 and sys.argv[3] != "-":
    held.claim_conversation(sys.argv[3])
print("ready", flush=True)
time.sleep(60)
'''
        proc = self._python(code, owner, tab, conversation or "-")
        self.assertEqual(proc.stdout.readline().strip(), "ready", proc.stderr.read() if proc.poll() is not None else "")
        return proc

    def _contend(self, owner, tab, conversation=None):
        code = r'''
import sys
import chatgpt_claims as claims
try:
    held = claims.ClaimSet(sys.argv[1])
    held.claim_tab(int(sys.argv[2]))
    if len(sys.argv) > 3 and sys.argv[3] != "-":
        held.claim_conversation(sys.argv[3])
except claims.ClaimBusy as exc:
    print(str(exc))
    raise SystemExit(23)
else:
    held.close()
    print("acquired")
'''
        started = time.monotonic()
        proc = self._python(code, owner, tab, conversation or "-")
        out, err = proc.communicate(timeout=3)
        return proc.returncode, out, err, time.monotonic() - started

    def test_claim_module_exists(self):
        self.assertIsNotNone(claims, "chatgpt_claims module must exist")

    def test_same_tab_is_refused_immediately_and_names_holder(self):
        if claims is None:
            self.fail("chatgpt_claims module must exist")
        self._owner(owner="review-one", tab=301)
        code, out, err, elapsed = self._contend("review-two", 301)
        self.assertEqual(code, 23, err)
        self.assertLess(elapsed, 1.0)
        self.assertIn("review-one", out)
        self.assertIn("301", out)

    def test_same_conversation_is_refused_even_through_another_tab(self):
        if claims is None:
            self.fail("chatgpt_claims module must exist")
        self._owner(owner="session-one", tab=401, conversation="shared-conversation")
        code, out, err, elapsed = self._contend("session-two", 402, "shared-conversation")
        self.assertEqual(code, 23, err)
        self.assertLess(elapsed, 1.0)
        self.assertIn("session-one", out)
        self.assertIn("shared-conversation", out)

    def test_sigkill_releases_claim_without_cleanup(self):
        if claims is None:
            self.fail("chatgpt_claims module must exist")
        owner = self._owner(owner="doomed", tab=501, conversation="kill-me")
        os.kill(owner.pid, signal.SIGKILL)
        owner.wait(timeout=3)
        code, out, err, _ = self._contend("successor", 501, "kill-me")
        self.assertEqual(code, 0, out + err)
        self.assertIn("acquired", out)


class SessionStoreConcurrencyTest(unittest.TestCase):
    """Parallel runs each record a binding. All of them must survive.

    Read-modify-write on one shared file loses every update but the last, and
    the store is left valid - just quietly shorter, which is the hardest shape
    of data loss to notice.
    """

    def test_parallel_writers_all_survive_and_no_reader_sees_half_a_file(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        env = os.environ.copy()
        env["HOME"] = home.name

        writer = (
            "import importlib.util, os, sys\n"
            "spec = importlib.util.spec_from_file_location('agent',"
            " os.path.join(sys.argv[1], 'chatgpt-agent.py'))\n"
            "agent = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(agent)\n"
            "agent.save_session(sys.argv[2], 'https://chatgpt.com/c/' + sys.argv[2])\n"
        )
        reader = (
            "import json, os, sys, time\n"
            "path = os.path.join(os.environ['HOME'], '.chatgpt-agent', 'sessions.json')\n"
            "torn = 0\n"
            "deadline = time.time() + 6\n"
            "while time.time() < deadline:\n"
            "    try:\n"
            "        with open(path) as handle:\n"
            "            json.load(handle)\n"
            "    except FileNotFoundError:\n"
            "        pass\n"
            "    except ValueError:\n"
            "        torn += 1\n"
            "print(torn)\n"
        )

        watcher = subprocess.Popen([sys.executable, "-c", reader], env=env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        writers = [
            subprocess.Popen([sys.executable, "-c", writer, HERE, "session-%02d" % i],
                             env=env, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for i in range(12)
        ]
        for proc in writers:
            _, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
        torn, err = watcher.communicate(timeout=30)
        self.assertEqual(watcher.returncode, 0, err)
        self.assertEqual(torn.strip(), "0", "a reader saw a partially written store")

        with open(os.path.join(home.name, ".chatgpt-agent", "sessions.json")) as handle:
            stored = json.load(handle)
        self.assertEqual(sorted(stored), ["session-%02d" % i for i in range(12)])


if __name__ == "__main__":
    unittest.main()
