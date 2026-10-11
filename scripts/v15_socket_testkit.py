"""Test-only dual runtime kit consuming the central runner's private resources.

No production auth hook, arbitrary URL option, worker, credential output or log file.
"""

import argparse
import asyncio
import base64
import hashlib
import importlib.util
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.infrastructure.security.jwt_service import JwtService
from juya_miniapp_api.shared.ids import new_ulid

ROOT = Path(__file__).resolve().parents[1]
ADMIN = ROOT.parent / "juya-admin-api"
_ISOLATED = re.compile(r"juya_v13_mini_([0-9a-f]{32})\Z")


def require_loopback(
    url: str, *, schemes: set[str], ports: set[int] | None = None, allow_query: bool = False
) -> None:
    try:
        parts = urlsplit(url)
        valid = parts.scheme in schemes and parts.hostname == "127.0.0.1" and parts.port is not None
        valid = valid and (ports is None or parts.port in ports)
        valid = valid and (allow_query or not parts.query) and not parts.fragment
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError("Only verified private loopback endpoints are allowed")


@dataclass(frozen=True)
class PrivateResources:
    task_id: str
    database_name: str
    database_url: SecretStr = field(repr=False)
    redis_url: SecretStr = field(repr=False)

    @classmethod
    def from_environment(cls, environ: dict[str, str] | None = None) -> "PrivateResources":
        env = dict(os.environ) if environ is None else environ
        match = _ISOLATED.fullmatch(env.get("JUYA_V13_ISOLATED_DATABASE", ""))
        if env.get("JUYA_ENVIRONMENT") != "test" or match is None:
            raise RuntimeError("Central UUID-isolated test runner required")
        database = env.get("JUYA_TEST_DATABASE_URL", "")
        redis = env.get("JUYA_TEST_REDIS_URL", "")
        require_loopback(database, schemes={"mysql+pymysql", "mysql+asyncmy"})
        require_loopback(redis, schemes={"redis"})
        db, cache = urlsplit(database), urlsplit(redis)
        if (
            db.path != "/" + match.group()
            or db.username != "root"
            or not db.password
            or cache.path != "/0"
            or cache.username is not None
            or cache.password is not None
        ):
            raise RuntimeError("Private runner database and Redis identity mismatch")
        return cls(match[1], match.group(), SecretStr(database), SecretStr(redis))

    def verify_containers(
        self, inspector: Callable[[list[str]], list[dict[str, Any]]] | None = None
    ) -> None:
        inspect = inspector or _inspect_local_containers
        names = ["juya-v15-bff-mysql-" + self.task_id, "juya-v15-bff-redis-" + self.task_id]
        rows = inspect(names)
        if len(rows) != 2:
            raise RuntimeError("Private runner container ownership is unavailable")
        ports = [
            urlsplit(self.database_url.get_secret_value()).port,
            urlsplit(self.redis_url.get_secret_value()).port,
        ]
        for row, name, container_port, port in zip(
            rows, names, ("3306/tcp", "6379/tcp"), ports, strict=True
        ):
            labels = row.get("Config", {}).get("Labels", {}) or {}
            bindings = row.get("NetworkSettings", {}).get("Ports", {}).get(container_port)
            if (
                row.get("Name") != "/" + name
                or not row.get("State", {}).get("Running")
                or labels.get("juya.isolated-test") != self.task_id
                or bindings != [{"HostIp": "127.0.0.1", "HostPort": str(port)}]
            ):
                raise RuntimeError(
                    "Container label or loopback port does not belong to this runner"
                )


def _command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
        timeout=15,
    )


def _inspect_local_containers(names: list[str]) -> list[dict[str, Any]]:
    # Resolve the endpoint first, then explicitly select the local engine pipe/socket.
    endpoint = _command(
        ["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"]
    )
    try:
        host = json.loads(endpoint.stdout) if not endpoint.returncode else None
    except ValueError:
        host = None
    if not isinstance(host, str) or not host.startswith(("npipe://", "unix:///")):
        raise RuntimeError("A local Docker engine is required for ownership verification")
    result = _command(["docker", "--host", host, "inspect", *names])
    try:
        rows = json.loads(result.stdout) if not result.returncode else None
    except ValueError:
        rows = None
    if not isinstance(rows, list):
        raise RuntimeError("Private runner containers are unavailable")
    return rows


