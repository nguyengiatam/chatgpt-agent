#!/usr/bin/env python3
"""The operations the model is allowed to request.

By default every op here only reads: files, directories, search, git history,
and the GitNexus graph when one exists. File paths go through `resolve_path`,
which every read is routed through and no prompt can talk its way past.

A caller that passes `--allow-shell` adds one more op that runs real commands
on the machine, as the user who started the run. That exists because some
questions - does this test suite actually catch this bug? - cannot be answered
by reading. It is off unless asked for, every command is printed before it
runs, and `shell_objection` refuses work that is not a reviewer's. Those
refusals bound the role, not the blast radius; see the note above them.

So the honest summary is conditional: with shell off, an instruction hidden in
a repository cannot reach a capability that does not exist. With shell on, it
can, and the operator chose that trade.
"""

import json
import os
import re
import subprocess

from chatgpt_protocol import truncate

MAX_CHARS = 64000
SEARCH_LIMIT = 50

ALLOWED_OPS = (
    "read", "list", "search", "git_diff", "git_log", "git_show",
    "graph_status", "impact", "context", "trace", "graph_query", "detect_changes",
)

# Directories that are never worth a model's context budget.
SKIP_DIRS = frozenset([
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".mypy_cache", ".pytest_cache", ".ruff_cache",
])

_KEY_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
_KEY_PREFIXES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
_SECRET_NAMES = frozenset([
    ".netrc", ".npmrc", ".pypirc", ".htpasswd", "credentials", ".git-credentials",
])

# Git refs and paths arrive from the model. We never use a shell, but git parses
# a leading dash as an option, and some of those options execute programs.
_SAFE_REF = re.compile(r"^[A-Za-z0-9._/~^@{}-]+$")

EXTENSION_LANGS = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".ts": "typescript",
    ".tsx": "tsx", ".jsx": "jsx", ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".java": "java", ".kt": "kotlin", ".swift": "swift", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cs": "csharp", ".php": "php", ".sh": "bash", ".zsh": "bash",
    ".sql": "sql", ".json": "json", ".yml": "yaml", ".yaml": "yaml",
    ".toml": "toml", ".md": "markdown", ".html": "html", ".css": "css",
}


class OpError(Exception):
    """A request that must not be served. The reason goes back to the model."""


# --- the fence -------------------------------------------------------------


def _is_blocked(relative):
    """True when any component of `relative` names a secret or git internals."""
    for part in relative.split(os.sep):
        if not part or part == ".":
            continue
        lowered = part.lower()
        if lowered.startswith(".env"):
            return True
        if lowered == ".git" or lowered in _SECRET_NAMES:
            return True
        if lowered.startswith(_KEY_PREFIXES):
            return True
        if lowered.endswith(_KEY_SUFFIXES):
            return True
    return False


def resolve_path(root, candidate):
    """Absolute path for `candidate`, guaranteed inside `root` and not secret.

    realpath runs before the containment check on purpose: a symlink inside the
    repo pointing out of it is the one escape a string comparison would miss.
    """
    if candidate is None:
        raise OpError('missing "path"')
    root = os.path.realpath(root)
    joined = candidate if os.path.isabs(candidate) else os.path.join(root, candidate)
    real = os.path.realpath(joined)

    if real != root and not real.startswith(root + os.sep):
        raise OpError("blocked: " + str(candidate) + " resolves outside the workspace")

    relative = os.path.relpath(real, root)
    if _is_blocked(relative):
        raise OpError("blocked: " + str(candidate) + " is a secret or git-internal path")
    return real


# --- helpers ---------------------------------------------------------------


def number_lines(text, start=1):
    """Prefix each line with its number, so the model can cite exact lines."""
    lines = (text or "").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    width = len(str(start + len(lines) - 1)) if lines else 1
    out = []
    for offset, line in enumerate(lines):
        out.append(str(start + offset).rjust(width) + "| " + line)
    return "\n".join(out)


def _lang_for(path):
    return EXTENSION_LANGS.get(os.path.splitext(path)[1].lower(), "")


def _run(root, argv, ok_codes=(0,)):
    """Run `argv` in `root` with no shell. Returns (ok, output).

    `ok_codes` exists for ripgrep, which exits 1 to mean "searched fine, found
    nothing" - a result, not a failure.
    """
    try:
        proc = subprocess.run(
            argv, cwd=root, capture_output=True, text=True, errors="replace"
        )
    except OSError as exc:
        return False, str(exc)
    if proc.returncode in ok_codes:
        return True, proc.stdout
    if proc.stdout:
        return True, proc.stdout
    return False, (proc.stderr or "").strip() or ("exit " + str(proc.returncode))


