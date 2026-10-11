# V1.5 小程序后端实施进度

- 分支：feature/v1.5-20261011；起始 HEAD：d3fadeba6c6d00c7d55caefde31d63cc18b3f551。
- 独占仓库：juya-miniapp-api。共享迁移、服务重启及根目录文档由协调对话处理。
- 依据：20261011 完整需求、技术方案 00/02/05/06/07/08；执行 executing-plans，保留既有身份、联系方式和反馈编排。
- 当前阶段：本仓新增代码和公开契约本轮收尾完成，录音新旧版、资料入口配置、安全封面、排序/历史、固定收藏媒体及可靠释放事件已通过完整隔离门禁；冻结JSON/TS供消费者同步。根接口文档、前端固定fixture及最终真实页面联调由协调对话负责。

## 任务

1. 同版详情和真实收藏批量状态；安全预览及版本校验。
2. 独立收藏快照、取消/重收幂等、完整分页复习及旧接口安全兼容。
3. 本人有效收藏独立发音授权、受控查询及当前来源三态。
4. 独立短文列表/预览/正文/进度/读完/全文音频。
5. 清空及注销清理、可靠资源引用事件、统计回归。
6. OpenAPI/消费者契约、静态检查、单元及隔离 MySQL/Redis/HMAC 验证。

## 验证与交付边界

- 当前分层证据见下文；不把 Mock、InMemory 或 SQL 假 guard 视为真实授权链路通过。
- 禁止创建迁移链、跨仓库修改、清主站库、改 .env、推送或部署。
- 外部待验收：真实版权、供应商账户/预算、人工听音、OSS CORS/Range、微信设备及生产。
- 状态：READY_FOR_COORDINATED_ACCEPTANCE_LOCAL_ONLY；本仓本轮代码/契约门禁完成，待协调消费新冻结基线与最终页面/外部验收。此前门禁保留为对应提交历史证据，以本文末尾最新记录为准。

## P0/P1 领域阶段

- 新增独立快照引用、完整资料、不可变修订、并发目标锁、取消及复习授权端口；英语字符串不进入唯一键。
- 新增纯阅读 domain/repository/service；开始、位置与读完独立存储，版本更新保留读完并重置位置，受限操作每次重新核权。
- TDD：两个新增模块先运行缺失实现的测试（RED），实现后 10/10 通过（GREEN）。
- 当前命令：`uv run pytest tests/unit tests/contract -q` → 138 passed；`uv run mypy src` → 105 source files 无错误。
- 本阶段仅领域及 InMemory 证据，不是 SQL/HMAC 集成。SQL/路由尚未装配，不发布临时公共契约。
- 已读取管理后端正在产出的 `v15_schema.py` 与 `v15_contracts.py`：快照稳定行/不可变修订/引用和短文进度表已出现，迁移与内部路由仍待最终产出。
- Ruling: 使用本仓库进度文件作为 ledger，不新建 worktree — 用户指定分支及文件位置 — 若记录格式不足仅补充此文件。

## SQL、复习与公共契约阶段

- `dc559b6`：独立快照与纯阅读领域模型；`cc4429a`：受控发音模块（装配及真实链路待集成）。
- 按管理后端实际 0017 表实现快照唯一键、不可变修订、命令去重、单事务引用及 outbox；同源并发重试只建立一个有效收藏。
- 复习固定 snapshot_version 与 snapshot_revision_id；旧会话不能完成已取消或重收的卡片；并发完成只写一次复习打卡。
- 清空/注销清理先同事务生成递增资源释放事件，再删除快照、映射、短文进度与 v15 命令快照；资源引用保留无个人正文的 inactive 顺序 tombstone。
- 公共新增 `/favorite-snapshots` 引用请求与平铺响应；`entry_type` 由管理权威条目推导，拒绝客户端资料和授权字段。旧详情仅链接当前正文顶部，不拼历史修订/未解析定位。
- SQL 当前验证：`uv run python scripts/test-v15-isolated.py tests/integration/test_v15_snapshots_sql.py tests/integration/test_v15_reading_sql.py -q --tb=short --show-capture=no` → 2 passed。私有 UUID MySQL/Redis，实际 admin 迁移链；不重启共享服务。
- 上述 SQL 测试 guard 为可控测试端口，仅证明持久化/回滚/并发/清理，尚未证明管理权威授权与 HMAC；最终必须真实链路测试。
- 新引用推导/发音单元当前 36 passed，新增快照路由 1 passed；最近 mypy 发现一处 SQL Any 返回并已修正，待下一轮门禁。

