"""Metric collectors. One class per topic; each is sampled only while a client is subscribed."""

from __future__ import annotations

import sys
import time
from typing import Any, Protocol

import psutil


class Collector(Protocol):
    topic: str
    interval_s: float
    min_interval_s: float

    def unsupported_reason(self) -> str | None:
        """None when the collector can run on this machine, otherwise a short reason code."""

    def sample(self) -> dict[str, Any]:
        """Blocking call; the hub runs it in a worker thread."""


class _Base:
    interval_s = 1.0
    min_interval_s = 0.5

    def unsupported_reason(self) -> str | None:
        return None


class CpuCollector(_Base):
    topic = "system.cpu"

    def __init__(self) -> None:
        self._last = self._times()

    @staticmethod
    def _times() -> list[tuple[float, float]]:
        """(busy, total) seconds per core since boot."""
        return [(sum(t) - t.idle - getattr(t, "iowait", 0.0), sum(t)) for t in psutil.cpu_times(percpu=True)]

    def sample(self) -> dict[str, Any]:
        # Computed from our own baseline. psutil.cpu_percent keeps one baseline per calling thread,
        # which gives zeros when samples come from a thread pool.
        now, before = self._times(), self._last
        self._last = now
        per_core = [
            round(min(100.0, max(0.0, 100.0 * (b1 - b0) / (t1 - t0))), 1) if t1 > t0 else 0.0
            for (b0, t0), (b1, t1) in zip(before, now, strict=False)
        ]
        freq = psutil.cpu_freq()
        return {
            "percent": round(sum(per_core) / len(per_core), 1) if per_core else 0.0,
            "per_core": per_core,
            "freq_mhz": round(freq.current) if freq else None,
            "logical_cores": psutil.cpu_count(logical=True),
            "physical_cores": psutil.cpu_count(logical=False),
        }


class MemoryCollector(_Base):
    topic = "system.memory"

    def sample(self) -> dict[str, Any]:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        return {
            "total": vm.total,
            "used": vm.total - vm.available,
            "available": vm.available,
            "percent": vm.percent,
            "swap_total": sw.total,
            "swap_used": sw.used,
            "swap_percent": sw.percent,
        }


class _RateCollector(_Base):
    """Turns monotonically increasing counters into per-second rates."""

    def __init__(self) -> None:
        self._last: tuple[float, tuple[int, int]] | None = None

    def _rates(self, counters: tuple[int, int]) -> tuple[float, float]:
        now = time.monotonic()
        previous = self._last
        self._last = (now, counters)
        if previous is None or now - previous[0] > 10:  # no baseline, or one too old to be meaningful
            return 0.0, 0.0
        elapsed = max(now - previous[0], 1e-6)
        a = max(counters[0] - previous[1][0], 0) / elapsed
        b = max(counters[1] - previous[1][1], 0) / elapsed
        return round(a), round(b)


class NetworkCollector(_Base):
    """Total and per-adapter transfer rates."""

    topic = "system.network"

    def __init__(self) -> None:
        self._last: tuple[float, dict[str, tuple[int, int]]] | None = None

    def sample(self) -> dict[str, Any]:
        now = time.monotonic()
        counters = {
            n: (io.bytes_sent, io.bytes_recv) for n, io in psutil.net_io_counters(pernic=True).items()
        }
        previous = self._last
        self._last = (now, counters)
        fresh = previous is not None and now - previous[0] <= 10  # an older baseline is not meaningful
        elapsed = max(now - previous[0], 1e-6) if previous else 1.0
        nics = []
        for name, (sent, recv) in counters.items():
            if sent == 0 and recv == 0:
                continue  # adapter that has never carried traffic
            before = previous[1].get(name, (sent, recv)) if fresh and previous else (sent, recv)
            nics.append(
                {
                    "name": name,
                    "up_bps": round(max(sent - before[0], 0) / elapsed),
                    "down_bps": round(max(recv - before[1], 0) / elapsed),
                }
            )
        return {
            "up_bps": sum(n["up_bps"] for n in nics),
            "down_bps": sum(n["down_bps"] for n in nics),
            "total_sent": sum(c[0] for c in counters.values()),
            "total_recv": sum(c[1] for c in counters.values()),
            "nics": nics,
        }


class DiskIoCollector(_RateCollector):
    topic = "system.disk_io"

    def unsupported_reason(self) -> str | None:
        return None if psutil.disk_io_counters() is not None else "no_disk_counters"

    def sample(self) -> dict[str, Any]:
        io = psutil.disk_io_counters()
        read, write = self._rates((io.read_bytes, io.write_bytes))
        return {"read_bps": read, "write_bps": write}


class DiskCollector(_Base):
    topic = "system.disk"
    interval_s = 30.0
    min_interval_s = 5.0

    def sample(self) -> dict[str, Any]:
        volumes = []
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue  # drive not ready (empty card reader, locked volume)
            volumes.append(
                {
                    "mount": part.mountpoint,
                    "fs": part.fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "percent": usage.percent,
                }
            )
        return {"volumes": volumes}


class BatteryCollector(_Base):
    topic = "system.battery"
    interval_s = 30.0
    min_interval_s = 5.0

    def unsupported_reason(self) -> str | None:
        return None if psutil.sensors_battery() is not None else "no_battery"

    def sample(self) -> dict[str, Any]:
        b = psutil.sensors_battery()
        secs = (
            b.secsleft if b.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) else None
        )
        return {"percent": round(b.percent, 1), "plugged": b.power_plugged, "secs_left": secs}