def audit_loopback(event: str, args: tuple[object, ...]) -> None:
    if event == "socket.connect":
        address = args[1]
        if not isinstance(address, tuple) or address[0] != "127.0.0.1":
            raise RuntimeError("Isolated socket runtime blocked an external connection")
    if event == "socket.getaddrinfo" and args[0] != "127.0.0.1":
        raise RuntimeError("Isolated socket runtime blocked external name resolution")


@dataclass(frozen=True)
class FixtureSession:
    user_id: str
    session_id: str
    access_token: SecretStr = field(repr=False)

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.access_token.get_secret_value()}


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class SocketTestKit:
    """Context manager returning in-memory endpoints, fixture IDs and actual JWT sessions."""

    def __init__(self, resources: PrivateResources):
        self.resources = resources
        self.admin_base_url = "http://127.0.0.1:" + str(_free_port())
        self.miniapp_base_url = "http://127.0.0.1:" + str(_free_port())
        self.scene_ids: list[str] = []
        self.revision_ids: list[str] = []
        self.content: dict[str, Any] = {}
        self.cover_id = ""
        self._jwt_secret = secrets.token_urlsafe(32)
        self._hmac_secret = secrets.token_urlsafe(32)
        self._processes: list[subprocess.Popen[bytes]] = []
        self._modules: list[dict[str, Any]] = []
        self._configs: list[int] = []
        self._ports = {
            int(urlsplit(self.admin_base_url).port or 0),
            int(urlsplit(self.miniapp_base_url).port or 0),
        }

    def _environment(self) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "SYSTEMDRIVE",
                "COMSPEC",
                "PATHEXT",
            }
        }
        return {
            **env,
            "PYTHONIOENCODING": "utf-8",
            "JUYA_ENVIRONMENT": "test",
            "JUYA_V13_ISOLATED_DATABASE": self.resources.database_name,
            "JUYA_TEST_DATABASE_URL": self.resources.database_url.get_secret_value(),
            "JUYA_TEST_REDIS_URL": self.resources.redis_url.get_secret_value(),
            "JUYA_DATABASE_URL": self.resources.database_url.get_secret_value().replace(
                "mysql+pymysql://", "mysql+asyncmy://", 1
            ),
            "JUYA_REDIS_URL": self.resources.redis_url.get_secret_value(),
            "JUYA_INTERNAL_HMAC_SECRET": self._hmac_secret,
            "JUYA_JWT_SECRET": self._jwt_secret,
            "JUYA_JWT_KEY_ID": "v15-isolated",
            "JUYA_FIELD_ENCRYPTION_KEY_BASE64": base64.urlsafe_b64encode(
                secrets.token_bytes(32)
            ).decode(),
            "JUYA_FIELD_LOOKUP_KEY": secrets.token_urlsafe(32),
            "JUYA_WECHAT_APP_ID": "wx-acceptance",
            "JUYA_WECHAT_APP_SECRET": "provider-fixture",
            "JUYA_LOCAL_DEV_MODE": "false",
            "JUYA_LOG_LEVEL": "CRITICAL",
            "JUYA_CONTENT_SECURITY_ENABLED": "false",
            "JUYA_TTS_ENABLED": "false",
            "JUYA_ASR_ENABLED": "false",
            "JUYA_CONTENT_AI_ENABLED": "false",
            "JUYA_OSS_REGION": "cn-test",
            "JUYA_OSS_BUCKET": "acceptance-local",
            "JUYA_OSS_EXPECTED_BUCKET": "acceptance-local",
            "OSS_ACCESS_KEY_ID": "acceptance-placeholder",
            "OSS_ACCESS_KEY_SECRET": "acceptance-placeholder",
            "JUYA_ADMIN_API_BASE_URL": self.admin_base_url,
            "JUYA_MINIAPP_API_BASE_URL": self.miniapp_base_url,
        }

    def __enter__(self) -> "SocketTestKit":
        self.resources.verify_containers()
        try:
            asyncio.run(self._seed_content())
            base_env = self._environment()
            for role, python, source, endpoint in (
                ("admin", ADMIN / ".venv/Scripts/python.exe", ADMIN / "src", self.admin_base_url),
                ("miniapp", Path(sys.executable), ROOT / "src", self.miniapp_base_url),
            ):
                env = {
                    **base_env,
                    "JUYA_SOCKET_TEST_ROLE": role,
                    "PYTHONPATH": os.pathsep.join(
                        [str(source), str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "scripts")]
                    ),
                }
                self._processes.append(
                    subprocess.Popen(
                        [
                            str(python),
                            "-m",
                            "uvicorn",
                            "v15_socket_entrypoint:app",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(urlsplit(endpoint).port),
                            "--no-access-log",
                            "--log-level",
                            "critical",
                        ],
                        cwd=ROOT,
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                )
            with self.client() as client:
                for endpoint in (self.admin_base_url, self.miniapp_base_url):
                    deadline = time.monotonic() + 40
                    while True:
                        if any(process.poll() is not None for process in self._processes):
                            raise RuntimeError("Private socket runtime exited before readiness")
                        try:
                            if client.get(endpoint + "/health/ready").status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Private socket runtime readiness timed out")
                        time.sleep(0.1)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        for process in self._processes:
            process.terminate()
        for process in self._processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self._processes.clear()
        if self._modules or self._configs:
            asyncio.run(self._restore_seed_configuration())

    def client(self, session: FixtureSession | None = None) -> httpx.Client:
        def check(request: httpx.Request) -> None:
            require_loopback(
                str(request.url), schemes={"http"}, ports=self._ports, allow_query=True
            )
            if request.url.username or request.url.password:
                raise RuntimeError("Credentials in request URLs are forbidden")

        return httpx.Client(
            base_url=self.miniapp_base_url,
            timeout=10,
            trust_env=False,
            headers=session.auth_headers if session else None,
            event_hooks={"request": [check]},
        )

    def public_capabilities(self) -> dict[str, bool]:
        """Read only public readiness/OpenAPI and return safe capability booleans."""
        with self.client() as client:
            admin_ready = client.get(self.admin_base_url + "/health/ready").status_code == 200
            mini_ready = client.get(self.miniapp_base_url + "/health/ready").status_code == 200
            admin = client.get(self.admin_base_url + "/openapi.json")
            mini = client.get(self.miniapp_base_url + "/openapi.json")
            admin_paths = admin.json().get("paths", {}) if admin.status_code == 200 else {}
            mini_paths = mini.json().get("paths", {}) if mini.status_code == 200 else {}
        return {
            "admin_ready": admin_ready,
            "miniapp_ready": mini_ready,
            "scene_delegation": "/internal/v1/content/{content_type}/{content_id}/open"
            in admin_paths,
            "favorite_snapshots": "/api/v1/favorite-snapshots" in mini_paths,
            "short_readings": "/api/v1/short-readings" in mini_paths,
            "pronunciations": "/api/v1/pronunciations/{target_id}/resolve" in mini_paths,
        }

    async def _seed_content(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "_juya_socket_seed", ROOT / "tests/e2e/test_miniapp_socket_acceptance.py"
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Existing socket content seed is unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        database = self.resources.database_url.get_secret_value()
        self.scene_ids, self.revision_ids, self.content, self.cover_id = await module.seed_contract(
            database
        )
        engine = create_engine(database.replace("mysql+pymysql://", "mysql+asyncmy://", 1))
        try:
            async with create_session_factory(engine)() as sql, sql.begin():
                self._modules = [
                    dict(row)
                    for row in (
                        await sql.execute(
                            text("SELECT module_type,enabled FROM learning_module_config")
                        )
                    )
                    .mappings()
                    .all()
                ]
                await sql.execute(
                    text(
                        "UPDATE learning_module_config SET enabled=1 "
                        "WHERE module_type='scene_learning'"
                    )
                )
                for scene in self.scene_ids:
                    config = await sql.scalar(
                        text(
                            "SELECT i.config_id FROM open_scene_item i "
                            "JOIN scene s ON s.id=i.scene_id WHERE s.public_id=:id"
                        ),
                        {"id": scene},
                    )
                    if config is not None and int(config) not in self._configs:
                        self._configs.append(int(config))
        finally:
            await engine.dispose()

    async def _restore_seed_configuration(self) -> None:
        engine = create_engine(
            self.resources.database_url.get_secret_value().replace(
                "mysql+pymysql://", "mysql+asyncmy://", 1
            )
        )
        try:
            async with create_session_factory(engine)() as sql, sql.begin():
                for row in self._modules:
                    await sql.execute(
                        text(
                            "UPDATE learning_module_config SET enabled=:enabled "
                            "WHERE module_type=:module_type"
                        ),
                        row,
                    )
                for config in self._configs:
                    await sql.execute(
                        text("DELETE FROM open_scene_item WHERE config_id=:id"), {"id": config}
                    )
                    await sql.execute(
                        text("DELETE FROM open_scene_config WHERE id=:id"), {"id": config}
                    )
        finally:
            self._modules.clear()
            self._configs.clear()
            await engine.dispose()

    def issue_session(self) -> FixtureSession:
        """Test-only SQL fixture session, validated by the unchanged production current_user."""
        self.resources.verify_containers()
        return asyncio.run(self._issue_session())

    async def _issue_session(self) -> FixtureSession:
        now = datetime.now(UTC)
        user, identity = new_ulid(now), new_ulid(now)
        engine = create_engine(
            self.resources.database_url.get_secret_value().replace(
                "mysql+pymysql://", "mysql+asyncmy://", 1
            )
        )
        try:
            async with create_session_factory(engine)() as sql, sql.begin():
                await sql.execute(
                    text(
                        "INSERT INTO user_account(public_id,juya_number,status) "
                        "VALUES(:id,:number,'ACTIVE')"
                    ),
                    {"id": user, "number": "JY" + user[-12:]},
                )
                owner = await sql.scalar(text("SELECT LAST_INSERT_ID()"))
                await sql.execute(
                    text(
                        "INSERT INTO user_profile(user_id,nickname,source) "
                        "VALUES(:user,'Isolated fixture','TEST_FIXTURE')"
                    ),
                    {"user": owner},
                )
                await sql.execute(
                    text(
                        "INSERT INTO user_session(id,user_id,refresh_token_hash,expires_at) "
                        "VALUES(:id,:user,:hash,:expires)"
                    ),
                    {
                        "id": identity,
                        "user": owner,
                        "hash": hashlib.sha256(secrets.token_bytes(32)).digest(),
                        "expires": (now + timedelta(minutes=15)).replace(tzinfo=None),
                    },
                )
            token = JwtService(self._jwt_secret, kid="v15-isolated").issue_access_token(
                user, identity, now
            )
            return FixtureSession(user, identity, SecretStr(token))
        finally:
            await engine.dispose()


def main() -> int:
    argparse.ArgumentParser(
        description="Probe only central-runner private socket runtimes"
    ).parse_args()
    try:
        with SocketTestKit(PrivateResources.from_environment()) as kit:
            identity = kit.issue_session()
            with kit.client(identity) as client:
                response = client.get("/api/v1/me")
                if response.status_code != 200:
                    raise RuntimeError("Actual JWT/session authentication probe failed")
            print(
                json.dumps(
                    {
                        "scope": "private-dual-socket",
                        "authenticated": True,
                        "scene_count": len(kit.scene_ids),
                        "cloud_acceptance": False,
                    }
                )
            )
        return 0
    except Exception:
        # SQL/network exceptions can contain credentials; never serialize their details.
        print("Private socket testkit failed; no credential details emitted")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
