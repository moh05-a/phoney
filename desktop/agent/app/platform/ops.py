"""Everything that touches the operating system directly. The only OS-specific module in the agent."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Protocol


class PlatformError(Exception):
    """An OS operation could not be performed. `reason` is a short code safe to show on the phone."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PlatformOps(Protocol):
    def press_key(self, key: str) -> None: ...
    def lock(self) -> None: ...
    def power(self, mode: str) -> None: ...
    def launch(self, program: str, args: list[str], cwd: Path | None) -> None: ...
    def run(self, program: str, args: list[str], cwd: Path | None, timeout_s: int) -> tuple[int, str]: ...
    def open_target(self, target: str) -> None: ...
    def volume_supported(self) -> bool: ...
    def set_volume(self, level: int) -> None: ...


def resolve_program(program: str) -> str:
    found = shutil.which(program)
    if found is None:
        raise PlatformError("program_not_found")
    return found


class _WindowsVolume:
    def volume_supported(self) -> bool:
        from app.platform import winmedia

        return winmedia.volume_supported()

    def set_volume(self, level: int) -> None:
        from app.platform import winmedia

        winmedia.set_volume(level)


class WindowsOps(_WindowsVolume):
    _KEYS = {
        "media.next": 0xB0,
        "media.prev": 0xB1,
        "media.stop": 0xB2,
        "media.play_pause": 0xB3,
        "volume.mute": 0xAD,
        "volume.down": 0xAE,
        "volume.up": 0xAF,
    }
    _POWER = {
        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "restart": ["shutdown.exe", "/r", "/t", "5"],
        "shutdown": ["shutdown.exe", "/s", "/t", "5"],
    }

    def press_key(self, key: str) -> None:
        import ctypes

        vk = self._KEYS[key]
        user32 = ctypes.windll.user32
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP

    def lock(self) -> None:
        import ctypes

        if not ctypes.windll.user32.LockWorkStation():
            raise PlatformError("lock_failed")

    def power(self, mode: str) -> None:
        subprocess.Popen(self._POWER[mode], creationflags=subprocess.CREATE_NO_WINDOW)

    def launch(self, program: str, args: list[str], cwd: Path | None) -> None:
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(
                [resolve_program(program), *args],
                cwd=cwd,
                creationflags=flags,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as exc:
            raise PlatformError("launch_failed") from exc

    def run(self, program: str, args: list[str], cwd: Path | None, timeout_s: int) -> tuple[int, str]:
        try:
            done = subprocess.run(
                [resolve_program(program), *args],
                cwd=cwd,
                timeout=timeout_s,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise PlatformError("timeout") from exc
        except OSError as exc:
            raise PlatformError("launch_failed") from exc
        return done.returncode, (done.stdout or "")[-600:]

    def open_target(self, target: str) -> None:
        try:
            os.startfile(target)  # noqa: S606 - target comes from the PC-side allowlist only
        except OSError as exc:
            raise PlatformError("open_failed") from exc


class UnsupportedOps:
    """Placeholder for operating systems the agent does not control yet. Monitoring still works."""

    def _no(self, *_: object, **__: object) -> None:
        raise PlatformError("unsupported_os")

    press_key = lock = power = launch = open_target = set_volume = _no

    def volume_supported(self) -> bool:
        return False

    def run(self, *_: object, **__: object) -> tuple[int, str]:
        raise PlatformError("unsupported_os")


def default_platform() -> PlatformOps:
    return WindowsOps() if sys.platform == "win32" else UnsupportedOps()
