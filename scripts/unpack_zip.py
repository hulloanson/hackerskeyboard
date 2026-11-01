#!/usr/bin/env python3
"""Extract a ZIP archive while preserving ordinary Unix permission bits."""

import os
import pathlib
import stat
import sys
import zipfile


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: unpack_zip.py ARCHIVE DESTINATION", file=sys.stderr)
        return 2
    archive = pathlib.Path(sys.argv[1])
    destination = pathlib.Path(sys.argv[2]).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            path = pathlib.PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if path.is_absolute() or ".." in path.parts:
                raise SystemExit(f"unsafe path in ZIP archive: {member.filename!r}")
            if stat.S_IFMT(mode) == stat.S_IFLNK:
                raise SystemExit(f"symbolic links are not accepted in ZIP archives: {member.filename!r}")
        zf.extractall(destination)
        for member in zf.infolist():
            mode = (member.external_attr >> 16) & 0o777
            if mode:
                target = (destination / pathlib.PurePosixPath(member.filename)).resolve()
                try:
                    target.relative_to(destination)
                except ValueError as exc:
                    raise SystemExit(f"extracted ZIP path escapes destination: {member.filename!r}") from exc
                os.chmod(target, mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
