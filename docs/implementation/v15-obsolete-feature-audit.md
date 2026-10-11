# V1.5 小程序后端旧功能与重复接口专项核查

2026-10-11；源码基线0045a9b，分支feature/v1.5-20261011。本次仅核查小程序后端及本文件；其他仓库只读追踪消费者。手机为本次产品范围，未启动平板验证。未运行Docker、数据库、种子、收费云任务或共享服务。

## 依据与裁决

最新依据：`D:/个人/juya/doc/句芽英语V1.5开发交付包-20261011/句芽英语V1.5完整需求文档-开发交付整合确认版-20261011.md`。

- §7.1、§19.1：授权详情一次提供本篇词汇/语块及真实收藏状态，无逐词查词/翻译主流程；M10/M11独立页取消。
- §8.2、§8.5、§18.2：保存的旧义项、来源、解释、版本及失权后的合法收藏保留；不能删除历史数据或从当前词库造旧意思。
- §18.2、§30：短文讲解/点词停止返回，纯阅读无词卡、词语发音或新收藏入口；历史收藏仍保留。
- §19.1：保留本人档案、收藏等能力，需求不强制重命名兼容路由。

Ruling: 仅静态前端无调用或单次浏览器零请求不能证明旧公开接口及历史兼容无用。只有完整调用链和替代能力均确证时才删除内部实现；公开路由、运行中消费者或历史数据语义变更先由协调对话收口。本次没有为了减少文件数而删除历史兼容。

## 接口、调用、替代与风险清单

表中“无当前调用”指本次检查的当前前端业务源码及最终代表性浏览器运行，不代表服务器访问日志或所有已发布客户端零流量。generated契约和Mock不算当前产品HTTP调用，但仍是同步或测试依赖。

| 旧能力/路径 | 实际调用与装配 | 新版替代及差异 | 本次处理/风险 |
| --- | --- | --- | --- |
| GET/POST `/api/v1/favorites`；GET/DELETE `/api/v1/favorites/{favorite_id}` | runtime.py始终装配LegacyFavoriteAdapter。当前前端favorite-service.ts统一使用favorite-snapshots；旧路由仍提供未映射旧行读取、映射ID解析、歧义409及取消/回填同锁 | 新快照只按内容/条目/义项/来源保存，旧POST权威解析后进入同一SnapshotService，忽略客户端任意正文/释义；新列表只读有效快照，不能替代未映射旧行 | 保留。删除整个router/service/repository会同时删除复习端点、旧会话完成及历史兼容，不属于安全的重复代码清理。旧公开入口是否逐步停用由协调决定 |
| GET `/api/v1/reviews/queue`的snapshot_schema=1分支 | 旧路径兼容旧ID；schema2由SnapshotService分页。新版前端创建reviews时显式snapshot_schema=2 | 新队列/银行均读取完整快照；旧请求通过review_ids映射，缺失映射409、多义歧义409；完成端点还识别历史review_session | 保留历史会话完成和ID映射。不能只因当前前端不创建旧会话就删除旧完成逻辑 |
| GET `/api/v1/scenes/{scene_id}/entries/{entry_id}` | access_router.py→AccessService.get_entry→AdminApiClient.get_entry。前端scene-service.ts仍声明请求；use-scene-page.ts inspectEntry仍调用并导出，position-page-view.vue来源模式也有直接调用点 | schema2 POST scene/open一次返回SceneDetail.words/chunks及批量真实状态；新版收藏只提交引用。该旧GET是固定发布修订/条目/定位的权威读取，未发现自由输入词典或AI翻译服务 | **公开移除候选，待协调**。先清理前端inspectEntry/旧定位来源调用，确认schema1旧内容仍能直接展示/发音及返回当前原文；然后同步删除路由、client/protocol/service、DTO必要依赖及双方快照。此轮不自行改公开契约 |
| PATCH `/api/v1/me/profile` | users/router.py两个装饰器绑定同一个update_profile函数及UserService.update_profile；当前profile-service.ts使用POST；Mock与既有公开契约测试仍支持PATCH | 正式POST已有相同校验、存储、头像签名及no-store。不存在第二套数据/保存逻辑 | **公开别名移除候选，待协调**。当前应用可用POST，但删除PATCH改变公开契约并影响旧调用方；本次保留别名及共用实现 |
| GET `/api/v1/learning/open-history` | access_router.py→AccessService.open_history→SQLAlchemyOpenHistory.list_opened；当前前端未找到业务HTTP调用 | GET `/api/v1/history/scenes`是正式分页学习历史，返回进度/时间，不自带can_open；当前可打开状态仍需独立access投影。旧接口形状/分页和访问事实不同 | **次级公开移除候选，待协调**。不能仅删除SQLAlchemyOpenHistory，scene/open仍调用record_open保存真实打开历史 |
| POST `/api/v1/media/{target_id}/signed-url` | 当前scene-service.ts getSignedUrl仍存在缺scene/revision时的fallback；AccessService和AdminApiClient仍有调用链 | 新场景资源GET签名与受控pronunciations按固定版本/授权身份处理；旧媒体读取也是权限委托，不是独立TTS或词典 | 保留直到消费者fallback及旧资源路径完成替换；签名/授权语义不能直接拿一个不同参数接口替换 |
| 旧短文句子讲解、逐词翻译/查词、短文新收藏 | short_reading/router.py仅列表、preview、open、progress、complete、history、全文resource；ReadingDetail/Preview为strict DTO，无words/chunks/explanation字段；SnapshotReference不接受short_reading | 独立纯阅读已经使用完整英文自然段、全文中文、可选全文音频及独立进度；SceneContent只接受dialogue/word_card/vocabulary | 已停止提供这类新短文能力；本仓没有发现可以删除的独立旧讲解/翻译端点。保留历史收藏资料及来源鉴权，未删数据 |
| 自由词典/整篇自动翻译服务 | 本仓当前src及正式OpenAPI未发现dictionary/translate/lookup公开路径；field_lookup_key是个人字段不可逆检索密钥，不是查词功能 | 当前词语学习取已确认资料；发音首次TTS仍是独立音频准备，不是释义生成 | 无此类运行实现可删除；不能误删隐私检索或发音模块 |

