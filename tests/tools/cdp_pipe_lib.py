#!/usr/bin/env python3
"""Shared CDP-over-pipe infrastructure for Browser CLI Gate A harnesses.

Extracted from the validated tab/window harness so multiple scenarios (tab,
window, window.open popup, ...) reuse the same launch path:

  * launches the signed app with `--remote-debugging-pipe` and passes the pipe
    fds as fd 3 (Chrome reads) / fd 4 (Chrome writes) using NUL-terminated JSON;
  * always passes `--use-mock-keychain` and a per-run `--user-data-dir=`;
  * provides a browser-PID on-screen window sampler (ObjC helper) and small
    Runtime.evaluate / attach helpers.

Safety: never uses `open`/Dock/Launchpad, never orders a window on screen,
creates only throwaway profiles (caller-provided or tempfile.mkdtemp()).

The fd-inheritance fix is critical: CPython runs preexec_fn BEFORE closing fds,
so `close_fds=False` is required or fd 3/4 get closed and Chrome aborts with
"Remote debugging pipe file descriptors are not open".
"""

import json
import os
import select
import subprocess
import tempfile
import threading
import time


class CDPError(Exception):
    pass


class PipeCDP:
    """Minimal CDP client over the --remote-debugging-pipe ASCII-Z protocol."""

    def __init__(self, read_fd, write_fd):
        self._read = os.fdopen(read_fd, "rb", buffering=0)
        self._write = os.fdopen(write_fd, "wb", buffering=0)
        self._buffer = b""
        self._next_id = 1

    def _read_message(self, timeout):
        deadline = time.time() + timeout
        while True:
            nul = self._buffer.find(b"\0")
            if nul != -1:
                raw = self._buffer[:nul]
                self._buffer = self._buffer[nul + 1:]
                if not raw:
                    continue
                return json.loads(raw.decode("utf-8"))
            remaining = deadline - time.time()
            if remaining <= 0:
                raise CDPError("timeout waiting for CDP message")
            ready, _, _ = select.select([self._read], [], [], remaining)
            if not ready:
                raise CDPError("timeout waiting for CDP message")
            chunk = os.read(self._read.fileno(), 65536)
            if not chunk:
                raise CDPError("CDP pipe closed")
            self._buffer += chunk

    def request(self, method, params=None, session_id=None, timeout=30.0):
        message_id = self._next_id
        self._next_id += 1
        message = {"id": message_id, "method": method}
        if params is not None:
            message["params"] = params
        if session_id is not None:
            message["sessionId"] = session_id
        self._write.write(json.dumps(message).encode("utf-8") + b"\0")
        while True:
            reply = self._read_message(timeout)
            if reply.get("id") != message_id:
                continue  # ignore events and other replies
            if "error" in reply:
                raise CDPError("%s -> %s" % (method, reply["error"]))
            return reply.get("result", {})

    def close(self):
        try:
            self._read.close()
        except OSError:
            pass
        try:
            self._write.close()
        except OSError:
            pass


class WindowSampler:
    """Samples on-screen windows owned by the browser PID via the ObjC helper."""

    def __init__(self, helper, pid, interval=0.5):
        self.helper = helper
        self.pid = str(pid)
        self.interval = interval
        self.samples = []
        self.max = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                out = subprocess.check_output(
                    [self.helper, self.pid], stderr=subprocess.DEVNULL, timeout=5
                ).decode().strip()
                count = int(out)
            except (subprocess.SubprocessError, ValueError):
                count = -1
            self.samples.append(count)
            if count > self.max:
                self.max = count
            self._stop.wait(self.interval)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)
        return self.max


def pipe_preexec(read_fd, write_fd):
    def _preexec():
        dup_read = os.dup(read_fd)
        dup_write = os.dup(write_fd)
        os.dup2(dup_read, 3)
        os.dup2(dup_write, 4)
        os.close(dup_read)
        os.close(dup_write)
    return _preexec


def default_profile():
    """Isolated throwaway profile for callers that do not supply one."""
    return tempfile.mkdtemp(prefix="browser-cli-cdp-")