def _safe_ref(value, label):
    if value is None:
        return None
    text = str(value)
    if text.startswith("-") or not _SAFE_REF.match(text):
        raise OpError("blocked: unusable " + label + " " + repr(text))
    return text


def _ok(label, body, lang="", note=None):
    result = {"label": label, "lang": lang, "body": body}
    if note:
        result["note"] = note
    return result


# --- shell, when the caller opts in ----------------------------------------
#
# These patterns bound the ROLE, not the blast radius. A reviewer copies a
# tree, seeds a mutation and runs a test suite; it has no business deleting
# trees, escalating, publishing, reaching another host or reading keys. Saying
# no to those catches a model that drifts or blunders.
#
# They are not a security boundary and must never be described as one. Anything
# expressible in a shell can be spelled another way, and the command already
# runs as the user who started the tool. The real control is that the whole op
# is absent unless --allow-shell was passed, and that every command is printed
# before it runs.

SHELL_TIMEOUT = 120
SHELL_TIMEOUT_MAX = 900

_OUT_OF_ROLE = [
    (r"\brm\s+-[a-zA-Z]*[rR]", "recursive delete"),
    (r"\bmkfs|\bdd\s+[^;&|]*\bof=|>\s*/dev/", "writing to a device"),
    (r"\bsudo\b|\bdoas\b|\bsu\s+-", "privilege escalation"),
    (r"\bshutdown\b|\breboot\b|\bhalt\b|\bkillall\b|\bcrontab\b|\blaunchctl\b",
     "controlling the machine"),
    (r"\.ssh/id_|\.aws/credentials|\.netrc\b|security\s+find-\w+-password",
     "reading credentials"),
    (r"\b(curl|wget)\b[^|]*\|\s*(ba|z|k)?sh\b", "piping the network into a shell"),
    # ssh must sit in command position, so that a path like ~/.ssh/config
    # is left to the credentials rule above rather than mislabelled here.
    (r"(?:^|[\s;&|(])ssh\s|\bscp\s|\bsftp\s", "reaching another host"),
    (r"\bgit\s+push\b|\bnpm\s+publish\b|\byarn\s+publish\b|\bgh\s+(release|pr)\s",
     "publishing"),
    (r"\bgit\s+reset\s+--hard\b|\bgit\s+clean\b|\bgit\s+checkout\s+--force\b",
     "destroying local work"),
]

_OUT_OF_ROLE = [(re.compile(pattern, re.IGNORECASE), why) for pattern, why in _OUT_OF_ROLE]


def shell_objection(cmd):
    """Why this command is not a reviewer's to run, or None.

    Matched against the whole string, so a refusal hidden behind `&&` is still
    caught. Not a sandbox - see the note above.
    """
    text = " " + " ".join((cmd or "").split())
    for pattern, why in _OUT_OF_ROLE:
        if pattern.search(text):
            return (
                "refused: " + why + " is out of scope for a review. Work in a copy "
                "and keep to reading, editing that copy, and running its tests."
            )
    return None


def _op_shell(root, op, limit):
    cmd = op.get("cmd")
    if not cmd:
        raise OpError('missing "cmd"')
    objection = shell_objection(str(cmd))
    if objection:
        raise OpError(objection)

    # cwd is not fenced, and pretending otherwise would be theatre: the command
    # string can `cd`. A copy of the tree elsewhere is the point of the op.
    where = op.get("cwd")
    cwd = os.path.realpath(os.path.expanduser(str(where))) if where else os.path.realpath(root)
    if not os.path.isdir(cwd):
        raise OpError("no such directory: " + str(where))

    seconds = min(int(op.get("timeout") or SHELL_TIMEOUT), SHELL_TIMEOUT_MAX)
    try:
        proc = subprocess.run(
            str(cmd), shell=True, cwd=cwd, capture_output=True,
            text=True, errors="replace", timeout=seconds,
        )
    except subprocess.TimeoutExpired:
        raise OpError("timed out after " + str(seconds) + "s: " + str(cmd))
    except OSError as exc:
        raise OpError("could not run: " + str(exc))

    output = (proc.stdout or "") + (proc.stderr or "")
    body, note = truncate(output.strip() or "(no output)", limit)
    label = "shell (exit " + str(proc.returncode) + "): " + " ".join(str(cmd).split())[:120]
    return _ok(label, body, "", note)


# --- operations ------------------------------------------------------------


def _op_read(root, op, limit):
    path = resolve_path(root, op.get("path"))
    if not os.path.isfile(path):
        raise OpError("not a file: " + str(op.get("path")))
    with open(path, "r", errors="replace") as handle:
        text = handle.read()

    start = int(op.get("start") or 1)
    end = op.get("end")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    total = len(lines)
    end = total if end is None else int(end)
    start = max(1, start)
    end = min(total, end)
    selected = "\n".join(lines[start - 1:end])

    body, note = truncate(number_lines(selected, start), limit)
    label = "read " + str(op.get("path"))
    if start != 1 or end != total:
        label += " (lines " + str(start) + "-" + str(end) + " of " + str(total) + ")"
    return _ok(label, body, _lang_for(path), note)


