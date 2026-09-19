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
import subprocess
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


def find_chatgpt_tab(tabs):
    """First stable tab id whose *host* is ChatGPT, or None.

    Matching on host rather than on a substring keeps an unrelated page that
    merely mentions chatgpt.com in its query string from being hijacked.
    """
    for _window_index, _tab_index, tab_id, url in tabs:
        try:
            host = urlsplit(url).hostname
        except ValueError:
            continue
        if host and host.lower() in CHATGPT_HOSTS:
            return tab_id
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

    def reset(self):
        """Forget the run of stable samples - the message was not done after all."""
        self._stable = 0


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


def _record_window(tab_id, window_id):
    os.makedirs(os.path.dirname(WINDOW_STATE), exist_ok=True)
    data = []
    try:
        with open(WINDOW_STATE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        pass
    data.append({"tab_id": int(tab_id), "window_id": int(window_id), "created": time.time()})
    tmp = WINDOW_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True)
        handle.flush()
    os.replace(tmp, WINDOW_STATE)

def open_tab(url, timeout):
    """Open `url` in a private tool window and return its tab id."""
    raw = bridge("open_window", url)
    parts = raw.split("\t")
    tab_id = int(parts[0])
    if len(parts) > 1:
        _record_window(tab_id, int(parts[1]))
    deadline = time.time() + timeout
    while time.time() < deadline:
        if bridge("loading", tab_id) == "done":
            return tab_id
        time.sleep(0.5)
    return tab_id


def _load_windows():
    try:
        with open(WINDOW_STATE, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return []


def cleanup_window(tab_id, url):
    """Close only a recorded single-tab window that still proves ownership."""
    kept = []
    for item in _load_windows():
        if int(item.get("tab_id", -1)) != int(tab_id):
            kept.append(item)
            continue
        if claims.conversation_id(url) and bridge("close_window", item["window_id"], tab_id, claims.conversation_id(url)) == "ok":
            continue
        kept.append(item)
    if kept != _load_windows():
        tmp = WINDOW_STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(kept, handle, sort_keys=True)
        os.replace(tmp, WINDOW_STATE)


def ensure_tab(timeout):
    """Find the open ChatGPT tab, or open one and wait for it to load."""
    found = find_chatgpt_tab(parse_tabs(bridge("list")))
    if found:
        return found
    return open_tab(CHATGPT_URL, timeout)


def claim_one_shot_tab(held, timeout):
    """Claim an available ChatGPT tab and the conversation it currently shows."""
    first = ensure_tab(timeout)

    def claim_candidate(tab_id, known_url=None):
        try:
            held.claim_tab(tab_id)
        except claims.ClaimBusy:
            return False
        try:
            url = known_url
            if url is None:
                value = eval_js(tab_id, "location.href")
                url = value if isinstance(value, str) else None
            held.claim_conversation(claims.conversation_id(url))
        except claims.ClaimBusy:
            # A busy tab is replaceable; an already-owned conversation is not.
            # Release the tab we just took, then preserve the holder diagnostic.
            held.release_tab(tab_id)
            raise
        return True

    # Keep the common path cheap: if the first reusable tab is free, no second
    # bridge listing is needed. Only contention makes us enumerate alternatives.
    if claim_candidate(first):
        return first

    for _window, _index, tab_id, url in parse_tabs(bridge("list")):
        if tab_id == first:
            continue
        try:
            host = urlsplit(url).hostname
        except ValueError:
            continue
        if not host or host.lower() not in CHATGPT_HOSTS:
            continue
        if claim_candidate(tab_id, url):
            return tab_id

    tab_id = open_tab(CHATGPT_URL, timeout)
    held.claim_tab(tab_id)
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


def wait_for_reply(tab_id, marker, timeout, poll):
    tracker = StabilityTracker()
    call = "CGPT.state(" + js_string(marker) + ")"
    deadline = time.time() + timeout
    latest = {}
    while time.time() < deadline:
        time.sleep(poll)
        latest = eval_js(tab_id, call)
        if tracker.update(
            latest.get("found", False),
            latest.get("streaming", False),
            latest.get("isLast", False),
            latest.get("text", ""),
        ):
            markdown = latest.get("markdown", "")
            # Settled text is not a settled message: the code block is still
            # being rebuilt around it. Sampling here yields a truncated request.
            if proto.is_still_rendering(markdown):
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
        reply = wait_for_reply(tab_id, marker, args.timeout, args.poll)
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
