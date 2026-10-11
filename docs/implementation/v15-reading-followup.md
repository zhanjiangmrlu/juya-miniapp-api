# V1.5 短文联调修复

## 完成收据重新核权

完成命令的幂等键仍绑定用户、稳定短文 ID 和原请求 content_version。响应只重放该成功命令的原进度元数据，不再次写进度、完成时间或统计事件。

先使用当前权威短文获取当前访问权限；即使旧完成命令已成功，模块关闭、账号失效、权益过期或内容下线后仍拒绝重放。SQL 仓储在 ACTIVE 账号锁后重新调用真实 FinalFactsGuard，持有当前 module/grant/published revision 的锁才返回收据，防止 HTTP 核权后关模块的窗口。

`FinalFactsGuard.reading(..., version=None)` 专用于重放权限复核：必须有当前已发布版本、有效用户和全文权限，并在锁后检查到期；不把原成功命令的旧 version 与当前 publication 比对。当前仍有权限时，短文从 v1 更新为 v2，原键/v1 请求仍可重放原收据。未成功的新命令继续传实际请求整数 version，版本不符为 409。同键改变原请求版本仍为 IDEMPOTENCY_CONFLICT；先失去权限时返回 403。

InMemoryReadingRepository 可注入锁内 guard，测试模拟同样的访问与版本边界；生产仍使用必填真实 guard 的 SQL 仓储。

## 运营排序上游依赖

2026-10-11 核对实际 admin-api 源码：

- `infrastructure/db/v15_schema.py` 的 short_reading 表没有运营 sort_order / position。
- `modules/short_reading/router.py` 管理列表为 updated_at DESC，这是编辑管理列表顺序，没有发布到用户端的运营排序命令。
- `modules/short_reading/internal_router.py` 的用户安全列表明确为已发布 public_id ASC；BFF 当前游标查询与此一致。
- typed content_package_item.position 只表示某包内部位置。同一短文可以属于多个包，没有跨包或独立短文全局排序规则。

本轮保留真实 internal 合同定义的 public_id ASC，不将更新时间或任一内容包 position 推断为运营排序。要完成独立短文全局运营排序，需要 admin-api 交付：明确的权威排序字段、运营写入接口/命令，以及同值裁定和排序调整时的分页游标失效规则。BFF 不拥有共享迁移，不创建虚构排序字段或委托接口。

若上游最终选择 position 或 sort_order 作为独立短文运营字段，建议明确按该值升序、public_id 升序裁定同值，并以两者构成分页位置。运营重排后需要权威排序配置版本使旧 cursor 失效，cursor 同时绑定用户及该版本。这是待上游确认的最小排序/分页依赖，不是已存在的字段或接口合同。

## 验证

单元测试覆盖当前授权先于收据、当前版更后合法旧收据、最终锁内撤权、同键版本冲突；实际 HMAC/Redis/SQL 链路更新为过期和关模块后的旧收据拒绝，并保留仍授权版更重放。

2026-10-11：13项短文单元测试通过（0.72s）；短文模块8个源文件mypy通过；相关Ruff check通过。私有脚本联合执行 test_v15_reading_chain.py 与 test_v15_reading_sql.py，实际3项SQL测试通过（1.82s），容器由finally清理。未修改env或其他仓库。
