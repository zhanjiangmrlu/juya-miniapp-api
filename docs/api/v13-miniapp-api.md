# Juya Miniapp API 接口文档

> 文档基线：juya-miniapp-api main 分支，2026-10-01 V1.3 实施
> 服务版本：0.1.0
> 整理日期：2026-10-01
> 依据：当前 FastAPI 实际挂载路由、请求模型、领域服务和下游客户端实现

## 1. 文档范围

本文档记录 juya-miniapp-api 当前实现的全部 HTTP 接口：

| 类别 | 数量 | 说明 |
|---|---:|---|
| 小程序业务接口 | 42 | /api/v1 下的登录、用户、学习、收藏、消息、反馈和隐私接口 |
| 内部服务接口 | 12 | /internal/v1 下的后台服务调用接口 |
| 健康检查 | 2 | /health/live、/health/ready |
| 监控指标 | 1 | /internal/metrics |
| 合计 | 57 | 不含 FastAPI 自动生成的文档页面 |

FastAPI 还自动提供以下开发辅助地址：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /openapi.json | OpenAPI 描述 |
| GET | /docs | Swagger UI |
| GET | /docs/oauth2-redirect | Swagger OAuth2 回调页 |
| GET | /redoc | ReDoc |

## 2. 通用约定

### 2.1 基础地址

接口路径均为相对路径，实际域名由部署环境决定，例如：

    https://miniapp-api.example.com/api/v1/home

### 2.2 数据格式

- 除 /internal/metrics 外，请求和响应均使用 application/json。
- 时间字段使用 ISO 8601 格式，服务内部按 UTC 保存，例如 2026-09-28T08:30:00Z。
- 无响应体的成功接口返回 HTTP 204。
- 请求模型默认禁止未声明的额外字段。

### 2.3 用户认证

除下列接口外，/api/v1 业务接口都需要访问令牌：

- POST /api/v1/session/wechat
- POST /api/v1/session/refresh
- POST /api/v1/session/logout
- GET /api/v1/learning/modules

认证请求头：

| 请求头 | 必填 | 示例 |
|---|---|---|
| Authorization | 是 | Bearer eyJ... |

访问令牌缺失、无效或过期时返回 401。

### 2.4 幂等请求头

以下接口必须携带 Idempotency-Key：

- POST /api/v1/scenes/{scene_id}/open
- POST /api/v1/scenes/{scene_id}/complete
- POST /api/v1/reviews
- POST /api/v1/reviews/{review_id}/complete
- POST /api/v1/feedback
- POST /api/v1/feedback/{feedback_id}/supplements
- POST /api/v1/feedback/{feedback_id}/resolution

建议每次业务操作生成一个全局唯一值。同一业务重试时必须复用相同的值。学习完成和创建复习接口限制最大 128 个字符。

### 2.5 链路标识

客户端可传入：

| 请求头 | 说明 |
|---|---|
| X-Request-ID | 请求标识；服务会在响应中返回 |
| traceparent | W3C Trace Context；服务会校验、生成或继续传递 |

### 2.6 统一错误响应

所有业务错误和参数校验错误使用统一结构：

    {
      "code": "VALIDATION_ERROR",
      "message": "请求参数不正确",
      "request_id": "01K...",
      "details": {
        "errors": []
      }
    }

| 字段 | 类型 | 说明 |
|---|---|---|
| code | string | 稳定的机器可读错误码 |
| message | string | 面向用户或调用方的错误说明 |
| request_id | string | 本次请求标识 |
| details | object | 附加信息，无附加信息时为空对象 |

常见 HTTP 状态：

| 状态码 | 场景 |
|---:|---|
| 400 | 无效请求 |
| 401 | 登录凭证或内部签名无效 |
| 403 | 账号被停用 |
| 404 | 资源不存在 |
| 405 | HTTP 方法不允许 |
| 409 | 状态冲突、重复事件或重放请求 |
| 422 | 字段校验或业务输入不合法 |
| 429 | 请求频率超限 |
| 503 | 下游服务、媒体地址或服务就绪状态异常 |

### 2.7 分页

收藏和消息列表使用游标分页：

| 参数 | 类型 | 默认值 | 限制 |
|---|---|---:|---|
| cursor | string/null | null | 上一页返回的 next_cursor |
| limit | integer | 50 | 1 至 100 |

分页响应：

    {
      "items": [],
      "next_cursor": null,
      "has_more": false
    }

## 3. 健康检查

### 3.1 存活检查

GET /health/live

认证：无。

成功响应 200：

    {
      "status": "ok",
      "service": "juya-miniapp-api"
    }

### 3.2 就绪检查

GET /health/ready

认证：无。

成功响应 200：

    {
      "status": "ready",
      "checks": {
        "mysql": true,
        "schema": true,
        "redis": true
      }
    }

任一检查失败时返回 503，错误码 SERVICE_NOT_READY，details.checks 给出各检查项状态。

## 4. 会话接口

### 4.1 微信登录

POST /api/v1/session/wechat

认证：无。限流：同一来源每分钟 20 次。

请求体：

