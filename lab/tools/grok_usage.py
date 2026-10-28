#!/usr/bin/env python3
"""Read the Grok subscription allowance without spending any of it.

`grok dashboard` has no session, so its /usage modal shows only the account's weekly limit and makes no model
call. This drives the TUI in a pseudo-terminal, renders it with pyte, prints the gauge lines and quits.

Usage: uv run --no-project --with pyte python grok_usage.py
"""
import fcntl, os, pty, re, select, struct, termios, time

import pyte

COLS, ROWS = 160, 50
pid, fd = pty.fork()
if pid == 0:
    os.environ["TERM"] = "xterm-256color"
    os.chdir("/tmp")  # inside a repo the dashboard can open on a folder prompt instead
    os.execvp("grok", ["grok", "dashboard"])
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
screen = pyte.Screen(COLS, ROWS)
stream = pyte.ByteStream(screen)


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        if select.select([fd], [], [], 0.2)[0]:
            try:
                stream.feed(os.read(fd, 65536))
            except OSError:
                return


def text():
    return "\n".join(line.rstrip() for line in screen.display)


pump(6)
for ch in "/usage":  # typed slowly: the slash menu filters as it goes
    os.write(fd, ch.encode())
    time.sleep(0.08)
pump(1.5)
os.write(fd, b"\r")  # opens the show/manage submenu
pump(3)
os.write(fd, b"\r")  # "show"
found = ""
for _ in range(8):
    pump(2)
    if "Resets" in text():
        found = text()
        break
for ch in (b"\x1b", b"\x03", b"\x03"):
    os.write(fd, ch)
    pump(0.5)
try:
    os.kill(pid, 9)
except ProcessLookupError:
    pass
if not found:
    raise SystemExit("grok_usage: the usage modal did not appear")
lines = [re.sub(r"[│░▓█▒■]+", " ", l).strip() for l in found.splitlines()]
print(" | ".join(l for l in lines if re.search(r"limit \(|%|Resets", l)))
