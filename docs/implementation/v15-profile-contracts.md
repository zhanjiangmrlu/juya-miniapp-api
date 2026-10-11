# V1.5 本人资料联调契约

## 已实施

- `GET /api/v1/me` 和 `POST/PATCH /api/v1/me/profile` 继续返回平铺资料, 新增 `avatar_url: string | null`。三条操作共享 `MeResponse` 响应模型, OpenAPI 可直接识别字段与 POST。
- 新增真实 POST 兼容微信请求方法限制。POST 与原 PATCH 使用同一处理函数、`ProfileUpdate` 输入校验、昵称清洗、头像字节确认与 SQL 保存逻辑。输入仍为完整昵称和头像键, null 或省略沿用原有清空语义。
- `avatar_object_key` 仍是数据库保存值。`avatar_url` 只由服务端私有 OSS GET 签名产生, 不拼接公开地址。用户资料中的无头像、历史上传临时键或非本人键返回 null, 不调用签名器。
- `AvatarStore.sign_avatar_url(user_id, key)` 再次限定本人 `avatars/` 固定目录。拒绝跨用户、临时上传键、路径穿越和任意 URL。签名最多 300 秒, STS 需有带时区有效期且预留 30 秒余量。凭据或 SDK 错误映射为安全业务错误。
- 资料响应继续使用 `Cache-Control: private, no-store`。签名地址不写入数据库。

## 集成端口

`UserService` 新增可选 `avatar_url_signer: Callable[[str, str], Awaitable[str]]`。集成负责人在现有 runtime 的 `UserService(...)` 注入 `avatar_url_signer=avatar_store.sign_avatar_url`。未注入时明确返回 null。本子任务未修改 runtime、环境文件、共享服务或其他模块。

## 验证

- TDD 首轮 8 项新增测试全部 RED: 缺少签名注入、签名器和 POST。后补 OpenAPI 字段测试先 RED, 明确失败于泛化响应未声明 avatar_url。
- 最终 9 项新增资料单测与原有 7 项头像归属/图片字节单测全部通过。
- 真实 OSS SDK 使用测试静态凭据在本地生成 V4 GET 签名, 校验 HTTPS、本人固定路径、签名字段和有效期上限, 无云端网络调用。
- 私有 UUID MySQL/Redis 应用当前 admin 迁移后, 真实 `SQLAlchemyUserRepository` + FastAPI ASGI HTTP 验证 POST/PATCH 写入、GET 读取签名、SQL 保存结果与跨用户拒绝。上传字节验证沿用已有单测, SQL/HTTP 测试使用明确的确认端口桩。
- Scoped Ruff check/format 与 mypy 通过。未声称真实 OSS 桶、微信设备或生产验收。

## 录音权威配置调查（只读）

- 前端 `docs/implementation/v15-progress.md` 明确 schema2 `recording_enabled` 为待定适配输入, 缺失时保守关闭录音。
- BFF `integrations/admin_api/v15_content.py` 与 admin `modules/content/v15_contracts.py` 的 `SceneDetail` 均未声明录音开关。
- 双方 `src` 检索 `recording/record_enabled/follow_reading/microphone/录音` 只有 BFF local_dev 音频素材夹具注释命中。BFF `infrastructure/config.py` 未提供录音配置。
- admin `modules/system_config/service.py` 有通用配置存储, 现有实际读取端口只有反馈 SLA 和权益预警, 未发现录音配置键的消费者。此调查不能推断实际数据库中不存在任意自定义配置。
- 上游需要先定稿配置键、缺省行为和读取接口, 然后确定 admin 到 BFF 的详情透传契约。本子任务没有增改 learning 或 config 字段。
