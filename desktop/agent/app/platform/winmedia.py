"""Volume level and now-playing information on Windows.

Both parts are optional (`pip install -e ".[audio]"`):
- volume uses Core Audio through `pycaw`;
- now playing uses the system media session (the same source as the Windows volume flyout) through `winrt`.
Every function degrades to "not available" when its package is missing.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.platform.ops import PlatformError

log = logging.getLogger("phoney.media")
MAX_ART_BYTES = 2_000_000


def _endpoint() -> Any:
    """The default speaker's volume interface. COM must be initialized on each thread that uses it."""
    import comtypes
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    return AudioUtilities.GetSpeakers().EndpointVolume


def volume_supported() -> bool:
    if sys.platform != "win32":
        return False
    try:
        _endpoint()
    except Exception:  # package missing, or no audio output device
        return False
    return True


def get_volume() -> tuple[int, bool] | None:
    """(level 0-100, muted), or None when it cannot be read."""
    try:
        endpoint = _endpoint()
        return round(endpoint.GetMasterVolumeLevelScalar() * 100), bool(endpoint.GetMute())
    except Exception:
        return None


def set_volume(level: int) -> None:
    try:
        endpoint = _endpoint()
        endpoint.SetMasterVolumeLevelScalar(max(0, min(100, level)) / 100, None)
        if level > 0 and endpoint.GetMute():
            endpoint.SetMute(0, None)  # moving the slider up is a clear request to hear something
    except Exception as exc:
        raise PlatformError("volume_unavailable") from exc


def now_playing_supported() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winrt.windows.media.control  # noqa: F401
    except ImportError:
        return False
    return True


def _app_name(model_id: str) -> str:
    name = model_id.rsplit("!", 1)[-1].rsplit("\\", 1)[-1]
    return name.removesuffix(".exe").removesuffix(".EXE")[:40]


def progress(
    position_s: float, start_s: float, end_s: float, updated: datetime | None, playing: bool
) -> tuple:
    """(position, duration) in seconds, or (None, None) when the player reports no timeline.

    Players report the position only now and then, with the time they measured it, so while a track
    is playing the real position is the reported one plus the time that has passed since.
    """
    duration = end_s - start_s
    if duration <= 0:
        return None, None
    position = position_s - start_s
    if playing and updated is not None and updated.year > 2000:
        position += (datetime.now(UTC) - updated).total_seconds()
    return round(max(0.0, min(position, duration)), 1), round(duration, 1)


# The cover of the current track, kept so the panel can fetch it once per track rather than per sample.
_art: dict[str, Any] = {"key": None, "id": None, "bytes": None}


def current_art() -> tuple[bytes, str] | None:
    """(image bytes, media type) for the track last reported by now_playing()."""
    data = _art["bytes"]
    if not data:
        return None
    return data, "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


async def _read_thumbnail(reference: Any) -> bytes | None:
    from winrt.windows.storage.streams import DataReader

    stream = await reference.open_read_async()
    size = int(stream.size)
    if not 0 < size <= MAX_ART_BYTES:
        return None
    reader = DataReader(stream.get_input_stream_at(0))
    await reader.load_async(size)
    return bytes(reader.read_buffer(size))


async def _now_playing() -> dict[str, Any] | None:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager,
    )
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
    )

    session = (await Manager.request_async()).get_current_session()
    if session is None:
        return None
    properties = await session.try_get_media_properties_async()
    playing = session.get_playback_info().playback_status == Status.PLAYING
    title, artist = properties.title or "", properties.artist or ""

    key = (session.source_app_user_model_id, title, artist, properties.album_title)
    if key != _art["key"]:  # new track: read its cover once
        _art.update(key=key, id=None, bytes=None)
        try:
            data = await _read_thumbnail(properties.thumbnail) if properties.thumbnail else None
        except Exception:
            log.info("cover art could not be read for the current track", exc_info=True)
            data = None
        if data:
            _art.update(id=hashlib.sha1(data, usedforsecurity=False).hexdigest()[:12], bytes=data)

    position = duration = None
    try:
        timeline = session.get_timeline_properties()
        position, duration = progress(
            timeline.position.total_seconds(),
            timeline.start_time.total_seconds(),
            timeline.end_time.total_seconds(),
            timeline.last_updated_time,
            playing,
        )
    except Exception:
        log.debug("no timeline for the current track", exc_info=True)
    return {
        "title": title[:120],
        "artist": artist[:120],
        "album": (properties.album_title or "")[:120],
        "app": _app_name(session.source_app_user_model_id or ""),
        "playing": playing,
        "position_s": position,
        "duration_s": duration,
        "art": _art["id"],
    }


# The media-session calls complete through callbacks that never arrive on a thread where COM was set
# up for the volume interface, so they get a thread of their own. One worker also keeps calls in order.
_worker = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="media-session")
QUERY_TIMEOUT_S = 4


def now_playing() -> dict[str, Any] | None:
    """What the current media session is playing, or None when nothing is registered."""
    try:
        track = _worker.submit(lambda: asyncio.run(asyncio.wait_for(_now_playing(), QUERY_TIMEOUT_S))).result(
            QUERY_TIMEOUT_S + 1
        )
    except Exception:
        log.debug("media session query failed", exc_info=True)
        return None
    if track is None:
        _art.update(key=None, id=None, bytes=None)
    return track
