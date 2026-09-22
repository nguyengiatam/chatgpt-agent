#!/usr/bin/env python3
"""The c2c wire format: how ChatGPT asks for data and how results go back.

The model asks by emitting one or more fenced blocks tagged `c2c`, each
holding {"ops": [...]}; every block is served, in order, as one op list. A reply
with no such block is the final answer.
That rule is chosen so the benign failure is the likely one - a model that
forgets the block ends the loop early, rather than one that forgets a "done"
marker and hangs it forever.

Nothing here touches the filesystem or the browser, so it is all unit-tested.
"""

import json
import re

# Fenced blocks, capturing the info string and the body. Non-greedy so two
# adjacent blocks stay two blocks.
_FENCE = re.compile(r"^```([^\n]*)\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)

_BACKTICK_RUN = re.compile(r"`+")

# Fence openers and closers, counted to spot a block that has not closed yet.
_FENCE_LINE = re.compile(r"^```", re.MULTILINE)


class ProtocolError(Exception):
    """The model's request could not be understood. The text is sent back to it."""


def extract_ops(reply):
    """Return the op list from `reply`, or None when the reply is the answer.

    Raises ProtocolError when a c2c block exists but does not hold a usable
    request - the caller turns that into one corrective turn.

    A reply may carry more than one block, and every tagged block is a request:
    returning on the first one dropped the rest without a word, so the model got
    results for half of what it asked and either re-asked or carried on believing
    it had data it never received.
    """
    ops = []
    untagged = []
    for match in _FENCE.finditer(reply or ""):
        tag = match.group(1).strip().lower()
        if tag == "c2c":
            ops.extend(_parse_request(match.group(2)))
            continue
        # ChatGPT puts the language in a header element beside the code, not on
        # the <code>, and that header lands late: sampled early, the same block
        # renders with no tag at all. An untagged block shaped exactly like a
        # request is one of ours, and losing it costs a whole round.
        if not tag and _looks_like_request(match.group(2)):
            untagged.append(match.group(2))
    if ops:
        # The tag landed, so the untagged blocks are the model's own JSON-shaped
        # prose, not a request whose label is still on its way. Serving them
        # would run ops nobody asked for; gather them only when nothing is tagged.
        return ops
    for raw in untagged:
        ops.extend(_parse_request(raw))
    return ops or None


def _looks_like_request(raw):
    """True only for the protocol's own shape: {"ops": [...]} with entries."""
    try:
        payload = json.loads(raw, strict=False)
    except ValueError:
        return False
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("ops"), list)
        and bool(payload["ops"])
    )


def is_still_rendering(reply):
    """True when `reply` looks like a half-drawn message rather than a finished one.

    ChatGPT rebuilds a code block while it streams: the body arrives in pieces
    and the language label is attached by a separate header element that lands
    late. Sampling in that window yields a closed ```c2c fence whose body is cut
    mid-token - measured repeatedly, e.g. `{"ops":[{"op":"git`. Treating that as
    the model's mistake burns a round and, three times over, the whole run.

    A finished request always parses. So: a c2c block that does not, or an odd
    number of fences, means keep waiting rather than keep asking.
    """
    text = reply or ""
    if len(_FENCE_LINE.findall(text)) % 2:
        return True
    for match in _FENCE.finditer(text):
        if match.group(1).strip().lower() != "c2c":
            continue
        try:
            json.loads(match.group(2), strict=False)
        except ValueError:
            return True
    return False


def is_blank_answer(reply):
    """True when `reply` carries no answer at all - only fences and whitespace.

    A reply with no c2c block is taken as the final answer, so an empty one
    ends the run and gets written to --out as if it were a result. That has
    happened: a malformed-block round was followed by a bare "```c\n\n```",
    which the loop accepted and saved as a ten-byte report.

    Fenced blocks count as content when they hold something; a fence with an
    empty body is as blank as no fence at all.
    """
    text = reply or ""
    for match in _FENCE.finditer(text):
        if match.group(2).strip():
            return False
    outside = _FENCE.sub(" ", text)
    return not _BACKTICK_RUN.sub(" ", outside).strip()


# Notices the page itself renders in place of an answer. They arrive as an
# assistant message like any other, so the loop reads them as the model's final
# word and ends the run reporting success. Twice in four dispatches that turned
# a dead run into an exit code of 0.
#
# Matched only in a short reply: a long answer that happens to quote one of
# these is discussing it, not suffering it. The list is deliberately literal -
# guessing at the shape of an error message ends good runs.
_PAGE_ERRORS = (
    "đã hết thời gian chờ gửi tin nhắn",
    "message send timed out",
    "something went wrong",
    "there was an error generating a response",
    "network error",
    "conversation not found",
    "bạn đã đạt giới hạn",
    "you've reached our limit",
)

