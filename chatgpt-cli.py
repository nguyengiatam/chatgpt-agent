#!/usr/bin/env python3
"""Talk to the ChatGPT web UI from the shell, using your own logged-in Edge tab.

    chatgpt-cli.py "summarise this file"
    cat notes.md | chatgpt-cli.py

Drives the page through Edge's AppleScript `execute javascript` command rather
than by synthesising keystrokes, so it does not need focus and cannot type into
the wrong window.

Requires, once: Edge > View > Developer > Allow JavaScript from Apple Events.
"""

import argparse
import atexit
import json
import os
import fcntl
import subprocess
import tempfile
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chatgpt_claims as claims  # noqa: E402  (path set just above)
import chatgpt_protocol as proto  # noqa: E402

try:
    from urllib.parse import urlsplit
except ImportError:  # pragma: no cover - py2 never supported, kept explicit
    raise SystemExit("python3 required")

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(HERE, "chatgpt_bridge.applescript")
DOM_JS = os.path.join(HERE, "chatgpt_dom.js")

CHATGPT_URL = "https://chatgpt.com/"
CHATGPT_HOSTS = frozenset(["chatgpt.com", "www.chatgpt.com", "chat.openai.com"])

ENABLE_HINT = (
    "Edge is refusing to run JavaScript from AppleScript.\n"
    "Enable it once: Edge menu bar > View > Developer > "
    "Allow JavaScript from Apple Events."
)
AUTOMATION_HINT = (
    "This terminal is not allowed to control Microsoft Edge.\n"
    "Grant it in System Settings > Privacy & Security > Automation."
)


class CliError(Exception):
    """A failure with an explanation the user can act on."""


class TabGone(CliError):
    """The stable Edge tab id no longer names any open tab."""


class ReplyNotStarted(CliError):
    """ChatGPT never began answering our message: no Stop button, no text."""


# --- pure helpers (unit-tested in test_chatgpt_cli.py) ----------------------


def js_string(text):
    """Return `text` as one JavaScript string literal, safe to concatenate.

    ensure_ascii keeps the result 7-bit so it survives osascript's argv
    encoding, and escapes the newlines that would otherwise be a syntax error.
    """
    return json.dumps(text, ensure_ascii=True)


def parse_tabs(raw):
    """Parse the `window<TAB>tab<TAB>id<TAB>url` rows printed by the bridge."""
    tabs = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        try:
            window_index = int(parts[0])
            tab_index = int(parts[1])
            tab_id = int(parts[2])
        except ValueError:
            continue
        tabs.append((window_index, tab_index, tab_id, parts[3]))
    return tabs


def is_chatgpt_url(url):
    """True when `url`'s *host* is ChatGPT.

    Matching on host rather than on a substring keeps an unrelated page that
    merely mentions chatgpt.com in its query string from being mistaken for one
    of ours.
    """
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    return bool(host) and host.lower() in CHATGPT_HOSTS


def parse_tab_state(raw):
    """Reject incomplete bridge evidence instead of guessing browser ownership."""
    parts = raw.split("\t", 4)
    if len(parts) != 5:
        return None
    try:
        window_id, tab_count, active, minimized = [int(value) for value in parts[:4]]
    except ValueError:
        return None
    if active not in (0, 1) or minimized not in (0, 1):
        return None
    return window_id, tab_count, bool(active), bool(minimized), parts[4]


# The action bar - the Copy / thumbs row ChatGPT renders under an assistant
# message only once that message is complete - is the positive end-of-message
# signal. Two stable polls are not enough on their own: ChatGPT streams in
# bursts and the stop button is absent during the gaps, so a mid-stream pause
# used to be read as a finished message. The settle window then re-checks the
# text after the signal first appears, and the fallback bounds how long a
# missing signal is waited for before accepting a stable reply anyway.
SETTLE_SECONDS = 10.0
ACTION_BAR_FALLBACK_SECONDS = 60.0
THROTTLE_CHECK_SECONDS = 15.0
# ChatGPT sometimes loses a response outright: the message is posted and no
# reply ever starts. The Stop button appears as soon as generation begins,
# thinking included, so never having seen it - and no text either - for this
# long means the reply is lost, not slow. Without this a lost reply costs the
# whole --timeout.
NO_START_SECONDS = 60.0
# How many times a fallback-accepted reply (no action bar) that still looks
# half-drawn is waited on again before it is handed back as it is.
RENDER_RECHECKS = 2


