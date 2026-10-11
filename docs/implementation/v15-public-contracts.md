# V1.5 公共响应契约

本子任务仅负责 `favorites/snapshot_router.py`、`short_reading/router.py`、两目录的 `public_dto.py` 和独立unit tests。领域模型、SQL、runtime、其他路由和主ledger由集成负责人维护。保留当前分支，不新建worktree、不推送；无外部收费服务调用。

## 响应模型

所有新增公共响应都有具体OpenAPI组件和 `extra=forbid`，不再把正确响应描述成任意JSON对象。

| 接口 | 具体响应组件 |
| --- | --- |
| POST `/api/v1/favorite-snapshots`、GET `/{favorite_id}` | FavoriteSnapshotResponse |
| GET `/api/v1/favorite-snapshots` | FavoriteSnapshotPageResponse |
| DELETE `/api/v1/favorite-snapshots/{favorite_id}` | FavoriteSnapshotCancelResponse |
| GET `/api/v1/favorite-snapshots/{favorite_id}/source-resolution` | FavoriteSourceResolutionResponse |
| GET `/api/v1/short-readings` | ReadingPageResponse |
| GET `/api/v1/short-readings/{identity}/preview` | ReadingPreview |
| POST `/api/v1/short-readings/{identity}/open` | ReadingDetail |
| PUT `/api/v1/short-readings/{identity}/progress` | ReadingProgress |
| POST `/api/v1/short-readings/{identity}/complete` | ReadingCompleteResponse |
| GET `/api/v1/short-readings/{identity}/resources/{target}/signed-url` | ReadingResourceResponse |

收藏响应保持平铺，包含本篇引用、收藏状态、完整保存资料、可选固定图／发音、时间和明确的legacy_incomplete／missing_fields。content_type允许历史SCENE，content_version／saved_content_version／revision_id允许null，缺失英文或中文仍允许空字符串，不伪造版本和内容。revision_id新增为平铺响应中的真实固定修订或null，以便OpenAPI明确历史缺失情况；不会返回内部data包裹或_confirmation_credential。条目类型始终使用已有归一化word／chunk。

收藏的source_locator保留既有 `dict[str, str | int]` 以兼容已保存entry_id／整数锚点，safe_summary保留既有字符串映射；这些合法动态映射仍有具体值类型。来源响应复用既有SourceResolution的状态／位置验证，NO_ACCESS或UPDATED_OR_UNRESOLVED不能携带精确locator。

短文分页items只能为安全ReadingPreview，不能混入英文正文、译文、音频或进度。完整详情复用已有纯阅读ReadingDetail，禁止words／chunks／解释／义项；无音频继续显式null。进度响应保持平铺；完成保持既有 `{progress, created}`，progress中不重复created。资源响应保持 `{resource_id, url, expires_at}`。原no-store、请求引用、权限流程和状态码没有更改。

## 执行与验证

继续采用测试驱动与完成前验证技能，本文档记录本独立子任务ledger。

1. RED：新增6项消费者契约测试，先运行得到5 failed／1 passed：OpenAPI仍是任意对象、历史revision_id未公开、DTO尚缺失、列表夹入完整译文没有拒绝。1项既有实际JSON形状测试通过。
2. GREEN：新增具体DTO并给11个公共端点声明response_model，6项新测试全部通过；既有收藏路由和短文行为一起16项通过。
3. `uv run ruff check`／`ruff format --check`针对分配的4个实现和2个测试文件通过；`uv run mypy`针对4个实现文件通过。
4. 完整 `uv run pytest -q`：198 passed／17 skipped／1 failed；唯一失败为 `tests/e2e/test_miniapp_core_flow.py::test_openapi_snapshot_exposes_typed_published_scene_and_versioned_resources`，当前生成schema包含新模型而仓库契约快照仍待集成负责人统一重生成。未修改主契约快照文件。数据库／Redis测试按未配置隔离变量跳过。

这些测试验证真实FastAPI输出和OpenAPI结构，使用真实快照／短文服务及严格领域模型，远程授权为可控适配器。未宣称真实HMAC、数据库／OSS、人工或设备验收。本子任务的OpenAPI快照和前端生成契约由主执行者在合并其他接口改造后统一生成。
