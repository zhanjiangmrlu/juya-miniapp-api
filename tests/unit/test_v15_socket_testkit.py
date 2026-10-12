import importlib.util
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

_SPEC = importlib.util.spec_from_file_location(
    "v15_socket_testkit", Path(__file__).resolve().parents[2] / "scripts/v15_socket_testkit.py"
)
assert _SPEC is not None and _SPEC.loader is not None
_KIT = sys.modules.get(_SPEC.name)
if _KIT is None:
    _KIT = importlib.util.module_from_spec(_SPEC)
    sys.modules[_SPEC.name] = _KIT
    _SPEC.loader.exec_module(_KIT)
FixtureSession, PrivateResources, SocketTestKit = (
    _KIT.FixtureSession,
    _KIT.PrivateResources,
    _KIT.SocketTestKit,
)
audit_loopback = _KIT.audit_loopback

TASK = "a" * 32


def private_env():
    name = "juya_v13_mini_" + TASK
    return {
        "JUYA_ENVIRONMENT": "test",
        "JUYA_V13_ISOLATED_DATABASE": name,
        "JUYA_TEST_DATABASE_URL": "mysql+pymysql://root:secret@127.0.0.1:43001/" + name,
        "JUYA_TEST_REDIS_URL": "redis://127.0.0.1:43002/0",
    }


def containers():
    return [
        {
            "Name": "/juya-v15-bff-" + kind + "-" + TASK,
            "Config": {"Labels": {"juya.isolated-test": TASK}},
            "State": {"Running": True},
            "NetworkSettings": {"Ports": {port: [{"HostIp": "127.0.0.1", "HostPort": host}]}},
        }
        for kind, port, host in [("mysql", "3306/tcp", "43001"), ("redis", "6379/tcp", "43002")]
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"JUYA_ENVIRONMENT": "production"},
        {"JUYA_V13_ISOLATED_DATABASE": "juya_shared"},
        {"JUYA_TEST_DATABASE_URL": "mysql+pymysql://root:secret@shared-db:3306/juya"},
        {"JUYA_TEST_DATABASE_URL": "mysql+pymysql://root:secret@127.0.0.1:43001/shared"},
        {"JUYA_TEST_REDIS_URL": "redis://localhost:43002/0"},
        {"JUYA_TEST_REDIS_URL": "redis://127.0.0.1:43002/1"},
        {"JUYA_TEST_REDIS_URL": "redis://127.0.0.1:43002/0?url=http://external"},
    ],
)
def test_rejects_shared_wrong_identity_and_non_loopback_before_connections(changes):
    with pytest.raises(RuntimeError):
        PrivateResources.from_environment(private_env() | changes)


def test_ownership_requires_generated_container_names_labels_and_exact_loopback_hostport():
    resources = PrivateResources.from_environment(private_env())
    resources.verify_containers(lambda names: containers())
    for field, value in (("label", "another-task"), ("port", "43003"), ("host", "0.0.0.0")):
        rows = containers()
        if field == "label":
            rows[0]["Config"]["Labels"]["juya.isolated-test"] = value
        else:
            key = "HostPort" if field == "port" else "HostIp"
            rows[0]["NetworkSettings"]["Ports"]["3306/tcp"][0][key] = value
        with pytest.raises(RuntimeError):
            resources.verify_containers(lambda names, rows=rows: rows)


def test_tokens_urls_and_keys_are_not_serialized_by_fixture_repr_or_child_env(monkeypatch):
    monkeypatch.setenv("JUYA_TTS_SECRET_KEY", "real-cloud-secret")
    monkeypatch.setenv("OSS_SESSION_TOKEN", "real-sts-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://external-proxy")
    resources = PrivateResources.from_environment(private_env())
    kit = SocketTestKit(resources)
    identity = FixtureSession("user", "session", SecretStr("private-jwt-value"))
    assert "secret@" not in repr(resources) and "private-jwt-value" not in repr(identity)
    env = kit._environment()
    assert not {"JUYA_TTS_SECRET_KEY", "OSS_SESSION_TOKEN", "HTTPS_PROXY"}.intersection(env)
    assert env["JUYA_TTS_ENABLED"] == env["JUYA_ASR_ENABLED"] == "false"
    assert "secret@" not in repr(kit) and kit._jwt_secret not in repr(kit)