class StabilityTracker:
    """Decides when *our* reply is finished.

    The page-wide stop button only counts against us while our reply is still
    the newest message; once someone else has answered after us, ours is
    necessarily complete and their streaming must not hold us up.

    A reply is finished only when the stop button is clear, the text has been
    stable for ``stable_polls`` samples, AND the action bar under our own turn
    is present. The action bar is the real signal - the stop button alone is
    absent during ordinary streaming gaps - so when it is present the text is
    re-read once more after ``settle_seconds`` before the reply is handed back,
    and a reply whose text grew, or whose action bar vanished, in that window is
    not finished after all.

    If the action bar never appears, a reply that stays stable and unstreaming
    for ``fallback_seconds`` is accepted anyway, with one warning: a ChatGPT
    redesign then degrades to slow-but-correct rather than to a hang.
    """

    def __init__(self, stable_polls=2, settle_seconds=SETTLE_SECONDS,
                 fallback_seconds=ACTION_BAR_FALLBACK_SECONDS):
        self.stable_polls = stable_polls
        self.settle_seconds = settle_seconds
        self.fallback_seconds = fallback_seconds
        self._last_text = None
        self._stable = 0
        self._settle_until = None
        self._settle_text = None
        self._fallback_since = None

    def update(self, found, streaming, is_last, text, action_bar, now=None):
        """One poll. Returns "finished", "fallback", or None to keep waiting."""
        now = time.time() if now is None else now
        ours_still_streaming = streaming and is_last
        if not found or ours_still_streaming or not text:
            self.reset()
            self._last_text = text
            return None
        self._stable = self._stable + 1 if text == self._last_text else 0
        self._last_text = text

        if action_bar:
            self._fallback_since = None
            if self._stable < self.stable_polls:
                self._settle_until = None
                return None
            if self._settle_until is None:
                self._settle_until = now + self.settle_seconds
                self._settle_text = text
                return None
            if now < self._settle_until:
                return None
            if text == self._settle_text:
                return "finished"
            self._settle_until = None
            return None

        self._settle_until = None
        if self._stable < self.stable_polls:
            self._fallback_since = None
            return None
        if self._fallback_since is None:
            self._fallback_since = now
            return None
        if now - self._fallback_since >= self.fallback_seconds:
            _notify(
                "warning: no action bar appeared under the reply after "
                + str(int(self.fallback_seconds)) + "s of stable text; accepting "
                "it without the end-of-message signal (the ChatGPT DOM may have "
                "changed)."
            )
            return "fallback"
        return None

    def reset(self):
        """Forget the run of stable samples - the message was not done after all."""
        self._stable = 0
        self._settle_until = None
        self._settle_text = None
        self._fallback_since = None


# --- browser plumbing ------------------------------------------------------


def _explain(stderr):
    """Turn an osascript failure into an instruction the user can act on.

    macOS localises these messages, so match on the numeric AppleScript error
    codes and on "JavaScript" - a product name that is never translated -
    rather than on English wording.
    """
    low = stderr.lower()
    if "javascript" in low:
        return ENABLE_HINT
    if "-1743" in stderr:
        return AUTOMATION_HINT
    if "-600" in stderr or "-609" in stderr:
        return "Microsoft Edge is not running. Open it and sign in to ChatGPT first."
    return "AppleScript bridge failed:\n" + stderr.strip()


