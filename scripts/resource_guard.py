#!/usr/bin/env python3
"""Fail-closed resource checks and process-tree supervision for build scripts."""

from __future__ import annotations

import argparse
import ctypes
import dataclasses
import os
import pathlib
import posixpath
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence

GIB = 1024**3
HOST_MEMORY_RESERVE = 2 * GIB
CGROUP_MEMORY_RESERVE = 2 * GIB
ROOT_FREE_BYTES_RESERVE = 1 * GIB
WORKSPACE_FREE_BYTES_RESERVE = 10 * GIB
ROOT_FREE_INODES_RESERVE = 50_000
WORKSPACE_FREE_INODES_RESERVE = 100_000
POLL_SECONDS = 2.0
TERM_GRACE_SECONDS = 10.0


class ResourceError(RuntimeError):
    """A resource value is unavailable or has crossed its safety reserve."""


class CgroupControllerUnavailable(ResourceError):
    """A cgroup filesystem is mounted, but it exposes no memory controller."""


@dataclasses.dataclass(frozen=True)
class DiskState:
    path: str
    device: int
    free_bytes: int
    free_inodes: int
    bytes_reserve: int
    inodes_reserve: int


@dataclasses.dataclass(frozen=True)
class ResourceState:
    host_available: int
    cgroup_available: int
    cgroup_note: str
    disks: tuple[DiskState, ...]


@dataclasses.dataclass(frozen=True, order=True)
class ProcessIdentity:
    pid: int
    start_time: int


@dataclasses.dataclass(frozen=True)
class ProcessRecord:
    identity: ProcessIdentity
    parent_pid: int
    process_group: int
    state: str


def parse_mem_available(meminfo: str) -> int:
    for line in meminfo.splitlines():
        key, sep, value = line.partition(":")
        if key == "MemAvailable" and sep:
            fields = value.split()
            if len(fields) < 2 or fields[1] != "kB":
                raise ResourceError("cannot parse host MemAvailable from /proc/meminfo")
            try:
                available = int(fields[0]) * 1024
            except ValueError as exc:
                raise ResourceError("invalid host MemAvailable value") from exc
            if available < 0:
                raise ResourceError("negative host MemAvailable value")
            return available
    raise ResourceError("host MemAvailable is missing from /proc/meminfo")


def _unescape_mount_path(path: str) -> str:
    # mountinfo represents spaces, tabs, newlines, and backslashes as octal escapes.
    for escaped, literal in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        path = path.replace(escaped, literal)
    return path


def _mounts(mountinfo: str, fs_type: str) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for line in mountinfo.splitlines():
        left, sep, right = line.partition(" - ")
        if not sep:
            continue
        right_fields = right.split()
        left_fields = left.split()
        if len(right_fields) < 3 or len(left_fields) < 6 or right_fields[0] != fs_type:
            continue
        # Keep (mount point, mount root, super options) in that order.
        found.append((
            _unescape_mount_path(left_fields[4]),
            _unescape_mount_path(left_fields[3]),
            right_fields[2],
        ))
    return found


def _relative_cgroup_path(cgroup_path: str, mount_root: str) -> str:
    cgroup_path = posixpath.normpath(cgroup_path)
    mount_root = posixpath.normpath(mount_root)
    if mount_root == "/":
        return cgroup_path.lstrip("/")
    if cgroup_path == mount_root:
        return ""
    if cgroup_path.startswith(mount_root.rstrip("/") + "/"):
        return cgroup_path[len(mount_root.rstrip("/")) + 1 :]
    raise ResourceError("current cgroup path is outside its mounted cgroup root")


def _read_integer(path: pathlib.Path, *, unlimited: bool = False) -> int | None:
    try:
        text = path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ResourceError(f"cannot read cgroup resource value {path}: {exc}") from exc
    if unlimited and text in {"max", "-1"}:
        return None
    try:
        value = int(text)
    except ValueError as exc:
        raise ResourceError(f"invalid cgroup resource value in {path}") from exc
    if value < 0:
        raise ResourceError(f"negative cgroup resource value in {path}")
    return value