class ProcessCollector(_Base):
    """Top processes by CPU. CPU share is computed from the change in CPU time between samples."""

    topic = "system.processes"
    interval_s = 5.0
    min_interval_s = 2.0
    limit = 15

    def __init__(self) -> None:
        self._cores = psutil.cpu_count(logical=True) or 1
        self._last_time = time.monotonic()
        self._last_cpu = {pid: cpu for pid, _, cpu, _ in self._list()}

    @staticmethod
    def _list() -> list[tuple[int, str, float, int]]:
        """(pid, name, cpu seconds so far, memory bytes) for every process."""
        if sys.platform == "win32":
            from app.platform.winproc import list_processes

            return list(list_processes())
        rows = []
        for proc in psutil.process_iter(["pid", "name", "cpu_times", "memory_info"]):
            info = proc.info
            if info["cpu_times"] is None or info["memory_info"] is None:
                continue  # access denied
            times = info["cpu_times"]
            rows.append(
                (info["pid"], info["name"] or "?", times.user + times.system, info["memory_info"].rss)
            )
        return rows

    def sample(self) -> dict[str, Any]:
        now = time.monotonic()
        rows = self._list()
        capacity = max(now - self._last_time, 1e-6) * self._cores  # CPU seconds the machine had available
        items = []
        for pid, name, cpu, mem in rows:
            before = self._last_cpu.get(pid)
            used = max(cpu - before, 0.0) if before is not None else 0.0  # new process: no baseline yet
            # 100 means the whole machine, like Task Manager
            items.append(
                {"pid": pid, "name": name, "cpu_percent": round(100 * used / capacity, 1), "mem_bytes": mem}
            )
        self._last_time = now
        self._last_cpu = {pid: cpu for pid, _, cpu, _ in rows}
        items.sort(key=lambda p: (p["cpu_percent"], p["mem_bytes"]), reverse=True)
        return {"sort": "cpu", "items": items[: self.limit]}


class GpuCollector(_Base):
    """NVIDIA only, through NVML. Needs the optional `nvidia-ml-py` package and an NVIDIA driver."""

    topic = "system.gpu"
    interval_s = 2.0
    min_interval_s = 1.0

    def __init__(self) -> None:
        self._nvml: Any = None
        self._reason: str | None = None
        try:
            import pynvml  # provided by nvidia-ml-py

            pynvml.nvmlInit()
            self._nvml = pynvml
        except ImportError:
            self._reason = "nvidia_ml_py_not_installed"
        except Exception:  # NVMLError: no driver or no NVIDIA GPU
            self._reason = "no_nvidia_gpu"

    def unsupported_reason(self) -> str | None:
        return self._reason

    def _optional(self, fn: Any, *args: Any) -> Any:
        try:
            return fn(*args)
        except self._nvml.NVMLError:
            return None  # this card does not report the value

    def sample(self) -> dict[str, Any]:
        n = self._nvml
        gpus = []
        for index in range(n.nvmlDeviceGetCount()):
            handle = n.nvmlDeviceGetHandleByIndex(index)
            name = n.nvmlDeviceGetName(handle)
            util = self._optional(n.nvmlDeviceGetUtilizationRates, handle)
            mem = self._optional(n.nvmlDeviceGetMemoryInfo, handle)
            power_mw = self._optional(n.nvmlDeviceGetPowerUsage, handle)
            gpus.append(
                {
                    "index": index,
                    "name": name.decode() if isinstance(name, bytes) else name,
                    "util_percent": util.gpu if util else None,
                    "mem_used": mem.used if mem else None,
                    "mem_total": mem.total if mem else None,
                    "temp_c": self._optional(n.nvmlDeviceGetTemperature, handle, n.NVML_TEMPERATURE_GPU),
                    "power_w": round(power_mw / 1000, 1) if power_mw is not None else None,
                }
            )
        return {"gpus": gpus}


class MediaCollector(_Base):
    """Volume level and what is playing. Either half may be missing; the other still works."""

    topic = "media.state"
    interval_s = 2.0
    min_interval_s = 1.0

    def __init__(self) -> None:
        from app.platform import winmedia

        self._media = winmedia
        self._volume = winmedia.volume_supported()
        self._playing = winmedia.now_playing_supported()

    def unsupported_reason(self) -> str | None:
        return None if self._volume or self._playing else "audio_extra_not_installed"

    def sample(self) -> dict[str, Any]:
        volume = self._media.get_volume() if self._volume else None
        track = self._media.now_playing() if self._playing else None
        return {
            "volume": volume[0] if volume else None,
            "muted": volume[1] if volume else None,
            "title": track["title"] if track else None,
            "artist": track["artist"] if track else None,
            "album": track["album"] if track else None,
            "app": track["app"] if track else None,
            "playing": track["playing"] if track else False,
            "position_s": track["position_s"] if track else None,
            "duration_s": track["duration_s"] if track else None,
            "art": track["art"] if track else None,  # changes with the cover; fetch it from /media/art
        }


def system_collectors(include_processes: bool = True) -> list[Collector]:
    collectors: list[Collector] = [
        CpuCollector(),
        MemoryCollector(),
        NetworkCollector(),
        DiskIoCollector(),
        DiskCollector(),
        BatteryCollector(),
        GpuCollector(),
        MediaCollector(),
    ]
    if include_processes:
        collectors.append(ProcessCollector())
    return collectors
