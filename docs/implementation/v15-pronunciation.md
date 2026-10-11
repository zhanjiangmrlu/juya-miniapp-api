# V1.5 BFF 受控发音

本子任务实现 `modules/pronunciation`、独立单元测试及本文档。公共接口为 POST `/api/v1/pronunciations/{target_id}/resolve` 和 GET `/api/v1/pronunciations/tasks/{task_id}`。只接收受控引用，不接收文本、authorized、user_id、purpose、音色或供应商参数。新增模块不保存云密钥、不调用收费云服务、不维护第二套合成队列。

## 集成端口

```python
PronunciationService(snapshots, content, client, *, clock=utc_clock)
create_pronunciation_router(service, *, user_dependency=active_account_dependency)
```

- `SnapshotContextPort.target_for_review(user_id, favorite_id) -> str`：从本人有效快照推导目标，用于没有 target_id 的任务查询。
- `SnapshotContextPort.review_context(user_id, favorite_id, target_id) -> dict[str, object]`：校验所有者、active、固定词语 target／resource_version；返回服务端目的、用户、收藏及固定快照资料。
- `SnapshotReviewAdapter(SnapshotService)`：复用现有快照 detail／review_context；再次读取时使用完整同一快照资料，携带 snapshot_revision_id。每次操作重新核对，本人收藏不要求源权益仍有效，不因源模块关闭而失效。
- `CurrentContentPort.pronunciation_context(user_id, reference, target_id: str | None) -> dict[str, object]`：校验当前权限／版本和唯一条目关系；target_id 为空时只能由权威引用推导，不能先无权限查询任务。
- `CurrentContentAdapter(CurrentDetailPort)`：detail port 为 `open_scene(user_id, content_id) -> integrations.admin_api.v15_content.SceneDetail`。该端口每次必须重验账号、最新模块、发布指针及当前完整内容权益；不得接公共缓存／预览或旧已打开详情。适配器核对 content_type/id/version、唯一 entry/sense/source 及其 pronunciation 固定资源；短文当前内容拒绝词语发音。由集成负责人提供实际内部详情适配器，不假设未知内部场景路径／外层结构。
- `PronunciationClient.resolve(target_id, context)` 和 `poll(task_id, context) -> dict[str, Any]`：真实实现由集成负责人提供。管理端已存在 POST `/internal/v1/pronunciations/{target_id}/resolve` 和 POST `/internal/v1/pronunciations/tasks/{task_id}`；poll 必须由管理端核对任务 cache_key 与上下文目标。允许已核权的相同目标复用缓存任务，task_id 本身不授予访问权。

路由 user_dependency 必须使用现有会话和可用账号校验：待注销／已删除账号不能仅凭 favorite_id 请求。service 的 user_id 只能由该依赖或同等可信内部调用提供。HMAC、nonce 防重放、BFF 调用方身份、跨服务目标核验及应用注册由主执行者集成验证；本模块生成每次请求的新 nonce，但生成 nonce 不等于防重放已接通。

## 公共和内部契约

公开 body/query 支持 favorite_id 或当前内容 content_type/content_id/content_version（正整数）/entry_id，以及可选 sense_id/source_id/resource_version。GET 可选 target_id；无 target 时从有效快照或唯一当前条目关联取得，绝不以任务查询结果代替授权。只有 task_id 或未知 query 字段返回 422。favorite_id 优先绑定完整固定快照，其他内容提示不能改写快照或拓展其资源授权；客户端 resource_version 只能约束服务器固定版本，一致才允许。

发往管理端的白名单为 purpose、user_id、content_type、content_id、content_version、entry_id、favorite_id、snapshot_version、snapshot_revision_id、target_id、resource_version、nonce。content_version／snapshot_version 转为字符串；sense_id／source_id 用于本地关系校验，不发送到当前 extra=forbid 的内部模型。purpose 由 BFF 生成 CONTENT 或 FAVORITE_REVIEW，不从客户端采信。