| 字段 | 类型 | 必填 | 限制 |
|---|---|---|---|
| code | string | 是 | 1 至 256 字符，微信登录临时凭证 |
| device | string | 是 | 1 至 200 字符，设备标识 |

    {
      "code": "wx-login-code",
      "device": "miniapp-device-id"
    }

成功响应 200：

    {
      "access_token": "jwt-access-token",
      "refresh_token": "opaque-refresh-token",
      "refresh_expires_at": "2026-10-28T08:30:00Z",
      "session_id": "01K...",
      "user": {
        "public_id": "01K...",
        "juya_number": "JY000001",
        "status": "ACTIVE"
      },
      "account_summary": {}
    }

响应头包含 Cache-Control: no-store。

主要错误：WECHAT_CODE_INVALID、ACCOUNT_DELETING、ACCOUNT_SUSPENDED、RATE_LIMITED。

### 4.2 刷新会话

POST /api/v1/session/refresh

认证：无。限流：同一刷新令牌每分钟 10 次。

请求体：

| 字段 | 类型 | 必填 | 限制 |
|---|---|---|---|
| refresh_token | string | 是 | 28 至 512 字符 |

成功响应与微信登录相同，并返回新的一组访问令牌和刷新令牌。响应头包含 Cache-Control: no-store。

主要错误：REFRESH_TOKEN_INVALID、REFRESH_TOKEN_REPLAYED、SESSION_INVALID、SESSION_REVOKED、SESSION_EXPIRED、RATE_LIMITED。

### 4.3 退出登录

POST /api/v1/session/logout

认证：无。

请求体：

| 字段 | 类型 | 必填 | 限制 |
|---|---|---|---|
| session_id | string | 是 | 固定 26 字符 |

成功响应：204，无响应体。

## 5. 用户资料与联系方式

### 5.1 获取当前用户

GET /api/v1/me

认证：Bearer。

成功响应 200：

    {
      "public_id": "01K...",
      "juya_number": "JY000001",
      "status": "ACTIVE",
      "nickname": "用户昵称",
      "avatar_object_key": "avatars/01K....png",
      "created_at": "2026-09-01T00:00:00Z",
      "last_active_at": "2026-09-28T08:30:00Z",
      "contact": null
    }

contact 有值时结构见 5.3。响应头包含 Cache-Control: private, no-store。

### 5.2 修改当前用户资料

PATCH /api/v1/me/profile

认证：Bearer。

请求体：

| 字段 | 类型 | 必填 | 限制 |
|---|---|---|---|
| nickname | string/null | 否 | 最大 64 字符 |
| avatar_object_key | string/null | 否 | 最大 512 字符；必须是合法头像对象键 |

成功响应与 GET /api/v1/me 相同。

主要错误：AVATAR_OBJECT_KEY_INVALID、USER_NOT_FOUND。

### 5.3 获取联系方式

GET /api/v1/me/contact

认证：Bearer。

未提交联系方式时响应 null。已提交时响应：

    {
      "wechat_id": "wechat_123",
      "self_edit_count": 0,
      "change_pending": false,
      "contact_status": "PENDING",
      "can_self_edit": true,
      "requires_correction": false,
      "consent_version": "v1",
      "consented_at": "2026-09-28T08:30:00Z",
      "withdrawn_at": null,
      "verified_at": null,
      "verified_by": null,
      "updated_at": "2026-09-28T08:30:00Z"
    }

contact_status 可取 NOT_PROVIDED、PENDING、CONTACTED、VERIFIED、INVALID。

### 5.4 保存或修改联系方式

PUT /api/v1/me/contact

认证：Bearer。限流：每用户每小时 5 次。

请求体：

| 字段 | 类型 | 必填 | 限制 |
|---|---|---|---|
| wechat_id | string | 是 | 1 至 64 字符；规范化后须匹配小写字母开头、总长 6 至 20、仅字母数字下划线和连字符 |
| consent_version | string | 是 | 1 至 32 字符 |
| consent_confirmed | boolean | 是 | 必须为 true |
| source | string | 否 | 默认 PROFILE，1 至 32 字符 |

成功响应为联系方式对象。

主要错误：CONTACT_CONSENT_REQUIRED、WECHAT_ID_INVALID、CONTACT_SELF_EDIT_LIMIT、CONTACT_CORRECTION_REQUIRED、RATE_LIMITED。

### 5.5 撤回联系方式

DELETE /api/v1/me/contact

认证：Bearer。

成功响应：204，无响应体。

主要错误：CONTACT_NOT_FOUND。

### 5.6 申请联系方式更正

POST /api/v1/me/contact/corrections

认证：Bearer。

请求体：

| 字段 | 类型 | 必填 | 限制 |
|---|---|---|---|
| reason | string | 是 | 去除首尾空白后 2 至 500 字符 |

成功响应 201：

    {
      "id": "01K...",
      "status": "PENDING",
      "created_at": "2026-09-28T08:30:00Z"
    }

主要错误：CORRECTION_REASON_INVALID、CONTACT_CORRECTION_ACTIVE。

## 6. 首页与打卡

### 6.1 首页聚合

GET /api/v1/home

认证：Bearer。