def _cgroup_limits_v2(cgroup_path: str, mountinfo: str, fs_root: pathlib.Path) -> list[tuple[int, int, str]] | None:
    mounts = _mounts(mountinfo, "cgroup2")
    if not mounts:
        raise ResourceError("cgroup v2 is active but no cgroup2 mount is visible")
    mount_point, mount_root, _ = mounts[0]
    relative = _relative_cgroup_path(cgroup_path, mount_root)
    start = pathlib.Path(mount_point) / relative
    try:
        relative_start = start.relative_to(pathlib.Path(mount_point))
    except ValueError as exc:
        raise ResourceError("resolved cgroup path escapes the cgroup2 mount") from exc
    current = fs_root / relative_start
    mount_base = fs_root
    results: list[tuple[int, int, str]] = []
    controller_seen = False
    while True:
        max_path = current / "memory.max"
        high_path = current / "memory.high"
        current_path = current / "memory.current"
        if max_path.exists() or current_path.exists():
            controller_seen = True
            hard = _read_integer(max_path, unlimited=True)
            used = _read_integer(current_path)
            high = _read_integer(high_path, unlimited=True) if high_path.exists() else None
            limits = [limit for limit in (hard, high) if limit is not None]
            if limits:
                results.append((min(limits), used, str(current)))
        if current == mount_base:
            break
        parent = current.parent
        if parent == current or mount_base not in parent.parents and parent != mount_base:
            raise ResourceError("cgroup ancestor traversal escaped the cgroup2 mount")
        current = parent
    if not controller_seen:
        raise CgroupControllerUnavailable("cgroup2 mount does not expose memory controller files")
    if not results:
        # The memory controller is exposed, but no finite hard or high limit is
        # configured. Host MemAvailable remains the limiting availability value.
        return None
    return results


def _cgroup_limits_v1(cgroup_path: str, mountinfo: str, fs_root: pathlib.Path) -> list[tuple[int, int, str]] | None:
    mounts = [entry for entry in _mounts(mountinfo, "cgroup") if "memory" in entry[2].split(",")]
    if not mounts:
        raise ResourceError("cgroup v1 memory controller is active but no memory mount is visible")
    mount_point, mount_root, _ = mounts[0]
    relative = _relative_cgroup_path(cgroup_path, mount_root)
    start = pathlib.Path(mount_point) / relative
    try:
        relative_start = start.relative_to(pathlib.Path(mount_point))
    except ValueError as exc:
        raise ResourceError("resolved cgroup path escapes the cgroup memory mount") from exc
    current = fs_root / relative_start
    mount_base = fs_root
    results: list[tuple[int, int, str]] = []
    controller_seen = False
    while True:
        limit_path = current / "memory.limit_in_bytes"
        usage_path = current / "memory.usage_in_bytes"
        if limit_path.exists() or usage_path.exists():
            controller_seen = True
            limit = _read_integer(limit_path)
            usage = _read_integer(usage_path)
            # cgroup v1 commonly represents "unlimited" as a very large integer.
            if limit is not None and limit < (1 << 60):
                results.append((limit, usage, str(current)))
        if current == mount_base:
            break
        parent = current.parent
        if parent == current or mount_base not in parent.parents and parent != mount_base:
            raise ResourceError("cgroup ancestor traversal escaped the cgroup memory mount")
        current = parent
    if not controller_seen:
        raise CgroupControllerUnavailable("cgroup v1 memory mount does not expose memory controller files")
    if not results:
        return None
    return results