def _op_list(root, op, limit):
    path = resolve_path(root, op.get("path") or ".")
    if not os.path.isdir(path):
        raise OpError("not a directory: " + str(op.get("path")))
    entries = []
    for name in sorted(os.listdir(path)):
        if name in SKIP_DIRS or _is_blocked(name):
            continue
        entries.append(name + "/" if os.path.isdir(os.path.join(path, name)) else name)
    body = "\n".join(entries) if entries else "(empty)"
    body, note = truncate(body, limit)
    return _ok("list " + str(op.get("path") or "."), body, "", note)


def _op_search(root, op, limit):
    pattern = op.get("pattern")
    if not pattern:
        raise OpError('missing "pattern"')
    max_hits = min(int(op.get("max") or SEARCH_LIMIT), SEARCH_LIMIT)

    argv = ["rg", "--line-number", "--no-heading", "--color", "never",
            "--max-count", "5", "-e", str(pattern)]
    if op.get("glob"):
        argv += ["--glob", str(op["glob"])]
    ok, output = _run(root, argv, ok_codes=(0, 1))
    if not ok:
        # ripgrep missing: fall back rather than fail, so the tool works
        # on a machine that never installed it.
        ok, output = _grep_fallback(root, str(pattern))
    if not ok:
        raise OpError("search failed: " + output)

    hits = [line for line in output.split("\n") if line.strip()]
    hits = [h for h in hits if not _is_blocked(h.split(":", 1)[0])]
    shown, extra = hits[:max_hits], max(0, len(hits) - max_hits)
    body = "\n".join(shown) if shown else "no matches"
    note = ("showing " + str(max_hits) + " of " + str(len(hits)) + " hits") if extra else None
    body, cut = truncate(body, limit)
    return _ok('search "' + str(pattern) + '"', body, "", note or cut)


def _grep_fallback(root, pattern):
    """Used only when ripgrep is absent; keeps the tool working without it."""
    try:
        needle = re.compile(pattern)
    except re.error as exc:
        return False, "bad pattern: " + str(exc)
    found = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not _is_blocked(d)]
        for name in files:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root)
            if _is_blocked(rel):
                continue
            try:
                with open(full, "r", errors="replace") as handle:
                    for number, line in enumerate(handle, 1):
                        if needle.search(line):
                            found.append(rel + ":" + str(number) + ":" + line.rstrip())
            except OSError:
                continue
    return True, "\n".join(found)


def _git(root, op, argv, label, limit):
    ok, output = _run(root, argv)
    if not ok:
        raise OpError(label + " failed: " + output)
    body, note = truncate(output.strip() or "(no output)", limit)
    return _ok(label, body, "diff" if "diff" in label else "", note)


def _op_git_diff(root, op, limit):
    argv = ["git", "diff"]
    ref = _safe_ref(op.get("range"), "range")
    if ref:
        argv.append(ref)
    argv.append("--")
    if op.get("path"):
        argv.append(os.path.relpath(resolve_path(root, op["path"]), os.path.realpath(root)))
    return _git(root, op, argv, "git diff " + (ref or "(working tree)"), limit)


def _op_git_log(root, op, limit):
    count = str(min(int(op.get("n") or 20), 100))
    argv = ["git", "log", "--oneline", "-n", count, "--"]
    if op.get("path"):
        argv.append(os.path.relpath(resolve_path(root, op["path"]), os.path.realpath(root)))
    return _git(root, op, argv, "git log -n " + count, limit)


def _op_git_show(root, op, limit):
    ref = _safe_ref(op.get("ref"), "ref")
    if not ref:
        raise OpError('missing "ref"')
    argv = ["git", "show", ref, "--"]
    if op.get("path"):
        argv.append(os.path.relpath(resolve_path(root, op["path"]), os.path.realpath(root)))
    return _git(root, op, argv, "git show " + ref, limit)


# --- knowledge graph, when one has been built ------------------------------
#
# GitNexus answers in one call what search-and-read takes several rounds to
# approximate: who calls this, what breaks if it changes, how these two symbols
# connect. On a repository of any size that is the difference between a review
# that found the call sites and one that hoped it had.
#
# Optional by design. The index is per-repository and often absent, so every op
# here degrades to a plain explanation rather than an error the model cannot
# act on - `graph_status` exists so it can check before it commits to a plan.

GITNEXUS_BIN = os.environ.get("GITNEXUS_BIN", "gitnexus")