成功响应 200：

    {
      "greeting": "早上好",
      "checkins": {
        "current_streak": 3,
        "total_days": 12,
        "longest_streak": 7
      },
      "today_task": {
        "kind": "CONTINUE_SCENE",
        "target_id": "scene-001",
        "card_ids": []
      },
      "unread_message_count": 2
    }

today_task 无可推荐任务时为 null。kind 当前可能为 CONTINUE_SCENE、NEW_SCENE、FAVORITE_REVIEW、HISTORY_SCENE；收藏复习任务的 card_ids 最多包含 10 项。

### 6.2 打卡汇总

GET /api/v1/me/checkins/summary

认证：Bearer。

成功响应 200：

    {
      "current_streak": 3,
      "total_days": 12,
      "longest_streak": 7
    }

学习日按北京时间计算。

## 7. 学习内容与访问权限

### 7.1 获取学习模块

GET /api/v1/learning/modules

认证：无。

成功响应 200：

    {
      "items": [
        {
          "public_id": "module-001",
          "key": "basic",
          "title": "基础模块",
          "enabled": true
        }
      ]
    }

模块可包含 admin-api 返回的其他扩展字段。下游不可用时降级为空数组。

### 7.2 获取学习目录

GET /api/v1/learning/catalog

认证：Bearer。

成功响应 200：

    {
      "items": [
        {
          "scene_id": "scene-001",
          "title": "场景标题"
        }
      ],
      "authorization_pending": false
    }

items 内具体内容字段由 admin-api 的目录配置决定。下游不可用时返回 items 为空且 authorization_pending 为 true。

### 7.3 获取场景打开历史

GET /api/v1/learning/open-history

认证：Bearer。

成功响应 200：

    {
      "items": [
        {
          "scene_id": "scene-001",
          "opened_at": "2026-09-28T08:30:00Z",
          "last_learned_at": "2026-09-28T08:35:00Z",
          "completed_at": null
        }
      ]
    }

### 7.4 打开学习场景

POST /api/v1/scenes/{scene_id}/open

认证：Bearer。请求头：Idempotency-Key。

路径参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| scene_id | string | 场景标识 |

无需请求体。

成功响应 200：

    {
      "access": "FORMAL",
      "sources": ["entitlement-001"],
      "earliest_expires_at": null,
      "activated_at": "2026-09-28T08:30:00Z",
      "scene": {},
      "authorization_pending": false
    }

下游不可用、授权数据不完整或权益已过期时，authorization_pending 为 true，scene 和 access 可能为 null。

### 7.5 获取场景条目

GET /api/v1/scenes/{scene_id}/entries/{entry_id}

认证：Bearer。

路径参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| scene_id | string | 场景标识 |
| entry_id | string | 条目标识 |

成功响应至少包含 public_id、scene_id，也可包含 admin-api 返回的其他内容字段。

主要错误：SCENE_ENTRY_UNAVAILABLE。

### 7.6 获取媒体临时地址

POST /api/v1/media/{target_id}/signed-url

认证：Bearer。

路径参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| target_id | string | 媒体目标标识 |

无需请求体。

成功响应 200：

    {
      "target_id": "media-001",
      "url": "https://...",
      "expires_at": "2026-09-28T08:35:00Z"
    }

主要错误：SIGNED_MEDIA_UNAVAILABLE、SIGNED_MEDIA_EXPIRED。

### 7.7 获取当前用户权益

GET /api/v1/me/entitlements

认证：Bearer。

成功响应 200：

    {
      "formal": [],
      "limited": [],
      "version": "v1",
      "authorization_pending": false
    }

formal 和 limited 中单项字段由 admin-api 权益投影决定。下游不可用时 authorization_pending 为 true。

## 8. 学习进度与完成

### 8.1 保存学习进度

PUT /api/v1/scenes/{scene_id}/progress

认证：Bearer。

请求体：

| 字段 | 类型 | 必填 | 限制 |
|---|---|---|---|
| client_sequence | integer | 是 | 大于或等于 0，用于处理客户端进度顺序 |
| entry_id | string | 是 | 1 至 64 字符 |
| offset | integer | 否 | 默认 0，大于或等于 0 |

成功响应 200：

    {
      "scene_id": "scene-001",
      "source_type": "SCENE",
      "position": {
        "entry_id": "entry-001",
        "offset": 12
      },
      "client_sequence": 5,
      "started_at": "2026-09-28T08:30:00Z",
      "completed_at": null,
      "last_learned_at": "2026-09-28T08:35:00Z"
    }

主要错误：LEARNING_POSITION_INVALID。

### 8.2 完成学习场景

POST /api/v1/scenes/{scene_id}/complete

认证：Bearer。请求头：Idempotency-Key。

无需请求体。

成功响应 200：

    {
      "progress": {
        "scene_id": "scene-001",
        "source_type": "SCENE",
        "position": {
          "entry_id": "entry-001",
          "offset": 12
        },
        "client_sequence": 5,
        "started_at": "2026-09-28T08:30:00Z",
        "completed_at": "2026-09-28T08:40:00Z",
        "last_learned_at": "2026-09-28T08:40:00Z"
      },
      "created": true,
      "checkin_date": "2026-09-28"
    }

created 表示本次请求是否首次完成并创建打卡记录。

### 8.3 获取场景学习结果