def test_testkit_http_client_supports_route_queries_but_rejects_arbitrary_absolute_urls():
    kit = SocketTestKit(PrivateResources.from_environment(private_env()))
    with kit.client() as client:
        # Invoke the actual request hook without contacting either unstarted runtime.
        hook = client.event_hooks["request"][0]
        hook(httpx.Request("GET", kit.miniapp_base_url + "/api/v1/favorite-snapshots?limit=20"))
        for address in ("http://127.0.0.1:80/health/ready", "https://example.invalid/api"):
            with pytest.raises(RuntimeError):
                hook(httpx.Request("GET", address))


def test_socket_audit_blocks_external_connect_and_dns_before_importing_runtime():
    audit_loopback("socket.connect", (None, ("127.0.0.1", 43001)))
    audit_loopback("socket.getaddrinfo", ("127.0.0.1", 43001))
    for event, args in (
        ("socket.connect", (None, ("8.8.8.8", 443))),
        ("socket.getaddrinfo", ("api.external.invalid", 443)),
    ):
        with pytest.raises(RuntimeError):
            audit_loopback(event, args)


def test_fixture_headers_hold_a_real_signed_token_without_printable_fixture_secret():
    from juya_miniapp_api.infrastructure.security.jwt_service import JwtService

    now = datetime.now(UTC)
    signer = JwtService("isolated-unit-signature-key-never-production", kid="v15-isolated")
    token = signer.issue_access_token("owner", "session", now)
    identity = FixtureSession("owner", "session", SecretStr(token))
    claims = signer.decode_access_token(identity.auth_headers["Authorization"][7:], now=now)
    assert (claims["sub"], claims["sid"], claims["aud"]) == ("owner", "session", "miniapp")
    assert token not in repr(identity)


def test_safe_capability_probe_returns_booleans_from_actual_manifest_without_identity(monkeypatch):
    kit = SocketTestKit(PrivateResources.from_environment(private_env()))

    def respond(request):
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"status": "ready"})
        return httpx.Response(
            200,
            json={
                "paths": {
                    "/api/v1/favorite-snapshots": {},
                    "/api/v1/short-readings": {},
                    "/api/v1/pronunciations/{target_id}/resolve": {},
                    "/internal/v1/content/{content_type}/{content_id}/open": {},
                }
            },
        )

    monkeypatch.setattr(kit, "client", lambda: httpx.Client(transport=httpx.MockTransport(respond)))
    status = kit.public_capabilities()
    assert status == {
        "admin_ready": True,
        "miniapp_ready": True,
        "scene_delegation": True,
        "favorite_snapshots": True,
        "short_readings": True,
        "pronunciations": True,
    }
    assert all(isinstance(value, bool) for value in status.values())


def test_actual_admin_python_can_import_private_guard_without_bff_jwt_dependency():
    root = Path(__file__).resolve().parents[2]
    admin = Path(os.getenv("JUYA_CI_ADMIN_SOURCE", str(root.parent / "juya-admin-api")))
    python = Path(
        os.getenv(
            "JUYA_CI_ADMIN_PYTHON",
            str(admin / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")),
        )
    )
    if not python.is_file():
        pytest.skip("actual adjacent admin runtime interpreter required")
    result = subprocess.run(
        [str(python), "-c", "import v15_socket_testkit"],
        cwd=root,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(root / "scripts"), str(root / "src")]),
        },
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, "Admin isolation guard must not require BFF-only JWT dependency"


def test_startup_diagnostics_only_keep_error_types_modules_and_config_names():
    raw = (
        "ModuleNotFoundError: No module named 'jwt'\n"
        "RuntimeError: JUYA_JWT_SECRET=private-key Authorization: Bearer private-token\n"
        "mysql+pymysql://root:private-password@127.0.0.1:12345/private\n"
        "RuntimeError: Isolated socket runtime blocked external name resolution\n"
    )
    diagnostics = _KIT.safe_startup_diagnostics(raw)
    assert "ModuleNotFoundError(module=jwt)" in diagnostics
    assert "field=JUYA_JWT_SECRET" in diagnostics
    assert "audit=external_dns_blocked" in diagnostics
    serialized = repr(diagnostics)
    assert not any(
        value in serialized
        for value in (
            "private-key",
            "private-token",
            "private-password",
            "mysql+",
            "Authorization",
        )
    )
