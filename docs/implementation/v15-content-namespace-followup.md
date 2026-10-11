# V1.5 跨内容收藏身份联调修复

2026-10-11；本仓只修改小程序后端。管理后端拥有0019迁移及表元数据，协调对话拥有最终四项目浏览器复验。

## 触发与修复

最终浏览器联调中，对话和词卡复用entry/sense/source标识时，词卡详情错误显示对话收藏ID。再次保存也复用同一稳定行，取消词卡会取消对话收藏，复习提交随后因失效卡片返回422。公共复习请求结构与前端收藏ID读取正确，根因在后端身份及详情状态聚合遗漏内容范围。

收藏稳定身份现在统一为`(user_id, content_type, content_id, entry_type, entry_id, sense_id, source_id)`。领域/InMemory、SQL创建查找与写入、批量有效状态以及详情聚合均使用相同范围；content_version不进入身份。取消、重收、授权、幂等及复习修订校验保持原有约束，不过滤非法卡片掩盖错误。

SQL读取将不可变修订中的内容字段命名为saved_content_type/saved_content_id，避免新稳定行字段与旧存档字段同名；当前修订JOIN额外核对favorite_id归属，旧保存事实仍从其修订读取。

## 上游依赖

运行ready最低schema由0018提高到0019。管理端`0019_favorite_content_identity.py`从归属一致的当前不可变修订回填稳定行content_type/content_id，保留原收藏ID、修订及旧标签，并扩大uq_favorite_snapshot_1唯一键。先应用管理端0019，再启动本仓新代码。迁移、共享数据库及共享服务操作由对应所有者执行。

公开JSON/TS没有新增字段或结构变化；继续使用v15-contract-handoff.md第三轮契约冻结哈希。

## 最新验证

- 初始两项定向回归均RED：其他内容误带收藏状态，以及跨内容保存复用同一ID；修复后GREEN。
- 收藏/详情定向单元与正式OpenAPI快照：57 passed。
- 私有UUID MySQL/Redis应用实际管理迁移0019后，跨内容真实Socket回归及既有收藏SQL回归：2 passed。对话和词卡各保存两个相同标识条目得到四个独立ID；取消词卡一项不影响对话；有效复习创建/完成成功，重复、取消及他人卡片仍拒绝，旧会话取消后完成仍409。
- 私有环境既有管理权威/HMAC链回归：1 passed，2.64s；实际生产运行时、JWT/session、SQL及HMAC，外部微信身份交换和OSS仍为测试端口。
- 补充跨范围并发后，单次新私有环境运行`tests/integration/test_v15_content_namespace_sql.py`、`tests/integration/test_v15_snapshots_sql.py`及`tests/integration/test_v15_admin_chain.py`：4 passed，9.34s，清理确认true。新增SQL测试`test_mysql_two_content_scopes_concurrent_save_cancel_and_recollect`同时向两个content_type、相同content_id及条目标识各提交5个保存请求，最终各一个独立ID；有效收藏v2请求保留v1保存事实；各3次并发取消及各3次v2重收仍同稳定ID，版本均2，恰好4条不可变修订、6条outbox。
- 既有`tests/unit/favorites/test_v15_snapshots.py::test_repeat_active_preserves_snapshot_cancel_and_recollect_uses_new_revision`及`test_different_medium_senses_and_concurrent_retries_are_independent`均包含在57项新单元门禁；既有SQL`test_mysql_snapshot_concurrency_cancel_revision_events_and_atomic_guard`包含在4项新数据库门禁，覆盖10并发保存、取消后v2重收同ID、10并发复习完成只记一次和取消后旧复习拒绝。
- Ruff检查通过；格式检查229文件通过；mypy检查131源文件无错误。独立只读评审无新增确证P1/P2，另有17项定向测试通过。
- TEMP/TMP指向本仓D盘隔离临时目录。运行器仅删除本次UUID容器并通过`docker rm -f -v`清理其匿名卷；清理失败使门禁失败。最后回归确认owned_resources_cleaned=true；共享服务未重启。

此前378项完整门禁是修复前基线的历史证据。本次按协调范围仅运行有关定向检查，没有重新运行完整套件或大批生成内容。最终四项目浏览器复验、真实微信/设备、OSS CORS/Range、版权/人工听音及生产仍待对应验收。