GET /api/v1/scenes/{scene_id}/result

认证：Bearer。

有进度时返回 8.1 的进度对象；没有进度时返回 null。

### 8.4 获取场景学习历史

GET /api/v1/history/scenes

认证：Bearer。

成功响应 200：

    {
      "items": [],
      "has_more": false
    }

items 单项为 8.1 的进度对象。当前接口未做游标分页，has_more 固定为 false。

## 9. 收藏与复习

### 9.1 获取收藏列表

GET /api/v1/favorites

认证：Bearer。

查询参数：cursor、limit，规则见 2.7。

成功响应为游标分页结构，items 单项：

    {
      "id": "01K...",
      "entry_type": "VOCABULARY",
      "normalized_key": "hello",
      "entry_stable_id": "entry-001",
      "favorited_at": "2026-09-28T08:30:00Z",
      "last_reviewed_at": null,
      "sources": [
        {
          "scene_id": "scene-001",
          "sentence_snapshot": "Hello world.",
          "source_locator": "sentence-1",
          "original_link": null
        }
      ]
    }

### 9.2 创建收藏

POST /api/v1/favorites

认证：Bearer。

请求体：

| 字段 | 类型 | 必填 | 限制 |
|---|---|---|---|
| entry_type | string | 是 | VOCABULARY 或 PHRASE |
| text | string | 是 | 1 至 255 字符 |
| entry_stable_id | string | 是 | 1 至 64 字符 |
| scene_id | string | 是 | 1 至 64 字符 |
| sentence_snapshot | string | 是 | 至少 1 字符 |
| source_locator | string | 是 | 1 至 255 字符 |

成功响应 201，结构与收藏列表单项相同。相同规范化词条会复用收藏主体并增加来源。

主要错误：FAVORITE_TYPE_INVALID、FAVORITE_TEXT_INVALID。

### 9.3 获取收藏详情

GET /api/v1/favorites/{favorite_id}

认证：Bearer。

成功响应与收藏列表单项相同。用户仍有对应场景访问权限时，sources.original_link 形如 /scenes/{scene_id}#{source_locator}；无权限时为 null。

主要错误：FAVORITE_NOT_FOUND。

### 9.4 删除收藏

DELETE /api/v1/favorites/{favorite_id}

认证：Bearer。

成功响应：204，无响应体。

### 9.5 创建复习会话

POST /api/v1/reviews

认证：Bearer。请求头：Idempotency-Key。

请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| card_ids | array[string] | 是 | 本次复习的收藏卡片标识列表 |

成功响应 201：

    {
      "id": "01K...",
      "card_count": 5,
      "started_at": "2026-09-28T08:30:00Z"
    }

### 9.6 完成复习会话

POST /api/v1/reviews/{review_id}/complete

认证：Bearer。请求头：Idempotency-Key。

无需请求体。

成功响应 200：

    {
      "id": "01K...",
      "created": true,
      "completed_at": "2026-09-28T08:40:00Z",
      "checkin_date": "2026-09-28"
    }

主要错误：REVIEW_NOT_FOUND、IDEMPOTENCY_KEY_INVALID。

## 10. 站内消息

### 10.1 获取消息列表

GET /api/v1/messages

认证：Bearer。

查询参数：cursor、limit，规则见 2.7。

成功响应为游标分页结构，items 单项：

    {
      "id": "01K...",
      "type": "SYSTEM",
      "title": "消息标题",
      "summary": "消息摘要",
      "related_type": "FEEDBACK",
      "related_id": "feedback-001",
      "created_at": "2026-09-28T08:30:00Z",
      "read_at": null
    }

### 10.2 标记消息已读

POST /api/v1/messages/{message_id}/read

认证：Bearer。

无需请求体。成功响应为更新后的消息对象。重复调用保持已读状态。

主要错误：MESSAGE_NOT_FOUND。

## 11. 用户反馈

反馈业务数据由 admin-api 管理。除上传凭证接口外，本服务负责用户鉴权、归属校验、内容安全、限流、幂等转发，并透传 admin-api 的反馈对象。

### 11.1 获取反馈列表

GET /api/v1/feedback

认证：Bearer。

成功响应 200：

    {
      "items": [],
      "has_more": false
    }

items 单项字段以 admin-api 当前反馈模型为准。当前接口未做游标分页，has_more 固定为 false。

### 11.2 创建反馈

POST /api/v1/feedback

认证：Bearer。请求头：Idempotency-Key。限流：每用户每小时 5 次。

请求体：

| 字段 | 类型 | 必填 | 限制 |
|---|---|---|---|
| category | string | 是 | 1 至 64 字符 |
| description | string | 是 | 去除首尾空白后 1 至 300 字符，并通过内容安全校验 |
| source | object | 否 | 默认空对象；可记录页面、场景等来源 |
| screenshots | array[string] | 否 | 最多 1 个 OSS 对象键，必须以 feedback/{user_id}/ 开头 |

成功响应 200：透传 admin-api 创建的反馈对象。

主要错误：FEEDBACK_DESCRIPTION_INVALID、FEEDBACK_CONTENT_BLOCKED、FEEDBACK_SCREENSHOT_LIMIT、FEEDBACK_SCREENSHOT_INVALID、RATE_LIMITED。