## 实际授权、兼容和纯阅读闭环

- `9bc678c`：纯阅读 SQL 序号与完成收据；`b69fa50`：11个严格公共响应；`7dd5d6c`：真实短文授权与独立阅读闭环。
- 已装配实际 `AdminV15Client` 正文绑定HMAC、Redis nonce、防任意客户端purpose/user/text、当前授权SceneDetail及批量有效收藏状态、独立短文和发音路由；运行最低 schema 为0017。
- 收藏确认短凭据使用管理端实际 `build_v15_services` 的同一 `internal_hmac_secret`（至少16字节）；SQL事务在ACTIVE账号、配置、发布指针和权益锁下复查资料hash、版本和凭据；等锁后再次读UTC时钟检查凭据/权益期限。
- 实际HMAC链路证明两个 medium 意思分别保存、源模块关闭后快照文字仍可复习且固定词语发音仍可签发、取消后拒绝发音；外部签名器为测试端口，不能据此声称OSS播放通过。
- 旧POST仅稳定条目/修订/定位推导唯一新快照；忽略客户端text/sentence资料；无法唯一解析返回409。旧多来源或多映射ID的detail/delete/review拒绝409，单一映射精确取消；删除与历史回填锁相同favorite_source，避免晚回填复活。
- 旧GET兼容分页独立新快照与尚未映射旧收藏，无英语合并；映射完成的旧聚合项不重复显示。历史null版本/修订/缺失标记保持原状，不查当前词库补造。旧来源仅按当前事实返回UPDATED_OR_UNRESOLVED或NO_ACCESS，不生成旧全文/高亮。
- 已实现实际管理事件名 SHORT_READING_STARTED/COMPLETED，首次各一条，不写场景完成、打卡或联系资料。真实短文测试覆盖trial/formal/到期、每次重新核权、client_sequence、版本更新重置位置但保留完成、成功收据重放、模块关闭后的事务写入拒绝。
- 可靠引用路径为本地同事务favorite_resource_reference+miniapp_outbox → 实际通用OutboxDispatcher+HMAC handler → 管理favorite_resource_projection；重复与乱序旧sequence不重建引用，取消生成inactive释放。无资源的缺失历史不伪造revision/resource事件。
- 独立评审指出场景音频遗漏固定resource_version、历史VOCABULARY统计错误两项；已加RED回归并修正，正在整库GREEN复验。
- 普通pytest最近203 passed/17 skipped；最近私有整库218 passed/1 failed/1 skipped，覆盖80.76%。失败为实际旧entry DTO新增字段不一致，已对齐并升级socket验收schema2，最终完整验证尚未完成，不把该轮视为通过。

## 发布依赖与消费者契约

- 新公共契约：`docs/contracts/miniapp-api.json`和自动生成`miniapp-api.d.ts`；实际运行时62个path，收藏平铺schema2、source_locator对象、legacy nullable版本/修订，短文完成形状{progress,created}。
- 管理端0017先落库，再运行其显式历史回填、检查错误/缺失项，部署内部schema2服务后装配BFF；旧schema1场景未确认新义项/来源时新增收藏409，不能恢复英语合并写入。
- 管理端当前 `modules/content/v15_backfill.py` 仍不复制已知历史image_asset_id/pronunciation_target_id/resource_version，并保存SCENE/VOCABULARY历史标签；BFF已兼容文字及缺失模型，不能用当前词库或猜测资源替上游回填。历史已知资源保留属于协调对话需追踪的管理后端依赖，未声称全项目迁移验收完成。
- config `.env` 未修改；没有重启共享服务、应用共享数据库迁移、调用收费云任务、推送或部署。版权/人工听音/云账单/OSS CORS和Range/真机/生产独立待验收。

## 最终本地门禁（2026-10-11）