def cgroup_available_memory(
    cgroup_file: str,
    mountinfo: str,
    host_available: int,
    *,
    fs_root: str | pathlib.Path = "/sys/fs/cgroup",
) -> tuple[int, str]:
    fs_root = pathlib.Path(fs_root)
    unified: str | None = None
    v1_memory: str | None = None
    for line in cgroup_file.splitlines():
        fields = line.split(":", 2)
        if len(fields) != 3:
            continue
        hierarchy, controllers, path = fields
        if hierarchy == "0" and controllers == "":
            unified = path
            break
        if "memory" in controllers.split(","):
            v1_memory = path
    if unified is not None:
        try:
            limits = _cgroup_limits_v2(unified, mountinfo, fs_root)
        except CgroupControllerUnavailable:
            return host_available, "cgroup v2 mounted without a memory controller; host availability is the only memory metric"
        if limits is None:
            return host_available, "cgroup v2 memory controller is unlimited; host availability is limiting"
        available = min(max(0, limit - used) for limit, used, _ in limits)
        return min(host_available, available), "cgroup v2 finite limit"
    if v1_memory is not None:
        limits = _cgroup_limits_v1(v1_memory, mountinfo, fs_root)
        if limits is None:
            return host_available, "cgroup v1 memory controller is unlimited; host availability is limiting"
        available = min(max(0, limit - used) for limit, used, _ in limits)
        return min(host_available, available), "cgroup v1 finite limit"

    # Some VMs expose a cgroup filesystem without the memory controller. Record
    # that explicitly; there is no cgroup-specific limit to inspect in that setup.
    if _mounts(mountinfo, "cgroup2") or _mounts(mountinfo, "cgroup"):
        return host_available, "cgroup mounted without a memory controller; host availability is the only memory metric"

    # No readable cgroup memory controller is not equivalent to an unlimited cgroup.
    raise ResourceError("no active cgroup memory controller could be identified")


def _disk_states(paths: Sequence[str]) -> tuple[DiskState, ...]:
    grouped: dict[int, DiskState] = {}
    for raw_path in paths:
        path = os.path.abspath(raw_path)
        probe = pathlib.Path(path)
        while not probe.exists():
            parent = probe.parent
            if parent == probe:
                raise ResourceError(f"cannot find an existing filesystem ancestor for {path}")
            probe = parent
        try:
            stat = os.statvfs(probe)
            device = os.stat(probe).st_dev
        except OSError as exc:
            raise ResourceError(f"cannot inspect filesystem for {path}: {exc}") from exc
        free_bytes = stat.f_bavail * stat.f_frsize
        free_inodes = stat.f_favail
        if device == os.stat("/").st_dev:
            bytes_reserve = ROOT_FREE_BYTES_RESERVE
            inodes_reserve = ROOT_FREE_INODES_RESERVE
        else:
            bytes_reserve = WORKSPACE_FREE_BYTES_RESERVE
            inodes_reserve = WORKSPACE_FREE_INODES_RESERVE
        existing = grouped.get(device)
        if existing is None:
            grouped[device] = DiskState(path, device, free_bytes, free_inodes, bytes_reserve, inodes_reserve)
        else:
            # Keep the lower readings in case two paths on the same filesystem were
            # sampled on opposite sides of a concurrent write.
            grouped[device] = dataclasses.replace(
                existing,
                free_bytes=min(existing.free_bytes, free_bytes),
                free_inodes=min(existing.free_inodes, free_inodes),
                bytes_reserve=max(existing.bytes_reserve, bytes_reserve),
                inodes_reserve=max(existing.inodes_reserve, inodes_reserve),
            )
    return tuple(grouped.values())


def gather_resources(paths: Sequence[str]) -> ResourceState:
    try:
        meminfo = pathlib.Path("/proc/meminfo").read_text(encoding="ascii")
        cgroup_file = pathlib.Path("/proc/self/cgroup").read_text(encoding="ascii")
        mountinfo = pathlib.Path("/proc/self/mountinfo").read_text(encoding="ascii")
    except OSError as exc:
        raise ResourceError(f"cannot read Linux resource accounting data: {exc}") from exc
    host = parse_mem_available(meminfo)
    cgroup, note = cgroup_available_memory(cgroup_file, mountinfo, host)
    disk_paths = ["/", *paths]
    disks = _disk_states(disk_paths)
    return ResourceState(host, cgroup, note, disks)


