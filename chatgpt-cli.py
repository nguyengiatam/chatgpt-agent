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
import json
import os
import subprocess
import sys
import time
import uuid

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


# --- pure helpers (unit-tested in test_chatgpt_cli.py) ----------------------


def js_string(text):
    """Return `text` as one JavaScript string literal, safe to concatenate.

    ensure_ascii keeps the result 7-bit so it survives osascript's argv
    encoding, and escapes the newlines that would otherwise be a syntax error.
    """
    return json.dumps(text, ensure_ascii=True)


def parse_tabs(raw):
    """Parse the `window<TAB>tab<TAB>url` rows printed by the bridge."""
    tabs = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        try:
            window_index = int(parts[0])
            tab_index = int(parts[1])
        except ValueError:
            continue
        tabs.append((window_index, tab_index, parts[2]))
    return tabs


def find_chatgpt_tab(tabs):
    """First (window, tab) whose *host* is ChatGPT, or None.

    Matching on host rather than on a substring keeps an unrelated page that
    merely mentions chatgpt.com in its query string from being hijacked.
    """
    for window_index, tab_index, url in tabs:
        try:
            host = urlsplit(url).hostname
        except ValueError:
            continue
        if host and host.lower() in CHATGPT_HOSTS:
            return (window_index, tab_index)
    return None


class StabilityTracker:
    """Decides when *our* reply is finished.

    The page-wide stop button only counts against us while our reply is still
    the newest message; once someone else has answered after us, ours is
    necessarily complete and their streaming must not hold us up.
    """

    def __init__(self, stable_polls=2):
        self.stable_polls = stable_polls
        self._last_text = None
        self._stable = 0

    def update(self, found, streaming, is_last, text):
        ours_still_streaming = streaming and is_last
        if not found or ours_still_streaming or not text:
            self._stable = 0
            self._last_text = text
            return False
        self._stable = self._stable + 1 if text == self._last_text else 0
        self._last_text = text
        return self._stable >= self.stable_polls


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
        raise CliError(_explain(proc.stderr))
    return proc.stdout.strip()


_dom_cache = []


def dom_source():
    if not _dom_cache:
        with open(DOM_JS, "r") as handle:
            _dom_cache.append(handle.read())
    return _dom_cache[0]


def eval_js(window_index, tab_index, expression):
    """Run `expression` in the tab and return its value, decoded from JSON."""
    script = dom_source() + "\n;JSON.stringify(" + expression + ");"
    raw = bridge("eval", script, window_index, tab_index)
    if not raw:
        raise CliError(ENABLE_HINT)
    try:
        return json.loads(raw)
    except ValueError:
        raise CliError("Unexpected reply from the page: " + raw[:200])


def ensure_tab(timeout):
    """Find the open ChatGPT tab, or open one and wait for it to load."""
    found = find_chatgpt_tab(parse_tabs(bridge("list")))
    if found:
        return found

    bridge("open", CHATGPT_URL)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        found = find_chatgpt_tab(parse_tabs(bridge("list")))
        if not found:
            continue
        while time.time() < deadline:
            if bridge("loading", found[0], found[1]) == "done":
                return found
            time.sleep(0.5)
        return found
    raise CliError("Opened " + CHATGPT_URL + " but it did not finish loading in time.")


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


def attach_payload(window_index, tab_index, payload, name, tries=ATTACH_TRIES):
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
            window_index, tab_index,
            "CGPT.attach(" + js_string(payload) + ", " + js_string(name) + ")",
        )
        if not result.get("ok"):
            raise CliError(
                "Could not attach this turn's data (" + str(result.get("error")) + ")."
            )
        deadline = time.time() + ATTACH_SETTLE
        while time.time() < deadline:
            time.sleep(0.4)
            if eval_js(window_index, tab_index, "CGPT.hasChip(" + js_string(name) + ")"):
                return
    raise CliError(
        name + " never appeared in the composer after " + str(tries) + " attempts.\n"
        "The file was handed to the page each time and the page never took it, so "
        "nothing was sent."
    )


def send(window_index, tab_index, prompt):
    """Type and submit `prompt`, returning the marker that identifies it.

    A payload too wide to type is uploaded instead; the marker is always typed,
    since that is what locates our reply afterwards.
    """
    marker = make_marker()
    attached = needs_attachment(prompt)
    name = attachment_name(marker) if attached else ""

    if attached:
        attach_payload(window_index, tab_index, prompt, name)
        typed = marker + "\n\n" + ATTACHED_NOTE
    else:
        typed = marker + "\n\n" + prompt

    result = eval_js(window_index, tab_index, "CGPT.insert(" + js_string(typed) + ")")
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
            ready = eval_js(window_index, tab_index, ready_call)
            if not ready.get("ready"):
                reason = str(ready.get("reason") or reason)
                continue
        result = eval_js(window_index, tab_index, "CGPT.submit()")
        if not result.get("ok"):
            continue
        if not attached:
            return marker
        # The file should have travelled with the turn. Asking the message
        # rather than the model keeps this independent of ChatGPT's wording.
        time.sleep(1.5)
        carried = eval_js(window_index, tab_index, "CGPT.sentWithAttachment(" + js_string(name) + ")")
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


def wait_for_reply(window_index, tab_index, marker, timeout, poll):
    tracker = StabilityTracker()
    call = "CGPT.state(" + js_string(marker) + ")"
    deadline = time.time() + timeout
    latest = {}
    while time.time() < deadline:
        time.sleep(poll)
        latest = eval_js(window_index, tab_index, call)
        if tracker.update(
            latest.get("found", False),
            latest.get("streaming", False),
            latest.get("isLast", False),
            latest.get("text", ""),
        ):
            return latest.get("markdown", "")
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
        window_index, tab_index = ensure_tab(args.timeout)
        if args.focus:
            bridge("focus", window_index, tab_index)

        status = eval_js(window_index, tab_index, "CGPT.probe()")
        if not status.get("ok"):
            raise CliError(
                "The ChatGPT tab has no composer (" + str(status.get("error")) + ").\n"
                "URL is " + str(status.get("url")) + " - sign in there first."
            )

        marker = send(window_index, tab_index, prompt)
        reply = wait_for_reply(window_index, tab_index, marker, args.timeout, args.poll)
    except CliError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        return 130

    print(reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
