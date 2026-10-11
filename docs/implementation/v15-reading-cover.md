# V1.5 短文安全封面

上游实际接口为 `POST /internal/v1/content/short_reading/{id}/preview-cover/signed-url`，
请求体仅 `user_id`，沿用真实 body-bound HMAC。BFF 不接受前端指定封面素材、版本或任意 URL，
不构造 OSS 地址。列表和安全预览共用 `V15ReadingAuthority.preview` 的封面消费路径。

签发前读取当前安全预览；封面为空则返回 `cover_url: null`，不请求签名。
非空封面仅接受实际七字段响应：`target_id`、`asset_id`、`role`、`resource_version`、
`duration_ms`、`url`、`expires_at`。目标与素材必须同时等于预览中的 `cover_asset_id`，
角色必须 `COVER`，资源版本与时长必须明确为 `null`，拒绝缺失字段与额外字段。
URL 必须 HTTPS、有效主机、无凭据、无片段、无空白控制字符；不改写签名查询参数。
到期时间必须是带时区的 ISO 字符串、仍有效且不超过当前时间后 60 秒。
此上限来自实际 admin `V15AccessService` 的 TTL 约束与 `SQLResourceSigner` 的最终签发限制。

签发返回后重新读取安全预览，要求同内容、版本、封面及安全元数据；发布变化返回 409，
后读权限拒绝继续返回 403。随后重新核对当前本地账号、模块和可见性，返回前再次检查签名到期。
公开响应仍仅安全摘要与 `cover_url`，不暴露正文、译文、音频引用或内部素材标识。
封面不会授予正文权限，也不写阅读进度、场景完成、签到或联系方式。

## 验证边界

独立单测覆盖成功透传、无封面、错误目标/素材/角色/版本/时长、缺字段、额外字段、
非 HTTPS/凭据/片段/无主机 URL、失效/过长/无时区/非字符串到期时间、
签发期间发布变化、权限变化及重核期间签名到期。

新增 `tests/integration/test_v15_reading_cover_sql.py` 供协调侧集中私有 MySQL/Redis 执行。
使用真实 `ShortReadingStore`、SQL 授权、`SQLResourceSigner` 与 admin 路由 HMAC，
只替换外部 OSS HEAD/签名端口为明确标记的测试夹具。覆盖无全文权限的当前封面、
列表/预览一致、无封面不签发、全文/通用资源拒绝、签发返回后模块关闭、失效账号及零业务写入。
夹具限定 test 环境、UUID 私有数据库与 loopback；恢复模块开关并清除自己的种子。

提交准备时新增封面 28 项单测通过，相关短文测试合计 37 项通过、私有 SQL 1 项因无环境跳过；
Ruff check/format 与 mypy `src` 的 131 个源文件通过。私有 SQL 链路与整库门禁由协调侧集中运行，
在拿到结果前不声明通过。不涉及真实 OSS、云 worker 或真微信验收。
