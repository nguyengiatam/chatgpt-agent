#!/usr/bin/env python3
"""The read-only operations the model is allowed to request.

There is no write and no shell here, by construction rather than by policy: a
prompt-injected instruction cannot reach a capability that does not exist. What
remains - reading and searching - is fenced by `resolve_path`, which every op
goes through and no prompt can talk its way past.
"""

import os
import re
import subprocess

from chatgpt_protocol import truncate

MAX_CHARS = 64000
SEARCH_LIMIT = 50

ALLOWED_OPS = ("read", "list", "search", "git_diff", "git_log", "git_show")

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


_HANDLERS = {
    "read": _op_read,
    "list": _op_list,
    "search": _op_search,
    "git_diff": _op_git_diff,
    "git_log": _op_git_log,
    "git_show": _op_git_show,
}


def execute(root, op, limit=None):
    """Run one op, always returning a result dict - never raising at the caller.

    `limit` is the caller's remaining data budget for this round; it only ever
    shrinks the per-op ceiling, never raises it.
    """
    limit = MAX_CHARS if limit is None else min(int(limit), MAX_CHARS)
    name = str(op.get("op") or "")
    handler = _HANDLERS.get(name)
    if handler is None:
        return {
            "label": name or "(no op)",
            "error": "unknown op " + repr(name) + "; allowed: " + ", ".join(ALLOWED_OPS),
        }
    try:
        return handler(root, op, limit)
    except OpError as exc:
        return {"label": name + " " + str(op.get("path") or op.get("pattern") or ""), "error": str(exc)}
    except (OSError, ValueError) as exc:
        return {"label": name, "error": type(exc).__name__ + ": " + str(exc)}
