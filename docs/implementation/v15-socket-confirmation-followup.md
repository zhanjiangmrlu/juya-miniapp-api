# V1.5 真实 Socket 收藏 409 修复

协调验收 request_id `55eec287877c41eeb16a611bbb6490b7` 的请求为来源句定位已确认、allow_synthesis=true、resource_version=null的 medium 条目。原推断为录音字段影响hash；实际条目hash不含SceneDetail.recording_enabled。

`tests/integration/test_v15_socket_confirmation.py` 在仅本次UUID MySQL/Redis、双实际Socket运行时中重建相同引用结构，不依赖根目录验收脚本或真实微信。先经真实admin HMAC确认，再比较本地锁前facts：revision、content version、ConfirmedEntry hash、module version、authorization version五项全部一致。原真实公共POST仍409，消息为“内容已更新,请刷新后收藏”。

根因是SQL与InMemory仓储的引用比较：公开请求是`SnapshotCreateReference`，管理确认的内部引用是`SnapshotReference`。Pydantic模型相等还要求相同模型类，即使所有字段相同也不相等。此条件在FinalFactsGuard之前触发，不能归因哈希或录音字段。

改为两个完整`model_dump()`的精确值比较。authority只能补充原来未传的entry_type，显式类型、内容类型、ID、版本、条目、义项、来源仍必须逐字段一致；最终凭据hash、当前账号/模块/权益、版本、到期、SQL锁与幂等逻辑全部保留。

RED：真实Socket POST409；另新增2项公开子类unit均409失败。GREEN：对应unit通过；真实Socket保存201、保存来源句和预备发音引用、同键重放同favorite_id通过。没有启动worker或收费任务。

2026-10-11集中隔离定向：`uv run python scripts/test-v15-isolated.py tests/integration/test_v15_private_socket_kit.py tests/integration/test_v15_socket_confirmation.py -q --tb=short --show-capture=no` → 2 passed，10.59s。可复用kit的真实JWT/SQL session、伪签名/未知sid/撤销401也在该轮通过；最终完整门禁另外登记。