### 11.3 获取截图直传凭证

POST /api/v1/feedback/uploads?content_type=image/jpeg

认证：Bearer。限流：每用户每小时 10 次。

查询参数：

| 参数 | 类型 | 必填 | 允许值 |
|---|---|---|---|
| content_type | string | 是 | image/jpeg、image/png、image/webp |

成功响应 200：

    {
      "host": "https://bucket.oss-cn-example.aliyuncs.com",
      "key": "feedback/USER_ID/01K....jpg",
      "policy": "base64-policy",
      "signature": "base64-signature",
      "access_key_id": "access-key-id",
      "content_type": "image/jpeg",
      "max_bytes": 5242880,
      "expires_at": "2026-09-28T08:35:00Z"
    }

客户端使用以上字段直接向 OSS 提交表单，最大文件大小为 5 MiB，凭证默认有效期 5 分钟。

主要错误：FEEDBACK_UPLOAD_TYPE_INVALID、RATE_LIMITED。

### 11.4 获取反馈详情

GET /api/v1/feedback/{feedback_id}

认证：Bearer。

成功响应 200：透传 admin-api 的反馈详情对象。服务会校验对象的 user_id 必须等于当前用户。

主要错误：FEEDBACK_NOT_FOUND。

### 11.5 补充反馈

POST /api/v1/feedback/{feedback_id}/supplements

认证：Bearer。请求头：Idempotency-Key。

请求体：

| 字段 | 类型 | 必填 | 限制 |
|---|---|---|---|
| text | string | 是 | 去除首尾空白后 1 至 300 字符 |

成功响应 200：透传 admin-api 更新后的反馈对象。

主要错误：FEEDBACK_NOT_FOUND、FEEDBACK_SUPPLEMENT_INVALID。

### 11.6 确认反馈处理结果

POST /api/v1/feedback/{feedback_id}/resolution

认证：Bearer。请求头：Idempotency-Key。

请求体：

| 字段 | 类型 | 必填 | 限制 |
|---|---|---|---|
| action | string | 是 | 处理动作；允许值由 admin-api 定义 |
| reason | string/null | 否 | 最大 300 字符 |

成功响应 200：透传 admin-api 更新后的反馈对象。

主要错误：FEEDBACK_NOT_FOUND，以及 admin-api 返回的状态转换错误。

## 12. 账号隐私与注销

### 12.1 清空学习数据

DELETE /api/v1/me/learning-data

认证：Bearer。

请求体：

| 字段 | 类型 | 必填 | 固定值 |
|---|---|---|---|
| confirmation | string | 是 | CLEAR_LEARNING_DATA |

成功响应：204，无响应体。

该操作清理当前用户的学习进度、打卡、收藏和复习数据，不删除账号。

主要错误：CONFIRMATION_REQUIRED。

### 12.2 申请账号注销

POST /api/v1/me/deletion

认证：Bearer。

无需请求体。

成功响应 202：

    {
      "id": "01K...",
      "status": "PENDING",
      "requested_at": "2026-09-28T08:30:00Z",
      "effective_at": "2026-10-05T08:30:00Z",
      "revoked_at": null,
      "completed_at": null
    }

注销冷静期为 7 天。响应头包含 Cache-Control: no-store。

主要错误：ACCOUNT_STATE_INVALID。

### 12.3 撤销账号注销

POST /api/v1/me/deletion/revoke

认证：Bearer。

无需请求体。

成功响应 200，结构与 12.2 相同，status 和 revoked_at 已更新。

主要错误：DELETION_NOT_FOUND、DELETION_ALREADY_PROCESSING、DELETION_STATE_INVALID。

## 13. 内部服务认证

/internal/v1 和 /internal/metrics 不使用用户 Bearer Token，而使用 HMAC-SHA256 服务签名。

必需请求头：

| 请求头 | 说明 |
|---|---|
| X-Juya-Service | 调用方服务名 |
| X-Juya-Timestamp | Unix 秒级时间戳，与服务时间偏差不得超过 300 秒 |
| X-Juya-Nonce | 单次随机串；同一服务 300 秒内不可重复 |
| X-Juya-Signature | 十六进制 HMAC-SHA256 签名 |

部分后台管理接口还需要：

| 请求头 | 说明 |
|---|---|
| X-Admin-Id | 执行管理操作的管理员标识 |

签名原文按以下 5 行拼接，换行符为 LF：

    HTTP_METHOD
    PATH_WITH_QUERY
    UNIX_TIMESTAMP
    NONCE
    SHA256_HEX_OF_RAW_BODY

计算方式：

    signature = HMAC_SHA256_HEX(internal_hmac_secret, canonical_text)

注意事项：

- HTTP_METHOD 必须转为大写。
- PATH_WITH_QUERY 必须与实际请求完全一致；没有查询串时不能附加问号。
- 请求体哈希基于实际发送的原始字节；无请求体时哈希空字节串。
- 重用 nonce 返回 409，错误码 INTERNAL_REQUEST_REPLAYED。
- 时间戳过期返回 401，错误码 INTERNAL_SIGNATURE_EXPIRED。
- 请求头缺失或签名不匹配返回 401，错误码 INVALID_INTERNAL_SIGNATURE。

