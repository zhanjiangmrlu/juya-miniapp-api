# 小程序后端本地计时与镜像瘦身验收

本次依据用户确认的“分段计时和保守瘦身，保留现有检查和回滚机制，完成本地验证并对比优化前后的包大小与耗时，暂不部署”实施。
涉及 `juya-miniapp-api` 镜像和 `juya-cli` 的现行发布引擎；不修改业务接口、依赖版本、数据库迁移规则或部署目标。UI 与设备验收不适用。

## 需求追踪与方案

下列编号为本次任务编号。

| 编号 | 用户要求 | 原行为与本次实现 | 验收证据与边界 |
| --- | --- | --- | --- |
| OPT-1 | 分段计时 | CLI 原来只记录发布阶段总耗时；新增 `engine/release_timing.py`，在 `publish.py`、`remote.py`、`api.py` 包裹现有步骤，输出开始/结果/秒数 | 真实子进程成功/失败、上传失败阻断、远端回滚计时测试；不连接 ECS |
| OPT-2 | 保守瘦身 | `Dockerfile` 在复制最终层前调用 `deploy/prune-runtime.py`，清除基础层 pip、Python 头文件以及 asyncmy/uvloop 的 `.c/.h/.pyx/.pxd` 文件 | 实际构建、包体比较、镜像内原生库及 API/Worker/Beat 验证；保留 `.so`、字节码、Python 源码和依赖元数据 |
| OPT-3 | 保留检查和回滚 | 依赖锁定/哈希检查、制品校验、镜像身份、发布锁、运行预检、健康检查、180 秒 Worker 停机预算、备份和回滚步骤不变 | 原有发布事务回归；计时文件不可写不覆盖成功结果或原异常 |
| OPT-4 | 本地前后对比 | 同一源码 `a338ac00574d27b8530ea8c095b093074e918be9`，原 Dockerfile 与本次工作区 Dockerfile，Linux/amd64，三轮交替测量 | 已预热本机缓存；不是云效冷构建、网络上传或 ECS 实测 |
| OPT-5 | 暂不部署 | 仅本机 Docker、测试 UUID 数据库/Redis、诊断包 | 未调用 SSH/SCP，未修改线上配置，未提交或推送 |

## 计时记录

- 本机：`<工作区>/.local-release/runs/<run-id>/timings.jsonl`，另保留原 `release.log`。
- API 服务器：发布根目录 `timings.jsonl`；仅在以后使用新 CLI 实际发布时产生。远端细分记录输出 stderr，不污染用于版本选择的 JSON stdout。
- 本机记录包含 `check_1..N`、`checks`、`docker_build`、`docker_save`、`image_identity`、`image_compress`、`bundle_package`、`upload_prepare`、`upload`、`remote_publish`、`public_health`。
- API 远端细分包括 `bundle_validate`（终端）、`release_validate_install`、`compose_preflight`、`image_load`（含身份校验）、`runtime_preflight`、`stop_beat/worker/api`、`start_api`、`api_readiness`、`start_background`、`service_health`、`application_restore`。
- 嵌套计时不能相加；远端总耗时包括内部各阶段。计时只记录固定阶段名、项目、时间、状态，不复制参数、环境变量或异常正文。
- 日常部署入口依据 `juya-cli/engine/ORIGIN.md`；历史 PowerShell 兼容引擎未同步新增计时，云效脚本已有的构建计时保持原样。所有使用当前 Dockerfile 的构建都会使用瘦身规则。

## 本地对比

原始数据和复现脚本位于工作区 `.local-release/benchmarks/miniapp-20261015/`：
`compare.py`、`measurements.json`、`summary.json`、`before-N/commands.log`、`after-N/commands.log`。
诊断镜像使用独立 `juya-miniapp-api:slim-before-20261015` / `slim-after-20261015` 标签，不覆盖已有发布标签。诊断包含 `diagnostic_only` 标记，不能当作干净提交的发布候选。

三轮交替运行的中位数，秒数使用单调时钟：

