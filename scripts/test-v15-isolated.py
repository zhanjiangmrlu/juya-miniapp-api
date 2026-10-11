"""Use private UUID MySQL/Redis and admin-owned migrations, never shared services."""

import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
ADMIN = ROOT.parent / "juya-admin-api"


def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        **kwargs,
    )  # type: ignore[arg-type]


def port(name: str, container_port: str) -> str:
    result = run(["docker", "port", name, container_port])
    address = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"127\.0\.0\.1:[0-9]+", address):
        raise RuntimeError("Private container port is unavailable")
    return address


def main() -> int:
    task_id = uuid4().hex
    mysql_name = "juya-v15-bff-mysql-" + task_id
    redis_name = "juya-v15-bff-redis-" + task_id
    password = secrets.token_urlsafe(32)
    created: list[str] = []

    def safe(output: str) -> str:
        return output.replace(password, "[redacted]").replace(
            quote(password, safe=""), "[redacted]"
        )

    try:
        for name, image, container_port, extra in [
            (mysql_name, "mysql:8.4", "3306", ["-e", "MYSQL_ROOT_PASSWORD=" + password]),
            (redis_name, "redis:7-alpine", "6379", []),
        ]:
            result = run(
                [
                    "docker",
                    "create",
                    "--pull=never",
                    "--name",
                    name,
                    "--label",
                    "juya.isolated-test=" + task_id,
                    "-p",
                    "127.0.0.1::" + container_port,
                    *extra,
                    image,
                ]
            )
            if result.returncode:
                raise RuntimeError("Private " + image + " creation failed: " + safe(result.stderr))
            created.append(name)
            if run(["docker", "start", name]).returncode:
                raise RuntimeError("Private container start failed")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            ping = run(
                [
                    "docker",
                    "exec",
                    mysql_name,
                    "sh",
                    "-c",
                    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" '
                    'mysql --protocol=tcp -h127.0.0.1 -uroot -e "SELECT 1"',
                ]
            )
            if not ping.returncode:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Private MySQL readiness timed out")
        database = "juya_v13_mini_" + task_id
        sql = "CREATE DATABASE `" + database + "` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
        result = run(
            [
                "docker",
                "exec",
                "-i",
                mysql_name,
                "sh",
                "-c",
                'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --protocol=socket -uroot',
            ],
            input=sql,
        )
        if result.returncode:
            raise RuntimeError("Private database creation failed: " + safe(result.stderr))
        address = port(mysql_name, "3306/tcp")
        url = "mysql+pymysql://root:" + quote(password, safe="") + "@" + address + "/" + database
        redis_url = "redis://" + port(redis_name, "6379/tcp") + "/0"
        env = dict(
            os.environ,
            PYTHONIOENCODING="utf-8",
            JUYA_ENVIRONMENT="test",
            JUYA_TEST_DATABASE_URL=url,
            JUYA_TEST_REDIS_URL=redis_url,
            JUYA_V13_ISOLATED_DATABASE=database,
            JUYA_RUN_LIVE_OSS_TESTS="false",
            JUYA_RUN_LIVE_OSS_BROWSER_TESTS="false",
            JUYA_CONTENT_SECURITY_ENABLED="false",
        )
        for key in ("JUYA_DATABASE_URL", "JUYA_REDIS_URL", "JUYA_MIGRATION_DATABASE_URL"):
            env.pop(key, None)
        admin_python = ADMIN / (
            ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
        )
        result = run(
            [
                str(admin_python),
                "-m",
                "alembic",
                "-c",
                str(ADMIN / "alembic.ini"),
                "upgrade",
                "head",
            ],
            env=dict(env, JUYA_MIGRATION_DATABASE_URL=url),
        )
        if result.returncode:
            raise RuntimeError("Private migration failed: " + safe(result.stdout + result.stderr))
        print("Private MySQL/Redis ready; applied current admin-owned migrations")
        print(json.dumps({"scope": "isolated-bff", "shared_services_restarted": False}))
        result = run([sys.executable, "-m", "pytest", *sys.argv[1:]], env=env)
        print(safe(result.stdout + result.stderr))
        return result.returncode
    except (OSError, RuntimeError) as error:
        print(safe(str(error)))
        return 1
    finally:
        for name in reversed(created):
            # Names are created above in this invocation, UUID-bound and never user-supplied.
            run(["docker", "rm", "-f", name])


if __name__ == "__main__":
    raise SystemExit(main())
