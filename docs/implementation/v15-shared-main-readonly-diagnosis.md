# 共享主站 V1.5 配置不兼容的只读诊断

2026-10-11，本次仅检查 Docker 元数据和日志中的固定异常/调用位置，以及已提交源码。没有打印密钥、读取或修改现有 `.env`、启动/停止/重建共享容器、应用共享迁移或部署。

## 观察证据

| 对象 | 状态 | HMAC 配置 | 代码来源 |
| --- | --- | --- | --- |
| `juya-main-mini-api` | restarting，退出码1 | `JUYA_INTERNAL_HMAC_SECRET`已存在，15个UTF-8字节 | 旧`juya-v13-mini-local`镜像，另有只读`src → /workspace/src`挂载和`PYTHONPATH=/workspace/src`，实际加载当前V1.5源码 |
| `juya-main-mini-worker` | running | 同名配置已存在，15个UTF-8字节 | 同一旧镜像，无源码挂载，运行镜像内旧代码 |
| `juya-admin-api-admin-api-1` | exited，退出码255，非OOM | 同名配置已存在，15个UTF-8字节 | 旧管理镜像，无源码挂载；其退出原因未在本次诊断中确认 |
| `juya-main-mini-api-before-published-h5-20261009` | exited，退出码0 | 同名配置已存在，15个UTF-8字节 | 同一旧mini镜像，无源码挂载，可供协调评估原运行配置基线；没有启动验证 |

只在内存中比较三个相关服务的HMAC值，结果一致；文档仅保存一致性结果和长度，不保存值、摘要、令牌或完整容器环境。API的`JUYA_JWT_SECRET`为64字节，但不参与这个检查。

日志中确证启动路径：`/workspace/src/juya_miniapp_api/api/runtime.py:228`装配`FinalFactsGuard`，随后`integrations/admin_api/v15_guard.py:108`抛出`ValueError: credential key must have at least 16 bytes`。未保存完整日志。

## 结论与最小恢复边界

直接原因是历史15字节共享HMAC配置与V1.5最低16字节要求不兼容。BFF装配直接读取`internal_hmac_secret`并编码为UTF-8；管理端`CredentialCodec`也要求同一配置至少16字节。没有JWT或其他密钥fallback。容器重启使源码挂载加载当前V1.5代码；旧worker正常不能作为新API兼容证据。

若仅恢复原共享主站，应由协调者评估恢复V1.3镜像原入口/移除V1.5源码覆盖，并恢复管理API依赖，随后核对实际ready及原用户流程。已有停止的备份容器只是可审查基线，未证明当前可直接恢复。本次不执行这些操作。

若要运行V1.5，应先由协调者准备mini/admin和相关worker/beat一致的足够强共享HMAC配置（最低16个UTF-8字节，建议至少32随机字节生成），核对两端代码及数据库最低基线，再按授权进行受控更新与真实健康/业务检查。不能只改API、直接沿用公开样例、在旧密钥后补字符或降低守卫。共享配置更新和容器重建不在本次只读诊断授权范围。

## 本轮交付证据的区分

四项目V1.5的UUID私有MySQL/Redis、合法JWT/session、真实SQL/HMAC和页面验收使用独立环境。测试端口空闲、旧worker运行、健康路由返回或本地Linux镜像构建分别只能证明对应范围，不能据此宣布共享主站已部署V1.5或生产通过。

378项隔离BFF门禁及Linux镜像验证不改变上述共享容器配置。root的实际Linux构建记录为`linux-images/7b6f57df23bc4b8597e68676a1856534/results.json`；该记录由协调者执行，本次不把其结果当作共享主站恢复验证。云媒体、真机、版权/人工听音与生产仍分别验收。
