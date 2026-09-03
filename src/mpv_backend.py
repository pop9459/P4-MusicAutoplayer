"""Minimal mpv subprocess + JSON IPC wrapper used by the demo TUI player.

mpv is launched once in idle mode with a unix socket IPC server. Track
changes, pause/resume, and status polling are all done by sending small
JSON commands over that socket rather than killing/restarting the process,
so playback state (like pause) survives across commands.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path


class MpvUnavailableError(RuntimeError):
    """Raised when the mpv binary cannot be found on PATH."""


class MpvBackend:
    """Controls a single background mpv process over its JSON IPC socket."""

    def __init__(self, mpv_path: str | None = None, startup_timeout: float = 5.0) -> None:
        self._mpv_path = mpv_path or shutil.which("mpv")
        if not self._mpv_path:
            raise MpvUnavailableError("mpv binary not found on PATH. Install mpv to use the player.")

        self._socket_path = Path(tempfile.gettempdir()) / f"mplayer-ipc-{uuid.uuid4().hex}.sock"
        self._process = subprocess.Popen(
            [
                self._mpv_path,
                "--idle=yes",
                "--no-video",
                "--no-terminal",
                f"--input-ipc-server={self._socket_path}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._sock: socket.socket | None = None
        self._request_id = 0
        try:
            self._connect(startup_timeout)
        except MpvUnavailableError:
            self._force_cleanup()
            raise

    def _force_cleanup(self) -> None:
        """Best-effort teardown used when startup fails partway through."""
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._socket_path.unlink(missing_ok=True)

    def _connect(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise MpvUnavailableError("mpv process exited before it became ready.")
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(str(self._socket_path))
                sock.settimeout(1.0)
                self._sock = sock
                return
            except OSError as error:
                last_error = error
                time.sleep(0.05)
        raise MpvUnavailableError(f"Timed out connecting to mpv IPC socket: {last_error}")

    def _send(self, command: list[object]) -> dict[str, object]:
        if self._sock is None:
            raise MpvUnavailableError("mpv IPC socket is not connected.")
        self._request_id += 1
        payload = json.dumps({"command": command, "request_id": self._request_id}) + "\n"
        self._sock.sendall(payload.encode("utf-8"))
        return self._read_response(self._request_id)

    def _read_response(self, request_id: int) -> dict[str, object]:
        buffer = b""
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                chunk = self._sock.recv(4096) if self._sock else b""
            except socket.timeout:
                continue
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line:
                    continue
                message = json.loads(line.decode("utf-8"))
                if message.get("request_id") == request_id:
                    return message
        raise MpvUnavailableError("No response received from mpv IPC socket.")

    def load_file(self, path: str) -> None:
        self._send(["loadfile", path, "replace"])
        self._send(["set_property", "pause", False])

    def pause(self) -> None:
        self._send(["set_property", "pause", True])

    def resume(self) -> None:
        self._send(["set_property", "pause", False])

    def toggle_pause(self) -> bool:
        """Flip the pause state and return the resulting paused flag."""
        currently_paused = self.get_property("pause", default=False)
        new_state = not bool(currently_paused)
        self._send(["set_property", "pause", new_state])
        return new_state

    def get_property(self, name: str, default: object = None) -> object:
        response = self._send(["get_property", name])
        if response.get("error") == "success":
            return response.get("data")
        return default

    def is_finished(self) -> bool:
        """True once the currently loaded file has finished playing (idle, not paused-at-start)."""
        if self._process.poll() is not None:
            return True
        idle_active = self.get_property("idle-active", default=False)
        path_loaded = self.get_property("path", default=None)
        return bool(idle_active) and path_loaded is None

    def stop(self) -> None:
        self._send(["stop"])

    def shutdown(self) -> None:
        try:
            if self._sock is not None:
                self._send(["quit"])
        except MpvUnavailableError:
            pass
        finally:
            self._force_cleanup()

    def __enter__(self) -> "MpvBackend":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()
