import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SHA = "a" * 40
IMAGE = f"juya-miniapp-api-test:{SHA}"
CONFIG = b'{"architecture":"amd64","os":"linux","rootfs":{"type":"layers","diff_ids":[]}}'
IMAGE_ID = "sha256:" + hashlib.sha256(CONFIG).hexdigest()


def posix(path: Path) -> str:
    value = path.resolve().as_posix()
    return f"/{value[0].lower()}{value[2:]}" if os.name == "nt" else value


def image_archive(path: Path, tag: str = IMAGE) -> None:
    entries = {
        "manifest.json": json.dumps(
            [{"Config": IMAGE_ID[7:] + ".json", "RepoTags": [tag], "Layers": []}]
        ).encode(),
        IMAGE_ID[7:] + ".json": CONFIG,
    }
    with tarfile.open(path, "w:gz" if path.suffix == ".gz" else "w") as archive:
        for name, data in entries.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))


@pytest.fixture
def deployment(tmp_path: Path) -> tuple[str, Path, dict[str, str]]:
    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).exists():
        pytest.skip("bash required")
    bundle = tmp_path / "bundle"
    (bundle / "deploy").mkdir(parents=True)
    (bundle / "deploy/docker-compose.ecs-test.yml").write_text("services: {}\n")
    (bundle / "deploy/runtime-preflight.py").write_text("print('fixture')\n")
    shutil.copyfile(ROOT / "deploy/image-identity.py", bundle / "deploy/image-identity.py")
    image_archive(bundle / "image.tar.gz")
    image_archive(tmp_path / "fixture-image.tar")
    (bundle / "image-ref.txt").write_text(IMAGE + "\n")
    (bundle / "image-id.txt").write_text(IMAGE_ID + "\n")
    sums = []
    for file in sorted(bundle.rglob("*")):
        if file.is_file():
            sums.append(
                f"{hashlib.sha256(file.read_bytes()).hexdigest()}  "
                f"{file.relative_to(bundle).as_posix()}"
            )
    (bundle / "SHA256SUMS").write_text("\n".join(sums) + "\n", newline="\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "docker").write_text(
        "#!/bin/sh\n"
        'printf "%s|%s\\n" "${JUYA_MINIAPP_API_IMAGE:-}" "$*" >> "$CALL_LOG"\n'
        'case "$*" in\n'
        '  "save --output "*) cp "$FIXTURE_ARCHIVE" "$3";;\n'
        f'  *"image inspect"*) echo "${{LOADED_IMAGE_ID:-{IMAGE_ID}}}";;\n'
        '  *"{{.State.ExitCode}}"*) echo "${WORKER_EXIT_CODE:-0}";;\n'
        '  *"{{.State.Status}}"*)\n'
        '    if [ "${FAIL_BACKGROUND:-0}" = 1 ] && '
        '[ "$JUYA_MINIAPP_API_IMAGE" != old:previous ]; then echo "restarting 1";\n'
        '    else echo "running 0"; fi;;\n'
        '  *"ps -q"*) echo fixture-container;;\n'
        '  *"port miniapp-api 8000"*) echo 127.0.0.1:8001;;\n'
        '  *"run --rm --no-deps"*) [ "${FAIL_PREFLIGHT:-0}" != 1 ];;\n'
        '  *"up -d --no-deps"*) [ "${FAIL_UP:-0}" != 1 ] || '
        '[ "$JUYA_MINIAPP_API_IMAGE" = old:previous ];;\n'
        "esac\n",
        encoding="utf-8",
        newline="\n",
    )
    (binaries / "curl").write_text('#!/bin/sh\n[ "${FAIL_READY:-0}" != 1 ]\n')
    (binaries / "flock").write_text("#!/bin/sh\nexit 0\n")
    (binaries / "sleep").write_text("#!/bin/sh\nexit 0\n")
    (binaries / "python3").write_text(
        f"#!/bin/sh\nexec '{posix(Path(sys.executable))}' \"$@\"\n", encoding="utf-8", newline="\n"
    )
    subprocess.run([bash, "-c", f"chmod +x '{posix(binaries)}'/*"], check=True)
    server = tmp_path / "server"
    server.mkdir()
    env_file = tmp_path / "compose.env"
    env_file.write_text("JUYA_RUNTIME_ENV_FILE=/etc/juya/miniapp-api.env\n")
    environment = {
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": os.environ.get("TEMP", "/tmp"),
        "PATH": posix(binaries) + ":/usr/bin:/bin",
        "JUYA_TEST_PATH": posix(binaries) + ":/usr/bin:/bin",
        "MSYS": "winsymlinks:nativestrict",
        "CALL_LOG": posix(tmp_path / "calls"),
        "FIXTURE_ARCHIVE": posix(tmp_path / "fixture-image.tar"),
        "JUYA_DEPLOY_TEST_MODE": "1",
        "JUYA_DEPLOY_TEST_ROOT": posix(server),
        "JUYA_COMPOSE_ENV_FILE": posix(env_file),
    }
    return bash, bundle, environment


def run_deploy(deployment, branch="test", **flags):
    bash, bundle, environment = deployment
    return subprocess.run(
        [
            bash,
            "-c",
            'export PATH="$JUYA_TEST_PATH"; exec sh "$1" "$2" "$3"',
            "test-runner",
            posix(ROOT / "deploy/ecs-artifact-deploy.sh"),
            branch,
            posix(bundle),
        ],
        env={**environment, **flags},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )


