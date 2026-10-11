# 可复用 V1.5 双服务私有 Socket 测试入口

仅用于测试与协调验收，不是生产会话签发 API。读取技术方案02／06／实际08-并行开发任务与协作约定，保留当前分支、共享 runner 和正常 current_user 实现。

## 文件与调用

- `scripts/v15_socket_testkit.py`：SocketTestKit context manager、PrivateResources 验证、仅测试的真实 JWT/SQL session 方法；CLI只输出安全探测结果。
- `tests/v15_socket_entrypoint.py`：隔离子进程入口，复用已有 socket_runtime_app / socket_admin_runtime_app；导入真实 app 前安装仅 loopback 的 connect/DNS 审计钩子。
- `tests/unit/test_v15_socket_testkit.py`：环境、归属、网络目标、敏感repr与能力探测边界。
- `tests/integration/test_v15_private_socket_kit.py`：两实际 uvicorn、真实 JWT＋数据库 session、BFF→admin HMAC Socket 链路。

由协调者集中启动唯一一次隔离环境，本 helper 不创建 MySQL/Redis 容器，也不修改共享 runner：

```powershell
uv run python scripts/test-v15-isolated.py tests/integration/test_v15_private_socket_kit.py -q --tb=short
```

也可以把该测试文件加入集中执行的完整套件。CLI `uv run python scripts/v15_socket_testkit.py` 只在已经由集中 runner 注入私有环境变量的进程中使用；没有私有标记即拒绝，不支持传入 URL、密码或输出令牌的参数。

协调 Python 代码可从本仓库根目录通过文件路径加载 helper（适用于 pytest、CLI和独立协调器，无需改全局pythonpath）：

```python
import importlib.util
import sys
from pathlib import Path

path = Path("D:/个人/juya/juya-miniapp-api/scripts/v15_socket_testkit.py")
spec = importlib.util.spec_from_file_location("v15_socket_testkit", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

with module.SocketTestKit(module.PrivateResources.from_environment()) as kit:
    identity = kit.issue_session()
    # base URLs, fixture IDs和headers只在协调代码内存中传递，不打印/存盘。
    admin_base_url = kit.admin_base_url
    miniapp_base_url = kit.miniapp_base_url
    scene_ids, revision_ids = kit.scene_ids, kit.revision_ids
    auth_headers = identity.auth_headers
    with kit.client(identity) as client:
        assert client.get("/api/v1/me").status_code == 200
    capabilities = kit.public_capabilities()
```

context manager为同步接口，和现有真实Socket e2e风格相同；应在同步测试/协调入口调用。`kit.public_capabilities()` 仅读取当前实际两服务的 ready/OpenAPI，返回六项 boolean；能力存在不代表供应商或设备验收通过。

## 安全与实际边界

只接收 runner 的 `juya_v13_mini_<32位hex UUID>` 数据库名；数据库/Redis必须是127.0.0.1、精确对应私有库及Redis DB0。开连接前使用本地 Docker npipe/unix engine inspect 核对两个UUID容器的名称、运行状态、`juya.isolated-test` label和精确 HostPort。拒绝任意URL、共享库、外部Docker daemon与不归属当前runner的端口。

两个 uvicorn 只绑定127.0.0.1，subprocess cwd均为本仓绝对根路径；admin使用其已有Python及实际src，由明确PYTHONPATH加载。环境使用操作系统必要字段白名单和本次内存随机密钥，不继承云密钥、代理、原数据库或收费配置；TTS/ASR/content AI关闭，不启动worker。服务stdout/stderr通过PIPE后台排空，原输入仅为内存中有界暂存；只保留/暴露最多12项错误类型、模块标识、JUYA配置字段名和固定网络audit原因，不输出原trace/env/token或URL，不落日志文件或令牌文件。client关闭环境代理，不跟随redirect，request hook仅允许本次两服务的loopback端口（允许真实API query）。