def validate_resources(state: ResourceState) -> None:
    if state.host_available < HOST_MEMORY_RESERVE:
        raise ResourceError(
            f"host MemAvailable is {state.host_available / GIB:.2f} GiB; "
            f"the {HOST_MEMORY_RESERVE / GIB:.0f} GiB reserve would be breached"
        )
    if state.cgroup_available < CGROUP_MEMORY_RESERVE:
        raise ResourceError(
            f"effective cgroup memory availability is {state.cgroup_available / GIB:.2f} GiB; "
            f"the {CGROUP_MEMORY_RESERVE / GIB:.0f} GiB reserve would be breached"
        )
    for disk in state.disks:
        if disk.free_bytes < disk.bytes_reserve:
            raise ResourceError(
                f"{disk.path} filesystem has {disk.free_bytes / GIB:.2f} GiB free; "
                f"the {disk.bytes_reserve / GIB:.0f} GiB reserve would be breached"
            )
        if disk.free_inodes < disk.inodes_reserve:
            raise ResourceError(
                f"{disk.path} filesystem has {disk.free_inodes} free inodes; "
                f"the {disk.inodes_reserve:,} inode reserve would be breached"
            )


def check_resources(paths: Sequence[str]) -> ResourceState:
    state = gather_resources(paths)
    validate_resources(state)
    return state


def _format_state(state: ResourceState) -> str:
    disks = ", ".join(
        f"{disk.path}: {disk.free_bytes / GIB:.1f} GiB/{disk.free_inodes:,} inodes free"
        for disk in state.disks
    )
    return (
        f"resources: host {state.host_available / GIB:.1f} GiB available; "
        f"cgroup {state.cgroup_available / GIB:.1f} GiB available ({state.cgroup_note}); "
        f"filesystems {disks}"
    )