def previous_release(deployment, tmp_path: Path) -> None:
    server = tmp_path / "server"
    previous = server / "releases/previous"
    (previous / "deploy").mkdir(parents=True)
    (previous / "deploy/docker-compose.ecs-test.yml").write_text("services: {}\n")
    (previous / "image-ref.txt").write_text("old:previous\n")
    subprocess.run(
        [deployment[0], "-c", f"ln -s '{posix(previous)}' '{posix(server / 'current-release')}'"],
        env=deployment[2],
        check=True,
    )


def test_rejects_non_test_branch_without_docker(deployment, tmp_path):
    result = run_deploy(deployment, branch="main")
    assert result.returncode != 0 and "only test" in result.stderr
    assert not (tmp_path / "calls").exists()


def test_rejects_corrupt_package_before_docker(deployment, tmp_path):
    (deployment[1] / "image.tar.gz").write_bytes(b"corrupted")
    result = run_deploy(deployment)
    assert result.returncode != 0
    assert not (tmp_path / "calls").exists()


def test_first_release_only_starts_miniapp_services(deployment, tmp_path):
    result = run_deploy(deployment)
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls").read_text()
    assert "-p juya-miniapp-test" in calls
    assert "runtime-preflight.py" in calls
    assert "up -d --no-deps miniapp-api" in calls
    assert "up -d --no-deps miniapp-worker miniapp-beat" in calls
    assert not any(word in calls for word in ["migrate", "mysqldump", "down", "admin-worker"])
    assert (tmp_path / "server/current-release").is_symlink()


def test_missing_business_configuration_does_not_stop_old_services(deployment, tmp_path):
    previous_release(deployment, tmp_path)
    result = run_deploy(deployment, FAIL_PREFLIGHT="1")
    assert result.returncode != 0
    assert "stop " not in (tmp_path / "calls").read_text()
    assert (tmp_path / "server/current-release").resolve().name == "previous"


@pytest.mark.parametrize("failure", ["FAIL_READY", "FAIL_BACKGROUND", "FAIL_UP"])
def test_failed_upgrade_restores_previous_release(deployment, tmp_path, failure):
    previous_release(deployment, tmp_path)
    result = run_deploy(deployment, **{failure: "1"})
    assert result.returncode != 0
    assert "old:previous|" in (tmp_path / "calls").read_text()
    assert "restoring previous" in result.stderr
    assert (tmp_path / "server/current-release").resolve().name == "previous"


def test_failed_first_release_stops_partial_application(deployment, tmp_path):
    result = run_deploy(deployment, FAIL_READY="1")
    assert result.returncode != 0
    assert "stop miniapp-beat miniapp-worker miniapp-api" in (tmp_path / "calls").read_text()
    assert not (tmp_path / "server/current-release").exists()


def test_loaded_image_must_match_package(deployment, tmp_path):
    result = run_deploy(deployment, LOADED_IMAGE_ID="sha256:" + "c" * 64)
    assert result.returncode == 65, result.stderr
    assert "stop " not in (tmp_path / "calls").read_text()


def test_force_killed_worker_aborts_upgrade(deployment, tmp_path):
    previous_release(deployment, tmp_path)
    result = run_deploy(deployment, WORKER_EXIT_CODE="137")
    assert result.returncode != 0
    assert "graceful shutdown" in result.stderr
    assert "restoring previous" in result.stderr


def test_build_rejects_wrong_branch_before_docker(deployment, tmp_path):
    bash, _, environment = deployment
    result = subprocess.run(
        [
            bash,
            "-c",
            'export PATH="$JUYA_TEST_PATH"; exec sh "$1" main "$2" "$3"',
            "test-runner",
            posix(ROOT / "deploy/build-artifact.sh"),
            SHA,
            posix(tmp_path / "output"),
        ],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode != 0 and "only test" in result.stderr
    assert not (tmp_path / "calls").exists()


def test_build_exports_verifiable_artifact_without_daemon_version_query(deployment, tmp_path):
    bash, _, environment = deployment
    output = tmp_path / "output"
    result = subprocess.run(
        [
            bash,
            "-c",
            'export PATH="$JUYA_TEST_PATH"; exec sh "$1" test "$2" "$3"',
            "test-runner",
            posix(ROOT / "deploy/build-artifact.sh"),
            SHA,
            posix(output),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert (output / "image-id.txt").read_text().strip() == IMAGE_ID
    assert (output / "image-ref.txt").read_text().strip() == IMAGE
    assert (output / "image.tar.gz").is_file()
    for line in (output / "SHA256SUMS").read_text().splitlines():
        digest, filename = line.split(maxsplit=1)
        assert (
            hashlib.sha256((output / filename.removeprefix("*")).read_bytes()).hexdigest() == digest
        )
    assert "version" not in (tmp_path / "calls").read_text()


@pytest.mark.parametrize("environment", ["test", "production"])
def test_preflight_rejects_missing_real_business_configuration(environment):
    env = {key: value for key, value in os.environ.items() if not key.startswith(("JUYA_", "OSS_"))}
    env["JUYA_ENVIRONMENT"] = environment
    result = subprocess.run(
        [sys.executable, str(ROOT / "deploy/runtime-preflight.py")],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    assert result.returncode != 0
    assert ("incomplete" if environment == "test" else "real test environment") in result.stderr


def test_conflicting_same_sha_release_does_not_retag_previous_image(deployment, tmp_path):
    release = tmp_path / "server/releases" / SHA
    release.mkdir(parents=True)
    (release / "SHA256SUMS").write_text("old-release-contents\n")
    result = run_deploy(deployment)
    assert result.returncode != 0
    assert "different content" in result.stderr
    calls = (tmp_path / "calls").read_text() if (tmp_path / "calls").exists() else ""
    assert "load --input" not in calls
