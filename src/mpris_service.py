"""org.mpris.MediaPlayer2 D-Bus service so desktop media keys (Play/Pause,
Next, Previous, Stop) can control playback even when the TUI's terminal
isn't OS-focused -- this is the standard mechanism Linux desktop
environments use to route hardware media keys to the "now playing"
application.

Runs dbus-next's asyncio event loop on a background daemon thread. That
thread never touches curses/mpv/`PlayerEngine` directly: D-Bus method calls
push a requested action onto a lock-guarded `MprisActionQueue`, drained once
per `run_loop` tick by `Player3Column._poll_mpris_task`, which applies it
through the same methods a keypress would use. Read-only MPRIS properties
are served from a lock-guarded `MprisState` mirror, updated once per tick
from the main thread -- the same "small copied snapshot" pattern already
used by `PlayerEngine.queue_snapshot()`/`ScanTask.progress()`.
"""
from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import dataclass, field

try:
    from dbus_next import PropertyAccess, Variant
    from dbus_next.aio import MessageBus
    from dbus_next.service import ServiceInterface, dbus_property, method

    DBUS_AVAILABLE = True
except ImportError:
    DBUS_AVAILABLE = False

BUS_NAME = "org.mpris.MediaPlayer2.musicautoplayer"
OBJECT_PATH = "/org/mpris/MediaPlayer2"


@dataclass
class MprisState:
    """Lock-guarded mirror of player state, updated once per `run_loop` tick
    from the main thread, read by the D-Bus thread for `PlaybackStatus`/
    `Metadata` queries."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    playing: bool = False
    has_track: bool = False
    title: str = ""
    artist: str = ""
    track_id: str = ""
    position_seconds: float = 0.0

    def update(
        self,
        *,
        playing: bool,
        has_track: bool,
        title: str,
        artist: str,
        track_id: str,
        position_seconds: float = 0.0,
    ) -> None:
        with self.lock:
            self.playing = playing
            self.has_track = has_track
            self.title = title
            self.artist = artist
            self.track_id = track_id
            self.position_seconds = position_seconds

    def snapshot(self) -> "MprisState":
        with self.lock:
            return MprisState(
                playing=self.playing,
                has_track=self.has_track,
                title=self.title,
                artist=self.artist,
                track_id=self.track_id,
                position_seconds=self.position_seconds,
            )


@dataclass
class MprisActionQueue:
    """Thread-safe FIFO of requested actions ("play_pause"/"play"/"pause"/
    "next"/"previous"/"stop"), pushed by D-Bus method calls on the asyncio
    thread, drained by `Player3Column._poll_mpris_task` on the main thread."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    _pending: list[str] = field(default_factory=list)

    def push(self, action: str) -> None:
        with self.lock:
            self._pending.append(action)

    def drain(self) -> list[str]:
        with self.lock:
            pending, self._pending = self._pending, []
            return pending


def _sanitize_track_id(track_id: str) -> str:
    """A D-Bus object path segment may only contain [A-Za-z0-9_]."""
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", track_id)
    return sanitized or "none"