| 项目 | 优化前 | 优化后 |
| --- | ---: | ---: |
| 最外层压缩包 | 99,694,468 字节（95.08 MiB） | 96,584,154 字节（92.11 MiB） |
| Docker 构建（缓存已热） | 1.556 秒 | 1.482 秒 |
| 镜像导出 | 2.016 秒 | 1.924 秒 |
| 镜像身份校验 | 0.135 秒 | 0.134 秒 |
| 内层 gzip | 2.095 秒 | 2.188 秒 |
| 校验清单及外层打包 | 2.153 秒 | 2.286 秒 |
| 构建至打包合计 | 8.002 秒 | 8.053 秒 |
| 包校验与解包 | 0.304 秒 | 0.370 秒 |
| 本机 Docker 加载（层已存在） | 7.383 秒 | 7.008 秒 |

包体减少 3,110,314 字节，约 2.97 MiB / 3.12%。仅此保守瘦身没有证明本地构建打包显著加速；0.051 秒的中位数差异不能作为性能改善。加载测试只覆盖本机已有层，不代表 ECS 首次导入或线上 111 秒发布链路的改善。gzip 级别、归档格式和校验机制保持原样。

## 检查与回归

- TDD：缺失计时、清理实现时新测试先失败；真实基础镜像的 pip 符号链接分支另补失败测试后修复。删除只解除明确选定的 pip 启动链接，不跟随其目标；目录与父路径链接仍拒绝。
- Miniapp：`uv sync --frozen`、Ruff 检查/格式、mypy（131 个源文件）通过；`scripts/test-v15-isolated.py -q` 得到 **487 passed, 1 skipped**，临时资源已清理。保留一项既有 Starlette TestClient 弃用警告。
- CLI Windows：Python 引擎首次 **170 passed, 41 skipped**；共享工作区出现另一项压缩参数改动后再次验证为 **175 passed, 41 skipped**；Node **54 passed**。并发改动保留，miniapp recipe 未设置该参数，仍使用原外层 gzip 级别 1。
- CLI Linux：在有 git 的一次性容器内运行同一引擎用例，**201 passed, 10 skipped**，补验 Windows 未执行的 POSIX 发布事务。剩余跳过为平台限定用例，不宣称全部用例均执行。
- CLI Ruff 检查及本次修改文件格式检查通过。全目录格式检查另发现原有 `tests/engine/test_preflight_liveness.py` 不符合格式；该文件未在本次修改。
- 初次 Linux 测试将仓库挂载在 `/work`，触发原有 `HERE.parents[2]` 默认路径越界；使用与实际工作区一致的层级后重跑通过，未借此改动无关默认路径逻辑。
- 初次 miniapp 全套测试发现新测试捕获 UTF-8 子进程输出时使用了 Windows 默认 GBK；已明确编码后重跑通过，未放宽断言。

## 镜像内实际运行

使用 `smoke.py`、`runtime_probe.py` 对优化前后镜像分别验证，结果见同一证据目录的
`runtime-results.json`、`runtime-before.log`、`runtime-after.log`、`smoke.log`：

- 两者均为只读文件系统、非 root（UID 999），网络限于本次创建的内部 Docker 网络，连接临时 MySQL/Redis；未读取现有业务凭据。
- asyncmy 在真实 MySQL 执行 `SELECT 42`，uvloop 驱动异步事件循环，Redis PING 成功。
- Pillow 实际编码/解码 PNG、JPEG、WebP，cryptography 和 PyCryptodome 执行加解密/加密，JWT 签发验签成功。
- 加载真实业务路由（`JUYA_LOCAL_DEV_MODE=false`），`/health/live` 返回 200，匿名 `/api/v1/me` 返回 401 / `ACCESS_TOKEN_REQUIRED`。
- 实际 Uvicorn 入口分别启动三次并访问 `/health/live`；Celery Worker 连接临时 Redis 并报告 ready，Beat 实际启动。此处不宣称完整业务 readiness 或任务消费验收。
- 前后 **68 个已安装包及版本完全一致，2,528 个 pyc 文件数量一致**。
- 所有本次创建的容器、数据卷、内部网络在 `finally` 清理；未操作其他会话容器。

首次 smoke 的诊断配置生成了错误长度的 AES 测试密钥，原镜像按契约拒绝启动；修正为由 32 字节生成的 Base64 测试值后，两种镜像全部通过。未修改应用密钥校验。

只读独立代码审查未发现阻断问题，指出的原生依赖和真实进程运行验证已补齐。

本地测试和诊断包不代表线上部署、真实微信/OSS 业务验收。本次未部署。