# Symbol names and search phrases, not options: a leading dash would be read by
# the CLI as a flag.
_SAFE_ARG = re.compile(r"^[A-Za-z0-9_.:/\\ ()\[\]@#-]{1,200}$")


def _safe_arg(value, label):
    text = str(value or "").strip()
    if not text:
        raise OpError('missing "' + label + '"')
    if text.startswith("-") or not _SAFE_ARG.match(text):
        raise OpError("blocked: unusable " + label + " " + repr(value))
    return text


_NO_GRAPH = (
    "no GitNexus index answers for this repository, so graph ops cannot help "
    "here. Use search, read and the git ops instead."
)

# gitnexus exits 0 whatever happens, prints a pino log line about the FTS
# extension on every call, and reports real failures as a JSON object in
# stdout. So the exit code says nothing and the output has to be read.
_GITNEXUS_NOISE = re.compile(r'^\s*(\{"level":\d+|GitNexus \w+ \(\d)')


def _gitnexus_clean(output):
    kept = [
        line for line in (output or "").splitlines()
        if line.strip() and not _GITNEXUS_NOISE.match(line)
    ]
    return "\n".join(kept).strip()


def _gitnexus(root, argv, label, limit):
    ok, output = _run(root, [GITNEXUS_BIN] + argv)
    if not ok:
        lowered = output.lower()
        if "no such file" in lowered or "not found" in lowered or "enoent" in lowered:
            raise OpError(
                "gitnexus is not installed, so graph ops are unavailable for this "
                "run. Use search and read instead."
            )
        raise OpError(label + " failed: " + output)

    cleaned = _gitnexus_clean(output)
    lowered = cleaned.lower()
    if "not indexed" in lowered or "not a git repository" in lowered:
        raise OpError(_NO_GRAPH)
    if cleaned.startswith("{") and '"error"' in cleaned:
        try:
            detail = json.loads(cleaned).get("error") or cleaned
        except ValueError:
            detail = cleaned
        raise OpError(label + ": " + str(detail))

    body, note = truncate(cleaned or "(no output)", limit)
    return _ok(label, body, "", note)


def _op_graph_status(root, op, limit):
    return _gitnexus(root, ["status"], "graph status", limit)


def _op_impact(root, op, limit):
    target = _safe_arg(op.get("symbol") or op.get("target"), "symbol")
    return _gitnexus(root, ["impact", target], "impact " + target, limit)


def _op_context(root, op, limit):
    name = _safe_arg(op.get("symbol") or op.get("name"), "symbol")
    return _gitnexus(root, ["context", name], "context " + name, limit)


def _op_trace(root, op, limit):
    source = _safe_arg(op.get("from"), "from")
    target = _safe_arg(op.get("to"), "to")
    return _gitnexus(root, ["trace", source, target], "trace " + source + " -> " + target, limit)


def _op_graph_query(root, op, limit):
    phrase = _safe_arg(op.get("query"), "query")
    return _gitnexus(root, ["query", phrase], 'graph query "' + phrase + '"', limit)


def _op_detect_changes(root, op, limit):
    return _gitnexus(root, ["detect-changes"], "detect changes", limit)


_HANDLERS = {
    "graph_status": _op_graph_status,
    "impact": _op_impact,
    "context": _op_context,
    "trace": _op_trace,
    "graph_query": _op_graph_query,
    "detect_changes": _op_detect_changes,
    "read": _op_read,
    "list": _op_list,
    "search": _op_search,
    "git_diff": _op_git_diff,
    "git_log": _op_git_log,
    "git_show": _op_git_show,
}


def available_ops(allow_shell=False):
    """The op names a run offers, which is what the model is told about."""
    return ALLOWED_OPS + (("shell",) if allow_shell else ())


def execute(root, op, limit=None, allow_shell=False):
    """Run one op, always returning a result dict - never raising at the caller.

    `limit` is the caller's remaining data budget for this round; it only ever
    shrinks the per-op ceiling, never raises it.

    `allow_shell` decides whether the shell op exists at all. Left off, it is
    not a refused op but an unknown one, and the model is never told about it.
    """
    limit = MAX_CHARS if limit is None else min(int(limit), MAX_CHARS)
    name = str(op.get("op") or "")
    handler = _op_shell if (name == "shell" and allow_shell) else _HANDLERS.get(name)
    if handler is None:
        return {
            "label": name or "(no op)",
            "error": "unknown op " + repr(name) + "; allowed: "
                     + ", ".join(available_ops(allow_shell)),
        }
    try:
        return handler(root, op, limit)
    except OpError as exc:
        return {"label": name + " " + str(op.get("path") or op.get("pattern") or ""), "error": str(exc)}
    except (OSError, ValueError) as exc:
        return {"label": name, "error": type(exc).__name__ + ": " + str(exc)}
