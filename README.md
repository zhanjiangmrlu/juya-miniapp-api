# juya-miniapp-api

句芽微信小程序的独立 FastAPI BFF，负责微信会话、本人资料、学习进度、收藏、打卡、消息、反馈编排和账号隐私生命周期。内容、正式/限时权益与反馈主数据由 `juya-admin-api` 提供，本服务通过 HMAC 签名的内部接口访问。

## 开发环境

- Python `3.13.x`
- `uv`
- MySQL `8.4`（必须使用真实 MySQL 执行并发/约束测试）
- Redis `7.x`
- `juya-admin-api` 及其数据库迁移

### 小程序本地联调

不连接微信、OSS、MySQL、Redis 和 admin-api 时，可启动隔离的本地开发模式：

```powershell
./scripts/start-local.ps1
```

服务监听 `http://127.0.0.1:8000`，提供小程序 V1.3 页面所需的契约数据、可变反馈/消息状态、静音音频和本地上传接收端。该模式仅在 `JUYA_ENVIRONMENT=local|test` 且 `JUYA_LOCAL_DEV_MODE=true` 时启用，生产环境不会注册这些路由。

安装锁定依赖：

```powershell
uv sync --locked
```

运行 API：

```powershell
$env:JUYA_PROCESS_TYPE = "api"
./scripts/entrypoint.ps1
```

运行 worker（开发环境可同时启用 Beat，生产只允许一个 Beat 实例）：

```powershell
$env:JUYA_PROCESS_TYPE = "worker"
$env:JUYA_ENABLE_BEAT = "true"
./scripts/entrypoint.ps1
```

## 必需环境变量

变量统一使用 `JUYA_` 前缀：

| 变量 | 用途 |
|---|---|
| `JUYA_DATABASE_URL` | `mysql+asyncmy://` RDS/MySQL 地址 |
| `JUYA_REDIS_URL` | Redis Broker、缓存、限流和 nonce 存储 |
| `JUYA_ADMIN_API_BASE_URL` | admin-api 内网地址 |
| `JUYA_INTERNAL_HMAC_SECRET` | 两服务内部请求签名密钥 |
| `JUYA_JWT_SECRET` / `JUYA_JWT_KEY_ID` | access token 签名和密钥版本 |
| `JUYA_FIELD_ENCRYPTION_KEY_BASE64` | 16/24/32 字节 AES 密钥的 URL-safe Base64 |
| `JUYA_FIELD_LOOKUP_KEY` | OpenID/微信号精确匹配 HMAC 密钥 |
| `JUYA_WECHAT_APP_ID` / `JUYA_WECHAT_APP_SECRET` | 微信 code2session |
| `JUYA_OSS_ENDPOINT` / `JUYA_OSS_BUCKET` | 私有 OSS Bucket |
| `JUYA_OSS_ACCESS_KEY_ID` / `JUYA_OSS_ACCESS_KEY_SECRET` | OSS 临时上传策略签名 |

密钥应由阿里云 KMS 或云效密钥变量注入，禁止写入仓库。生产 API 只经 SLB/Nginx 暴露 HTTPS；worker 不开放端口。RDS、Redis、OSS 和 admin-api 仅走内网。

## 数据库和跨服务契约

当前 schema 由相邻 `juya-admin-api/migrations` 管理。启动前先执行该项目的 Alembic 迁移；`/health/ready` 会拒绝低于最低版本的 schema。admin-api 需要提供学习目录、访问投影、场景 open、正文/媒体、权益、反馈和账号删除接口。注销使用 transactional outbox 和唯一事件 ID，admin-api 完成后回调 `/internal/v1/users/{user_id}/deletion-cleanup-result`。

## Docker Compose

先复制 `.env.example` 为 `.env` 并填写密钥，再执行：

```powershell
docker compose -f docker-compose.dev.yml up --build
```

Compose 提供 MySQL、Redis、API 和 worker，但不会越权代替 admin-api 执行迁移。镜像以非 root 用户运行，支持只读根文件系统；同一镜像由 `JUYA_PROCESS_TYPE=api|worker` 选择进程。

## 验证

```powershell
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest --cov=juya_miniapp_api --cov-report=term-missing
```

要启用真实基础设施测试，额外设置：

```powershell
$env:JUYA_TEST_DATABASE_URL = "mysql+asyncmy://..."
$env:JUYA_TEST_REDIS_URL = "redis://..."
```

未设置时，相应 MySQL/Redis 集成测试会明确标记为 skipped；单元、契约和不依赖外部服务的端到端测试仍会执行。

云效的 verify 阶段会强制要求两个测试 URL，并要求目标 MySQL 已执行 admin-api schema 迁移；缺少真实基础设施或总覆盖率低于 80% 时流水线直接失败，不允许以 skipped 结果发布。
