import pathlib
import os
import signal
import tempfile
import sys
import time
import subprocess
import unittest

from scripts import resource_guard as guard


class ResourceGuardTests(unittest.TestCase):
    def _write_cgroup(self, root: pathlib.Path, path: str, values: dict[str, str]) -> None:
        directory = root / path.lstrip("/")
        directory.mkdir(parents=True, exist_ok=True)
        for name, value in values.items():
            (directory / name).write_text(value, encoding="ascii")

    def test_host_meminfo_parser_requires_memavailable(self):
        self.assertEqual(guard.parse_mem_available("MemTotal: 20 kB\nMemAvailable: 10 kB\n"), 10 * 1024)
        with self.assertRaises(guard.ResourceError):
            guard.parse_mem_available("MemTotal: 20 kB\n")
        with self.assertRaises(guard.ResourceError):
            guard.parse_mem_available("MemAvailable: broken kB\n")

    def test_cgroup_v2_uses_nearest_finite_limit_and_host_cap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self._write_cgroup(root, "team/job", {
                "memory.max": str(2 * guard.GIB),
                "memory.high": "max",
                "memory.current": str(guard.GIB + 128 * 1024**2),
            })
            self._write_cgroup(root, "team", {
                "memory.max": str(4 * guard.GIB),
                "memory.high": str(3 * guard.GIB),
                "memory.current": str(guard.GIB),
            })
            mountinfo = "42 33 0:27 / /sys/fs/cgroup rw,nosuid - cgroup2 cgroup2 rw\n"
            available, note = guard.cgroup_available_memory(
                "0::/team/job\n", mountinfo, 8 * guard.GIB, fs_root=root
            )
            self.assertEqual(available, guard.GIB - 128 * 1024**2)
            self.assertEqual(note, "cgroup v2 finite limit")

    def test_cgroup_v2_unlimited_uses_host_availability(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self._write_cgroup(root, "group", {
                "memory.max": "max", "memory.high": "max", "memory.current": "1234"
            })
            mountinfo = "42 33 0:27 / /sys/fs/cgroup rw - cgroup2 cgroup2 rw\n"
            available, note = guard.cgroup_available_memory(
                "0::/group\n", mountinfo, 3 * guard.GIB, fs_root=root
            )
            self.assertEqual(available, 3 * guard.GIB)
            self.assertIn("unlimited", note)

    def test_cgroup_v2_unknown_usage_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self._write_cgroup(root, "group", {"memory.max": str(4 * guard.GIB)})
            mountinfo = "42 33 0:27 / /sys/fs/cgroup rw - cgroup2 cgroup2 rw\n"
            with self.assertRaises(guard.ResourceError):
                guard.cgroup_available_memory("0::/group\n", mountinfo, 3 * guard.GIB, fs_root=root)

    def test_cgroup_v1_finds_memory_controller_in_super_options(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self._write_cgroup(root, "user/job", {
                "memory.limit_in_bytes": str(3 * guard.GIB),
                "memory.usage_in_bytes": str(guard.GIB),
            })
            mountinfo = "42 33 0:27 / /sys/fs/cgroup/memory rw - cgroup cgroup rw,memory\n"
            available, note = guard.cgroup_available_memory(
                "5:cpu,memory:/user/job\n", mountinfo, 5 * guard.GIB, fs_root=root
            )
            self.assertEqual(available, 2 * guard.GIB)
            self.assertEqual(note, "cgroup v1 finite limit")

    def test_reserve_thresholds_fail_at_one_byte_below(self):
        disk = guard.DiskState(
            path="/workspace", device=1,
            free_bytes=guard.WORKSPACE_FREE_BYTES_RESERVE,
            free_inodes=guard.WORKSPACE_FREE_INODES_RESERVE,
            bytes_reserve=guard.WORKSPACE_FREE_BYTES_RESERVE,
            inodes_reserve=guard.WORKSPACE_FREE_INODES_RESERVE,
        )
        state = guard.ResourceState(
            host_available=guard.HOST_MEMORY_RESERVE,
            cgroup_available=guard.CGROUP_MEMORY_RESERVE,
            cgroup_note="test",
            disks=(disk,),
        )
        guard.validate_resources(state)
        with self.assertRaises(guard.ResourceError):
            guard.validate_resources(guard.dataclasses.replace(state, host_available=guard.HOST_MEMORY_RESERVE - 1))
        with self.assertRaises(guard.ResourceError):
            guard.validate_resources(guard.dataclasses.replace(state, cgroup_available=guard.CGROUP_MEMORY_RESERVE - 1))
        with self.assertRaises(guard.ResourceError):
            guard.validate_resources(guard.dataclasses.replace(
                state, disks=(guard.dataclasses.replace(disk, free_inodes=disk.inodes_reserve - 1),)
            ))

    def test_resource_breach_terminates_grandchild_even_after_leader_exits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            term_marker = root / "child-term"
            ready_marker = root / "child-ready"
            child_code = (
                "import pathlib,signal,time; "
                f"signal.signal(signal.SIGTERM, lambda *_: (pathlib.Path({str(term_marker)!r}).write_text('term'), exit(0))); "
                f"pathlib.Path({str(ready_marker)!r}).write_text('ready'); "
                "time.sleep(60)"
            )
            command_code = (
                "import pathlib,subprocess,sys,time\n"
                f"subprocess.Popen([sys.executable,'-c',{child_code!r}])\n"
                f"ready=pathlib.Path({str(ready_marker)!r})\n"
                "deadline=time.monotonic()+2\n"
                "while not ready.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
            )
            good_state = guard.ResourceState(8 * guard.GIB, 8 * guard.GIB, "test", ())
            calls = 0

            def checker(_paths):
                nonlocal calls
                calls += 1
                if calls == 3:
                    deadline = time.monotonic() + 2
                    while not ready_marker.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    raise guard.ResourceError("simulated reserve breach")
                return good_state

            result = guard.run_command(
                [sys.executable, "-c", command_code],
                label="test child tree",
                paths=[str(root)],
                checker=checker,
                poll_seconds=0.05,
                term_grace_seconds=1,
            )
            self.assertEqual(result, 125)
            self.assertTrue(term_marker.exists(), "grandchild did not receive SIGTERM")

    def test_wrapper_sigterm_is_forwarded_to_child_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            ready_marker = root / "child-ready"
            term_marker = root / "child-term"
            child_code = (
                "import pathlib,signal,time; "
                f"signal.signal(signal.SIGTERM, lambda *_: (pathlib.Path({str(term_marker)!r}).write_text('term'), exit(0))); "
                f"pathlib.Path({str(ready_marker)!r}).write_text('ready'); time.sleep(60)"
            )
            helper_code = (
                "import pathlib,subprocess,sys; "
                "from scripts import resource_guard as g; "
                "state=g.ResourceState(8*g.GIB,8*g.GIB,'test',()); "
                f"raise SystemExit(g.run_command([sys.executable,'-c',{child_code!r}], "
                "label='signal forwarding', paths=[], checker=lambda _:state, "
                "poll_seconds=.02, term_grace_seconds=1))"
            )
            wrapper = subprocess.Popen([sys.executable, "-c", helper_code], cwd=pathlib.Path(__file__).parents[1])
            try:
                deadline = time.monotonic() + 3
                while not ready_marker.exists() and time.monotonic() < deadline:
                    if wrapper.poll() is not None:
                        self.fail("guard wrapper exited before its child was ready")
                    time.sleep(0.02)
                self.assertTrue(ready_marker.exists(), "child did not start")
                os.kill(wrapper.pid, signal.SIGTERM)
                self.assertEqual(wrapper.wait(timeout=4), 128 + signal.SIGTERM)
                self.assertTrue(term_marker.exists(), "guard wrapper did not forward SIGTERM to its child group")
            finally:
                if wrapper.poll() is None:
                    wrapper.kill()
                    wrapper.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
