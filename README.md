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

注销 Worker 调用管理服务 `POST /internal/v1/account-deletions`，携带 `user_id`、
`deletion_request_id` 和唯一 `event_id`。管理服务清理和结果 outbox 写入同一事务，
通过带 HMAC 的清理结果回调通知本服务；暂时失败按退避重试，不以一次 HTTP 提交代替闭环。

2026-10-01 的本地联调使用独立 `juya_v13_local_e2e` 数据库、Redis `6398/0`、
管理 API `18000` 和用户 API `18001`。已有两个隔离 API 容器时，可以从工作区根目录执行：

```powershell
# 默认只检查环境、数据库、内部地址与 HMAC 是否匹配
& juya-miniapp-api\.venv\Scripts\python.exe juya-miniapp-api\scripts\local-stack-worker.py --role mini-worker
# 确认该隔离环境后启动；同名容器已存在则拒绝替换
& juya-miniapp-api\.venv\Scripts\python.exe juya-miniapp-api\scripts\local-stack-worker.py --role mini-worker --start
& juya-miniapp-api\.venv\Scripts\python.exe juya-miniapp-api\scripts\local-stack-worker.py --role admin-domain --start
& juya-miniapp-api\.venv\Scripts\python.exe juya-miniapp-api\scripts\local-stack-worker.py --role admin-beat --start
```

该工具继承已运行隔离容器的镜像和环境，只启动指定 Worker/Beat，不初始化数据库、改写 `.env`
或切换主站环境。开发用 mini Worker 以 `JUYA_PROCESS_TYPE=worker` 和 `JUYA_ENABLE_BEAT=true`
启动。生产环境仍需按部署文档管理独立且唯一的 Beat 实例。

V1.3 最低 schema 为 `0015`。完整场景返回 `scene_id/revision_id/content_version/content`，其中 `content` 使用严格的共享正文类型；预览仅返回标题、系列、封面、简介等白名单元数据，不写入学习历史。词卡查询及收藏请求须携带 `revision_id/entry_version/source_locator`；收藏使用管理后端授权返回的词卡和句子快照，保留不同修订的历史来源。独立词卡发音可以为空。

场景资源使用 `GET /api/v1/scenes/{scene_id}/resources/{resource_id}/signed-url?revision_id=...` 获取短期授权 URL。服务携带用户和修订信息通过 HMAC 委托管理后端校验引用关系；上游不可用、响应类型不符或 URL 已到期时拒绝访问。资源响应禁止缓存。

复习队列通过 `GET /api/v1/reviews/queue?limit=50&cursor=...` 完整分页，单页最多 100 条，无总量上限。今日任务不再截断十条。联系方式提示曝光由认证的 `POST /api/v1/me/contact/prompt-exposures` 记录，须传 `Idempotency-Key`；小程序界面的曝光触发在后续前端批次接入。

真实成功的学习、复习、收藏、联系方式及账号生命周期操作在同一事务中追加唯一业务事件。活跃用户定义为成功打开获授权场景、保存学习进度、首次完成场景或首次完成复习的用户；北京时间日、自然周（周一至周日）、自然月分别去重。账号注销生效时移除事件用户关联并将事件键改为匿名随机 ID，保留周期时间和匿名统计。每日聚合由管理后端执行，不能用账号 ACTIVE 状态代替行为活跃。

## Docker Compose

先复制 `.env.example` 为 `.env` 并填写密钥，再执行：

```powershell
docker compose -f docker-compose.dev.yml up --build
```

Compose 提供 MySQL、Redis、API 和 worker，但不会越权代替 admin-api 执行迁移。镜像以非 root 用户运行，支持只读根文件系统；同一镜像由 `JUYA_PROCESS_TYPE=api|worker` 选择进程。

## 验证

本地、测试和生产默认 `JUYA_CONTENT_SECURITY_ENABLED=false`，不执行反馈文本内容过滤。
反馈字数、截图数量、归属和登录权限校验保留。原过滤代码保留，后续设置该变量为
`true` 并重启 API 即可恢复；管理服务的图片和音频审核开关见其 README。

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

V1.3 当前接口见 [接口文档](docs/api/v13-miniapp-api.md)、[实际 OpenAPI 快照](docs/contracts/miniapp-api.json) 与 [生成类型](docs/contracts/miniapp-api.d.ts)：56 个 OpenAPI 操作及 1 个隐藏 metrics。Ruff、格式、mypy 与隔离 MySQL/Redis 测试 102 passed/1 skipped 通过。真实跨服务场景、资源授权及暂停权益验证见 [交付记录](../juya-admin-api/docs/implementation/v13-content/evidence.md)；小程序前端及微信真机验收延期。