## 14. 内部用户与联系方式接口

除 14.2 外，本节接口均要求内部 HMAC 签名和 X-Admin-Id，并返回 Cache-Control: no-store。

### 14.1 搜索用户

POST /internal/v1/users/search

附加请求头：X-Admin-Id。

请求体：

| 字段 | 类型 | 必填 | 限制 |
|---|---|---|---|
| juya_number | string/null | 条件必填 | 最大 20 字符 |
| nickname | string/null | 条件必填 | 最大 64 字符 |
| wechat_id | string/null | 条件必填 | 最大 64 字符 |
| limit | integer | 否 | 默认 50，1 至 100 |

juya_number、nickname、wechat_id 至少提供一个。wechat_id 搜索为规范化后的精确匹配；其他字段按仓储搜索规则执行。

成功响应 200：

    {
      "items": [
        {
          "public_id": "01K...",
          "juya_number": "JY000001",
          "status": "ACTIVE",
          "nickname": "用户昵称",
          "avatar_object_key": null,
          "created_at": "2026-09-01T00:00:00Z",
          "last_active_at": "2026-09-28T08:30:00Z",
          "wechat_id": "wechat_123",
          "contact_status": "PENDING",
          "contact_change_pending": false
        }
      ],
      "has_more": false
    }

### 14.2 拒绝查询串搜索

GET /internal/v1/users/search

认证：无。此路由不读取或返回用户数据，仅用于显式阻止通过 URL 查询串传递敏感联系方式，固定返回 405：

    {
      "code": "METHOD_NOT_ALLOWED",
      "message": "用户搜索只允许 POST",
      "request_id": "01K...",
      "details": {}
    }

### 14.3 获取用户详情

GET /internal/v1/users/{user_id}

附加请求头：X-Admin-Id。

成功响应与 GET /api/v1/me 相同，包含联系方式详情。

### 14.4 更新联系状态

POST /internal/v1/users/{user_id}/contact-status

附加请求头：X-Admin-Id。

请求体：

| 字段 | 类型 | 必填 | 允许值 |
|---|---|---|---|
| status | string | 是 | NOT_PROVIDED、PENDING、CONTACTED、VERIFIED、INVALID |

成功响应为完整用户详情。

主要错误：CONTACT_STATUS_INVALID、CONTACT_NOT_FOUND。

### 14.5 确认联系方式变更

POST /internal/v1/users/{user_id}/contact/verify-change

附加请求头：X-Admin-Id。

无需请求体。成功响应为完整用户详情。

主要错误：CONTACT_NOT_FOUND。

### 14.6 审批联系方式更正

POST /internal/v1/contact-corrections/{correction_id}/decision

附加请求头：X-Admin-Id。

请求体：

| 字段 | 类型 | 必填 | 允许值 |
|---|---|---|---|
| decision | string | 是 | APPROVED、REJECTED |

成功响应 200：

    {
      "id": "01K...",
      "status": "APPROVED",
      "processed_at": "2026-09-28T08:30:00Z"
    }

主要错误：CORRECTION_DECISION_INVALID、CONTACT_CORRECTION_NOT_FOUND、CONTACT_CORRECTION_DECIDED。

## 15. 内部消息接口

### 15.1 创建站内消息

POST /internal/v1/users/{user_id}/messages

认证：内部 HMAC。

请求体：

| 字段 | 类型 | 必填 | 限制 |
|---|---|---|---|
| event_id | string | 是 | 1 至 128 字符；用于事件幂等 |
| message_type | string | 是 | 1 至 64 字符 |
| title | string | 是 | 1 至 120 字符 |
| summary | string | 是 | 1 至 500 字符 |
| related_type | string/null | 否 | 最大 64 字符 |
| related_id | string/null | 否 | 最大 64 字符 |

成功响应 201，为 10.1 中的消息对象。响应头包含 Cache-Control: no-store。

同一 event_id 以相同内容重试会返回已有消息；内容冲突返回 409，错误码 MESSAGE_EVENT_CONFLICT。

## 16. 内部账号接口

### 16.1 回报跨域注销清理结果

POST /internal/v1/users/{user_id}/deletion-cleanup-result

认证：内部 HMAC。

请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| deletion_request_id | string | 是 | 注销申请标识 |
| succeeded | boolean | 是 | admin-api 跨域数据清理是否成功 |

成功响应为 12.2 的注销申请对象。响应头包含 Cache-Control: no-store。

主要错误：DELETION_NOT_FOUND、DELETION_STATE_INVALID。

## 17. Prometheus 指标

GET /internal/metrics

认证：内部 HMAC。该路由不出现在 OpenAPI 文档中。

成功响应 200，Content-Type 为 Prometheus 文本格式，包含 HTTP 请求、登录、进度失败、admin-api 延迟、数据库连接池等指标。

## 18. 下游 admin-api 依赖

以下不是 juya-miniapp-api 对客户端暴露的接口，而是本服务运行时调用 admin-api 的契约。部署联调时必须由 admin-api 提供。