- `uv run ruff check src tests scripts`：All checks passed；`uv run ruff format --check src tests scripts`：200 files already formatted；`uv run mypy src`：125 source files无错误。
- `uv run pytest -q --tb=short`：203 passed/17 skipped；普通模式未配置私有MySQL/Redis的项目明确skip，不能作为数据库验收证据。
- `uv run python scripts/test-v15-isolated.py -q --tb=short --show-capture=no --cov=juya_miniapp_api --cov-report=term:skip-covered --cov-fail-under=80`：219 passed/1 skipped，覆盖80.94%，19.94s；唯一skip为需显式授权环境的外部OSS验收。
- Socket门禁使用新私有MySQL/Redis、唯一loopback端口、真实两端运行时、登录JWT/会话、正文绑定HMAC与0017SQL；schema2固定entry契约、签名资源、旧POST→独立快照、同版详情真实状态、个人中心统计、清空及注销申请/撤销完整通过。只替换外部WeChat身份交换与OSS HEAD元数据测试端口，未连云媒体，测试发布态不是人工/版权批准。
- 来源三态真实测试覆盖当前同版EXACT、模块关闭NO_ACCESS、历史定位不可唯一解析UPDATED_OR_UNRESOLVED；保留历史保存文字、不补当前字典；有权只返回当前版本信息和顶部路径语义。
- 独立评审两项确证问题均修复：whole/slice/entry固定版本有4项RED→GREEN；迁移VOCABULARY计单词有真实SQL复现和回填断言GREEN。场景非法目标/历史revision仍被拒绝。
- 管理SQLAuthorizer与本地最终事实投影AST同版一致；实际管理build_v15_services凭据密钥与BFF一致。OpenAPI按配置完整运行时生成；TypeScript以openapi-typescript@7.10.1自动生成，未手改。
- 隔离运行器改为TCP readiness，避免误把MySQL初始化临时socket服务当最终服务；只清理本次UUID容器。共享服务没有重启。
- 收尾提交由本对话执行中文Conventional Commit；未推送、部署、切分支或修改其他仓库。READY仅表示本仓库可被协调对话集成，不代表历史已知媒体上游回填、收费预算、人工听音、版权、OSS/微信真机/生产已经验收。

- 收尾代码提交：bb7a179 feat(v1.5): 装配真实授权闭环与旧收藏安全兼容；本次包含运行时、旧兼容、清理投递、同版状态、固定资源版本、OpenAPI/TypeScript和完整回归。提交后工作区干净。

## 七项联调跟进（基线3f9207a）

协调对话已要求修复真实契约缺口，并负责根目录接口文档；本对话仅改BFF与仓库contracts。本轮OpenAPI已刷新并通过快照验证，最终以下新门禁证明五项修复；旧219项是历史证据。

| 项目 | 当前结论 | 证据/责任 |
| --- | --- | --- |
| /me avatar_url | `c6b7887`已实现并装配，完整门禁通过 | 仅本人已确认avatars/私有键的受控短签名，空/未配置返回null，public DTO实际声明；SDK本地签名及私有SQL验证，不代表OSS可访问 |
| /me/profile POST/PATCH兼容 | `c6b7887`已新增真实POST，保留PATCH，完整门禁通过 | 相同验证、头像确认、用户写入；真实JWT/Socket POST保存后GET读取通过 |
| 录音审核开关 | schema2及schema1均已接入，最新完整门禁通过 | actual learning_features_v15配置及HMAC读取端口已交付，严格布尔消费；缺失/坏配置/端点不可用关闭录音且保留已授权旧正文，preview不暴露开关 |
| RUNNING/RECOVER发音 | 已RED→GREEN，完整门禁通过 | actual admin pronunciation.repository.claim/tasks写RUNNING/RECOVER，public_task原样返回；真实HMAC/SQL任务轮询映射PREPARING，仍无收费重试、非READY无URL |
| user_stage | 权威事实投影已装配，完整门禁通过 | 需求6.1/31.4；本人有效正式/限时权益或已开始非开放内容为ENTITLED，其余NEW；仅呈现不授予访问；授权待定时null。6项单元、`4664e30`18项真实SQL及JWT/Socket目录通过 |
| 短文运营排序 | actual admin0018 sort_order已在a9a97b8接入，完整门禁通过 | (sort_order,public_id)排序；游标绑定本人和实际发布排序摘要，重排/发布变化422；读完、权益与历史均不作为运营顺序替代 |
| 完成重放核权 | `86080ac`及实际guard装配已完成，完整门禁通过 | service先核当前全文权限；SQL account锁下guard(None)核当前模块/发布/权益/到期，仍授权版更同key旧metadata可回放；new写入guard(int)必须当前版 |

