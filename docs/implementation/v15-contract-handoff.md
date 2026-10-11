# V1.5 小程序公共契约交付

2026-10-11 第一冻结基线：`19f28ef`（进度记录提交 `6c86134`）。实际配置完整运行时包含62条path、71个operation。

| 文件 | 绝对路径 | 当前文件 SHA-256 |
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