| 方法 | admin-api 路径 | 用途 | 附加幂等头 |
|---|---|---|---|
| GET | /internal/v1/learning/modules | 学习模块 | 无 |
| POST | /internal/v1/learning/catalog | 用户学习目录 | 无 |
| POST | /internal/v1/access/batch | 批量场景权限 | 无 |
| POST | /internal/v1/scenes/{scene_id}/open | 打开场景并校验权益 | Idempotency-Key |
| POST | /internal/v1/scenes/{scene_id}/entries/{entry_id} | 获取场景条目 | 无 |
| POST | /internal/v1/media/{target_id}/signed-url | 获取媒体签名地址 | 无 |
| POST | /internal/v1/entitlements | 获取用户权益 | 无 |
| POST | /internal/v1/feedback | 创建反馈 | X-Idempotency-Key |
| POST | /internal/v1/feedback/query | 查询用户反馈 | 无 |
| GET | /internal/v1/feedback/{feedback_id} | 反馈详情 | 无 |
| POST | /internal/v1/feedback/{feedback_id}/supplements | 补充反馈 | X-Idempotency-Key |
| POST | /internal/v1/feedback/{feedback_id}/resolution | 确认反馈处理结果 | X-Idempotency-Key |
| POST | /internal/v1/account-deletions | 跨域账号数据清理 | X-Event-Id |

上述调用同样使用第 13 节的内部 HMAC 签名，并向下游传递 X-Request-ID 和 traceparent。

## 19. 主要错误码索引

| 模块 | 错误码 |
|---|---|
| 通用 | VALIDATION_ERROR、RATE_LIMITED、USER_NOT_FOUND |
| 登录会话 | ACCESS_TOKEN_REQUIRED、ACCESS_TOKEN_INVALID、ACCESS_TOKEN_EXPIRED、WECHAT_CODE_INVALID、REFRESH_TOKEN_INVALID、REFRESH_TOKEN_REPLAYED、SESSION_INVALID、SESSION_REVOKED、SESSION_EXPIRED、ACCOUNT_DELETING、ACCOUNT_SUSPENDED |
| 联系方式 | CONTACT_CONSENT_REQUIRED、WECHAT_ID_INVALID、CONTACT_SELF_EDIT_LIMIT、CONTACT_CORRECTION_REQUIRED、CONTACT_NOT_FOUND、CONTACT_CORRECTION_ACTIVE、CONTACT_CORRECTION_NOT_FOUND、CONTACT_CORRECTION_DECIDED、CORRECTION_REASON_INVALID、CORRECTION_DECISION_INVALID、CONTACT_STATUS_INVALID |
| 学习 | LEARNING_POSITION_INVALID、IDEMPOTENCY_KEY_INVALID、SCENE_ENTRY_UNAVAILABLE、SIGNED_MEDIA_UNAVAILABLE、SIGNED_MEDIA_EXPIRED、ADMIN_API_UNAVAILABLE |
| 收藏复习 | FAVORITE_TYPE_INVALID、FAVORITE_TEXT_INVALID、FAVORITE_NOT_FOUND、REVIEW_NOT_FOUND |
| 消息 | MESSAGE_NOT_FOUND、MESSAGE_EVENT_CONFLICT |
| 反馈 | FEEDBACK_DESCRIPTION_INVALID、FEEDBACK_CONTENT_BLOCKED、FEEDBACK_SCREENSHOT_LIMIT、FEEDBACK_SCREENSHOT_INVALID、FEEDBACK_UPLOAD_TYPE_INVALID、FEEDBACK_SUPPLEMENT_INVALID、FEEDBACK_NOT_FOUND |
| 账号 | CONFIRMATION_REQUIRED、ACCOUNT_STATE_INVALID、DELETION_NOT_FOUND、DELETION_ALREADY_PROCESSING、DELETION_STATE_INVALID |
| 内部认证 | INVALID_INTERNAL_SIGNATURE、INTERNAL_SIGNATURE_EXPIRED、INTERNAL_REQUEST_REPLAYED |
| 健康检查 | SERVICE_NOT_READY |

## 20. 联调建议

1. 优先使用 /docs 或 /openapi.json 核对当前运行实例的实际路由。
2. 登录后保存 access_token、refresh_token 和 session_id；业务接口只使用 access_token。
3. 对完成、打开、复习和反馈写操作生成并持久化 Idempotency-Key，网络重试时复用。
4. 反馈截图先调用上传凭证接口，再直传 OSS，最后把返回的 key 放入 screenshots。
5. 内部接口签名必须针对最终发送的原始请求体计算，序列化后不可再次改变空格、字段顺序或编码。
6. 遇到服务端错误时记录响应中的 request_id，便于日志和 trace 定位。


## 21. V1.3 当前接口契约（2026-10-01）

本节及当前 OpenAPI 优先于前文历史示例，不维护旧内容格式。共有56个OpenAPI操作（42 public、12 internal、2 health）和1个不进入OpenAPI的指标接口，合计57个HTTP操作。仓库 docs/contracts/miniapp-api.json 为实际运行服务导出；miniapp-api.d.ts为可供后续小程序接入的生成类型，本次小程序前端没有修改。

