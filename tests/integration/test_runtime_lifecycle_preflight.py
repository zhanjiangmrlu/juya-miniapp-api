import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/local-stack-worker.py"
spec = importlib.util.spec_from_file_location("local_stack_worker", SCRIPT)
assert spec is not None and spec.loader is not None
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def isolated_environment() -> dict[str, str]:
    # 功能:在测试中构造隔离生命周期验收的容器环境变量
    # 参数:
    #     无形参。
    # 返回:测试限定的数据库、Redis、服务地址与环境配置
    return {
        "JUYA_ENVIRONMENT": "local",
        "JUYA_DATABASE_URL": "mysql+asyncmy://user:secret@host.docker.internal:3306/juya_v13_local_e2e",
        "JUYA_REDIS_URL": "redis://host.docker.internal:6398/0",
        "JUYA_INTERNAL_HMAC_SECRET": "test-only-secret",
        "JUYA_MINIAPP_API_BASE_URL": "http://host.docker.internal:18001",
        "JUYA_ADMIN_API_BASE_URL": "http://host.docker.internal:18000",
        "PYTHONPATH": "/workspace/src",
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("JUYA_DATABASE_URL", "mysql+asyncmy://user:secret@host.docker.internal:3306/juya"),
        ("JUYA_REDIS_URL", "redis://host.docker.internal:6379/0"),
        ("JUYA_ENVIRONMENT", "production"),
        ("JUYA_ADMIN_API_BASE_URL", "http://host.docker.internal:8000"),
        ("JUYA_INTERNAL_HMAC_SECRET", "wrong-secret"),
    ],
)
def test_helper_refuses_business_database_and_mismatched_stack(key: str, value: str) -> None:
    # 功能:验证生命周期辅助脚本拒绝业务数据库与不一致的隔离环境
    # 参数:
    #     key: 本轮参数化测试修改的验收环境变量名称
    #     value: 请求或测试配置中待校验的小程序字段内容
    # 返回:无返回值;断言失败时由pytest报告测试失败
    admin = isolated_environment()
    mini = {**admin, key: value}
    with pytest.raises(ValueError):
        helper.validate_stack(admin, mini)


def test_mini_worker_role_and_secrets_are_not_put_on_command_line() -> None:
    # 功能:验证小程序worker角色正确且凭证不出现在命令行
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    env = isolated_environment()
    info = {"Config": {"Image": "test-image", "Env": [f"{k}={v}" for k, v in env.items()]}}
    args, child = helper.command("mini-worker", info, Path("D:/test"))
    assert child["JUYA_PROCESS_TYPE"] == "worker"
    assert child["JUYA_ENABLE_BEAT"] == "true"
    assert child["JUYA_DATABASE_URL"] not in args
    assert child["JUYA_INTERNAL_HMAC_SECRET"] not in " ".join(args)
    assert "--read-only" in args
    assert args[args.index("--tmpfs") + 1] == "/tmp"