| 管理状态 | BFF 状态 | 行为 |
| --- | --- | --- |
| READY | READY | 要求合法 HTTP(S) 地址、非空固定资源版本及未到期的带时区 expires_at；已固定版本必须相同 |
| PENDING / PREPARING / RETRY | PREPARING | 要求 task_id；不返回资源地址，不开放用户收费重试 |
| FAILED | FAILED | 仅上游明确 can_retry=true 才允许重试；实际重试仍由管理预算／冷却／有限尝试控制 |
| UNKNOWN | FAILED | can_retry=false，返回 PRONUNCIATION_RESULT_UNKNOWN（或上游明确码），避免未知收费结果再次触发收费 |
| UNAVAILABLE | UNAVAILABLE | 无地址、不可重试 |

公开响应仅 status、url、resource_version、task_id、can_retry、code、expires_at。非 READY 清除地址和期限；未知状态、缺少准备任务ID、过期／缺少期限／资源版本不符或返回错误任务ID报 PRONUNCIATION_CONTRACT_INVALID（503）。部分已知合成关闭、预算不足、长度限制和资源停用业务错误映射为 UNAVAILABLE 并保留安全码；权限错误继续按现有 AppError 返回，不掩盖为允许播放。

成功的解析／任务响应使用 `Cache-Control: private, no-store`。受限签名期限由管理资源端签发，建议不超过60秒并收紧至当前授权期限；BFF检验有效期但不会改写签名或声称零秒撤销。已下载字节、签名生效期内撤销窗口及微信真机播放需单独验收。

## 本地执行记录

依据20261011完整需求第7、8、19、30章，以及技术方案02／05／08执行；保留现有 feature/v1.5-20261011，不切分支、不建worktree、不推送。Ruling：此子任务按已分配独占目录开发，执行 ledger 保存在本文档，主执行者负责共享runtime、HMAC、迁移和最终联调；不写共享临时工作目录。

1. 先新增安全／状态测试与最小接口骨架，运行观察20项因尚未实现的 resolve/poll 抛出 NotImplementedError 而 RED。
2. 实现权威收藏及当前详情适配器、内部白名单与nonce、四状态和期限校验，20项 GREEN。
3. 扩充错误映射、任务不一致、账号依赖拒绝、短文拒绝和固定版本约束；发现 bool 会被Pydantic强转为版本1，观察1 failed／28 passed，再增加前置字段校验，29项 GREEN。
4. `uv run ruff check src/juya_miniapp_api/modules/pronunciation tests/unit/pronunciation` 通过；对应 format check 通过；`uv run mypy src/juya_miniapp_api/modules/pronunciation` 6源文件通过；模块 pytest 29 passed。FastAPI现有TestClient依赖产生1条Starlette httpx弃用提醒，未升级依赖。
5. 项目 `uv run pytest -q`：182 passed、14 skipped、1 failed。失败 `tests/unit/favorites/test_normalization.py::test_same_normalized_entry_merges_sources_and_keeps_snapshots`，旧测试要求 `/scenes/scene-2#sentence-2`，当前主执行者的来源安全修改返回 `/scenes/scene-2`；已报告主执行者，未越界修改favorites。14项MySQL／Redis测试因隔离变量未配置而跳过；主执行者负责隔离数据库真实联调与最终全套门禁。

本模块测试使用真实快照服务和详情模型，外部授权／内部client采用可控边界适配器。测试覆盖到期收藏仍发音、取消拒绝、跨用户收藏拒绝、错误／非词语目标、唯一关系、版本更新、账号不可用、四状态、资源期限和严格公共字段。它们不构成真实 BFF → HMAC → admin-api → 数据库／OSS 成功证据；没有调用腾讯云或假造听音、版权、设备、生产验收。

最终自检：逐个读完适配器、请求模型、端口、路由和service，核对权限在client前、无任务ID独立授权、固定资源版本、内部extra白名单及UNKNOWN不可重试。真实HMAC/client/runtime由主执行者另做fresh review与完整集成门禁。
