#!/usr/bin/env python3
"""Replace legacy F-Droid index JAR signatures with the modern apksigner scheme."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from collections.abc import Callable


def modernize_repo(
    repository: pathlib.Path,
    *,
    sign_jar: Callable[..., None] | None = None,
) -> tuple[str, ...]:
    repository = repository.resolve(strict=True)
    if not repository.is_dir():
        raise RuntimeError("F-Droid output path is not a directory")

    jars: list[pathlib.Path] = []
    for name in ("index-v1.jar", "index.jar"):
        candidate = repository / name
        if candidate.is_symlink():
            raise RuntimeError("F-Droid output contains a symlinked index JAR")
        if candidate.exists():
            if not candidate.is_file():
                raise RuntimeError("F-Droid output index JAR is not a regular file")
            jars.append(candidate)
    if not jars:
        raise RuntimeError("F-Droid did not create an index-v1.jar or index.jar")

    if sign_jar is None:
        from fdroidserver import common, signindex

        config = common.read_config()
        common.options = argparse.Namespace(verbose=False, quiet=True)
        signindex.config = config
        sign_jar = signindex.sign_jar

    signed: list[str] = []
    for jar in jars:
        sign_jar(str(jar), use_old_algs=False)
        signed.append(jar.name)
    return tuple(signed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        repository = args.repository.resolve(strict=True)
        config_root = next(
            (parent for parent in repository.parents if (parent / "config.yml").is_file()),
            None,
        )
        if config_root is None:
            raise RuntimeError("cannot locate the private F-Droid config.yml")
        os.chdir(config_root)
        signed = modernize_repo(repository)
    except Exception:
        print("F-Droid modern index signing failed; diagnostic output suppressed", file=sys.stderr)
        return 1
    print("Modernized F-Droid index signatures: " + ", ".join(signed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