seed复用既有 `tests/e2e/test_miniapp_socket_acceptance.py::seed_contract` 的schema2三开放场景fixture，不声称真实校对/版权/听审/发布。仅临时开启私有环境中的场景模块；退出时恢复原模块enabled，并删除seed自身的开放配置。场景/用户等测试数据由集中runner最终删除其私有容器。context finally仅终止本次自己创建的两个服务进程，不重启共享进程。

`kit.issue_session()` 在再次校验私有归属后，向私有 SQL 写入 ACTIVE account、TEST_FIXTURE profile和15分钟有效 user_session，只存随机refresh secret的SHA256摘要。使用生产 `JwtService.issue_access_token` 按本次随机JWT key与v15-isolated kid真实签名；返回的FixtureSession隐藏token repr，auth_headers仅供内存调用。它不注册任何登录后门，不替换JWT验签，不覆盖生产 current_user；真实runtime仍核签名和SQL session。正常JWT自身期限沿用现有2小时，SQL session的15分钟期限更早约束有效性。

现有外部 WeChat exchange fixture与OSS HEAD fixture保持明确测试边界。Socket测试验证正确JWT/SQL session可访问、无JWT/伪签名/真实签名但未入库sid/撤销会话都被正常鉴权拒绝，及真实BFF→admin HMAC打开同版本场景。没有调用腾讯/阿里云、微信账户、真实OSS文件或TTS；不提供这些外部验收结论。

## 准备证据

2026-10-11：正常 `uv run pytest` 定向执行新增安全单测及Socket集成文件，13 passed、1 isolated-env skip（0.52s）；skip表示尚未在集中私有环境实测Socket。正常完整 collect-only已收集318项成功，没有scripts包导入错误。新文件Ruff/format通过，mypy src+helper共131源文件通过。

上述为首次准备证据；后续真实Socket结果见下面最新实测记录，不以单元测试或skip作为双服务验收。

## 启动问题诊断与修复

协调者首次集中门禁发现同库重复种子的 images/type+sha256 冲突，由其修复既有e2e seed，在唯一约束下复用同一已确认私有fixture资产；helper复用该seed，不改资源或HEAD合同。

随后发现admin启动退出：actual admin Python没有PyJWT依赖，而helper顶层加载BFF JwtService间接导入jwt。实际admin解释器import helper回归先失败 ModuleNotFoundError(jwt)，将JwtService延后至仅BFF协调进程的会话签发方法后通过；不安装或修改admin依赖。两端Settings源码均仅读取环境，没有dotenv配置；该问题与cwd或cookie allow_insecure_http无关。

最新准备证据：15个单元测试通过，含actual admin解释器import和诊断脱敏；无集中私有环境时Socket仍明确skip。Ruff/format及mypy src+helper的131源文件通过。启动失败现在指出admin/miniapp角色及内存安全diagnostics，无原日志落盘。

## 最新真实Socket实测

2026-10-11，协调执行者使用集中UUID私有 MySQL/Redis、实际管理端迁移，定向运行 `tests/integration/test_v15_private_socket_kit.py` 与其另行新增 `tests/integration/test_v15_socket_confirmation.py`，两项通过：`2 passed in 10.59s`。

本工具用例实际通过两个真实uvicorn进程、真实签名JWT与SQL ACTIVE account/session、正常current_user验证、BFF→admin HMAC的schema2场景打开，以及无JWT、未入库ghost sid、伪签名、撤销会话被拒绝。纯公开ready/OpenAPI能力探测也通过。没有模拟生产JWT/session校验，没有云调用/worker或共享服务变更。

该结论仅为两项定向真实Socket通过；本轮完整门禁仍由协调者继续执行，不在本文声明全套门禁通过。WeChat exchange/OSS HEAD仍为明确外部测试fixture，内容seed是隔离发布态fixture，不等于真实微信账号、OSS、版权、校对或听审验收。
