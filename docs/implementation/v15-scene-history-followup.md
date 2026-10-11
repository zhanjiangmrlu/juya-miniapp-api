# V1.5 场景历史完整候选与分页

独占范围为 learning/repository.py、learning/router.py、checkins/router.py、新增历史分页测试与本文档。没有修改 runtime、契约快照、环境、数据库迁移或其他仓库。

## 问题与修复

原 list_history 默认只读取最近 100 条，公开历史却恒返回 has_more=false。101 条场景时第 101 条不可读取，首页“最久未复习”候选同样漏项；公开 open-history 投影也被截断。

- 内部 list_history 默认读取完整候选，没有固定总数量上限。home 与 SQLAlchemyOpenHistory 使用同一仓库，均不再漏掉第 101 条。
- 公开 GET `/api/v1/history/scenes` 新增可选 cursor 和 limit 参数。limit 默认为原有单次 100 条，允许 1—100；这是传输限制。排序为 last_learned_at DESC、scene_id DESC，同时间记录用稳定 ID 打破平局。
- 响应保留 items 的原进度结构与 has_more，新增 next_cursor:string|null；has_more 由 limit+1 查询真实计算。终页 next_cursor=null，非终页可继续请求。游标绑定认证用户和分页结构版本，跨用户／损坏位置返回 422 CURSOR_INVALID。
- 声明具体 SceneHistoryPageResponse、SceneHistoryItemResponse、SceneHistoryPositionResponse，OpenAPI 可以生成 next_cursor 及原进度字段类型。响应 private,no-store，不包含正文、资源或新的权限。
- 管理端实际 AccessBatchRequest.scene_ids 最多 100 项，home 现按每批最多 100 项核对全部当前候选。完整读取不会发送一个超限请求，也不会据历史记录猜授权。

## 消费契约

首次仍可不传参数：`GET /api/v1/history/scenes`。继续使用返回的 next_cursor 请求 `GET /api/v1/history/scenes?cursor=...&limit=100`，累积 items 直到 has_more=false。不能把默认页当完整集合。集成负责人统一导出 OpenAPI 与生成消费者类型。

## 验证

- RED：真实 SQLAlchemyLearningRepository 原查询通过内存 SQLite 执行，真实 FastAPI route 输出。101 条历史、同时间稳定分页、home 最旧候选和 open-history 完整投影四项全部失败。
- 核对管理端真实 max_length=100 后，完整历史 home 测试以相同边界拒绝超限核权，观察 422 失败；分批处理后通过。
- 具体 OpenAPI 分页契约测试先因任意 JSON 对象响应而失败，添加响应 DTO 后通过。
- GREEN：历史 5 项与首页 9 项，共 14 passed。覆盖同时间不重不漏、跨用户游标拒绝、101 项、授权批次边界和公开 schema。
- Scoped Ruff check / format、mypy 三个实现文件通过。
- 完整 pytest：280 passed / 37 skipped / 1 failed。唯一失败 `tests/e2e/test_miniapp_core_flow.py::test_openapi_snapshot_exposes_typed_published_scene_and_versioned_resources`，当前应用增加了接口／模型，而冻结快照待集成负责人统一重新生成。未越权编辑 contracts。

内存 SQLite 证明实际 SQL 与消费者行为，不代替真实 MySQL、HMAC、云端或设备验收。没有启动容器或共享服务。原 Starlette httpx 弃用警告仍存在。