此表按最新消费者产出更新；下方旧门禁为对应历史提交证据。前端实际消费与最终页面验收由协调对话核对，不能仅凭字段产出宣称已完成。

## 联调跟进最终本地门禁（2026-10-11）

- `uv run ruff check src tests scripts` All checks passed；`uv run ruff format --check src tests scripts` 205 files already formatted；`uv run mypy src` 126 source files无错误。
- `uv run pytest -q --tb=short` 221 passed/36 skipped，4.75s；普通模式缺少隔离数据库的项目skip，不作为SQL证据。
- `uv run python scripts/test-v15-isolated.py -q --tb=short --show-capture=no --cov=juya_miniapp_api --cov-report=term:skip-covered --cov-fail-under=80` 最终256 passed/1 skipped，覆盖81.76%，19.65s。唯一skip是需显式授权环境的外部OSS验收。
- 前轮254 passed/2 failed/1 skipped只用于诊断：两处新增夹具分别为缓存头等值断言过严、admin enqueue漏传cache_key；修正后重新执行上述完整门禁通过，没有降低覆盖率门槛。
- SQL学习阶段18项证明有效/到期/暂停/撤销权益、陌生/失效用户、配置轮换下原开放历史不重分类、短文独立历史和真实清空；模块关闭后仍保留展示阶段而实际SQLAuthorizer拒绝全文。资料/联系方式均不构成阶段条件。
- Socket完整用户流程继续通过，新增真实登录JWT下POST资料保存→GET本人读取avatar_url字段、目录NEW和no-store；发音RUNNING/RECOVER用实际admin持久任务/HMAC轮询，未启动worker或收费调用。
- 独立只读评审未发现新的确证P1/P2；相关56项单元通过，SQL投影另用SQLite核验基础规则；最终生产SQL证据以上MySQL门禁为准。
- OpenAPI由配置完整运行时重新生成：62条path、71个operation；TypeScript由openapi-typescript@7.10.1自动生成。新增真实POST /me/profile，MeResponse显式avatar_url，LearningCatalog显式NEW/ENTITLED/null。
- 根目录接口文档由协调对话维护，其原3f9207a/70 operation基线必须更新到本轮新契约提交；本仓库不越界修改。
- 本轮代码/公共契约冻结提交：`19f28ef fix(v1.5): 补齐联调阶段与发音状态授权契约`；配套前置提交`86080ac`、`c6b7887`与`4664e30`。
- 未改.env、重启共享服务、应用共享库迁移、推送或部署。上游录音配置、短文全局运营排序及历史已知媒体回填依赖继续单独追踪，外部版权/人工听音/云端/微信设备/生产边界保持。

## 剩余范围完整收尾（2026-10-11）

1. `19f28ef` 公共JSON/TS路径和SHA-256、五项消费者接入要求已记录 `v15-contract-handoff.md`，供协调与前端立即消费。
2. 按实际需求02/05/06/08全面核对公开API与admin内部装配，先修复可独立实现的缺口；不因上游能力尚未出现停止本仓工作。
3. 管理后端产出权威recording_enabled后同步严格consumer DTO与页面实际DTO；不推断Settings或模块开关等价于录音审核开关。
4. 管理后端产出short_reading.sort_order后实现稳定顺序、过滤权限、游标排序版本；上游重排使旧游标明确失效，不静默漏项/重复。
5. 验证历史已知image/pronunciation/resource_version回填消费，未知历史保留缺失，不查当前词库造旧数据。
6. 交付仅UUID隔离资源、loopback、真实两端运行时和合法数据库session/JWT的可复用测试入口，避免依赖真实微信账户；不进入生产认证API。
7. 每次最终代码变化后刷新配置完整运行时OpenAPI/自动生成TS，执行完整静态/隔离SQL门禁，再冻结提交；云、设备、人工、生产独立报告。

Ruling: 沿用用户指定feature分支和本进度ledger，必要独立子任务使用既有代理 — 方案08明确许可且不互改仓库 — 最终契约/运行时/统一门禁由本对话唯一集成。

### 本轮新确证缺口及修复

