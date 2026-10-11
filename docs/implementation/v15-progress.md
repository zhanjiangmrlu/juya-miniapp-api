# V1.5 小程序后端实施进度

- 分支：feature/v1.5-20261011；起始 HEAD：d3fadeba6c6d00c7d55caefde31d63cc18b3f551。
- 独占仓库：juya-miniapp-api。共享迁移、服务重启及根目录文档由协调对话处理。
- 依据：20261011 完整需求、技术方案 00/02/05/06/07/08；执行 executing-plans，保留既有身份、联系方式和反馈编排。
- 当前阶段：SQL 持久化、公共路由和实际内部授权装配。已核验管理后端 0017 和 schema2 源码；内部路由与授权适配器仍持续核对。

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
- 状态：IMPLEMENTING；未满足 READY_FOR_INTEGRATION。

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
