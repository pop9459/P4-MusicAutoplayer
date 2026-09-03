from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from src.mpv_backend import MpvBackend, MpvUnavailableError


class FakeSocket:
    """Stand-in for a unix socket that echoes canned IPC responses.

    Each call to sendall() decodes the JSON command, looks up a response in
    ``self.responses`` keyed by the command name, and buffers it so the next
    recv() call returns it framed with a trailing newline.
    """

    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses
        self.sent_commands: list[list[object]] = []
        self._pending = b""

    def connect(self, _path: str) -> None:
        return None

    def settimeout(self, _seconds: float) -> None:
        return None

    def sendall(self, data: bytes) -> None:
        message = json.loads(data.decode("utf-8"))
        command = message["command"]
        self.sent_commands.append(command)
        key = command[0]
        response = dict(self.responses.get(key, {"error": "success", "data": None}))
        response["request_id"] = message["request_id"]
        self._pending += (json.dumps(response) + "\n").encode("utf-8")

    def recv(self, _size: int) -> bytes:
        chunk, self._pending = self._pending, b""
        return chunk

    def close(self) -> None:
        return None


def _make_backend(responses: dict[str, dict]) -> tuple[MpvBackend, FakeSocket]:
    fake_socket = FakeSocket(responses)
    fake_process = MagicMock()
    fake_process.poll.return_value = None

    with patch("src.mpv_backend.shutil.which", return_value="/usr/bin/mpv"), \
         patch("src.mpv_backend.subprocess.Popen", return_value=fake_process), \
         patch("src.mpv_backend.socket.socket", return_value=fake_socket):
        backend = MpvBackend()
    return backend, fake_socket


class MpvUnavailableTests(unittest.TestCase):
    def test_raises_when_mpv_binary_missing(self) -> None:
        with patch("src.mpv_backend.shutil.which", return_value=None):
            with self.assertRaises(MpvUnavailableError):
                MpvBackend()


class MpvBackendIpcTests(unittest.TestCase):
    def test_load_file_sends_loadfile_and_unpauses(self) -> None:
        backend, fake_socket = _make_backend({})
        backend.load_file("/music/track.mp3")

        self.assertEqual(fake_socket.sent_commands[0], ["loadfile", "/music/track.mp3", "replace"])
        self.assertEqual(fake_socket.sent_commands[1], ["set_property", "pause", False])

    def test_pause_and_resume_send_expected_commands(self) -> None:
        backend, fake_socket = _make_backend({})
        backend.pause()
        backend.resume()

        self.assertEqual(fake_socket.sent_commands[0], ["set_property", "pause", True])
        self.assertEqual(fake_socket.sent_commands[1], ["set_property", "pause", False])

    def test_toggle_pause_flips_current_state(self) -> None:
        responses = {"get_property": {"error": "success", "data": False}}
        backend, fake_socket = _make_backend(responses)

        new_state = backend.toggle_pause()

        self.assertTrue(new_state)
        self.assertEqual(fake_socket.sent_commands[-1], ["set_property", "pause", True])

    def test_is_finished_true_when_idle_with_no_path(self) -> None:
        backend, _ = _make_backend({})
        with patch.object(backend, "get_property", side_effect=[True, None]):
            self.assertTrue(backend.is_finished())

    def test_is_finished_false_when_process_running_and_not_idle(self) -> None:
        backend, _ = _make_backend({})
        with patch.object(backend, "get_property", side_effect=[False, "/music/track.mp3"]):
            self.assertFalse(backend.is_finished())

    def test_shutdown_sends_quit_and_terminates_process(self) -> None:
        backend, fake_socket = _make_backend({})
        with patch("pathlib.Path.unlink") as mock_unlink:
            backend.shutdown()

        self.assertEqual(fake_socket.sent_commands[-1], ["quit"])
        mock_unlink.assert_called_once()


if __name__ == "__main__":
    unittest.main()
