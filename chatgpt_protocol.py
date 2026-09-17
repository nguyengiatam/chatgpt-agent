#!/usr/bin/env python3
"""The c2c wire format: how ChatGPT asks for data and how results go back.

One turn carries at most one request. The model emits a fenced block tagged
`c2c` holding {"ops": [...]}; a reply with no such block is the final answer.
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


class ProtocolError(Exception):
    """The model's request could not be understood. The text is sent back to it."""


def extract_ops(reply):
    """Return the op list from `reply`, or None when the reply is the answer.

    Raises ProtocolError when a c2c block exists but does not hold a usable
    request - the caller turns that into one corrective turn.
    """
    for match in _FENCE.finditer(reply or ""):
        if match.group(1).strip().lower() != "c2c":
            continue
        return _parse_request(match.group(2))
    return None


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
