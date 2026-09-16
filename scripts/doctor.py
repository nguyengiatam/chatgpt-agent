#!/usr/bin/env python3
"""Check every prerequisite and say exactly which one is missing.

This tool has more hard requirements than most - an operating system, a
specific browser, two permissions granted by hand, and a signed-in tab. Each
one fails in its own way and some of those failures look alike from the
outside, so they are checked here one at a time rather than left for a user to
untangle from a single error message.

Exit 0 when everything needed to run is in place.
"""

import os
import platform
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import importlib.util

_spec = importlib.util.spec_from_file_location("cgpt", os.path.join(ROOT, "chatgpt-cli.py"))
cgpt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cgpt)

EDGE_APP = "/Applications/Microsoft Edge.app"


class Check:
    def __init__(self, name):
        self.name = name
        self.ok = False
        self.detail = ""
        self.fix = ""
        self.fatal = True


def _report(check):
    mark = "✓" if check.ok else ("✗" if check.fatal else "-")
    line = "  " + mark + " " + check.name
    if check.detail:
        line += ": " + check.detail
    print(line)
    if not check.ok and check.fix:
        for fix_line in check.fix.split("\n"):
            print("      " + fix_line)


def check_macos():
    c = Check("macOS")
    c.ok = platform.system() == "Darwin"
    c.detail = platform.system() + " " + platform.release()
    c.fix = "This tool drives the browser through Apple Events. There is no\nequivalent on Linux or Windows."
    return c


def check_edge_installed():
    c = Check("Microsoft Edge installed")
    c.ok = os.path.isdir(EDGE_APP)
    c.detail = EDGE_APP if c.ok else "not found"
    c.fix = "Install Microsoft Edge. Only Edge is supported; the AppleScript\nvocabulary is resolved against it at compile time."
    return c


def check_edge_running():
    c = Check("Edge is running")
    try:
        found = subprocess.run(
            ["pgrep", "-f", "MacOS/Microsoft Edge"], capture_output=True, text=True
        ).returncode == 0
    except OSError:
        found = False
    c.ok = found
    c.detail = "yes" if found else "no process"
    c.fix = "Open Microsoft Edge and leave it open."
    return c


def check_automation():
    c = Check("Terminal may control Edge")
    try:
        cgpt.bridge("list")
        c.ok = True
        c.detail = "granted"
    except cgpt.CliError as exc:
        c.detail = str(exc).split("\n")[0]
        c.fix = "System Settings > Privacy & Security > Automation >\nyour terminal app > enable Microsoft Edge."
    return c


def check_apple_events_js(tab):
    c = Check("Allow JavaScript from Apple Events")
    if tab is None:
        c.detail = "skipped - no tab to test on"
        return c
    try:
        value = cgpt.bridge("eval", "1+1", tab[0], tab[1])
        c.ok = value.strip() == "2"
        c.detail = "enabled" if c.ok else "unexpected reply " + repr(value[:40])
    except cgpt.CliError as exc:
        c.detail = str(exc).split("\n")[0]
    c.fix = "In Edge's menu bar: View > Developer >\nAllow JavaScript from Apple Events."
    return c


def check_chatgpt_tab():
    c = Check("A ChatGPT tab is open")
    try:
        tabs = cgpt.parse_tabs(cgpt.bridge("list"))
    except cgpt.CliError:
        c.detail = "could not list tabs"
        return c, None
    found = cgpt.find_chatgpt_tab(tabs)
    c.ok = found is not None
    c.detail = "window " + str(found[0]) + " tab " + str(found[1]) if found else "none"
    c.fix = "Open https://chatgpt.com/ in Edge. The tool will also open one\nitself, but signing in has to happen by hand."
    return c, found


def check_signed_in(tab):
    c = Check("Signed in to ChatGPT")
    if tab is None:
        c.detail = "skipped - no ChatGPT tab"
        return c
    try:
        probe = cgpt.eval_js(tab[0], tab[1], "CGPT.probe()")
        c.ok = bool(probe.get("ok"))
        c.detail = "composer ready" if c.ok else str(probe.get("error"))
    except cgpt.CliError as exc:
        c.detail = str(exc).split("\n")[0]
    c.fix = "Sign in to ChatGPT in that tab. Automated sign-in is blocked by\ndesign and this tool does not attempt it."
    return c


def check_ripgrep():
    c = Check("ripgrep (optional)")
    c.fatal = False
    try:
        c.ok = subprocess.run(["rg", "--version"], capture_output=True).returncode == 0
    except OSError:
        c.ok = False
    c.detail = "found" if c.ok else "missing - a slower Python scan is used instead"
    c.fix = "brew install ripgrep"
    return c


def main():
    print("chatgpt-agent doctor\n")
    checks = [check_macos(), check_edge_installed(), check_edge_running(), check_automation()]

    tab = None
    if checks[-1].ok:
        tab_check, tab = check_chatgpt_tab()
        checks.append(tab_check)
        checks.append(check_apple_events_js(tab))
        checks.append(check_signed_in(tab if checks[-1].ok else None))
    checks.append(check_ripgrep())

    for check in checks:
        _report(check)

    broken = [c for c in checks if c.fatal and not c.ok]
    print("")
    if broken:
        print("Not ready: " + str(len(broken)) + " requirement(s) unmet.")
        return 1
    print("Ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
