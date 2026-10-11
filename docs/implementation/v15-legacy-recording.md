# V1.5 旧版场景录音兼容

旧schema1发布内容保留原正文和媒体引用；BFF在已成功授权的`PublishedScene`外层新增`recording_enabled`，每次打开通过actual `POST /internal/v1/learning-features`读取权威配置，不修改不可变内容快照。

内部响应严格校验布尔值及非负整数version。缺失/坏配置由管理端关闭；配置HTTP403/404/503或响应合同无效时BFF仅关闭可选录音，保持已经授权的正文。场景授权本身的错误继续拒绝；PREVIEW不读取或暴露该开关。打开响应使用`private, no-store`，同幂等键再次打开仍读取当前配置。

前端需同步公开`PublishedScene.recording_enabled`并在旧版scene-response adapter透传至页面模型。schema2原`SceneDetail.recording_enabled`同样继续消费权威值；不把正文模块开关或本地Settings当作录音配置。

验证：14单元用例通过，含真实AdminV15Client对可选配置403/404/503的降级。实际UUID隔离MySQL/Redis及两个生产uvicorn进程，正常JWT/session/HMAC，schema1开关true→false→missing→malformed、同键重开与安全缓存头：1 passed in 7.91s。独立只读评审修正后17项相关单测通过，无新增确证P1/P2。

以上仅证明配置及授权响应兼容；真实微信录音、上传、供应商审核、云端音频和设备仍需独立验收。完整新门禁和最终契约提交见v15-progress.md。