def launch_app(app, profile, log_path, extra_args=None, disable_extensions=True,
               no_startup_window=True):
    """Launch the signed app with the shared pipe/mock-keychain/profile hygiene.

    `disable_extensions` defaults to True (previous harnesses keep exactly the
    same command line). Extension-scenario harnesses pass False so they can use
    `--disable-extensions-except=<dir>` + `--load-extension=<dir>` to load only
    their own unpacked fixture; the three flags are mutually redundant/confusing
    and must never be combined.

    `no_startup_window` defaults to True. Session-restore scenarios pass False
    for the restore phase: `StartupBrowserCreator::ShouldLoadProfileWithoutWindow`
    skips window/restore startup entirely when the switch is present, so the
    restore relaunch must NOT use it (the restored windows are still suppressed
    by the kernel presentation policy).
    """
    log = open(log_path, "wb")
    cmd = [
        app,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
    ]
    if no_startup_window:
        cmd.append("--no-startup-window")
    if disable_extensions:
        cmd.append("--disable-extensions")
    cmd += [
        "--use-mock-keychain",
        "--user-data-dir=" + profile,
        "--remote-debugging-pipe",
        "--enable-logging=stderr",
        "--browser-cli-background-presentation",
    ]
    cmd.extend(extra_args or [])
    read_r, read_w = os.pipe()    # Chrome reads fd 3 from read_r
    write_r, write_w = os.pipe()  # Chrome writes fd 4 to write_w
    # NOTE: close_fds=False is required. CPython runs preexec_fn *before*
    # closing fds, so with close_fds=True (or pass_fds) the dup2'ed fds 3/4 are
    # closed again before exec and Chrome aborts with "Remote debugging pipe
    # file descriptors are not open". The app is launched before any sampler
    # thread starts, so preexec_fn is safe here.
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        preexec_fn=pipe_preexec(read_r, write_w),
        close_fds=False,
    )
    os.close(read_r)
    os.close(write_w)
    cdp = PipeCDP(write_r, read_w)
    cmdline = ""
    try:
        cmdline = subprocess.check_output(
            ["ps", "-ww", "-o", "command=", "-p", str(proc.pid)], timeout=5
        ).decode().strip()
    except subprocess.SubprocessError:
        pass
    return proc, cdp, cmdline


def stop_app(proc):
    if proc.poll() is not None:
        return proc.returncode
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    return proc.returncode


def evaluate(cdp, session_id, expression, timeout, user_gesture=False,
             await_promise=False):
    params = {"expression": expression, "returnByValue": True}
    if user_gesture:
        params["userGesture"] = True
    if await_promise:
        params["awaitPromise"] = True
    result = cdp.request(
        "Runtime.evaluate",
        params,
        session_id=session_id,
        timeout=timeout,
    )
    if result.get("exceptionDetails"):
        raise CDPError("Runtime.evaluate failed: %s" % result["exceptionDetails"])
    return result.get("result", {}).get("value")


def find_target(cdp, needle, timeout, target_type=""):
    """Poll Target.getTargets until a target URL contains `needle`."""
    deadline = time.time() + timeout
    while True:
        infos = cdp.request("Target.getTargets", timeout=timeout).get("targetInfos", [])
        for info in infos:
            if needle in info.get("url", "") and (
                not target_type or info.get("type") == target_type
            ):
                return info
        if time.time() >= deadline:
            return None
        time.sleep(0.2)


def wait_for_page(cdp, session_id, timeout):
    """Wait until the page finished loading and the marker element has content."""
    deadline = time.time() + timeout
    while True:
        state = evaluate(cdp, session_id, "document.readyState", timeout)
        marker = evaluate(
            cdp,
            session_id,
            "document.getElementById('marker')"
            " ? document.getElementById('marker').textContent : ''",
            timeout,
        )
        if state == "complete" and marker:
            return
        if time.time() >= deadline:
            raise CDPError("page did not finish loading within %.0fs" % timeout)
        time.sleep(0.2)


def attach(cdp, target_id, timeout):
    attached = cdp.request(
        "Target.attachToTarget", {"targetId": target_id, "flatten": True}, timeout=timeout
    )
    return attached["sessionId"]


def inspect_target(cdp, target_id, timeout):
    session_id = attach(cdp, target_id, timeout)
    wait_for_page(cdp, session_id, timeout)
    info = {
        "sessionId": session_id,
        "title": evaluate(cdp, session_id, "document.title", timeout),
        "href": evaluate(cdp, session_id, "location.href", timeout),
        "body_marker": evaluate(
            cdp, session_id, "document.getElementById('marker').textContent", timeout
        ),
        "has_focus": evaluate(cdp, session_id, "document.hasFocus()", timeout),
        "visibility_state": evaluate(
            cdp, session_id, "document.visibilityState", timeout
        ),
    }
    cdp.request("Target.detachFromTarget", {"sessionId": session_id}, timeout=timeout)
    return info
