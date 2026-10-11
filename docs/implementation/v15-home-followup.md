# V1.5 首页任务与问候补充

独占范围为 `modules/checkins/router.py`、新增 `tests/unit/checkins/test_v15_home_priority.py` 和本文档。依据需求 5.2、5.4、6.1，保留 feature/v1.5-20261011；未修改 runtime、共享契约或其他仓库。

## 行为

- catalog 的明确 `user_stage=ENTITLED` 与当前 batch access 决策共同决定推荐。此阶段把 `OPEN` 排除出继续学习／新学习主推荐，优先当前可访问的正式／限时内容；开放场景已有历史只作为复习兜底。NEW 保留原开放主路径。缺少阶段信息时不从历史或联系资料猜造权益阶段。
- 当前可访问场景无学习任务时，收藏复习仍优先于历史场景，全部有效卡继续分页加载。无收藏时按已保存的最近重访时间从早到晚选择历史场景；失权候选不参与。
- 当前 scene 进度只有 `started_at`、`completed_at`、`last_learned_at`；独立 `last_reviewed_at` 仅存在于收藏记录。再次打开已完成场景会更新 `last_learned_at`，因此历史兜底使用这一现有重访记录。没有新增或伪造独立听音／场景复习事件，也不把它计为新的有效学习日。
- 首页问候先转换为 Asia/Shanghai，再判断上午／下午／晚上；服务默认 UTC 时钟不再错位八小时。

## 验证

1. TDD RED：新增真实 FastAPI 首页路由测试 9 项，首次 7 failed / 2 passed。失败确认覆盖权益场景顺序、开放复习兜底、历史最久重访和北京时间。
2. GREEN：新增测试加已有任务选择回归 10 passed。
3. Scoped Ruff check / format、mypy 单文件检查通过。
4. 完整 `.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -q`：248 passed / 36 skipped；存在一项原有 Starlette httpx 弃用警告。隔离 MySQL／Redis测试因未配置变量跳过，没有启动容器或共享服务，不声称真实 SQL／云端／设备验证。

## 只读审计移交

已向集成负责人报告：收藏固定词汇图缺少独立授权取址；短文失权／下线后的安全标题与进度摘要缺少读取入口。后续授权修复场景历史 100 条截断，详见 v15-scene-history-followup.md；其他问题由集成负责人处理。
