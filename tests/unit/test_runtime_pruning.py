"""Exercise the image cleanup against a disposable filesystem, never the host root."""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "deploy/prune-runtime.py"


def test_pruning_removes_only_build_inputs_and_keeps_runtime_files(tmp_path):
    removed = [
        "usr/local/lib/python3.13/site-packages/pip/__init__.py",
        "usr/local/lib/python3.13/site-packages/pip-25.0.dist-info/METADATA",
        "usr/local/bin/pip3.13",
        "usr/local/include/python3.13/Python.h",
        "app/.venv/lib/python3.13/site-packages/asyncmy/connection.c",
        "app/.venv/lib/python3.13/site-packages/uvloop/loop.pyx",
    ]
    retained = [
        "usr/local/bin/python3.13",
        "usr/local/lib/python3.13/ssl.py",
        "usr/local/lib/python3.13/site-packages/pip_other/__init__.py",
        "app/.venv/lib/python3.13/site-packages/asyncmy/connection.so",
        "app/.venv/lib/python3.13/site-packages/asyncmy/__pycache__/__init__.pyc",
        "app/.venv/lib/python3.13/site-packages/asyncmy-0.2.dist-info/RECORD",
        "app/.venv/lib/python3.13/site-packages/uvloop/__init__.py",
        "app/.venv/lib/python3.13/site-packages/other/runtime.c",
    ]
    for name in removed + retained:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    (tmp_path / "usr/local/bin/pip").symlink_to("pip3.13")
    for mode in ("dependencies", "base"):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(tmp_path), mode],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert result.returncode == 0, result.stderr
    assert all(not (tmp_path / name).exists() for name in removed)
    assert not (tmp_path / "usr/local/bin/pip").is_symlink()
    assert all((tmp_path / name).read_bytes() == b"fixture" for name in retained)


def test_pruning_rejects_symlinked_package_without_touching_external_files(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    source = outside / "connection.c"
    source.write_bytes(b"must survive")
    root = tmp_path / "root"
    package = root / "app/.venv/lib/python3.13/site-packages/asyncmy"
    package.parent.mkdir(parents=True)
    package.symlink_to(outside, target_is_directory=True)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(root), "dependencies"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert source.read_bytes() == b"must survive"