PAGE_ERROR_CHARS = 300


def is_page_error(reply):
    """True when `reply` is the page reporting a failure, not the model answering.

    The caller re-asks rather than accepting it, because these are transient:
    the same prompt usually goes through on the next attempt.
    """
    text = (reply or "").strip()
    if not text or len(text) > PAGE_ERROR_CHARS:
        return False
    low = text.lower()
    return any(phrase in low for phrase in _PAGE_ERRORS)


def _snippet(raw, width=120):
    """A one-line excerpt of a bad block, so the failure can be diagnosed."""
    flat = " ".join((raw or "").split())
    return flat if len(flat) <= width else flat[:width] + "..."


def _parse_request(raw):
    # strict=False accepts real newlines and tabs inside strings. Models write
    # multi-line regexes that way constantly, and rejecting one costs a round -
    # two in a row end the run. Malformed structure is still rejected below.
    try:
        payload = json.loads(raw, strict=False)
    except ValueError as exc:
        raise ProtocolError(
            "the c2c block is not valid JSON (" + str(exc) + "): " + _snippet(raw)
        )

    if not isinstance(payload, dict):
        raise ProtocolError('the c2c block must be an object such as {"ops": [...]}')
    if "ops" not in payload:
        raise ProtocolError('the c2c block has no "ops" key')

    ops = payload["ops"]
    if not isinstance(ops, list):
        raise ProtocolError('"ops" must be a list')
    if not ops:
        raise ProtocolError('"ops" was empty - omit the block entirely to finish')

    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            raise ProtocolError("ops[" + str(index) + "] must be an object")
        if not op.get("op"):
            raise ProtocolError('ops[' + str(index) + '] has no "op" name')
    return ops


def fence_for(body):
    """Shortest fence that `body` cannot close from the inside."""
    longest = 0
    for run in _BACKTICK_RUN.finditer(body or ""):
        longest = max(longest, len(run.group(0)))
    return "`" * max(3, longest + 1)


def truncate(text, limit):
    """Cut `text` to `limit` characters, returning (body, note-or-None).

    The note matters as much as the cut: a model that knows it saw 100 of 500
    characters asks for the rest, while one that was silently trimmed reviews
    half a file and never says so.
    """
    text = text or ""
    if len(text) <= limit:
        return text, None
    note = (
        "truncated: showing "
        + str(limit)
        + " of "
        + str(len(text))
        + " characters - narrow the request to see the rest"
    )
    return text[:limit], note


class Budget:
    """How much workspace data one run may pour into the conversation.

    Two ceilings, because they fail differently. The per-round cap stops a
    single greedy turn - six ops each allowed 64 KB would be 384 KB in one
    message. The run total stops the slower death: every result served stays in
    the ChatGPT conversation forever, so eight polite rounds can reach the
    context limit just as surely as one rude one.

    Hitting that limit is worth preventing rather than detecting, because it
    does not announce itself. The model starts forgetting the protocol instead,
    which surfaces as a malformed request or a shallow answer - symptoms that
    point at the parser or the preset, nowhere near the real cause.
    """

    LOW_WATER = 0.2

    def __init__(self, total, per_round):
        self.total = total
        self.per_round = per_round
        self.spent = 0
        self._round_spent = 0

    def begin_round(self):
        self._round_spent = 0

    def allowance(self):
        """Characters still servable this round, under both ceilings."""
        return max(0, min(self.per_round - self._round_spent, self.total - self.spent))

    def charge(self, count):
        self.spent += count
        self._round_spent += count

    def exhausted(self):
        return self.spent >= self.total

    def low(self):
        return (self.total - self.spent) < self.total * self.LOW_WATER

    def notice(self):
        """A line for the model when the run is running out of room."""
        if not self.low():
            return None
        return (
            "Data budget: " + str(max(0, self.total - self.spent)) + " of "
            + str(self.total) + " characters left for this run. Narrow your "
            "requests, and finish soon."
        )


def format_results(results):
    """Render executed ops as the next turn's prompt.

    Each result is {"label", "lang", "body", "note"} or {"label", "error"}.
    """
    chunks = []
    for result in results:
        head = "## " + result["label"]
        if "error" in result:
            chunks.append(head + "\n\n" + result["error"])
            continue
        fence = fence_for(result.get("body", ""))
        block = fence + (result.get("lang") or "") + "\n" + result.get("body", "") + "\n" + fence
        note = result.get("note")
        chunks.append(head + "\n\n" + block + ("\n\n" + note if note else ""))
    return "\n\n".join(chunks)