def bridge(*args):
    proc = subprocess.run(
        ["osascript", BRIDGE] + [str(a) for a in args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        if "tab not found" in proc.stderr.lower():
            raise TabGone(_explain(proc.stderr))
        raise CliError(_explain(proc.stderr))
    return proc.stdout.strip()


_dom_cache = []


def dom_source():
    if not _dom_cache:
        with open(DOM_JS, "r") as handle:
            _dom_cache.append(handle.read())
    return _dom_cache[0]


def eval_js(tab_id, expression):
    """Run `expression` in the tab and return its value, decoded from JSON."""
    script = dom_source() + "\n;JSON.stringify(" + expression + ");"
    raw = bridge("eval", script, tab_id)
    if not raw:
        raise CliError(ENABLE_HINT)
    try:
        return json.loads(raw)
    except ValueError:
        raise CliError("Unexpected reply from the page: " + raw[:200])



WINDOW_STATE = os.path.join(os.path.expanduser("~"), ".chatgpt-agent", "windows.json")
WINDOW_STATE_LOCK = WINDOW_STATE + ".lock"
DEFAULT_SCREEN_BOUNDS = (1920, 1080)
WINDOW_SIZE = (480, 360)
# Each concurrent tool window sits this far up and left of the previous one, so
# none of them covers another completely (macOS throttles fully covered windows).
WINDOW_CASCADE_PX = 40


def _window_lock():
    os.makedirs(os.path.dirname(WINDOW_STATE), exist_ok=True)
    handle = open(WINDOW_STATE_LOCK, "a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _window_write(data):
    fd, tmp = tempfile.mkstemp(prefix="windows.", dir=os.path.dirname(WINDOW_STATE))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, sort_keys=True)
            handle.flush()
        os.replace(tmp, WINDOW_STATE)
    finally:
        try: os.unlink(tmp)
        except OSError: pass


def _record_window(tab_id, window_id):
    lock = _window_lock()
    try:
        data = _load_windows()
        data.append({"tab_id": int(tab_id), "window_id": int(window_id), "created": time.time()})
        _window_write(data)
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()

def open_tab(url, timeout, held=None):
    """Open `url` in a private tool window and return its tab id."""
    raw = bridge("open_window", url, str(WINDOW_SIZE[0]), str(WINDOW_SIZE[1]),
                 str(_live_window_count()), str(WINDOW_CASCADE_PX))
    parts = raw.split("\t")
    tab_id = int(parts[0])
    if len(parts) > 1:
        window_id = int(parts[1])
        if held:
            try:
                held.claim_tab(tab_id)
                held.claim_window(window_id)
            except claims.ClaimBusy:
                held.release_tab(tab_id)
                try:
                    bridge("close", tab_id)
                except Exception:
                    pass
                raise
        _record_window(tab_id, window_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if bridge("loading", tab_id) == "done":
            return tab_id
        time.sleep(0.5)
    return tab_id


def _live_window_count():
    """How many recorded tool windows still have their tab open (cascade index)."""
    try:
        live = {tab_id for _w, _i, tab_id, _u in parse_tabs(bridge("list"))}
    except Exception:
        return 0
    return sum(1 for item in _load_windows() if int(item.get("tab_id", -1)) in live)


def _load_windows():
    try:
        with open(WINDOW_STATE, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return []


def cleanup_window(tab_id, url):
    """Close only a recorded single-tab window that still proves ownership."""
    lock = _window_lock()
    try:
        current = _load_windows()
        kept = []
        for item in current:
            if int(item.get("tab_id", -1)) != int(tab_id):
                kept.append(item)
                continue
            if claims.conversation_id(url) and bridge("close_window", item["window_id"], tab_id, claims.conversation_id(url)) == "ok":
                continue
            kept.append(item)
        if kept != current:
            _window_write(kept)
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def claim_reusable_tab(tab_id, held):
    """Claim only a tab whose registry and live shape still prove it is ours.

    A ChatGPT URL proves nothing about ownership: it may be the tab the person
    is reading.  The registry, a single active tab in its recorded window, and
    both kernel claims must all agree before any caller is allowed to use it.
    """
    if held is None:
        return False
    recorded_window = None
    for item in _load_windows():
        try:
            if int(item.get("tab_id", -1)) == int(tab_id):
                recorded_window = int(item["window_id"])
                break
        except (KeyError, TypeError, ValueError):
            continue
    if recorded_window is None:
        return False
    try:
        state = parse_tab_state(bridge("tab_state", tab_id))
    except CliError:
        return False
    if state is None:
        return False
    window_id, tab_count, active, _minimized, _url = state
    if window_id != recorded_window or tab_count != 1 or not active:
        return False
    try:
        held.claim_tab(tab_id)
    except claims.ClaimBusy:
        return False
    try:
        held.claim_window(window_id)
    except claims.ClaimBusy:
        held.release_tab(tab_id)
        return False
    return True


def ensure_tab(timeout, held=None):
    """Reuse only a proved tool tab; every other ChatGPT tab is the user's."""
    for _window_index, _tab_index, tab_id, url in parse_tabs(bridge("list")):
        if is_chatgpt_url(url) and claim_reusable_tab(tab_id, held):
            return tab_id
    return open_tab(CHATGPT_URL, timeout, held)


def claim_one_shot_tab(held, timeout):
    """Claim an available ChatGPT tab and the conversation it currently shows."""
    tab_id = ensure_tab(timeout, held)
    try:
        value = eval_js(tab_id, "location.href")
        url = value if isinstance(value, str) else None
        held.claim_conversation(claims.conversation_id(url))
    except claims.ClaimBusy:
        # A busy conversation is not replaceable: another tab would still name
        # the same durable object, so release our tab and preserve the holder.
        held.release_tab(tab_id)
        raise
    return tab_id


def make_marker():
    """A short tag to stamp on an outgoing message so we can find it again.

    The rendered message never matches the string we sent - markdown eats the
    backticks, and a long one gains "See more" button captions - so the reply
    is located by a marker instead of by the prompt text. Plain ASCII in square
    brackets survives rendering untouched; backticks would not.
    """
    return "[c2c:" + uuid.uuid4().hex[:8] + "]"


ATTACH_LINES = 120
# 60,000 characters on a single line was measured at 0.1s, so the size guard
# sits above that: it is there for territory nobody has measured, not for a
# case known to be fine. The line rule below is the one that does the work.
ATTACH_CHARS = 80000
ATTACH_WAIT = 90.0

# Deliberately nameless. Naming the file here put the filename into the turn we
# typed, and both guards below search the turn for that filename - so our own
# sentence satisfied them and neither could ever fail. The model does not need
# the name: it has to open the attachment, not look it up.
ATTACHED_NOTE = (
    "Everything for this turn is in the file attached to this message. It went "
    "as a file because pasting it would stall the page. Open it and continue "
    "exactly as if its contents had been typed here."
)


def needs_attachment(text):
    """True when typing `text` would make the composer crawl.

    The cost of execCommand("insertText") is quadratic in NEWLINES, not in
    characters: ProseMirror builds one block node per line inside a single
    transaction. Measured on the live composer with the same 40,000 characters
    throughout - 1 line 0.1s, 50 lines 0.5s, 200 lines 2.4s, 700 lines 16.7s,
    2000 lines 116.3s.

    So the line count is the test that matters. The character ceiling is a
    second guard for the rare single enormous line.
    """
    text = text or ""
    return text.count("\n") + 1 > ATTACH_LINES or len(text) > ATTACH_CHARS


def attachment_name(marker):
    """A filename derived from the marker, with nothing path-like left in it."""
    tag = "".join(ch for ch in str(marker) if ch.isalnum()) or "payload"
    return "c2c-" + tag[-8:] + ".txt"


ATTACH_TRIES = 3
ATTACH_SETTLE = 5.0


def _notify(line):
    """A note on stderr. Used where silence would hide a real signal."""
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def attach_payload(tab_id, payload, name, tries=ATTACH_TRIES):
    """Upload `payload` as `name`, returning once its chip is on screen.

    The first attach on a freshly navigated chat is the one that fails: React
    has not bound its handler to the file input yet, so the change event is
    dropped. Nothing downstream notices, because `input.files` still holds the
    file nobody consumed - which is why this is checked by the chip appearing
    rather than by the assignment succeeding.

    Re-dispatching against the now-settled page works, and every later attach in
    a run succeeds first time. Each retry is announced: a page that needs two
    goes is worth seeing, and swallowing it would hide the one signal that says
    the tab was cold.
    """
    for attempt in range(1, tries + 1):
        if attempt > 1:
            _notify("  the page had not bound its upload handler yet - "
                    "re-attaching " + name + " (try " + str(attempt) + " of " + str(tries) + ")")
        result = eval_js(
            tab_id,
            "CGPT.attach(" + js_string(payload) + ", " + js_string(name) + ")",
        )
        if not result.get("ok"):
            raise CliError(
                "Could not attach this turn's data (" + str(result.get("error")) + ")."
            )
        deadline = time.time() + ATTACH_SETTLE
        while time.time() < deadline:
            time.sleep(0.4)
            if eval_js(tab_id, "CGPT.hasChip(" + js_string(name) + ")"):
                return
    raise CliError(
        name + " never appeared in the composer after " + str(tries) + " attempts.\n"
        "The file was handed to the page each time and the page never took it, so "
        "nothing was sent."
    )


def send(tab_id, prompt):
    """Type and submit `prompt`, returning the marker that identifies it.

    A payload too wide to type is uploaded instead; the marker is always typed,
    since that is what locates our reply afterwards.
    """
    marker = make_marker()
    attached = needs_attachment(prompt)
    name = attachment_name(marker) if attached else ""

    if attached:
        attach_payload(tab_id, prompt, name)
        typed = marker + "\n\n" + ATTACHED_NOTE
    else:
        typed = marker + "\n\n" + prompt

    result = eval_js(tab_id, "CGPT.insert(" + js_string(typed) + ")")
    if not result.get("ok"):
        raise CliError(
            "Could not type into the ChatGPT composer (" + str(result.get("error")) + ").\n"
            "Check that the tab is signed in and showing a chat, not a login or "
            "Cloudflare page."
        )

    # React enables the send button a tick after the input event lands, and an
    # upload has to settle before the turn can carry it.
    #
    # When a file is involved the gate is a positive one - the chip bearing its
    # name is still on screen and the button is free. Waiting for the button
    # alone was not enough: it unblocks both when the upload finished and when
    # ChatGPT quietly removed the attachment, and gating on that sent turns
    # with no file in them. Timing out here is the correct outcome; sending
    # anyway is not, so this fails closed.
    ready_call = "CGPT.attachmentReady(" + js_string(name if attached else "") + ")"
    deadline = time.time() + (ATTACH_WAIT if attached else 4.0)
    result, reason = {}, "never became ready"
    while time.time() < deadline:
        time.sleep(0.4)
        if attached:
            ready = eval_js(tab_id, ready_call)
            if not ready.get("ready"):
                reason = str(ready.get("reason") or reason)
                continue
        result = eval_js(tab_id, "CGPT.submit()")
        if not result.get("ok"):
            continue
        if not attached:
            return marker
        # The file should have travelled with the turn. Asking the message
        # rather than the model keeps this independent of ChatGPT's wording.
        time.sleep(1.5)
        carried = eval_js(tab_id, "CGPT.sentWithAttachment(" + js_string(name) + ")")
        if carried.get("carried"):
            return marker
        raise CliError(
            "The turn was sent but arrived without " + name + ". Nothing was read "
            "from it, so any answer to it would be about nothing. Re-run; if it "
            "repeats, the conversation may be refusing uploads - try --new."
        )

    if attached:
        raise CliError(
            "Gave up waiting for " + name + " to be ready to send (" + reason + ").\n"
            "Nothing was sent. ChatGPT accepted the file and then dropped it, or "
            "the attachment chip moved; either way sending would have delivered an "
            "empty reference."
        )
    raise CliError("Could not press Send (" + str(result.get("error")) + ").")


def wait_for_reply(tab_id, marker, timeout, poll, warn=None):
    tracker = StabilityTracker()
    call = "CGPT.state(" + js_string(marker) + ")"
    deadline = time.time() + timeout
    next_throttle_check = time.time() + THROTTLE_CHECK_SECONDS if warn else None
    throttle_warned = False
    started = False
    rechecks = 0
    no_start_at = time.time() + NO_START_SECONDS
    latest = {}
    while time.time() < deadline:
        time.sleep(poll)
        if warn and time.time() >= next_throttle_check:
            next_throttle_check = time.time() + THROTTLE_CHECK_SECONDS
            try:
                state = parse_tab_state(bridge("tab_state", tab_id))
            except CliError:
                state = None
            if state is not None:
                _window_id, _tab_count, active, minimized, _url = state
                if (not active or minimized) and not throttle_warned:
                    warn(
                        "the run's tab is no longer the active tab of its window "
                        "(or the window is minimised); Edge throttles background tabs, "
                        "so replies may be slow. Bring that window forward to speed it up."
                    )
                    throttle_warned = True
        latest = eval_js(tab_id, call)
        started = started or bool(latest.get("streaming") or latest.get("text"))
        if not started and time.time() >= no_start_at:
            raise ReplyNotStarted(
                "ChatGPT did not start a reply within " + str(int(NO_START_SECONDS))
                + "s (no Stop button, no text): the response was lost.\n"
                "A new conversation usually works where this one stays silent."
            )
        verdict = tracker.update(
            latest.get("found", False),
            latest.get("streaming", False),
            latest.get("isLast", False),
            latest.get("text", ""),
            latest.get("actionBar", False),
        )
        if verdict:
            markdown = latest.get("markdown", "")
            # Without the action bar, settled text is not a settled message:
            # the code block may still be rebuilt around it, and sampling here
            # yields a truncated request. With the action bar the message is
            # done, so a c2c block that does not parse is the model's mistake -
            # waiting on it only burns the timeout, while handing it back lets
            # the loop ask for a corrected block. Measured 2026-09-24: an extra
            # "}" in a finished reply held a run for the full 900s.
            if (verdict == "fallback" and rechecks < RENDER_RECHECKS
                    and proto.is_still_rendering(markdown)):
                rechecks += 1
                tracker.reset()
                continue
            return markdown
    raise CliError(
        "Timed out after " + str(timeout) + "s waiting for the reply "
        "(our message found: " + str(latest.get("found")) + ", "
        "streaming: " + str(latest.get("streaming")) + ", "
        "characters so far: " + str(len(latest.get("text", ""))) + ").\n"
        "Raise it with --timeout."
    )


# --- cli -------------------------------------------------------------------


def read_prompt(words, stdin=None):
    """Combine the instruction given as arguments with any piped-in content.

    Both are prompt material: `git diff | chatgpt-cli.py "review this"` must
    send the instruction *and* the diff, not silently drop the pipe.
    """
    stream = sys.stdin if stdin is None else stdin
    instruction = " ".join(words).strip()
    piped = "" if stream.isatty() else stream.read().strip()
    if instruction and piped:
        return instruction + "\n\n" + piped
    return instruction or piped


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Send a prompt to ChatGPT in your logged-in Edge tab and print the reply."
    )
    parser.add_argument("words", nargs="*", help="the prompt; omit to read stdin")
    parser.add_argument("--timeout", type=float, default=180.0, help="seconds to wait for a reply")
    parser.add_argument("--poll", type=float, default=1.0, help="seconds between checks")
    parser.add_argument(
        "--focus",
        action="store_true",
        help="switch Edge to the ChatGPT tab (background tabs can render slowly)",
    )
    args = parser.parse_args(argv)

    prompt = read_prompt(args.words)
    if not prompt:
        parser.error("no prompt given")

    try:
        # A one-shot may use any free ChatGPT tab. A busy first candidate is not
        # a reason to fail when another tab (or a fresh one) can isolate the ask.
        held = claims.ClaimSet("ask")
        atexit.register(held.close)
        tab_id = claim_one_shot_tab(held, args.timeout)
        if args.focus:
            bridge("focus", tab_id)

        status = eval_js(tab_id, "CGPT.probe()")
        if not status.get("ok"):
            raise CliError(
                "The ChatGPT tab has no composer (" + str(status.get("error")) + ").\n"
                "URL is " + str(status.get("url")) + " - sign in there first."
            )

        marker = send(tab_id, prompt)
        # A fresh chat receives its durable /c/<id> only after the first turn is
        # accepted. Claim that identity before waiting so another process cannot
        # attach to the same conversation during the answer window.
        current_url = eval_js(tab_id, "location.href")
        held.claim_conversation(claims.conversation_id(current_url))
        reply = wait_for_reply(tab_id, marker, args.timeout, args.poll, warn=_notify)
    except claims.ClaimBusy as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    except CliError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        return 130

    print(reply)
    try:
        cleanup_window(tab_id, current_url)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
