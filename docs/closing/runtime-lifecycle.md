# V1.3 跨服务与定时任务收尾

2026-10-01 验证使用 admin `18000`、mini `18001`、MySQL `juya_v13_local_e2e` 和 Redis `6398/0`。业务库 `juya`、管理端 `8000` 未参与。仅使用脚本创建的用户、反馈、草稿和 16×16 合成图片；实际测试 OSS 对象在验证后清理。脚本不会启动、重启或替换服务，也不会修改 `.env`。

## 修复与执行语义

| 链路 | 实现与边界 |
| --- | --- |
| 小程序注销到期 | mini worker 每 60 秒执行 `juya.accounts.execute_due`；账号与申请先进入 `DELETING`，清除本地数据并持久化 mini outbox |
| 管理域清理 | mini outbox 每 15 秒投递 HMAC 请求到管理端 `POST /internal/v1/account-deletions`；管理端验证申请归属和 `DELETING/DELETED` 状态，撤销权益、断开反馈用户关联 |
| 注销结果回调 | 清理事件与 `DELETION_CLEANUP_RESULT` admin outbox 在同一事务提交；domain worker 发送 HMAC `POST /internal/v1/users/{user_id}/deletion-cleanup-result`，consumer 幂等完成注销 |
| 失败恢复 | 管理 outbox 使用 5 分钟认领租约；失败按 60、120、240 秒递增，上限 3600 秒；租约条件阻止旧执行者覆盖新执行者的结果。beat 每 5 分钟扫描 domain outbox |
| 截图删除 | 注销快照中的截图，无论反馈是否已结单，都必须具备清理完成事件、`PUBLISHED` 回调、`DELETED` 申请与账号的联合证明。普通处理中反馈继续受保护；对象键与其他引用仍检查。每小时整点运行 |
| 草稿回收与恢复 | 每小时第 15 分钟扫描到期 `TRASHED` 草稿。人工清理、自动回收、恢复共用 `transition_trash`：锁回收记录、场景和版本，检查发布与包/限时/开放/预览/派生版本引用；删除版本和提交 `CLEANED` 在同一事务。未到期不可清理，过期但未清理仍可恢复，`CLEANED` 恢复返回 409 |
| 匿名统计 | 注销回调清空事件的用户外键并随机化原去重键，匿名事件与汇总保留；北京时间每日 01:00 汇总已结束日。脚本使用显式合成事件验证真实 worker 聚合，不据此声称用户页面已验收 |

自动回收只删除草稿版本和级联版本引用，保留媒体资产。被引用草稿会保留；扫描通过递增主键翻页，受保护记录不会挡住后续到期记录。回调始终使用新的 HMAC nonce，重发业务事件保持相同 event id。

## 本地运行

从 `D:\个人\juya` 执行。先由主任务更新隔离 admin API 与 content worker。mini entrypoint 的角色变量是 `JUYA_PROCESS_TYPE=worker`，本地合并 beat 用 `JUYA_ENABLE_BEAT=true`。管理端使用 `JUYA_PROCESS_ROLE=admin-worker-domain/admin-beat`。两个 beat 的 schedule 都位于可写的 `/tmp/celerybeat-schedule`。

只读检查：

```powershell
juya-miniapp-api\.venv\Scripts\python.exe juya-miniapp-api\scripts\local-stack-worker.py --role mini-worker
juya-admin-api\.venv\Scripts\python.exe juya-miniapp-api\scripts\lifecycle-runtime.py
```

主任务统一启动缺失角色（已有同名容器会拒绝替换）：

```powershell
juya-miniapp-api\.venv\Scripts\python.exe juya-miniapp-api\scripts\local-stack-worker.py --role mini-worker --start
juya-miniapp-api\.venv\Scripts\python.exe juya-miniapp-api\scripts\local-stack-worker.py --role admin-domain --start
juya-miniapp-api\.venv\Scripts\python.exe juya-miniapp-api\scripts\local-stack-worker.py --role admin-beat --start
```

执行本分项合成验收：

```powershell
juya-admin-api\.venv\Scripts\python.exe juya-miniapp-api\scripts\lifecycle-runtime.py --execute
```

helper 从两个已有隔离 API 容器读取环境到内存，校验相同 HMAC、隔离数据库和 Redis、正确跨服务地址，再克隆启动配置。密钥不出现在命令行、报告或新文件中。验收脚本在触发全局清理任务前检查没有其他待执行注销、到期截图或到期草稿；出现其他记录时停止并要求协调，不清库。

## 验收证据

最终合成运行报告：[runtime-lifecycle-evidence.json](runtime-lifecycle-evidence.json)。以下任务均由实际 Redis 队列投递，实际 Celery worker 执行；脚本没有在本进程直接调用任务函数或伪造成功回调。

| 检查 | 实际结果 |
| --- | --- |
| 18001 未签名 / 错误密钥 | 两种请求均 401 |
| mini 到期执行与 outbox | 各处理 1 条，实际 HTTP 请求到管理端 |
| 正确签名但错误申请的回调 | HTTP consumer 拒绝；admin outbox `PENDING`、attempt 1、约 60 秒后重试；账号保持 `DELETING` |
| 回调失败期间截图 | 已结单注销截图仍存在于测试 OSS，清理 worker 删除 0 |
| 修正自有 fixture 后重投 | admin domain delivered 1；申请/账号 `DELETED`、mini `DELIVERED`、admin `PUBLISHED` |
| 完成回调重放 | 正确签名 HTTP 200，未产生重复注销 |
| 实际 OSS 清理 | 完成注销截图 HEAD 返回 `OSS_OBJECT_NOT_FOUND`；普通 `PROCESSING` 截图 HEAD 仍成功 |
| 草稿任务 | 首次 cleaned 1，第二次 cleaned 0；未到期草稿保持 `TRASHED` |
| 统计任务 | 真 worker 汇总 2026-09-30 匿名合成活动事件；对应场景维度值 1，事件用户关联已为空 |

本分项隔离回归：管理端注销、截图、草稿、HMAC 和媒体边界 **90 passed**；小程序 preflight/账号单元 **14 passed**，账号并发与匿名事件/收藏 SQL **2 passed**。新测试文件为 admin `tests/integration/test_runtime_lifecycle.py`、`tests/unit/user_projection/test_runtime_lifecycle_contract.py` 与 mini `tests/integration/test_runtime_lifecycle_preflight.py`。管理生产源码 mypy 118 文件通过，涉及源码与脚本 Ruff/format 通过。全仓最终计数由根任务交付证据记录。

## 尚未验收

本分项证明本地隔离实际 HTTP、签名、MySQL、Redis worker 与测试 OSS 的生命周期链路。阿里云真实内容安全、百度真实 OCR 与免费额度控制台、生产 OSS CORS、生产发布、微信真机页面触发和设备播放不在本证据内。小程序前端没有改动，前端联系提示触发仍延期。
