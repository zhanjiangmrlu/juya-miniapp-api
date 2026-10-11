# V1.5 小程序后端实施进度

- 分支：feature/v1.5-20261011；起始 HEAD：d3fadeba6c6d00c7d55caefde31d63cc18b3f551。
- 独占仓库：juya-miniapp-api。共享迁移、服务重启及根目录文档由协调对话处理。
- 依据：20261011 完整需求、技术方案 00/02/05/06/07/08；执行 executing-plans，保留既有身份、联系方式和反馈编排。
- 当前阶段：本仓库代码与本地门禁完成，等待协调对话集成。内部接口已核对实际管理后端运行时，OpenAPI/TypeScript 已生成。

## 任务

1. 同版详情和真实收藏批量状态；安全预览及版本校验。
2. 独立收藏快照、取消/重收幂等、完整分页复习及旧接口安全兼容。
3. 本人有效收藏独立发音授权、受控查询及当前来源三态。
4. 独立短文列表/预览/正文/进度/读完/全文音频。
5. 清空及注销清理、可靠资源引用事件、统计回归。
6. OpenAPI/消费者契约、静态检查、单元及隔离 MySQL/Redis/HMAC 验证。

## 验证与交付边界

- 当前分层证据见下文；不把 Mock、InMemory 或 SQL 假 guard 视为真实授权链路通过。
- 禁止创建迁移链、跨仓库修改、清主站库、改 .env、推送或部署。
- 外部待验收：真实版权、供应商账户/预算、人工听音、OSS CORS/Range、微信设备及生产。
- 状态：READY_FOR_INTEGRATION（本仓库代码与本地门禁）；全项目历史已知资源回填及外部验收仍按下文依赖执行。

## P0/P1 领域阶段

- 新增独立快照引用、完整资料、不可变修订、并发目标锁、取消及复习授权端口；英语字符串不进入唯一键。
- 新增纯阅读 domain/repository/service；开始、位置与读完独立存储，版本更新保留读完并重置位置，受限操作每次重新核权。
- TDD：两个新增模块先运行缺失实现的测试（RED），实现后 10/10 通过（GREEN）。
- 当前命令：`uv run pytest tests/unit tests/contract -q` → 138 passed；`uv run mypy src` → 105 source files 无错误。
- 本阶段仅领域及 InMemory 证据，不是 SQL/HMAC 集成。SQL/路由尚未装配，不发布临时公共契约。
- 已读取管理后端正在产出的 `v15_schema.py` 与 `v15_contracts.py`：快照稳定行/不可变修订/引用和短文进度表已出现，迁移与内部路由仍待最终产出。
- Ruling: 使用本仓库进度文件作为 ledger，不新建 worktree — 用户指定分支及文件位置 — 若记录格式不足仅补充此文件。

## SQL、复习与公共契约阶段

- `dc559b6`：独立快照与纯阅读领域模型；`cc4429a`：受控发音模块（装配及真实链路待集成）。
- 按管理后端实际 0017 表实现快照唯一键、不可变修订、命令去重、单事务引用及 outbox；同源并发重试只建立一个有效收藏。
- 复习固定 snapshot_version 与 snapshot_revision_id；旧会话不能完成已取消或重收的卡片；并发完成只写一次复习打卡。
- 清空/注销清理先同事务生成递增资源释放事件，再删除快照、映射、短文进度与 v15 命令快照；资源引用保留无个人正文的 inactive 顺序 tombstone。
- 公共新增 `/favorite-snapshots` 引用请求与平铺响应；`entry_type` 由管理权威条目推导，拒绝客户端资料和授权字段。旧详情仅链接当前正文顶部，不拼历史修订/未解析定位。
- SQL 当前验证：`uv run python scripts/test-v15-isolated.py tests/integration/test_v15_snapshots_sql.py tests/integration/test_v15_reading_sql.py -q --tb=short --show-capture=no` → 2 passed。私有 UUID MySQL/Redis，实际 admin 迁移链；不重启共享服务。
- 上述 SQL 测试 guard 为可控测试端口，仅证明持久化/回滚/并发/清理，尚未证明管理权威授权与 HMAC；最终必须真实链路测试。
- 新引用推导/发音单元当前 36 passed，新增快照路由 1 passed；最近 mypy 发现一处 SQL Any 返回并已修正，待下一轮门禁。

## 实际授权、兼容和纯阅读闭环

