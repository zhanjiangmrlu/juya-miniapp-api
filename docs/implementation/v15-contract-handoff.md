# V1.5 小程序公共契约交付

2026-10-11 第一冻结基线：`19f28ef`（进度记录提交 `6c86134`）。实际配置完整运行时包含62条path、71个operation。

| 文件 | 绝对路径 | 第一冻结基线 SHA-256 |
| --- | --- | --- |
| OpenAPI JSON | `D:/个人/juya/juya-miniapp-api/docs/contracts/miniapp-api.json` | `9f82dfb2a6de87d003274571bc373030c17d7cf68dc914f78f5e60efc25dd20d` |
| 自动生成 TypeScript | `D:/个人/juya/juya-miniapp-api/docs/contracts/miniapp-api.d.ts` | `1c97cd5d645e882abc4edbd15ce01194f6c14c80dcc0c44beb2de5f9e976bc9f` |

消费者应按实际公共契约接入以下五项修复。根目录接口文档由协调对话更新；本文件作为可立即消费的仓库交付证据。

| 能力 | 实际契约 | 消费要求 |
| --- | --- | --- |
| 本人头像 | `GET /api/v1/me` 的 `avatar_url: string \| null` | 展示服务端私有GET短签名；null时使用默认头像。签名不写入数据库，不拼公开OSS地址 |
| 本人资料保存 | 新增真实 `POST /api/v1/me/profile`，原 `PATCH` 保留 | 完整提交 `nickname` 和 `avatar_object_key`；null/省略沿用原有清空语义。返回同一 `MeResponse` |
| 发音准备状态 | 内部 `RUNNING`、`RECOVER` 公开映射为 `PREPARING` | 使用既有task_id轮询；非READY无URL，不触发新增收费重试 |
| 学习阶段 | `GET /api/v1/learning/catalog` 的 `user_stage: NEW \| ENTITLED \| null` | 基于本人有效正式/限时权益或非开放学习历史分区；null表示授权待定。此字段仅影响展示，不授予访问 |
| 短文完成重放 | `POST /api/v1/short-readings/{identity}/complete` 先重新鉴权 | 当前失去全文权限时即使旧命令成功仍拒绝；仍授权且内容更新时同键同原请求可重放原成功元数据 |

资料和目录均使用private/no-store；其他学习与收藏资源遵循各端点的no-store契约。

本冻结基线通过：Ruff、格式、mypy；隔离完整门禁256 passed/1 external skip，覆盖81.76%。真实Socket/JWT/HMAC/SQL链路已验证资料POST、学习目录及旧用户流程。外部OSS可访问性、微信设备和人工听音不在此证据范围内。

## 继续收尾范围

用户已授权四仓库剩余工作全部完成，本仓继续审计并接入管理端即将交付的真实录音审核开关、短文全局运营排序及历史已知媒体回填。新契约冻结后在此记录新提交与哈希，不把本基线当作三项上游能力已经交付。

## 第二轮可消费中间契约（2026-10-11）

此轮为64 paths/73 operations；下列实现与JSON/TS一同提交，可供前端立即接入，尚不是四项目最终验收冻结。

- `SceneDetail.recording_enabled` 严格消费管理后端权威布尔值；schema1旧场景同名外层字段正在补齐，当前不能据此宣称旧版录音兼容完成。
- 短文列表按admin0018 `sort_order,public_id`稳定排序，游标绑定用户及实际发布排序摘要，重排或发布变化返回422；最低schema为0018。
- `GET /api/v1/short-readings/history` 返回本人安全标题、进度、独立计数及can_open；失权/下线仍保留历史，不返回正文、音频或精确锚点。
- 场景历史新增真实游标分页；本人有效收藏词汇图使用固定资源取址接口，取消或重收后拒绝旧绑定。
- 公开收藏409误报已在`14a03fc`修复，真实双服务Socket/JWT/HMAC/SQL创建及同键重放已验证。

| 文件 | SHA-256 |
| --- | --- |
| `docs/contracts/miniapp-api.json` | `31925eeddc4f16770e7bcdfe6e2daf4de2c87fc00dd9d1281d534e50b300d099` |
| `docs/contracts/miniapp-api.d.ts` | `f117462c9c2f9544a4b39d6cf78a75f9467c150f58173168b7e4cbae98938e16` |

待上游交付的具体代码能力：安全预览封面、收藏旧固定音频优先解析、历史媒体类型规范化和同源音频别名回填；详见v15-progress.md。不用测试签名器或当前媒体替代这些缺口。

## 第三轮契约（旧版录音、资料入口与安全媒体）

最终代码/契约冻结提交：`b38294b`；前置能力提交`a9a97b8`、`c9e2f14`、`17a6552`。本轮完整隔离门禁378 passed/1 external skip、覆盖83.38%，静态检查全部通过。下面SHA以Git原始blob（LF）为准；当前JSON/TS已按LF逐一核对与提交字节一致。Windows未来检出可能转换CRLF，核对提交时应使用原始blob；换行不改变JSON语义。后续进度文档提交不改变这两个正式契约。

实际配置完整运行时仍为64 paths/73 operations，新增`PublishedScene.recording_enabled: boolean`，默认false；已授权schema1外层每次读取当前实际审核配置。前端`scene-response`旧版适配须将该外层值严格透传，不能读取不可变content快照中的臆造字段。

新增`LearningCatalog.profile_completion_enabled: boolean`，默认false；实际管理端现已交付同一个`learning_features_v15`中的独立严格布尔配置及HMAC读取端点。目录每次投影当前值；缺失/坏配置/读取失败/鉴权等待关闭该可选入口，同时正常授权目录继续返回。前端需同步正式类型并严格布尔消费，不使用contact_prompt_eligible替代。

短文既有`cover_url`现在从真实预览封面委托端点取得，经当前发布、资源绑定、授权及有效期重核；不是拼接的公开对象地址。历史固定媒体和释放事件已完成消费者回归，保存旧事实与未知标记不变。独立history已覆盖安全历史/计数，按协调及前端最新收口不增加重复progress_summary字段。

| 文件 | SHA-256 |
| --- | --- |
| `D:/个人/juya/juya-miniapp-api/docs/contracts/miniapp-api.json` | `6c6105d3b3d0e201b3824ecef796e38a595bc30e574b7b87aa3c04f049c4e780` |
| `D:/个人/juya/juya-miniapp-api/docs/contracts/miniapp-api.d.ts` | `0c2824feec6b89e6f3470572dcb16cb30bebcdcf860e86430e082eff3da255fc` |

本轮提交及统一门禁结果见v15-progress.md。本仓新契约必须取代前端a9a97b8固定fixture；根生成器以本轮最终提交`--ref HEAD`同步同一路径。最终页面、设备/云端/人工/生产仍由对应验收处理。
