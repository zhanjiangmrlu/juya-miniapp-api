# V1.5 短文阅读持久化

本模块将短文进度保存在独立 `short_reading_progress`，不复用场景进度。纯阅读模型仅有正文、全文中文、元信息、可选全文音频及独立进度；无词汇／语块、义项、讲解或切片字段。此子任务独占 domain、repository、service、sql_repository、对应unit／SQL测试和本文档；实际授权adapter、公共路由、runtime、迁移及全链路联调由主执行者负责。

## 集成接口

服务方法保持现有参数：

```python
await service.open(user_id, content_id, now) -> ReadingDetail
await service.save(user_id, content_id, ReadingPosition(...), now) -> ReadingProgress
await service.complete(user_id, content_id, idempotency_key, content_version, now) -> ReadingCompletion
```

`ReadingPosition` 增加 `client_sequence`，整数范围0至9223372036854775807；默认0只用于内部初始化／版本重置，公共PUT由集成负责人要求必填且>=1，service.save也拒绝0。`ReadingProgress`返回目前已接受的 `client_sequence`，前端按返回序号继续递增，同一版本跨设备也必须协调最新序号；服务端不以客户端时间戳排序。

`ReadingCompletion` 继承 ReadingProgress，增加 `created: bool`；现有 `.completed_at`／`.offset` 等访问保持兼容。公共router可以包装为 `{progress: result.model_dump(exclude={'created'}), created: result.created}`。第一次稳定短文读完created=true；已读完后使用新幂等键created=false；相同已成功键重放返回原结果，包括原created值，不新写完成或修改位置。

仓库新增：

- `open(user_id, content_id, version, paragraphs: dict[paragraph_id, length], now)`：在锁内读取最新进度，仅保留同版且仍合法的段落／offset，避免service先读取再保存导致并发覆盖。版本改变清空位置和序号，保留首次开始／已读完。
- `save(user_id, content_id, position, now)`：同版请求序号小于或等于已接受序号时返回当前进度，不回退位置、不更新最后阅读时间；新版本允许序号从1开始，实际当前版本由授权端口校验。
- `replay_completion(user_id, content_id, key, version)`：读取已提交成功命令，绑定用户、稳定内容ID、键和版本；不同版本复用键返回409 IDEMPOTENCY_CONFLICT。
- `complete(user_id, content_id, key, version, paragraphs, now)`：在锁内重验幂等，再保留当前合法最新位置、原完成时间并写稳定完成状态及幂等结果。

`SQLAlchemyReadingRepository(sessions, guard)` 的 guard 必填，签名为 `async guard(session: AsyncSession, user_id: str, content_id: str, version: int | None) -> None`。该函数必须使用调用方传入的事务session取得当前发布／模块／授权事实的锁并最终核对账号、正文权限和版本，不得独立提交／另开session。仓库先锁ACTIVE用户，再查事实／幂等／进度；新open/save/complete和成功收据重放都调用guard，缓存或前置HTTP检查不能取代最终事实校验。进度查询也限定ACTIVE账号。重放使用version=None，只跳过旧命令版本与当前发布版本的相等校验，仍要求当前已发布版本和有效正文权限；新写入使用整数version。

## 序号、位置及幂等

实际SQL使用管理端已有 `last_client_sequence` 列；没有新增迁移。用户行FOR UPDATE串行化首次插入、同版乱序写、打开和完成操作，再锁短文进度行读取当前状态。open和complete依据已授权同版本的段落长度判断旧位置合法性；无效锚点／超长offset安全重置，不猜测新版位置。last_read_at保留最大值，started_at和completed_at不倒退。短文完成只修改短文进度与本次命令收据，不写learning_progress、learning_completion_event、daily_checkin，也不调用场景／联系里程碑逻辑。

读完幂等写入现有 `user_command_dedup`：endpoint固定 `v15:reading:complete`，存储键为hash(content_id,客户端key)，request_hash绑定content_id和版本。相同用户的不同内容可使用相同客户端键；不同用户互不影响。幂等结果与进度同事务提交，ACTIVE用户锁保证并发只有一个首次created=true。

service.complete先获取当前全文授权，再查询已提交收据，仓储在用户锁后再次核当前权限。模块关闭、账号失效、权益过期或内容下线时，成功原命令也拒绝重放；当前仍有全文权限时，发布版本变化不阻止同键同原请求版本的进度元数据重放。新键／未提交命令仍核对请求版本并在最终SQLguard内检查事实。收据沿用快照命令生命周期，用户清空／注销由主执行者统一删除 `v15:%`命令记录；本模块不因追溯而保留已删除用户的数据。

## 验证记录

依据V1.5完整需求第30章及技术方案02／05实施，执行和验证技能继续沿用前一子任务。Ruling：保留现有分支，独占目录内本文档同时记录执行ledger，不新增迁移、共享runtime或授权adapter。

- RED：先补5个行为测试。加入领域序号字段后观察真实失败：旧请求把offset5回退到1；open／complete排在较新save后却覆盖旧位置；完成无created／版本幂等；无效旧锚点继续返回。
- GREEN：内存仓库／service移到锁内位置保留、序号保护和完成幂等，9项unit通过。
- 最新SQL真实验证：`.venv/Scripts/python.exe scripts/test-v15-isolated.py tests/integration/test_v15_reading_chain.py tests/integration/test_v15_reading_sql.py -q --tb=short` 创建私有UUID MySQL／Redis，应用管理端最新迁移，3项SQL测试通过（1.82s）；临时容器由脚本finally清理，无共享服务重启。覆盖last_client_sequence、同版乱序／重复序号、新版重置、读完key／版本绑定、成功收据当前授权复核、实际用户行锁竞争，以及场景进度／完成事件／打卡均为0。
- 并发SQL测试先等待performance_schema确认命令进入实际用户锁等待，再放行事务，避免按固定时间猜测执行顺序；并发序号10至15最后只保留15，完成时间仍一致。
- Ruff、format、mypy检查4个实现文件和相应测试；9项unit／2项SQL通过。完整普通pytest运行时190 passed／15 skipped／2 failed，失败是集成进行中的OpenAPI快照不同及favorites confirmation测试MockTransport缺少base_url；已交主执行者处理，未越界修改。

最新补测已通过实际授权guard、admin内部HMAC和Redis及BFF HTTP短文链路；当前13项单元测试、模块mypy与Ruff通过。完整说明和上游运营排序依赖见 `v15-reading-chain.md`、`v15-reading-followup.md`。没有调用外部收费服务，也没有微信设备、人工内容、听音、云端OSS或生产验收。
