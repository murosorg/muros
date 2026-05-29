# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Collecte de metriques systeme par lecture directe de /proc.

Approche minimaliste : on n'embarque pas node_exporter, on lit ce dont
on a besoin pour l'UI. Le calcul du CPU se fait sur la difference entre
deux echantillons stockes en memoire.
"""
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

PROC = Path("/proc")


class MemoryInfo(TypedDict):
    total_bytes: int
    available_bytes: int
    used_bytes: int
    used_percent: float


class SwapInfo(TypedDict):
    total_bytes: int
    used_bytes: int
    used_percent: float


class DiskInfo(TypedDict):
    mount: str
    total_bytes: int
    used_bytes: int
    used_percent: float


class NetInterfaceStats(TypedDict):
    name: str
    operstate: str
    rx_bytes: int
    tx_bytes: int
    rx_packets: int
    tx_packets: int
    rx_errors: int
    tx_errors: int
    rx_dropped: int
    tx_dropped: int


class ConntrackInfo(TypedDict):
    current: int
    max: int
    used_percent: float


@dataclass
class _CpuSample:
    timestamp: float
    total: int
    idle: int


_last_cpu: _CpuSample | None = None


def _read_proc(path: str) -> str:
    try:
        return (PROC / path).read_text()
    except OSError:
        return ""


def _read_int(path: str) -> int:
    raw = _read_proc(path).strip()
    try:
        return int(raw)
    except ValueError:
        return 0


def _cpu_totals() -> tuple[int, int]:
    """Retourne (total, idle) depuis /proc/stat ligne 'cpu '."""
    raw = _read_proc("stat")
    for line in raw.splitlines():
        if not line.startswith("cpu "):
            continue
        parts = line.split()[1:]
        try:
            vals = [int(x) for x in parts]
        except ValueError:
            return 0, 0
        # user nice system idle iowait irq softirq steal guest guest_nice
        total = sum(vals)
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return total, idle
    return 0, 0


def cpu_usage_percent() -> float:
    global _last_cpu
    total, idle = _cpu_totals()
    now = time.monotonic()
    if _last_cpu is None or total <= _last_cpu.total:
        _last_cpu = _CpuSample(now, total, idle)
        return 0.0
    dt = total - _last_cpu.total
    di = idle - _last_cpu.idle
    _last_cpu = _CpuSample(now, total, idle)
    if dt <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1.0 - di / dt)))


def cpu_cores() -> int:
    return os.cpu_count() or 1


def memory_info() -> MemoryInfo:
    raw = _read_proc("meminfo")
    fields: dict[str, int] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        parts = v.strip().split()
        if not parts:
            continue
        try:
            fields[k] = int(parts[0]) * 1024  # valeurs en KiB
        except ValueError:
            pass
    total = fields.get("MemTotal", 0)
    available = fields.get("MemAvailable", fields.get("MemFree", 0))
    used = max(0, total - available)
    pct = (100.0 * used / total) if total else 0.0
    return MemoryInfo(
        total_bytes=total, available_bytes=available,
        used_bytes=used, used_percent=round(pct, 2),
    )


def swap_info() -> SwapInfo:
    raw = _read_proc("meminfo")
    fields: dict[str, int] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        parts = v.strip().split()
        if not parts:
            continue
        try:
            fields[k] = int(parts[0]) * 1024
        except ValueError:
            pass
    total = fields.get("SwapTotal", 0)
    free = fields.get("SwapFree", 0)
    used = max(0, total - free)
    pct = (100.0 * used / total) if total else 0.0
    return SwapInfo(total_bytes=total, used_bytes=used, used_percent=round(pct, 2))


def load_average() -> list[float]:
    raw = _read_proc("loadavg").split()
    try:
        return [float(raw[0]), float(raw[1]), float(raw[2])]
    except (IndexError, ValueError):
        return [0.0, 0.0, 0.0]


def uptime_seconds() -> int:
    raw = _read_proc("uptime").split()
    try:
        return int(float(raw[0]))
    except (IndexError, ValueError):
        return 0


def disks_info(mounts: list[str] | None = None) -> list[DiskInfo]:
    if mounts is None:
        # On lit /proc/mounts et on garde les vrais systemes de fichiers
        keep_fs = {"ext4", "ext3", "ext2", "xfs", "btrfs", "zfs", "f2fs"}
        seen: set[str] = set()
        candidates: list[str] = []
        for line in _read_proc("mounts").splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            mount, fs = parts[1], parts[2]
            if fs in keep_fs and mount not in seen:
                seen.add(mount)
                candidates.append(mount)
        mounts = candidates or ["/"]

    out: list[DiskInfo] = []
    for m in mounts:
        try:
            st = os.statvfs(m)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = max(0, total - free)
        pct = (100.0 * used / total) if total else 0.0
        out.append(DiskInfo(
            mount=m, total_bytes=total, used_bytes=used, used_percent=round(pct, 2),
        ))
    return out


def interfaces_stats() -> list[NetInterfaceStats]:
    raw = _read_proc("net/dev").splitlines()
    if len(raw) <= 2:
        return []
    out: list[NetInterfaceStats] = []
    for line in raw[2:]:
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        name = name.strip()
        parts = rest.split()
        if len(parts) < 16:
            continue
        try:
            vals = [int(p) for p in parts[:16]]
        except ValueError:
            continue
        # rx: bytes packets errs drop fifo frame compressed multicast
        # tx: bytes packets errs drop fifo colls carrier compressed
        # Lit l'etat de lien depuis sysfs (up/down/unknown). Cas particulier
        # 'lo' = loopback, marquee 'unknown' par le noyau mais toujours
        # operationnelle ; on la considere up.
        try:
            with open(f"/sys/class/net/{name}/operstate", encoding="ascii") as fh:
                operstate = fh.read().strip()
        except OSError:
            operstate = "unknown"
        if name == "lo":
            operstate = "up"
        out.append(NetInterfaceStats(
            name=name,
            operstate=operstate,
            rx_bytes=vals[0], rx_packets=vals[1], rx_errors=vals[2], rx_dropped=vals[3],
            tx_bytes=vals[8], tx_packets=vals[9], tx_errors=vals[10], tx_dropped=vals[11],
        ))
    return out


def conntrack_info() -> ConntrackInfo:
    current = _read_int("sys/net/netfilter/nf_conntrack_count")
    maximum = _read_int("sys/net/netfilter/nf_conntrack_max")
    pct = (100.0 * current / maximum) if maximum else 0.0
    return ConntrackInfo(current=current, max=maximum, used_percent=round(pct, 2))