POST /api/v1/scenes/{scene_id}/open 返回 SceneOpenResult；完整权限时scene为PublishedScene（scene_id/revision_id/content_version/content），content是严格结构化四模块正文。PREVIEW时scene只能为PreviewScene（public_id、title、title_en、title_zh、series、cover_url、introduction、preview_status），不能带完整原图、正文、词条释义、音频或时间点，不写入用户学习历史。

GET /api/v1/scenes/{scene_id}/entries/{entry_id} 必须携带 revision_id、entry_version、source_locator；返回权威词条固定版本和上下文，不接外部词典。独立audio_target_id/audio_version_id可null。

GET /api/v1/scenes/{scene_id}/resources/{resource_id}/signed-url?revision_id=... 返回 SignedResource；原图与整段/词条发音每次经管理服务重验权益和实际引用，no-store，地址到期不超过权益有效期。无场景版本绑定的旧媒体入口不能绕过新鉴权。

POST /api/v1/favorites 请求增加 revision_id、entry_version，允许 sentence_snapshot省略/为空；服务使用授权返回的词条与上下文，拒绝客户端伪造快照。来源不同revision保持独立；词汇/语块列表来源没有句子时用权威英文，跨行语块保留全部句子。复习队列分页与今日任务不以10张为总量上限，SQL验收覆盖125条。

POST /api/v1/me/contact/prompt-exposures 接收合法固定入口与Idempotency-Key，接口完成，真实曝光触发随小程序延期。成功学习/收藏/复习/联系方式/注销事件事务去重；自然周/月独立活跃人数由日汇总生产者生成，注销去除用户关联。学习写入本身亦实时校验场景权益，拒绝未经授权或授权服务不可用的进度/完成数据。

正式期限 wire 值为 month_1、month_2、month_3、month_6、month_12、permanent；后端自然月与权益生命周期沿用。前端后续需消费Unicode片段偏移、整段音频区间、原图鉴权、空发音、版本冲突刷新和不限量复习；微信真机/登录供应商与设计稿不由本轮后端验收替代。

### 当前 OpenAPI 操作清单

| 方法 | 路径 |
|---|---|
| POST | `/api/v1/session/wechat` |
| POST | `/api/v1/session/refresh` |
| POST | `/api/v1/session/logout` |
| GET | `/api/v1/me` |
| PATCH | `/api/v1/me/profile` |
| POST | `/api/v1/me/contact/prompt-exposures` |
| GET | `/api/v1/me/contact` |
| PUT | `/api/v1/me/contact` |
| DELETE | `/api/v1/me/contact` |
| POST | `/api/v1/me/contact/corrections` |
| PUT | `/api/v1/scenes/{scene_id}/progress` |
| POST | `/api/v1/scenes/{scene_id}/complete` |
| GET | `/api/v1/scenes/{scene_id}/result` |
| GET | `/api/v1/history/scenes` |
| GET | `/api/v1/me/checkins/summary` |
| GET | `/api/v1/home` |
| GET | `/api/v1/learning/modules` |
| GET | `/api/v1/learning/catalog` |
| GET | `/api/v1/learning/open-history` |
| POST | `/api/v1/scenes/{scene_id}/open` |
| GET | `/api/v1/scenes/{scene_id}/entries/{entry_id}` |
| POST | `/api/v1/media/{target_id}/signed-url` |
| GET | `/api/v1/scenes/{scene_id}/resources/{resource_id}/signed-url` |
| GET | `/api/v1/me/entitlements` |
| GET | `/api/v1/favorites` |
| POST | `/api/v1/favorites` |
| GET | `/api/v1/reviews/queue` |
| GET | `/api/v1/favorites/{favorite_id}` |
| DELETE | `/api/v1/favorites/{favorite_id}` |
| POST | `/api/v1/reviews` |
| POST | `/api/v1/reviews/{review_id}/complete` |
| GET | `/api/v1/messages` |
| POST | `/api/v1/messages/{message_id}/read` |
| GET | `/api/v1/feedback` |
| POST | `/api/v1/feedback` |
| POST | `/api/v1/feedback/uploads` |
| GET | `/api/v1/feedback/{feedback_id}` |
| POST | `/api/v1/feedback/{feedback_id}/supplements` |
| POST | `/api/v1/feedback/{feedback_id}/resolution` |
| DELETE | `/api/v1/me/learning-data` |
| POST | `/api/v1/me/deletion` |
| POST | `/api/v1/me/deletion/revoke` |
| POST | `/internal/v1/users/search` |
| GET | `/internal/v1/users/search` |
| POST | `/internal/v1/users/contact-projections` |
| GET | `/internal/v1/users/{user_id}` |
| GET | `/internal/v1/users/{user_id}/learning-overview` |
| POST | `/internal/v1/users/{user_id}/contact-status` |
| POST | `/internal/v1/users/{user_id}/contact/verify-change` |
| POST | `/internal/v1/contact-corrections/search` |
| GET | `/internal/v1/contact-corrections/{correction_id}` |
| POST | `/internal/v1/contact-corrections/{correction_id}/decision` |
| POST | `/internal/v1/users/{user_id}/messages` |
| POST | `/internal/v1/users/{user_id}/deletion-cleanup-result` |
| GET | `/health/live` |
| GET | `/health/ready` |
