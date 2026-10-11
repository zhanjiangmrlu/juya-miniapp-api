# V1.5 历史媒体消费者回归

## 测试范围

扩展 `tests/integration/test_v15_admin_chain.py`, 使用私有 UUID MySQL/Redis、当前管理端迁移、真实管理 runtime 的授权回调、HMAC 请求及 nonce 验证。

- 创建固定 cup-v1 快照后, 将同一 pronunciation_target 当前上传版本改为 cup-v2, 再关闭源模块。本人有效收藏 resolve 仍 READY 且只向取址端口委托 cup-v1, 不能请求 cup-v2。
- 真实旧收藏服务写入资料, 由实际 FavoriteBackfill 生成 VOCABULARY/SCENE 数据。保存 icon_asset_id、pronunciation_target_id 和同源 audio_version_id 时, SQL 中仍保留旧类型及 cup-v1 引用。BFF 图片与发音调用经 HMAC 后由管理权威层规范为 dialogue、WORD_IMAGE/WORD, 不依赖原内容权限。
- 仅保存 pronunciation_target_id 而缺少版本的旧资料, 即使其精确档案或当前目标有版本, backfill 仍保持资源版本未知。resolve 返回403 PRONUNCIATION_REFERENCE_UNKNOWN, 不委托档案v1或当前v2。
- 已知旧引用的跨用户请求、改成v2的请求和取消后的请求被拒绝, 拒绝不会调用外部签名端口。
- 取消已知旧收藏后, 真实 release 事件通过 HMAC 管理投影端点, outbox 调度后状态 DELIVERED, 投影 active=false。

## RED 与验证记录

测试内曾临时恢复旧 resolve 的“选择当前上传版本”规则, 实际私有链在历史发音断言处 RED（PRONUNCIATION_FORBIDDEN）。该临时 patch 已完全移除, 生产代码未改动。

随后运行修复后的链, 历史图片/音频/未知版本/跨用户/取消断言通过, 但新 release 投递断言发现旧 SCENE 事件被管理契约422拒绝。主集成修正事件编码与未知修订的事件跳过逻辑后, 最终 fresh private runner 全链 GREEN（1项集成测试通过, 内含上述回归场景）。Scoped Ruff check/format 与 diff检查通过。

## 外部边界

本BFF链使用明确的外部签名端口桩, 因而验证真实SQL收藏权威与HMAC委托, 不能证明OSS实物、审核服务、云签名或播放器播放。

只读核对实际 `SQLResourceSigner`: 对没有 pronunciation_target 的旧固定上传, 仍要求本人有效快照绑定, audio_version 的 target、WORD/CHUNK角色及状态, media_asset 的固定路径、类型、审核、安全状态、SHA和对象HEAD事实。管理端自身已有 legacy 固定资源测试覆盖该路径; 本子任务未把阅读源码等同于执行该测试。

## 部分旧引用附加调查

只读纯函数复现: 无 revision_id/saved_content_version 的旧资料仍可能明确保存 image_asset_id、pronunciation_target_id 和 resource_version。实际 backfill 仅写 snapshot/revision/legacy_map, 不直接建立 reference/projection/outbox。

取消这种资料时, 旧 `_reference` 因存在媒体引用会发送 revision_id=null, 而管理事件模型 revision_id 为 str。纯模型验证确证该字段不合法。主集成采用未知修订直接跳过资源事件, 不虚构历史修订或扩大资源权限。

本链已增加实际旧服务与backfill生成此类资料的回归: 保存图片/目标/cup-v1且修订/内容版本保持null, 取消后收藏失效、旧引用不丢失, SQL reference/projection/outbox计数均为0。完整已知修订的release仍实际HMAC投递成功且outbox DELIVERED。