- `d8ad63c`：首页ENTITLED优先非开放有效权益，开放内容作复习兜底；最久重访排序；问候转换北京时间。场景历史100条硬截断及home单次核权100限制正在补真实分页/分批。
- `cc61eb3`：新增本人有效收藏固定词汇图GET取址；已装配运行时和actual admin HMAC委托，重签前后复核快照修订，取消/重收/他人/任意target拒绝。前端需消费新增资源接口展示已保存图。
- 短文新增本人安全history读取及独立started/completed计数，失权/模块关闭/下线仍保留标题与安全进度，不返回正文、译文、音频、paragraph_id或offset。正在执行真实SQL/HTTP回归。
- admin `learning_features/store.py` 已交付真实 `learning_features_v15`，缺失/坏配置false；SQLContentLoader实际注入SceneDetail.recording_enabled，BFF严格布尔DTO已接入，未编造开启状态。
- admin0018新增short_reading.sort_order并有真实运营写入；BFF按(sort_order,public_id)排序，游标绑定user及实际发布ID/排序/修订摘要，重排/发布变化422；前后复核排序摘要，不猜配置版本。生产最低schema已提高0018。
- 第一轮新契约已导出64 paths/73 operations（还在实施，不是最终冻结）。

### 新发现管理后端契约依赖（需协调跟进）

本段为中间阶段确证的四项依赖；后续管理端已交付且消费者接入/回归已完成，最新结论见末尾统一门禁，不继续列为未开发事项。

1. **短文安全封面**：actual preview返回cover_asset_id，但BFF ReadingPreview仅供cover_url，当前始终null；正文资源只允许whole_audio。admin v15_access.content_resource/SQLResourceSigner.binding当前一律full_access，不能用这一端点签无权PREVIEW卡封面。需要admin实际交付仅visible+当前发布COVER绑定/媒体审核的签名契约（或preview返回受控cover_url），随后BFF接入；不能通过本地拼OSS公开地址绕过授权。需求30.5/02方案§8。
2. **保存旧音频版本**：admin pronunciation/service.resolve直接选当前target.uploaded_resource_version，忽略已保存context.resource_version。同target收藏v1后后台换v2会被最终saved.version护栏拒绝403；BFF正确拒绝错版但历史known合法播放性缺失。需admin FAVORITE_REVIEW按已保存固定版本解析，未知历史不造版本。
3. **历史known媒体类型**：admin回填仍存SCENE/VOCABULARY/PHRASE；BFF查询已明确映射词/语块，但admin SQLFavoriteAuthority直接透传旧entry_type，favorite_resource及SQLResourceSigner只识别word的图角色。known图经本人收藏资源委托仍会拒绝；需admin在历史权威读端规范实际类型，不能让BFF重新猜存档语义。
4. **历史音频别名**：actual旧SceneContent条目保存audio_version_id，backfill.build_snapshot历史绑定读取resource_version，需上游明确已知audio_version_id和target的同源成对回填，禁止跨快照拼目标与版本。

上述为具体代码/契约缺口，不属于云端或人工验收；本仓继续完成其他安全、历史、排序、Socket入口与完整门禁，新增依赖不作为停止全部工作的理由。

### 第二轮中间交付门禁（2026-10-11）

- Ruff全部通过；220文件格式通过；mypy检查130源文件无错误。
- 本次重新执行完整私有门禁：326 passed/1 external skip，覆盖83.15%，36.42s；实际admin迁移0018、私有MySQL/Redis、双服务Socket及合法JWT/session/HMAC/SQL。唯一skip仍为外部OSS授权环境验收。
- 本轮录音schema2字段、短文运营排序、本人阅读历史、收藏词汇图运行时及64 paths/73 operations的JSON/TS一同作中间提交，消费者可立即接入。SHA见v15-contract-handoff.md。
- 独立评审另确证schema1旧场景录音开关未透传；actual POST /internal/v1/learning-features已有装配，本仓随后补已授权旧场景的权威外层字段，前端旧adapter也需透传。此中间提交不宣称此项已完成。
- 协调侧运行9dee3a7a3b3d4fc7bd8f48a56b1df80e记录PREFLIGHT_PASSED，私有资源已清理；该跨项目预检由协调侧执行，不代替最终页面验收。

### 中间提交之后的能力收口

