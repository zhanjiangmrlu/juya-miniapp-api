# V1.5 收藏固定词汇图取址

## 实施与边界

新增 `GET /api/v1/favorite-snapshots/{favorite_id}/resources/{target_id}/signed-url`, 返回 `{resource_id, url, expires_at}`, 使用 `private, no-store`。

服务先通过 `SnapshotService.detail` 检查本人有效收藏, 只接受 word 快照保存的 `image_asset_id`。他人、取消、无图、chunk 和任意目标均在调用上游前拒绝。不核源模块或源权益, 因而本人有效收藏在原内容下线或授权到期后仍可请求所保存的固定词汇图。

取址委托 `FavoriteResourceClient.review_resource(user_id, favorite_id, target_id, None)`。集成负责人需在 `AdminV15Client` 实现该端口, 以现有 HMAC 客户端调用真实管理路径:

`POST /internal/v1/favorite-snapshots/{favorite_id}/resources/{target_id}/signed-url`

body 为 `user_id` 和 `resource_version: null`, 不能传任意对象键或改用当前场景资源接口。管理端现有 `V15AccessService.review_resource` 再次查询本人有效固定快照, `favorite_resource` 限定 WORD_IMAGE, `SQLResourceSigner` 检查固定对象类型、审核和安全状态。

签名返回后再次读取收藏, 对比 favorite_id、snapshot_version、snapshot_revision_id、保存内容版本、内容修订、完整引用与图片标识。取消则404, 重收或修订变化则409。然后检查返回 target/asset 一致、resource_version 为空、角色为 WORD_IMAGE（上游提供时）、HTTP(S) URL 无凭据以及带时区且仍在未来的 expires_at。签名等待期间到期不会返回已过期地址。

## 集成

- runtime 构造 `FavoriteResourceService(snapshots, admin_v15_client)`, 并注册 `create_favorite_resource_router(service, user_dependency=current_user)`。
- 本子任务不改 runtime、已有快照路由、共享客户端或主进度账本。
- 当前前端收藏详情和翻卡未消费 image_asset_id 或调用图片取址。新增接口提供媒体能力, 可见图片仍需前端决定相应展示位置并接入。

## 验证

- 18 项行为测试在接口骨架阶段全部 RED, 实施后 GREEN, 再增加签名等待跨到期测试。
- 最终 19 项新增测试覆盖本人固定引用、取消、他人、无图、错目标、chunk、取消/重收竞态、错目标返回、错角色、音频版本误返回、非法URL及朴素/过期时间。
- FastAPI HTTP 测试验证真实路径、响应结构、no-store、权限拒绝和未登录401。关联收藏/发音共75项测试通过, scoped Ruff/mypy通过。
- 本轮不启动容器、不改环境、不调用云。测试使用明确的上游取址端口桩, HMAC客户端接入与真实SQL链路由集成负责人验证。

## 其余媒体审计证据

- 短文封面: 当前 authority 的 preview/open_reading 显式排除 cover_asset_id, 未填 cover_url。只读内存复现均返回null, 对保存封面请求 resource 返回404 RESOURCE_NOT_FOUND; 管理详情实际有 cover_asset_id, 前端 preview-card 实际读取 cover_url。此项已交给集成负责人修复。
- 已知历史发音: admin pronunciation resolve 读取当前 target.uploaded_resource_version, 不优先使用 FAVORITE_REVIEW 固定 context.resource_version。只读端口复现保存v1而选择v2。管理资源绑定与BFF响应版本检查会拒绝错版, 能防误播但无法继续播放旧版。上游应先按已保存版本解析, 不替换为当前版本。
- 全文译文无字段缺口: 场景用同版本全部 dialogue.chinese 展示完整译文, 符合共享模型方案; 短文 translation_zh 在内部DTO和公开 ReadingDetail 中保持必需字段且转换不裁剪, 前端页面直接展示。
- 当前场景媒体有路径: V15SceneAuthority 从当前授权详情收集整篇/切片音频、封面、学习图和 word图片, 并按修订与唯一目标版本授权。前端 use-scene-page 实际调用 getResource 展示学习原图与词汇图。chunk不允许图片。
- 快照媒体引用有保存: snapshot_revision 保存 image_asset_id、pronunciation_target_id、resource_version, 对外快照保留图片和发音引用。没有用当前词库覆盖已保存字段。
