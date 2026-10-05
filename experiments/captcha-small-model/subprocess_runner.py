"""Bound subprocess execution separately from draining inherited stdout pipes."""
from __future__ import annotations

import os
from pathlib import Path
import selectors
import signal
import subprocess
import time


def kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_logged_process(command, log: Path, timeout: float, drain_seconds: float = 2, echo: bool = True):
    if timeout <= 0 or drain_seconds <= 0:
        raise ValueError("process and pipe-drain limits must be positive")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               bufsize=0, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    exited_at = killed_at = None
    timed_out = drain_truncated = False
    try:
        with Path(log).open("x", encoding="utf-8") as output:
            while selector.get_map() or process.poll() is None:
                now = time.monotonic()
                returncode = process.poll()
                if returncode is not None and exited_at is None:
                    exited_at = now
                if exited_at is not None and now - exited_at >= drain_seconds:
                    # Compiler children can keep the pipe open after a successful
                    # parent exit. This is not a timeout of the benchmark process.
                    drain_truncated = bool(selector.get_map())
                    kill_group(process)
                    break
                if returncode is None and now >= deadline and not timed_out:
                    timed_out = True
                    killed_at = now
                    kill_group(process)
                if killed_at is not None and now - killed_at >= drain_seconds:
                    break
                for key, _ in selector.select(0.1):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    text = block.decode("utf-8", errors="replace")
                    output.write(text)
                    output.flush()
                    if echo:
                        print(text, end="", flush=True)
            returncode = process.wait(timeout=drain_seconds)
            if timed_out:
                output.write(f"\nTIMEOUT after {timeout} seconds\n")
            elif drain_truncated:
                output.write(f"\nSTDOUT_DRAIN_LIMIT after parent exit ({drain_seconds} seconds)\n")
        return {"returncode": returncode, "timed_out": timed_out,
                "timeout_seconds": timeout, "stdout_drain_truncated": drain_truncated}
    finally:
        selector.close()
        process.stdout.close()
        # Dispose of inherited-pipe children, and of a parent if log I/O failed.
        kill_group(process)
        if process.poll() is None:
            process.wait(timeout=drain_seconds)