- `c9e2f14`接入actual安全preview-cover端点，仅签当前发布COVER；BFF严格核对target/asset/role、HTTPS、60秒有效期，签名前后重新核对preview发布版本与封面，返回前再次核权及检查过期。独立封面单元28项及只读评审通过，独立SQLResourceSigner/HMAC/Redis回归纳入本轮统一门禁。
- `17a6552`实际历史媒体链通过：同target当前上传变v2后收藏仍使用已保存v1；actual回填SCENE/VOCABULARY保留旧标签且取图/发音委托规范角色；未知版本不拼当前资源；取消及陌生用户拒绝。
- 主集成新增legacy取消事件映射修复，存档不变，仅内部事件及引用投影SCENE→dialogue；完整known释放事件实际DELIVERED且管理投影inactive。缺失历史发布修订时保留已知图/目标/版本而不编造绑定或发送非法null修订事件，取消失效且资源引用/投影/outbox均0。
- schema1已授权正文外层录音布尔字段读取实际HMAC配置；true/false/missing/malformed、同键重开和no-store真实Socket通过。可选配置404/403/503只关闭录音，不影响成功授权的正文；preview不读开关。前端旧adapter须消费外层同名字段。
- 协调和前端已确认独立history区满足失权/下线进度保留，不要求重复progress_summary；本仓不增加该冗余字段，列表/预览已读徽标不能用本地猜测代替。
- 新确证代码依赖：需求6.3无权提示可配置次级资料入口`profile_completion_enabled`。截至本段记录actual管理端仍无持久配置及内部投影，当前正式LearningCatalog无字段；协调已要求管理端交付后再接入，不把contact_prompt_eligible代替配置，也不把此项归为外部验收。

### 资料入口权威配置已接入

管理端随后实际扩展learning_features_v15及HMAC读取响应，提供profile_completion_enabled严格布尔，缺失/坏配置false，与录音独立。BFF严格内部DTO允许该字段且旧上游缺失默认false；正式LearningCatalog显式boolean/defaultfalse，CatalogService每次读取实际权威配置，配置403/404/503不阻断正常目录但关闭可选入口，授权待定亦关闭。没有从联系方式、Mock或本地Settings推导开启。

消费者回归先6 failed/1 passed（缺端口/字段和严格DTO拒绝）→7 passed；与旧录音21项单元通过。真实legacy Socket用例同时校验本人GETcatalog的true/false/missing/malformed及no-store；独立只读评审27项通过，无新增确证P1/P2。最终新完整门禁随后记录。

## 本轮最终统一门禁与冻结（2026-10-11）

- `uv run ruff check src tests scripts`全部通过；`uv run ruff format --check src tests scripts`227文件格式通过；`uv run mypy src`131源文件无错误。
- `uv run pytest -q --tb=short`：339 passed/40 skipped，4.62s；这些无私有环境skip不作为SQL验收证据。
- `uv run python scripts/test-v15-isolated.py -q --tb=short --show-capture=no --cov=juya_miniapp_api --cov-report=term:skip-covered --cov-fail-under=80`：378 passed/1 skipped，覆盖83.38%，46.41s。唯一skip为外部OSS授权环境验收；此前371项/83.36%为资料入口配置接入前历史门禁。
- 实际admin0018迁移、UUID私有MySQL/Redis、两个生产uvicorn、合法JWT及真实SQL session、HMAC/nonce、事务授权和OutboxDispatcher投递均覆盖；新增实际SQLResourceSigner安全封面COVER签发，仅其外部OSS HEAD/sign为明确测试端口，不代表云访问。
- 历史固定资源v1、旧类型规范化、同源音频别名、未知版本拒绝、取消/他人拒绝、known释放事件DELIVERED、partial无修订原字段保留且无非法事件，均在实际SQL/HMAC链中通过。
- 新版及旧版录音与资料入口配置true/false/missing/malformed已实际Socket验证，所有受控打开/目录返回private,no-store；配置读取不可用仅关闭可选能力，不绕过或阻断已成功的内容授权。
- OpenAPI按实际配置完整运行时重新生成64 paths/73 operations，TS由openapi-typescript@7.10.1生成；最终绝对路径与SHA见v15-contract-handoff.md，必须替换a9a97b8前端固定fixture和根接口文档旧基线。
- 独立只读评审在旧录音降级、profile配置、安全封面及历史释放修正后无新增确证P1/P2；没有跨仓库编辑、共享迁移/重启、.env修改、push或部署。
- 本仓本轮代码缺口已闭合，可由协调消费；最终四项目真实页面、微信身份/设备、版权/人工听音、云预算/OSS CORS与Range、生产分别待对应验收，不能由这些本地门禁替代。