- `9bc678c`：纯阅读 SQL 序号与完成收据；`b69fa50`：11个严格公共响应；`7dd5d6c`：真实短文授权与独立阅读闭环。
- 已装配实际 `AdminV15Client` 正文绑定HMAC、Redis nonce、防任意客户端purpose/user/text、当前授权SceneDetail及批量有效收藏状态、独立短文和发音路由；运行最低 schema 为0017。
- 收藏确认短凭据使用管理端实际 `build_v15_services` 的同一 `internal_hmac_secret`（至少16字节）；SQL事务在ACTIVE账号、配置、发布指针和权益锁下复查资料hash、版本和凭据；等锁后再次读UTC时钟检查凭据/权益期限。
- 实际HMAC链路证明两个 medium 意思分别保存、源模块关闭后快照文字仍可复习且固定词语发音仍可签发、取消后拒绝发音；外部签名器为测试端口，不能据此声称OSS播放通过。
- 旧POST仅稳定条目/修订/定位推导唯一新快照；忽略客户端text/sentence资料；无法唯一解析返回409。旧多来源或多映射ID的detail/delete/review拒绝409，单一映射精确取消；删除与历史回填锁相同favorite_source，避免晚回填复活。
- 旧GET兼容分页独立新快照与尚未映射旧收藏，无英语合并；映射完成的旧聚合项不重复显示。历史null版本/修订/缺失标记保持原状，不查当前词库补造。旧来源仅按当前事实返回UPDATED_OR_UNRESOLVED或NO_ACCESS，不生成旧全文/高亮。
- 已实现实际管理事件名 SHORT_READING_STARTED/COMPLETED，首次各一条，不写场景完成、打卡或联系资料。真实短文测试覆盖trial/formal/到期、每次重新核权、client_sequence、版本更新重置位置但保留完成、成功收据重放、模块关闭后的事务写入拒绝。
- 可靠引用路径为本地同事务favorite_resource_reference+miniapp_outbox → 实际通用OutboxDispatcher+HMAC handler → 管理favorite_resource_projection；重复与乱序旧sequence不重建引用，取消生成inactive释放。无资源的缺失历史不伪造revision/resource事件。
- 独立评审指出场景音频遗漏固定resource_version、历史VOCABULARY统计错误两项；已加RED回归并修正，正在整库GREEN复验。
- 普通pytest最近203 passed/17 skipped；最近私有整库218 passed/1 failed/1 skipped，覆盖80.76%。失败为实际旧entry DTO新增字段不一致，已对齐并升级socket验收schema2，最终完整验证尚未完成，不把该轮视为通过。

## 发布依赖与消费者契约

- 新公共契约：`docs/contracts/miniapp-api.json`和自动生成`miniapp-api.d.ts`；实际运行时62个path，收藏平铺schema2、source_locator对象、legacy nullable版本/修订，短文完成形状{progress,created}。
- 管理端0017先落库，再运行其显式历史回填、检查错误/缺失项，部署内部schema2服务后装配BFF；旧schema1场景未确认新义项/来源时新增收藏409，不能恢复英语合并写入。
- 管理端当前 `modules/content/v15_backfill.py` 仍不复制已知历史image_asset_id/pronunciation_target_id/resource_version，并保存SCENE/VOCABULARY历史标签；BFF已兼容文字及缺失模型，不能用当前词库或猜测资源替上游回填。历史已知资源保留属于协调对话需追踪的管理后端依赖，未声称全项目迁移验收完成。
- config `.env` 未修改；没有重启共享服务、应用共享数据库迁移、调用收费云任务、推送或部署。版权/人工听音/云账单/OSS CORS和Range/真机/生产独立待验收。

## 最终本地门禁（2026-10-11）

- `uv run ruff check src tests scripts`：All checks passed；`uv run ruff format --check src tests scripts`：200 files already formatted；`uv run mypy src`：125 source files无错误。
- `uv run pytest -q --tb=short`：203 passed/17 skipped；普通模式未配置私有MySQL/Redis的项目明确skip，不能作为数据库验收证据。
- `uv run python scripts/test-v15-isolated.py -q --tb=short --show-capture=no --cov=juya_miniapp_api --cov-report=term:skip-covered --cov-fail-under=80`：219 passed/1 skipped，覆盖80.94%，19.94s；唯一skip为需显式授权环境的外部OSS验收。
- Socket门禁使用新私有MySQL/Redis、唯一loopback端口、真实两端运行时、登录JWT/会话、正文绑定HMAC与0017SQL；schema2固定entry契约、签名资源、旧POST→独立快照、同版详情真实状态、个人中心统计、清空及注销申请/撤销完整通过。只替换外部WeChat身份交换与OSS HEAD元数据测试端口，未连云媒体，测试发布态不是人工/版权批准。
- 来源三态真实测试覆盖当前同版EXACT、模块关闭NO_ACCESS、历史定位不可唯一解析UPDATED_OR_UNRESOLVED；保留历史保存文字、不补当前字典；有权只返回当前版本信息和顶部路径语义。
- 独立评审两项确证问题均修复：whole/slice/entry固定版本有4项RED→GREEN；迁移VOCABULARY计单词有真实SQL复现和回填断言GREEN。场景非法目标/历史revision仍被拒绝。
- 管理SQLAuthorizer与本地最终事实投影AST同版一致；实际管理build_v15_services凭据密钥与BFF一致。OpenAPI按配置完整运行时生成；TypeScript以openapi-typescript@7.10.1自动生成，未手改。
- 隔离运行器改为TCP readiness，避免误把MySQL初始化临时socket服务当最终服务；只清理本次UUID容器。共享服务没有重启。
- 收尾提交由本对话执行中文Conventional Commit；未推送、部署、切分支或修改其他仓库。READY仅表示本仓库可被协调对话集成，不代表历史已知媒体上游回填、收费预算、人工听音、版权、OSS/微信真机/生产已经验收。
