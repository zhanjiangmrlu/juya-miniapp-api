# V1.5 短文真实授权链路验证

执行入口：`.venv/Scripts/python.exe scripts/test-v15-isolated.py tests/integration/test_v15_reading_chain.py -q --tb=short`。

测试使用本次调用创建的 UUID 私有 MySQL 8.4、Redis 7 容器和 admin-api 当前实际迁移；完成后由隔离脚本删除自己的容器。没有修改 `.env`、重启共享服务、连接正式数据库或调用收费生成任务。

2026-10-11 最终结果：`1 passed in 1.58s`；该测试文件 Ruff check、format check 和 diff whitespace 检查通过。

## 验证路径

public short-reading router → ShortReadingService / V15ReadingAuthority → AdminV15Client 请求正文绑定 HMAC → 实际 admin `create_v15_access_router`、RedisNonceStore、SQLAuthorizer、SQLContentLoader、ShortReadingStore → BFF SQLAlchemyReadingRepository + FinalFactsGuard 的事务内事实复核。

- 未开通账号可获得严格安全 preview/list DTO；正文段落、译文、全篇音频引用未暴露。未开通的 open 和资源请求为 403，未签名内部委托为 401。
- SQL 试读开启后返回 TRIAL；关闭试读并授予 typed short_reading 包权益后返回 FORMAL。另一个 ACTIVE 账号无该权益，被拒绝且没有个人进度。正式权益实际过期后 open 为 403，安全 preview 降为 PREVIEW。
- 全篇音频委托使用 WHOLE 角色及已发布版本中的固定 resource_version；历史 revision 请求为 409。资源签名器是外部测试端口，返回 `media.invalid`，验证委托权限及引用归属，不验证 OSS。
- progress 保存实际段落 ID 与客户端序号；较小序号不能覆盖已接受位置。首次读完仅创建一次完成时间和 SHORT_READING_COMPLETED 事件。
- 发布指针改为第二版后 open 重置旧段落位置和序号，保留 completed_at；旧 content_version 的 progress 被拒绝。
- 关闭模块后 preview、open、progress、新 complete、全篇资源均被拒绝。直接仓储写入也由真实事务 guard 拒绝，覆盖先成功读取后权限关闭的边界。
- 已提交 complete 的同键同版本只重放进度元数据；同键改版本为 409；成功响应均设置 no-store。
- 每个稳定短文首次 SHORT_READING_STARTED / COMPLETED 各一条；无 learning_completion_event、daily_checkin、user_contact 写入。

## 验收边界

fixture 是隔离数据库中的测试发布态，由实际 ShortReadingStore 创建/插入版本；没有执行或声称通过人工校对、版权确认、听审或业务发布审批。public router 使用测试用户依赖注入，实际内部 HMAC、Redis 防重放及 SQL 授权全部执行；微信登录/JWT 接入由独立现有认证测试负责。此文件不能作为真机、云端 OSS、正式内容版权或生产验收证据。
