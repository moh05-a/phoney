"""Fast process listing for Windows.

psutil reads CPU time and memory one process at a time, and for every process the current user may
not open (most services) it falls back to enumerating the whole system. With a few hundred processes
that costs over a second of CPU per sample. Windows can return everything in a single call, which
is what Task Manager does; this module makes that call through ctypes.
"""

from __future__ import annotations

import ctypes
from ctypes import c_long, c_longlong, c_size_t, c_ulong, c_ulonglong, c_ushort, c_void_p
from typing import NamedTuple

SYSTEM_PROCESS_INFORMATION = 5
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004


class ProcessSample(NamedTuple):
    pid: int
    name: str
    cpu_seconds: float  # user + kernel time since the process started
    mem_bytes: int  # private working set, the "Memory" column in Task Manager


class _UnicodeString(ctypes.Structure):
    _fields_ = [("Length", c_ushort), ("MaximumLength", c_ushort), ("Buffer", c_void_p)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("NextEntryOffset", c_ulong),
        ("NumberOfThreads", c_ulong),
        ("WorkingSetPrivateSize", c_longlong),
        ("HardFaultCount", c_ulong),
        ("NumberOfThreadsHighWatermark", c_ulong),
        ("CycleTime", c_ulonglong),
        ("CreateTime", c_longlong),
        ("UserTime", c_longlong),
        ("KernelTime", c_longlong),
        ("ImageName", _UnicodeString),
        ("BasePriority", c_long),
        ("UniqueProcessId", c_void_p),
        ("InheritedFromUniqueProcessId", c_void_p),
        ("HandleCount", c_ulong),
        ("SessionId", c_ulong),
        ("UniqueProcessKey", c_void_p),
        ("PeakVirtualSize", c_size_t),
        ("VirtualSize", c_size_t),
        ("PageFaultCount", c_ulong),
        ("PeakWorkingSetSize", c_size_t),
        ("WorkingSetSize", c_size_t),
    ]


_buffer_size = 1 << 20


def list_processes() -> list[ProcessSample]:
    """One system call for every process. Raises OSError if Windows refuses."""
    global _buffer_size
    query = ctypes.windll.ntdll.NtQuerySystemInformation
    needed = c_ulong(0)
    while True:
        buffer = ctypes.create_string_buffer(_buffer_size)
        status = query(SYSTEM_PROCESS_INFORMATION, buffer, _buffer_size, ctypes.byref(needed)) & 0xFFFFFFFF
        if status == STATUS_INFO_LENGTH_MISMATCH:
            _buffer_size = max(_buffer_size * 2, needed.value + 65536)
            continue
        if status != 0:
            raise OSError(f"NtQuerySystemInformation failed with status {status:#x}")
        break

    samples: list[ProcessSample] = []
    offset = 0
    while True:
        info = _ProcessInfo.from_buffer(buffer, offset)
        pid = info.UniqueProcessId or 0
        if pid != 0:  # pid 0 is the idle pseudo-process
            name = (
                ctypes.wstring_at(info.ImageName.Buffer, info.ImageName.Length // 2)
                if info.ImageName.Buffer
                else "System"
            )
            samples.append(
                ProcessSample(
                    pid, name, (info.UserTime + info.KernelTime) / 10_000_000, info.WorkingSetPrivateSize
                )
            )
        if info.NextEntryOffset == 0:
            return samples
        offset += info.NextEntryOffset