def _read_process_record(pid: int, proc_root: pathlib.Path = pathlib.Path("/proc")) -> ProcessRecord | None:
    try:
        stat = (proc_root / str(pid) / "stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    except PermissionError as exc:
        raise ResourceError(f"cannot inspect child process {pid} under /proc: {exc}") from exc
    except OSError as exc:
        # A process can disappear between enumeration and reading its stat file.
        if not (proc_root / str(pid)).exists():
            return None
        raise ResourceError(f"cannot inspect process {pid} under /proc: {exc}") from exc
    close_paren = stat.rfind(")")
    if close_paren < 0:
        raise ResourceError(f"cannot parse /proc/{pid}/stat")
    fields = stat[close_paren + 1 :].split()
    if len(fields) < 20:
        raise ResourceError(f"/proc/{pid}/stat is missing process fields")
    try:
        state = fields[0]
        parent_pid = int(fields[1])
        process_group = int(fields[2])
        start_time = int(fields[19])
    except ValueError as exc:
        raise ResourceError(f"/proc/{pid}/stat contains invalid process fields") from exc
    return ProcessRecord(ProcessIdentity(pid, start_time), parent_pid, process_group, state)


def _descendant_snapshot(root_pid: int) -> dict[ProcessIdentity, ProcessRecord]:
    proc_root = pathlib.Path("/proc")
    try:
        entries = list(os.scandir(proc_root))
    except OSError as exc:
        raise ResourceError(f"cannot enumerate /proc to supervise child processes: {exc}") from exc
    records: dict[int, ProcessRecord] = {}
    for entry in entries:
        if not entry.name.isdigit():
            continue
        record = _read_process_record(int(entry.name), proc_root)
        if record is not None:
            records[record.identity.pid] = record
    if root_pid not in records:
        raise ResourceError(f"cannot inspect supervising process {root_pid} under /proc")
    children: dict[int, list[ProcessRecord]] = {}
    for record in records.values():
        children.setdefault(record.parent_pid, []).append(record)
    found: dict[ProcessIdentity, ProcessRecord] = {}
    pending = [root_pid]
    visited = {root_pid}
    while pending:
        parent = pending.pop()
        for child in children.get(parent, ()):
            pid = child.identity.pid
            if pid in visited:
                continue
            visited.add(pid)
            found[child.identity] = child
            pending.append(pid)
    return found


def _set_child_subreaper(enabled: bool) -> int:
    """Enable Linux PR_SET_CHILD_SUBREAPER and return the prior state."""
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    # Linux prctl constants: PR_GET_CHILD_SUBREAPER=37, PR_SET_CHILD_SUBREAPER=36.
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise ResourceError(f"cannot inspect child-subreaper state: {os.strerror(error)}")
    desired = 1 if enabled else 0
    if previous.value != desired and libc.prctl(36, desired, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise ResourceError(f"cannot set child-subreaper state: {os.strerror(error)}")
    return previous.value


def _restore_child_subreaper(previous: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, int(previous != 0), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise ResourceError(f"cannot restore child-subreaper state: {os.strerror(error)}")


def _record_is_live(identity: ProcessIdentity) -> bool:
    record = _read_process_record(identity.pid)
    return record is not None and record.identity == identity and record.state != "Z"


def _refresh_descendants(tracked: dict[ProcessIdentity, ProcessRecord]) -> None:
    tracked.update(_descendant_snapshot(os.getpid()))


def _signal_process(identity: ProcessIdentity, signum: int) -> None:
    """Signal one still-matching process, preferring pidfds to avoid PID reuse."""
    if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
        try:
            pidfd = os.pidfd_open(identity.pid, 0)
        except ProcessLookupError:
            return
        except OSError as exc:
            if exc.errno not in {getattr(os, "ENOSYS", 38), 22, 95}:
                raise ResourceError(f"cannot open process handle for {identity.pid}: {exc}") from exc
        else:
            try:
                record = _read_process_record(identity.pid)
                if record is None or record.identity != identity or record.state == "Z":
                    return
                try:
                    signal.pidfd_send_signal(pidfd, signum)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    raise ResourceError(f"cannot signal child process {identity.pid}: {exc}") from exc
                return
            finally:
                os.close(pidfd)
    record = _read_process_record(identity.pid)
    if record is None or record.identity != identity or record.state == "Z":
        return
    try:
        os.kill(identity.pid, signum)
    except ProcessLookupError:
        pass
    except OSError as exc:
        raise ResourceError(f"cannot signal child process {identity.pid}: {exc}") from exc


def _reap_adopted_children(tracked: dict[ProcessIdentity, ProcessRecord], leader_pid: int) -> None:
    for identity in tuple(tracked):
        if identity.pid == leader_pid:
            continue
        try:
            os.waitpid(identity.pid, os.WNOHANG)
        except ChildProcessError:
            # The process is still owned by a live descendant or was already reaped.
            pass
        except InterruptedError:
            pass


def _stop_process_tree(
    proc: subprocess.Popen[bytes],
    tracked: dict[ProcessIdentity, ProcessRecord],
    grace_seconds: float,
) -> None:
    """Stop the process group and all observed descendants, including detached ones."""
    signaled: set[ProcessIdentity] = set()

    def send_to_current(signum: int) -> None:
        try:
            _refresh_descendants(tracked)
        except ResourceError as exc:
            print(f"resource guard: cannot refresh child list during cleanup: {exc}", file=sys.stderr, flush=True)
        if signum in {signal.SIGTERM, signal.SIGKILL}:
            try:
                os.killpg(proc.pid, signum)
            except ProcessLookupError:
                pass
            except OSError as exc:
                print(f"resource guard: cannot signal child process group {proc.pid}: {exc}", file=sys.stderr, flush=True)
        for identity in tuple(tracked):
            if identity.pid == proc.pid and signum == signal.SIGTERM:
                # The leader was covered by SIGTERM to its process group.
                signaled.add(identity)
                continue
            try:
                _signal_process(identity, signum)
            except ResourceError as exc:
                print(f"resource guard: cannot signal tracked child {identity.pid}: {exc}", file=sys.stderr, flush=True)
            signaled.add(identity)

    send_to_current(signal.SIGTERM)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            _refresh_descendants(tracked)
        except ResourceError as exc:
            print(f"resource guard: cannot refresh child list during cleanup: {exc}", file=sys.stderr, flush=True)
        active = {identity for identity in tracked if _record_is_live(identity)}
        if not active:
            break
        for identity in active - signaled:
            try:
                _signal_process(identity, signal.SIGTERM)
            except ResourceError as exc:
                print(f"resource guard: cannot signal tracked child {identity.pid}: {exc}", file=sys.stderr, flush=True)
            signaled.add(identity)
        time.sleep(0.1)

    send_to_current(signal.SIGKILL)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    _reap_adopted_children(tracked, proc.pid)


def run_command(
    command: Sequence[str],
    *,
    label: str,
    paths: Sequence[str],
    checker: Callable[[Sequence[str]], ResourceState] = check_resources,
    poll_seconds: float = POLL_SECONDS,
    term_grace_seconds: float = TERM_GRACE_SECONDS,
) -> int:
    if not command:
        raise ResourceError("run requires a command after --")
    initial_state = checker(paths)
    print(f"{label}: {_format_state(initial_state)}", flush=True)
    # Process-tree tracking depends on /proc and on the Linux subreaper facility.
    # Verify /proc before launching work so the supervisor never starts blind.
    _descendant_snapshot(os.getpid())
    previous_subreaper = _set_child_subreaper(True)
    requested_signal: int | None = None
    previous_handlers: dict[int, signal.Handlers] = {}

    def request_stop(signum: int, _frame: object) -> None:
        nonlocal requested_signal
        requested_signal = signum

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, request_stop)
    try:
        proc = subprocess.Popen(list(command), start_new_session=True)
    except Exception:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        _restore_child_subreaper(previous_subreaper)
        raise
    tracked: dict[ProcessIdentity, ProcessRecord] = {}
    try:
        while True:
            if requested_signal is not None:
                print(
                    f"resource guard: received signal {requested_signal}; stopping {label} process tree",
                    file=sys.stderr,
                    flush=True,
                )
                _stop_process_tree(proc, tracked, term_grace_seconds)
                return 128 + requested_signal
            try:
                _refresh_descendants(tracked)
                state = checker(paths)
            except Exception as exc:
                print(f"resource guard: {label} stopped: {exc}", file=sys.stderr, flush=True)
                _stop_process_tree(proc, tracked, term_grace_seconds)
                return 125
            return_code = proc.poll()
            active_descendants = {
                identity for identity in tracked
                if identity.pid != proc.pid and _record_is_live(identity)
            }
            if return_code is not None and not active_descendants:
                _reap_adopted_children(tracked, proc.pid)
                return return_code
            if poll_seconds > 0:
                time.sleep(poll_seconds)
            else:
                time.sleep(0.01)
    except KeyboardInterrupt:
        print(f"resource guard: interrupt; stopping {label} process tree", file=sys.stderr, flush=True)
        _stop_process_tree(proc, tracked, term_grace_seconds)
        return 130
    except Exception as exc:
        print(f"resource guard: supervisor failed during {label}: {exc}", file=sys.stderr, flush=True)
        _stop_process_tree(proc, tracked, term_grace_seconds)
        return 125
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        _reap_adopted_children(tracked, proc.pid)
        _restore_child_subreaper(previous_subreaper)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    check = subparsers.add_parser("check", help="check reserves before starting a phase")
    check.add_argument("--path", action="append", default=[], help="additional path whose filesystem is monitored")
    run = subparsers.add_parser("run", help="run and monitor one child process tree")
    run.add_argument("--label", required=True, help="human-readable phase name")
    run.add_argument("--path", action="append", default=[], help="additional path whose filesystem is monitored")
    run.add_argument("command", nargs=argparse.REMAINDER, help="command after --")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = args.path
    try:
        if args.action == "check":
            state = check_resources(paths)
            print(_format_state(state))
            return 0
        command = list(args.command)
        if command and command[0] == "--":
            command.pop(0)
        return run_command(command, label=args.label, paths=paths)
    except ResourceError as exc:
        print(f"resource guard: refusing to continue: {exc}", file=sys.stderr)
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