## 内部重复实现检查

`FavoriteService.favorite`及旧SQL upsert按英文归一化的算法确为旧创建路径。当前完整runtime始终提供LegacyFavoriteAdapter，新旧公开POST都由权威引用进入SnapshotService，所以运行中的新收藏不走英语合并。旧算法仍由独立旧router组合及历史SQL测试夹具调用，不能据此宣称整个FavoriteService零调用。

仍需保留的具体职责：

- favorites/compatibility.py的listing/detail/delete/review_ids/source_resolution与legacy_snapshot：未映射旧行、历史SCENE标签、安全来源及ID映射；新source-resolution路由本身依赖这个resolver。
- favorites/repository.py的get、历史来源加载、complete_review：旧详情/历史会话完成仍可到达。
- favorites/service.py的detail/complete_review：旧未映射收藏详情与旧复习会话完成仍可到达；normalize_favorite_key还被本地预览适配使用。
- accounts/repository.py对review_session/favorite_entry及快照数据的清理：清空/注销必须同时处理旧新数据，不能删除旧表清理路径。
- learning/access_service.py的schema1 PublishedScene分支和recording_enabled兼容：最新需求保留旧已发布内容，本轮不能把schema1全部视为废弃。
- api/local_dev.py：仅显式local_dev_mode启用的隔离预览适配，有现有本地资源/Range测试；不作为真实生产实现或最终联调证据。本轮未擅自移除开发模式。

若协调决定下一轮内部瘦身，可以要求完整runtime不再支持无compatibility的旧创建factory组合，把仍必要的历史读/完成职责单独抽出，再删除旧写算法和对应非历史业务测试。此方案当前不是“确证零调用函数直接删除”，不能直接按名字批量删模块。

## 真实调用证据与当前验证

只读核查最终正式运行`045dc773363c4e75bbce10723980087c/miniapp-browser/browser-results.json`的请求元数据：favorite-snapshots9次、资料保存POST2次、short-readings20次；旧favorites、逐条entries、PATCH profile、open-history在该代表性运行中均未出现。新路径成功不是所有旧客户端已退出的证明，亦不替代本次调用点核查。未输出Cookie、JWT、签名URL或请求正文。

本次必要范围已有测试执行：

```text
uv run pytest tests/unit/favorites tests/unit/learning/test_v15_detail.py tests/unit/test_v15_profile_contracts.py tests/unit/test_v15_short_reading.py tests/contract/test_v13_scene_contract.py tests/e2e/test_miniapp_core_flow.py -q --tb=short
83 passed, 1 warning in 2.61s
```

覆盖旧来源/复习授权、当前快照身份、同版详情批量状态、POST/PATCH共用资料校验、纯阅读strict字段和正式路由/OpenAPI快照。warning为既有Starlette/httpx弃用提示。仅单元/内存HTTP/契约检查，未启动数据库或重跑SQL/千篇/构建。

清理首页旧分支之后，扩大必要范围到`tests/unit/checkins/test_v15_home_priority.py`及`tests/unit/learning/test_v15_history_pagination.py`，连同上面测试一起执行：97 passed，1 warning，2.90s。首页权益优先级、收藏优先复习、101场景历史分批与分页、旧收藏/新快照/纯阅读/资料契约均通过；正式OpenAPI与冻结JSON快照一致。Ruff检查通过、229文件格式检查通过、mypy检查131源文件无错误，git diff --check通过。未重新跑数据库/SQL或发布环境验收。

最终只读评审未发现源码确证P1/P2；已纠正历史接口差异措辞：分页场景历史无can_open字段，不把独立访问投影写作该接口现有能力。文中“银行”为需求§8定义的词汇银行/语块银行，不是中央词库。

## 交付结论

已清理1项确证在正式装配下不可达、且被新版完整替代的内部实现：首页复习旧聚合收藏分页fallback。checkins/router.py删除favorites参数、旧repo导入与备用分页分支，runtime.py删除多余favorites注入；首页队列继续通过SnapshotService完整分页，review_queue_url固定为既有schema2值。旧数据的读写/迁移、公开路由及新快照均未删除，公开契约保持不变。

公开接口移除0项、数据/迁移删除0项。已明确3项公开收口候选（逐条entries、资料PATCH别名、open-history），以及旧媒体fallback和旧favorites写路径的依赖条件。其他方法仍有调用或历史职责，不强行按名称删模块。公开候选及跨仓库替换先交协调对话；最终范围由协调决定并更新公开契约与消费者快照。
