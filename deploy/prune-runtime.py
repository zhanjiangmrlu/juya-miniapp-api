"""Remove allowlisted build inputs before copying the final runtime layers."""

import argparse
import shutil
from pathlib import Path


def prune(root: Path, mode: str) -> None:
    root = root.absolute()
    candidates: list[Path] = []
    if mode == "base":
        site = root / "usr/local/lib/python3.13/site-packages"
        candidates = [
            site / "pip",
            *site.glob("pip-*.dist-info"),
            root / "usr/local/include/python3.13",
            *(root / f"usr/local/bin/{name}" for name in ("pip", "pip3", "pip3.13")),
        ]
    else:
        site = root / "app/.venv/lib/python3.13/site-packages"
        for name in ("asyncmy", "uvloop"):
            package = site / name
            if package.is_symlink() or package.resolve() != package.absolute():
                raise ValueError("refusing symlink in runtime cleanup")
            candidates.extend(
                path for path in package.rglob("*") if path.suffix in {".c", ".h", ".pyx", ".pxd"}
            )
    # Validate the complete selection before deleting anything. Never traverse links.
    for path in candidates:
        if mode == "base" and path.parent == root / "usr/local/bin" and path.is_symlink():
            if path.parent.resolve() != path.parent.absolute():
                raise ValueError("refusing symlink in runtime cleanup parent")
            continue  # Unlink only this exact pip launcher, never its target.
        if path.is_symlink() or path.resolve() != path.absolute() or not path.is_relative_to(root):
            raise ValueError("refusing symlink or escaping runtime cleanup path")
        if path.is_dir() and any(child.is_symlink() for child in path.rglob("*")):
            raise ValueError("refusing symlink in runtime cleanup directory")
    for path in candidates:
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("mode", choices=("base", "dependencies"))
    args = parser.parse_args()
    prune(args.root, args.mode)
