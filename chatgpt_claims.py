#!/usr/bin/env python3
"""Kernel-held ownership claims for ChatGPT tabs and conversations."""

import fcntl
import hashlib
import json
import os
import time
from urllib.parse import urlsplit


CLAIM_DIR = os.environ.get(
    "CHATGPT_AGENT_CLAIM_DIR",
    os.path.join(os.path.expanduser("~"), ".chatgpt-agent", "claims"),
)


class ClaimBusy(RuntimeError):
    """The requested tab or conversation is owned by another live process."""


def conversation_id(url):
    """Return the stable id from a ChatGPT /c/<id> URL, or None."""
    if not isinstance(url, str):
        return None
    try:
        parts = [part for part in urlsplit(url).path.split("/") if part]
    except ValueError:
        return None
    if len(parts) >= 2 and parts[0] == "c" and parts[1]:
        return parts[1]
    return None


def _claim_path(kind, value):
    digest = hashlib.sha256((kind + "\0" + str(value)).encode("utf-8")).hexdigest()
    return os.path.join(CLAIM_DIR, kind + "-" + digest + ".lock")


def _read_metadata(handle):
    try:
        handle.seek(0)
        raw = handle.read()
        value = json.loads(raw) if raw else {}
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _describe_holder(metadata):
    owner = metadata.get("owner") or "another process"
    pid = metadata.get("pid")
    acquired = metadata.get("acquired")
    details = [str(owner)]
    if pid is not None:
        details.append("pid " + str(pid))
    if isinstance(acquired, (int, float)):
        details.append("held " + str(max(0, int(time.time() - acquired))) + "s")
    return ", ".join(details)


# A claim belongs to the process, not to the object that asked for it.
#
# flock() attaches to an open file description, so a second open() of the same
# path - even from the same process - is a rival and is refused. That is wrong
# here: one process is never its own competitor, and only another process is.
# Claims are therefore registered once per process and reference-counted, so
# asking twice is free and the last release is the one that lets a rival in.
_HELD = {}


def _acquire(kind, value, owner):
    key = (kind, str(value))
    entry = _HELD.get(key)
    if entry is not None:
        entry[1] += 1
        return key

    os.makedirs(CLAIM_DIR, exist_ok=True)
    handle = open(_claim_path(*key), "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        metadata = _read_metadata(handle)
        handle.close()
        raise ClaimBusy(
            kind + " " + repr(str(value)) + " is already claimed by "
            + _describe_holder(metadata)
        )

    handle.seek(0)
    handle.truncate()
    json.dump({"owner": str(owner), "pid": os.getpid(), "acquired": time.time(),
               "kind": kind, "value": str(value)}, handle, sort_keys=True)
    handle.flush()
    _HELD[key] = [handle, 1]
    return key


def _release(key):
    entry = _HELD.get(key)
    if entry is None:
        return
    entry[1] -= 1
    if entry[1] > 0:
        return
    handle = entry[0]
    del _HELD[key]
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


class ClaimSet:
    """One run's claims, held from before its first send until it ends.

    Nothing here judges whether a rival is alive. The kernel releases a flock
    when its holder dies by any means, including SIGKILL, so a claim can never
    outlive its owner and no contender is ever asked to decide whether a lock
    it cannot read belongs to a live process. Metadata inside the file is for
    the refusal message and carries no authority.
    """

    def __init__(self, owner):
        self.owner = str(owner)
        self._keys = []

    def claim_tab(self, tab_id):
        self._keys.append(_acquire("tab", int(tab_id), self.owner))

    def claim_conversation(self, identity):
        if identity:
            self._keys.append(_acquire("conversation", str(identity), self.owner))

    def release_tab(self, tab_id):
        """Release this run's claim on one vanished tab while keeping the rest."""
        key = ("tab", str(int(tab_id)))
        for index in range(len(self._keys) - 1, -1, -1):
            if self._keys[index] == key:
                self._keys.pop(index)
                _release(key)
                return

    def close(self):
        for key in reversed(self._keys):
            _release(key)
        self._keys = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False



def _unheld_claims(remove):
    """Claim files the kernel says nobody holds; optionally unlink them safely."""
    try:
        names = sorted(name for name in os.listdir(CLAIM_DIR) if name.endswith(".lock"))
    except OSError:
        return []
    available = []
    for name in names:
        path = os.path.join(CLAIM_DIR, name)
        try:
            handle = open(path, "a+", encoding="utf-8")
        except OSError:
            continue
        locked = False
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except BlockingIOError:
                continue
            available.append(name)
            if remove:
                try:
                    os.unlink(path)
                except OSError:
                    available.pop()
        finally:
            if locked:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
    return available


def list_unheld_claims():
    """Return abandoned claim filenames without deleting them."""
    return _unheld_claims(False)


def prune_unheld_claims():
    """Remove abandoned claim files, rechecking the kernel lock at deletion time."""
    return _unheld_claims(True)
