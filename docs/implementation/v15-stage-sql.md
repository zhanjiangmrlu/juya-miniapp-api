# V1.5 用户学习阶段 SQL 验证

测试文件：`tests/integration/test_v15_learning_stage_sql.py`；对象是实际 `SQLLearningStageReader`，不修改实现。直接复用协调执行者的私有 MySQL 和 admin 当前实际迁移，不启动第二套隔离容器。

18项用例覆盖：

- 正式 ACTIVE 永久、将来期限为 ENTITLED；精确到期和过去期限、PAUSED、REVOKED 为 NEW。
- 限时 PENDING 在 start_deadline 前、ACTIVE 在 expires_at 前为 ENTITLED；精确截止及过期、PAUSED、REVOKED、ENDED 为 NEW。
- 无记录的 ACTIVE 用户为 NEW，陌生或失效账号为 403；他人的权益、自己的 profile/contact 状态不能构成权限或学习阶段提升。
- 原开放场景在 started_at 当时所属 open_scene_config 中，之后配置换掉该场景仍为 NEW；当时非开放但后来进入开放集合的历史，权益撤销后仍为 ENTITLED。
- 隐藏模块和下线内容不清除历史阶段。实际内容 SQLAuthorizer 仍拒绝完整权限，证明 stage 仅为呈现投影。
- short_reading_progress 的开始历史也构成非开放历史；反复读取 stage 不写场景进度、完成、打卡、联系资料或权益，也不把短文标记完成。真实账户仓储清空学习数据后阶段回到 NEW。

fixture 使用唯一用户/actor/series。新增的开放配置、所属场景和系列在 finally 中按自己的唯一 actor/series 移除；临时模块开关在 finally 恢复，不留下影响后续测试的全局开放配置。

准备检查：Ruff check、format通过；collect-only 收集18项成功。

2026-10-11 实际 MySQL 证据：协调执行者统一运行 `scripts/test-v15-isolated.py` 私有整库测试，本文件18项全部通过。该轮整库为254 passed、2 failed、1 external skip；两个失败由协调执行者确认在其另行新增的缓存头/enqueue链路测试，正在修正重跑。因此本文件验证完成，不据此声称整库最终门禁已通过。测试使用协调者本轮UUID私有容器和管理端实际迁移；本子任务没有另启容器、修改env或实现文件。
