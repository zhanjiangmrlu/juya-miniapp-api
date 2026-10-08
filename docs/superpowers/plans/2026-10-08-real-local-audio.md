# 本地真实管理内容与录音接入 Implementation Plan

> **For agentic workers:** 使用 superpowers:executing-plans 在当前 main 顺序执行，按任务核验

**Goal:** 小程序本地预览显示管理端“更好的协作方式”的真实对白，并播放用户目录中对应录音

**Architecture:** 启动脚本只读查询本地管理端数据库的指定草稿及其素材哈希，生成无凭据的预览清单。仅本地开发路由启用清单，校验完整素材后提供原图、音频与 Range/HEAD；没有清单时保持隔离演示模式

**Tech Stack:** Python/FastAPI、现有管理端容器、FileResponse

**Spec:** 用户本对话明确选择现有录音场景，素材目录为 D:\个人\图片+音频\图片+音频

## Global Constraints

- 当前 main 开发，中文提交；不推送或部署
- 不改变管理端草稿状态，不伪造句子 timing_confirmed 或词条音频绑定
- 启动时读取管理端，目录文件必须匹配已绑定素材 SHA256，缺失报错
- 数据库凭据只在现有容器进程中读取；输出和清单不得包含凭据

## Review Focus

- 错配录音、缺失文件、文件被替换时不得回退静音
- 资源仅限指定场景与修订实际引用，禁止任意文件读取
- 整段音频提供真实时长、非静音 PCM、Range 和 HEAD
- 目录、首页、词卡和收藏来源统一使用真实修订
- 生产环境不得暴露草稿预览

### Task 1: 真实内容预览与启动

**Files:** 新增 api/local_real_content.py、scripts/start-local-real.py、tests/unit/test_local_real_content.py；修改 local_dev.py、main.py、config.py、README.md

- [x] 先写 HTTP 回归：指定快照替换首页/目录/正文，音频字节与文件一致且可 Range，拒绝旧修订/非引用资源/缺失素材
- [x] 运行测试，确认现有实现失败
- [x] 实现本地清单加载、真实资源路由、词卡与收藏来源、只读启动脚本
- [x] 跑单测、Ruff、Mypy；实际从管理端导入启动，核对源哈希和 PCM
- [x] 浏览器或可用运行环境验证整段播放；句子未确认时间保持禁止逐句
- [x] 中文提交并说明本地草稿预览边界

## 本轮结果

- 先验回归 3 项失败，补充审查回归再次见到旧收藏错误返回 200；修复后完整 API 测试 141 passed、12 skipped，Ruff、Mypy、格式检查通过
- 独立审查发现真实模式未知收藏回退城堡示例，已改为404；真实学习历史也不再保留城堡示例，仅按实际本地学习记录生成
- 管理端修订 `01M3VFQMRK4SQKNGZG1KZ1ZEBS`，场景 `01M3VFQMRKZPHB5A4R7C1T5VHV`；录音与数据库绑定哈希、用户原文件及HTTP返回字节一致，实际PCM24.38秒、非零采样，Range206字节切片一致
- 原 WAV 为流式长度标头，实际时间由读取的PCM字节数计算；未重编码或修改原文件
- 8001本地实例已切换；5401独立H5显示6句真实对白，点击整段播放进入播放中，随后正常结束，无播放错误；没有宣称耳机/真机实际听音验收
- 截图：`C:/Users/Administrator/.codex/visualizations/2026/10/08/01a1198b-1b53-7ab3-b7aa-561828eecb6c/real-audio-playing.jpg`
- Ruling: 使用用户原目录提供已绑定素材，不经OSS下载 — 本地文件SHA256与管理端确认素材一致且用户明确提供目录 — 生产OSS播放仍由正式发布链路负责
- Ruling: 保留草稿未确认的句子时段及词条音频空绑定 — 当前需求没有授权代替内容验收 — 本轮整段播放已接通，逐句与词条独立发音需要管理端后续确认
