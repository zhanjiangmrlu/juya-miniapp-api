"""Clone only the existing isolated stack's environment; default is read-only."""

import argparse
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

ROLES = {
    "mini-worker": ("juya-v13-mini-api", "juya-v13-mini-worker", "juya-miniapp-api"),
    "admin-domain": ("juya-v13-admin-api", "juya-v13-worker-domain", "juya-admin-api"),
    "admin-beat": ("juya-v13-admin-api", "juya-v13-beat", "juya-admin-api"),
}


def inspect_container(name: str) -> dict:
    # 功能:读取本地Docker容器配置与运行信息
    # 参数:
    #     name: 需要检查的Docker容器名称
    # 返回:Docker容器配置、环境变量与运行状态
    result = subprocess.run(["docker", "inspect", name], capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"Container unavailable: {name}")
    return json.loads(result.stdout)[0]


def environment(info: dict) -> dict[str, str]:
    # 功能:从容器配置提取环境变量映射
    # 参数:
    #     info: Docker inspect返回的容器配置与运行信息
    # 返回:容器环境变量名称到字符串值的映射
    return dict(item.split("=", 1) for item in info["Config"]["Env"] if "=" in item)


def validate_stack(admin: dict[str, str], mini: dict[str, str]) -> None:
    # 功能:校验管理端与小程序隔离数据库和内部签名配置一致
    # 参数:
    #     admin: 管理端容器导出的验收环境变量
    #     mini: 小程序容器导出的验收环境变量
    # 返回:无返回值。
    for env in (admin, mini):
        if env.get("JUYA_ENVIRONMENT") not in {"local", "test"}:
            raise ValueError("Only local/test stack permitted")
        database = urlsplit(env.get("JUYA_DATABASE_URL", ""))
        redis = urlsplit(env.get("JUYA_REDIS_URL", ""))
        if database.path != "/juya_v13_local_e2e" or database.port != 3306:
            raise ValueError("Database must be the named isolated fixture database")
        if database.hostname != "host.docker.internal":
            raise ValueError("Unexpected isolated database host")
        if (redis.hostname, redis.port, redis.path) != ("host.docker.internal", 6398, "/0"):
            raise ValueError("Redis must be isolated port 6398 database 0")
    if admin.get("JUYA_MINIAPP_API_BASE_URL") != "http://host.docker.internal:18001":
        raise ValueError("Admin callback must target isolated mini API")
    if mini.get("JUYA_ADMIN_API_BASE_URL") != "http://host.docker.internal:18000":
        raise ValueError("Mini worker must target isolated admin API")
    if not admin.get("JUYA_INTERNAL_HMAC_SECRET") or (
        admin["JUYA_INTERNAL_HMAC_SECRET"] != mini.get("JUYA_INTERNAL_HMAC_SECRET")
    ):
        raise ValueError("Cross-service HMAC mismatch")


def command(role: str, info: dict, root: Path) -> tuple[list[str], dict[str, str]]:
    # 功能:按worker角色生成本地启动命令与环境变量
    # 参数:
    #     role: 本地worker启动角色,决定任务模块与队列
    #     info: Docker inspect返回的容器配置与运行信息
    #     root: 当前后端项目的绝对工作目录
    # 返回:worker启动参数列表及其进程环境变量
    _, target, repo = ROLES[role]
    child = environment(info)
    if role == "mini-worker":
        child["JUYA_PROCESS_TYPE"] = "worker"
        child["JUYA_ENABLE_BEAT"] = "true"
    else:
        child["JUYA_PROCESS_ROLE"] = (
            "admin-worker-domain" if role == "admin-domain" else "admin-beat"
        )
    args = [
        "docker",
        "run",
        "-d",
        "--init",
        "--name",
        target,
        "--read-only",
        "--tmpfs",
        "/tmp",
        "--security-opt",
        "no-new-privileges:true",
        "--mount",
        f"type=bind,source={root / repo / 'src'},target=/workspace/src,readonly",
    ]
    for key in child:
        if key.startswith(("JUYA_", "OSS_")) or key == "PYTHONPATH":
            args.extend(["-e", key])  # Values stay in the child environment, not argv/logs.
    args.append(info["Config"]["Image"])
    return args, {**os.environ, **child}


def main() -> None:
    # 功能:校验隔离容器配置并启动指定角色worker
    # 参数:
    #     无形参。
    # 返回:无返回值。
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=ROLES, required=True)
    parser.add_argument("--start", action="store_true", help="Root-only explicit service creation")
    options = parser.parse_args()
    admin = inspect_container("juya-v13-admin-api")
    mini = inspect_container("juya-v13-mini-api")
    validate_stack(environment(admin), environment(mini))
    source, target, _ = ROLES[options.role]
    info = mini if source == "juya-v13-mini-api" else admin
    args, child = command(options.role, info, Path(__file__).resolve().parents[2])
    print(
        json.dumps(
            {
                "role": options.role,
                "container": target,
                "image": info["Config"]["Image"],
                "database": "juya_v13_local_e2e",
                "redis_port": 6398,
                "hmac_matches": True,
                "action": "start" if options.start else "read-only preflight",
            }
        )
    )
    if not options.start:
        return
    existing = subprocess.run(["docker", "inspect", target], capture_output=True, check=False)
    if existing.returncode == 0:
        raise RuntimeError(f"Refusing to replace existing container: {target}")
    result = subprocess.run(args, env=child, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("Isolated worker start failed; inspect Docker locally")
    print("Created isolated worker: " + target)


if __name__ == "__main__":
    main()