if DBUS_AVAILABLE:

    class _RootInterface(ServiceInterface):
        """Minimal `org.mpris.MediaPlayer2` interface -- required by the
        spec, but this player has no window to raise and no quit action of
        its own (the TUI owns its own lifecycle)."""

        def __init__(self) -> None:
            super().__init__("org.mpris.MediaPlayer2")

        @method()
        def Raise(self):  # noqa: N802 (MPRIS method names are CamelCase)
            pass

        @method()
        def Quit(self):  # noqa: N802
            pass

        @dbus_property(access=PropertyAccess.READ)
        def CanQuit(self) -> "b":  # noqa: N802
            return False

        @dbus_property(access=PropertyAccess.READ)
        def CanRaise(self) -> "b":  # noqa: N802
            return False

        @dbus_property(access=PropertyAccess.READ)
        def Identity(self) -> "s":  # noqa: N802
            return "Music Autoplayer"

        @dbus_property(access=PropertyAccess.READ)
        def DesktopEntry(self) -> "s":  # noqa: N802
            # Non-standard but widely queried by desktop shells (e.g. GNOME
            # Shell's media controls); we have no .desktop file, so report
            # empty rather than let it come back as an unknown-property error.
            return ""

        @dbus_property(access=PropertyAccess.READ)
        def HasTrackList(self) -> "b":  # noqa: N802
            return False

        @dbus_property(access=PropertyAccess.READ)
        def SupportedUriSchemes(self) -> "as":  # noqa: N802
            return []

        @dbus_property(access=PropertyAccess.READ)
        def SupportedMimeTypes(self) -> "as":  # noqa: N802
            return []

    class _PlayerInterface(ServiceInterface):
        """`org.mpris.MediaPlayer2.Player` interface. Method calls only
        enqueue an action; they never touch playback state directly since
        this runs on the D-Bus thread, not the curses main thread."""

        def __init__(self, state: MprisState, actions: MprisActionQueue) -> None:
            super().__init__("org.mpris.MediaPlayer2.Player")
            self._state = state
            self._actions = actions

        @method()
        def PlayPause(self):  # noqa: N802
            self._actions.push("play_pause")

        @method()
        def Play(self):  # noqa: N802
            self._actions.push("play")

        @method()
        def Pause(self):  # noqa: N802
            self._actions.push("pause")

        @method()
        def Stop(self):  # noqa: N802
            self._actions.push("stop")

        @method()
        def Next(self):  # noqa: N802
            self._actions.push("next")

        @method()
        def Previous(self):  # noqa: N802
            self._actions.push("previous")

        @dbus_property(access=PropertyAccess.READ)
        def PlaybackStatus(self) -> "s":  # noqa: N802
            state = self._state.snapshot()
            if not state.has_track:
                return "Stopped"
            return "Playing" if state.playing else "Paused"

        @dbus_property(access=PropertyAccess.READ)
        def Metadata(self) -> "a{sv}":  # noqa: N802
            state = self._state.snapshot()
            track_id = _sanitize_track_id(state.track_id)
            return {
                "mpris:trackid": Variant("o", f"{OBJECT_PATH}/Track/{track_id}"),
                "xesam:title": Variant("s", state.title),
                "xesam:artist": Variant("as", [state.artist] if state.artist else []),
            }

        @dbus_property(access=PropertyAccess.READ)
        def Position(self) -> "x":  # noqa: N802
            # Desktop media widgets (e.g. GNOME Shell's quick-settings
            # player card) query this unconditionally, regardless of
            # CanSeek -- an unimplemented property here isn't just
            # inaccurate, it's a crash: dbus-next's default properties
            # handler raises DBusError on an unknown property name, and
            # that traceback lands on stderr, which shares this terminal
            # with curses. MPRIS reports Position in microseconds.
            state = self._state.snapshot()
            return int(state.position_seconds * 1_000_000)

        @dbus_property(access=PropertyAccess.READ)
        def CanPlay(self) -> "b":  # noqa: N802
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanPause(self) -> "b":  # noqa: N802
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanGoNext(self) -> "b":  # noqa: N802
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanGoPrevious(self) -> "b":  # noqa: N802
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanSeek(self) -> "b":  # noqa: N802
            return False

        @dbus_property(access=PropertyAccess.READ)
        def CanControl(self) -> "b":  # noqa: N802
            return True


class MprisService:
    """Owns the background thread running the D-Bus service. Safe to
    construct and `start()` even without a D-Bus session/`dbus-next`
    installed -- failures are caught and leave `self.available` False
    rather than raising, so `Player3Column` construction (used directly by
    dozens of tests with a mocked backend and no real curses/D-Bus) never
    blocks, hangs, or crashes."""

    def __init__(self) -> None:
        self.state = MprisState()
        self.actions = MprisActionQueue()
        self.available = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not DBUS_AVAILABLE:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception:
            # No session bus, name already taken, dbus-next missing pieces,
            # etc. -- fail soft, media-key control just won't be available.
            self.available = False

    async def _async_main(self) -> None:
        bus = await MessageBus().connect()
        bus.export(OBJECT_PATH, _RootInterface())
        bus.export(OBJECT_PATH, _PlayerInterface(self.state, self.actions))
        await bus.request_name(BUS_NAME)
        self.available = True
        await asyncio.Event().wait()  # run until the process exits (daemon thread)

    def shutdown(self) -> None:
        # Daemon thread with no cross-thread asyncio cancellation wired up;
        # process exit reaps it. Known simplification for v1.
        pass


def start_mpris_service() -> MprisService:
    service = MprisService()
    service.start()
    return service
