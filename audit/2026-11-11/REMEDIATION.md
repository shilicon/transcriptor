# Transcriptor 审计修复记录（2026-11-11）

对应审计基线：`b547b15`。本次以当前工作区（包含后续 YouTube / 字幕功能 `90b7a3e`）为基础修复，没有回退版本。

## 验证摘要

- Python：`68` 个测试全部通过。
- 原生 macOS App：隔离 Swift debug 编译成功（临时 `--scratch-path`，未替换 `.app`）。
- 原生客户端探针：`./tests/run_native_probes.sh` 通过，覆盖任务切换竞态、同任务保存乱序、分章/正文重叠、A→B→A 忙碌状态、加载期间操作、草稿版本、大文件上传请求体和损坏列表解码。
- Web 状态探针：`node tests/web_state_probe.js` 通过，覆盖姓名旧响应覆盖新正文、分章/正文重叠刷新、迟到刷新覆盖新保存的反向顺序。
- 此前轮次已做过匿名首页/登录和实际 App 启动验证；本轮未启动或替换已安装 App，仅做隔离编译和测试替身验证。
- 没有读取真实用户任务、真实 API Key，没有调用付费 AI，也没有下载模型。
- 本轮证据：[remediation2-python-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation2-python-results.json)、[remediation2-native-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation2-native-results.json)、[remediation2-web-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation2-web-results.json)、[remediation2-python-tests.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation2-python-tests.log)、[remediation2-native-tests.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation2-native-tests.log)、[remediation2-isolated-swift-build.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation2-isolated-swift-build.log)。
- 第四轮证据：[remediation3-python-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation3-python-results.json)、[remediation3-native-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation3-native-results.json)、[remediation3-web-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation3-web-results.json)、[remediation3-python-tests.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation3-python-tests.log)、[remediation3-native-tests.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation3-native-tests.log)、[remediation3-isolated-swift-build.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation3-isolated-swift-build.log)。
- CLI 整合证据：[cli-integration-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/cli-integration-results.json)。
- 原审计探针 `reproduce.py` 以“复现错误”为输出；修复后它在删除运行中任务处因 `load_job()` 返回 `None` 而停止，不能作为通过标准。复审探针 `reaudit_reproduce.py` 也会在已修复的元数据校验处提前停止；复审原生探针在“切换加载期间”处对旧 `job` 做强制解包，修复后 `job=nil` 也会停止。正确行为由本次新增的断言测试覆盖。

```bash
.venv/bin/python -m unittest discover -s tests -v
./tests/run_native_probes.sh
node tests/web_state_probe.js
# 隔离编译，不替换 .app：
cd macapp && swift build -c debug --scratch-path /private/tmp/transcriptor-build
```

## 第二轮独立复审（R01–R05）状态

本轮不宣称“A01–A08 全部完成”这一笼统结论。按复审发现补修后：

| 复审 | 原编号 | 状态 | 说明 |
|---|---|---|---|
| R01 | A02 | ✅ 补修并回归 | 中文单字“不”、英文弯引号否定变体已纳入否定序列保护；中文整句和否定反转拒绝自动应用，合法局部校正保留 |
| R02 | A05 | ✅ 补修并回归 | 原生增加任务选择代数、mutation 代数、保存请求代数和草稿版本；同任务旧保存响应不能覆盖新状态或清空新草稿；切换加载期间 `job=nil` 且删除/保存等操作拒绝执行；Web 同步增加请求/草稿代数 |
| R03 | A06 | ✅ 补修并回归 | 匿名 `GET /` 不再发放凭据；新增 `POST /api/session` 登录引导；匿名首页后调用读写 API 仍为 401，错误凭据 401 |
| R04 | A04 | ✅ 补修并回归 | `_call_chat` 每次 POST、400 回退、退避重试前后检查取消；502/503 等重试不会在删除后继续；`extract_audio` 不再创建缺失父目录，删除窗口不会重现空任务目录 |
| R05 | A08 | ✅ 补修并回归 | 补齐 `title`、`created_at`、进度、音频、设置、统计、段落、章节、说话人、导出等字段类型校验；损坏任务返回安全摘要，健康列表和 Swift `JobSummary` 解码不受影响 |

A01、A03、A07 的原问题保持关闭；A02、A04、A05、A06、A08 在 R01–R05 修复后重新验证。下面保留第一轮记录，并在对应章节补充第二轮细节。

## 第三轮复审（REVIEW-2）补修

REVIEW-2 指出 R02/R05 仍有遗漏。本轮修复后：

| 复审 | 原编号 | 状态 | 修复与断言 |
|---|---|---|---|
| R02 续 | A05 | ✅ 补修并回归 | 分章与正文保存重叠时，过期 mutation 响应不再静默丢弃，而是安全重取最新详情；`generatingChaptersJobs` 用请求所有权 UUID 管理忙碌状态，A→B→A 后仍能解除；Web 所有修改使用全局 mutation 版本，迟到响应会触发最新状态刷新而不是覆盖旧快照；正式 Node 探针和原生探针覆盖三类时序 |
| R05 续 | A08 | ✅ 补修并回归 | `_is_number` 捕获 `OverflowError`；客户端整数限制为 int64；`progress` 限制 0–100；`stats`、`audio.size`、`num_speakers`、`segment_indices`、章节时间、段落范围和 `correction` 嵌套字段按接口契约校验；坏任务返回安全摘要和 422，原文件不变；真实 Swift `JobSummary` / `Job` 解码通过 |

新增证据：

- [remediation2-python-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation2-python-results.json)
- [remediation2-native-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation2-native-results.json)
- [remediation2-web-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation2-web-results.json)
- `tests/web_state_probe.js`
- `tests/native/AppModelRaceProbe.swift` 的章节重叠、A→B→A、同任务保存乱序和草稿断言

## 第四轮复审（REVIEW-3）补修

REVIEW-3 指出响应返回顺序不能代表服务器快照新旧，以及有限时间值仍可触发 Swift Int 转换崩溃。本轮修复后：

| 复审 | 原编号 | 状态 | 修复与断言 |
|---|---|---|---|
| R02 续 | A05 | ✅ 补修并回归 | 原生修改响应同时校验请求发起时的 `jobUpdateGeneration` 和 mutation 代数；刷新/其他修改已接受时迟到响应会重取最新状态，不能回退章节。Web 增加 `snapshotVersion`，修改响应、GET 刷新和轮询共享快照新旧判定；反向顺序均有正式回归 |
| R05 续 | A08 | ✅ 补修并回归 | 存储/API/展示统一时间上限 30 天（`MAX_TIME_SECONDS` / `Format.maxDisplaySeconds`）；后端拒绝超出范围的 duration/段落/章节/分段；Swift `Format.timestamp` / `duration` 对非有限、负值、超上限值安全降级，不做越界 `Int` 转换 |

新增正式断言：

- `tests/native/ChapterRefreshOrderProbe.swift`：刷新先显示 1 个章节，迟到的正文保存响应不能把章节退回 0；最终正文、章节和忙碌状态同时正确。
- `tests/web_state_probe.js`：保存先显示新正文、迟到 GET 旧快照不能覆盖；姓名旧响应和分章/正文重叠仍保留。
- `tests/native/FormatSafetyProbe.swift` + `tests/generate_format_fixtures.py`：真实 API 输出 → `JobSummary`/`Job` 解码 → 实际 `Format` 函数；覆盖 1e20、非有限值、30 天上界、上界附近取整、正常小数和旧章节 `"00:00"`。
- Python：`test_time_range_errors_are_isolated`、`test_time_boundaries_and_legacy_strings`。

新增证据：

- [remediation3-python-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation3-python-results.json)
- [remediation3-native-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation3-native-results.json)
- [remediation3-web-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation3-web-results.json)
- [remediation3-python-tests.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation3-python-tests.log)
- [remediation3-native-tests.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation3-native-tests.log)
- [remediation3-isolated-swift-build.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/remediation3-isolated-swift-build.log)

## CLI 分支整合（origin/codex/cli-support）

远程分支 `origin/codex/cli-support` 基于 `90b7a3e`，包含：

- `2e21c94 feat: add full command-line transcription workflow`
- `79a770a fix: allow GUI transcription without DeepSeek key`

本轮没有直接 merge 该分支，而是把 CLI 功能移植到当前审计修复工作区：

- 新增 `app/cli.py`、`app/job_setup.py`、`transcriptor`、`tests/test_cli.py`、README 命令行章节。
- `app/job_setup.build_job` 保留审计修复字段：`glossary`、`raw_segments`、`segments`，并新增 CLI `--glossary` / `--no-glossary`。
- `app/main.py` 改为复用 `job_setup`，保留会话认证、任务锁、损坏任务隔离、时间范围校验。
- CLI 复用当前 `run_pipeline`、`write_exports` 和任务生命周期逻辑，不绕过认证/锁。
- `APIClient` 保留 A07 的磁盘流式 multipart 上传，只移植 security-scoped file URL 访问；没有回退到整文件内存上传。
- `NewJobView` 移除“没有 DeepSeek Key 阻止开始”的前置检查，保留无 Key 时继续转写、跳过 AI 校正的行为。
- `scripts/setup.sh` 增加 `transcriptor` 可执行权限和命令提示。

验证：

- CLI 帮助：`./transcriptor --help` 正常。
- CLI 本地合成文件 → tiny 模型 → 完成，JSON 摘要包含 md/docx/srt/vtt。
- CLI 无 DeepSeek Key 且开启 AI 校正：任务完成，`correction.status=failed`，原转写保留，不阻塞。
- Python：`68` 个测试全部通过。
- 原生探针、格式探针、隔离 Swift debug 编译通过。

证据：

- [cli-integration-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/cli-integration-results.json)
- [cli-integration-python-tests.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/cli-integration-python-tests.log)
- [cli-integration-native-tests.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/cli-integration-native-tests.log)
- [cli-integration-help.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/cli-integration-help.log)
- [cli-integration-swift-build.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/cli-integration-swift-build.log)

限制：

- CLI 与 App 共享单机单进程任务存储；不要同时运行两个 CLI/App 进程写同一数据目录。
- CLI 的 YouTube 下载仍依赖 yt-dlp、网络、cookies/proxy，并遵守此前的 cookies 安全边界。
- CLI 不提供交互式会话登录；它直接本地运行流水线，不经过 Web API 认证层。

## A01 固定术语替换损坏正常文本

### 根因

- `GLOSSARY` 中的 `cades` 等规则没有完整词边界，`decades` 中的子串被替换成 `deCadence`。
- 术语替换在 `clean_text()` 中无条件执行，普通访谈也会被改写。
- `original_text` 在整理之后才赋值，原始 ASR 文本没有独立的持久化路径。
- 旧任务的 `original_text` 实际是整理结果，不能当作可恢复的原始识别文本。

### 修复

- 术语替换改为显式开关 `glossary`，默认关闭；API、原生 App、Web 界面均提供开关。
- `GLOSSARY` 规则增加词边界，`decades` 不再命中 `cades`。
- `clean_text(text, language, glossary=False)` 只有开启开关时才应用术语替换。
- 新增 `raw_segments`：Whisper 原始分段只读保存，编辑和字幕同步只修改可编辑的 `segments`。
- 段落新增 `raw_text`（原始 ASR）、`formatted_text`（整理结果），`original_text` 保留为 AI 校正前的整理基线；`text` 是当前文本。
- 旧任务加载时只把 `original_text` 标记为 `formatted_text`，不生成 `raw_text`，避免把整理结果冒充原始识别。
- 撤销 AI 校正只恢复 `correction_status == "corrected"` 且 `manual_edited == false` 的段落；人工编辑保留。

### 验证

- `test_formatting`：`decades` 在开关开/关时都不被改写；`chip stack` 仅在开关开启时替换；`raw_text` 与 `formatted_text` 分离。
- `test_pipeline_keeps_raw_asr_text_separate_from_glossary`：完整流水线中 `raw_segments`、段落 `raw_text` 保持原文，当前文本按开关整理。
- `test_legacy_job_does_not_claim_raw_transcript`：旧任务没有 `raw_text`，只迁移 `formatted_text`。
- `test_undo_restores_only_ai_corrected_paragraphs`：AI 修改恢复，人工修改保留。

### 剩余限制

- 旧任务无法凭空恢复真正的原始识别文本；需要重新转写才能获得 `raw_segments`。
- 原生详情页目前不提供独立的“查看原始转写”界面；原始文本已保存在任务数据中，可通过 API 读取。

## A02 AI 校正校验允许改变原意

### 根因

- 数字保护使用 `set`，丢失顺序和重复次数。
- `duplicate` 只验证替换串是原文子串，`not safe` → `safe` 被误认为重复删除。
- 中文跨度按空格分词，整句中文只有一个“词”。
- 置信度是模型自报字段，不能替代结构安全约束。

### 修复

- 数字改为有序列表比较，保留符号、顺序、次数和百分号；任何序列变化都拒绝。
- 新增相邻重复验证：原文必须恰好由两个相同的替换片段组成（允许中间标点/空格），否则拒绝。
- 新增 `_span_units()`：拉丁词按词计数，CJK 按字符计数；CJK/混合修改限制为 8 个跨度单位。
- 新增否定词/否定短语序列校验，英文和中文都覆盖；删除或改变否定词直接拒绝。
- 置信度仍作为附加门槛，但不再承担安全证明职责；无法可靠验证的修改直接拒绝。
- 既有的 `punctuation` / `casing` / `boundary` 仍只能做不改变规范化词序列的修改。

### 验证

新增回归测试覆盖：

- `10 to 20` → `20 to 10`：拒绝。
- `10, 10` → `10`：数字次数变化，拒绝。
- `not safe` → `safe`（duplicate / homophone / proper_noun）：拒绝。
- 中文整句改写：拒绝。
- 中文 `不能上线` → `可以上线`：否定词变化，拒绝。
- 合法的中文局部修改 `生成是` → `生成式`：接受。
- 既有 `Kaden's` → `Cadence`、真实相邻重复、标点边界测试继续通过。

### 第二轮复审补修（R01）

- 根因：否定词表只有“不能/不要/不是”等短语，遗漏单独的中文“不”；`can't` 等英文弯引号变体也未覆盖。
- 修复：`NEGATION_RE` 增加中文单字“不”和英文 `can’t` / `isn’t` 等弯引号形式；仍使用“原文否定序列 vs 候选文本否定序列”比较，删除或改变即拒绝。
- 回归：`我不赞成` → `我赞成`、`不安全` → `安全`、`isn’t safe` → `is safe` 均断言 `accepted=0`；`Kaden's` → `Cadence` 和中文 `生成是` → `生成式` 仍接受。
- 限制：词表仍是有限集合，无法证明所有语义等价；这是保守拒绝策略的已知边界。

### 剩余限制

- 自然语言语义等价无法完全程序化证明；本次采取“拒绝不确定修改”的安全策略。
- 被拒绝的修改只计入 `rejected` 数量，没有逐条“待确认建议”界面。

## A03 并发操作覆盖已保存的编辑

### 根因

- 分章在调用模型前读取整份 job，返回后把旧对象整体写回。
- 其他读—改—写接口、撤销、导出刷新也没有统一的任务级锁。
- JSON 原子替换只保证单次写入完整，不保证写入的是最新版本。

### 修复

- `app/utils.py` 新增每任务 `RLock`、`mutate_job()`，所有读—改—写在同一任务锁内重新读取最新 job 后再保存。
- 正文、说话人、章节、基础分章、撤销校正、自动分章、导出刷新均改为事务式更新。
- 自动分章只读取段落/说话人快照，模型返回后重新读取最新 job，仅更新 `chapters` 和导出文件，不再回写旧快照。
- 后台流水线按阶段只提交对应字段；完成阶段在锁内重新读取最新 job 写导出。
- 编辑类接口要求任务状态为 `done`，避免运行中的流水线与人工编辑互相覆盖。
- 导出文件使用临时文件 + `os.replace` 原子替换；导出生成在任务锁内基于最新 job 完成。

### 验证

- `test_chapter_response_preserves_text_and_speaker_edits`：分章等待期间保存正文和说话人姓名，分章返回后两者都保留，章节正常更新。
- `test_deleted_job_does_not_get_chapters_or_files`：分章等待期间删除任务，返回时不写章节、不重建目录。
- 现有字幕测试和完整流水线测试通过，确认共享代码修改没有破坏字幕功能。

### 剩余限制

- 锁和删除标记是单进程内存状态；当前产品仍是单机单进程队列，不支持多个后端进程共享同一数据目录。
- 外部 HTTP 请求无法在发出后撤回；只能保证返回后不写入已删除任务。

## A04 删除运行中任务后重新出现

### 根因

- 删除只 `rmtree`，工作线程持有旧 job 对象，后续 `save_job` 会重新创建目录。
- 删除与后台写入没有共享同步机制，也没有取消/墓碑状态。

### 修复

- 新增删除墓碑集合和每任务锁。
- `delete_job_storage()` 在任务锁内标记 deleted 并删除目录。
- 流水线在启动、每个阶段、提交前检查删除标记；`mutate_job()` 遇到墓碑直接抛 `JobDeletedError`，不会重建目录。
- 排队任务被删除后，执行器真正运行时直接返回；不会创建目录。
- AI 校正增加 `should_cancel` 回调，每批开始前和请求返回后检查；删除后不再发起后续批次，不应用已返回结果。
- 自动分章在模型返回后通过 `mutate_job()` 检查删除状态，删除时返回 404 且不写文件。
- 删除接口会清除内存中的任务密钥；已发送到外部服务的数据无法撤回，这一点在文档中明确。

### 验证

- `test_running_job_delete_does_not_resurrect`：运行中删除，释放工作线程后目录和任务不会重新出现。
- `test_queued_job_delete_never_runs`：排队删除后执行器无操作。
- `test_deleted_job_does_not_get_chapters_or_files`：分章期间删除，不写章节或文件。
- `test_delete_corrupt_job_removes_files`：损坏任务也可以显式删除。

### 第二轮复审补修（R04）

- 根因：取消检查只在批次前后，`_call_chat` 的 503 退避重试和 400 移除 `response_format` 的兼容回退仍会发起新 POST；`extract_audio` 自身执行 `mkdir`，删除后可能重现空目录。
- 修复：`_call_chat` 接收 `should_cancel`，在每次 POST 前、400 回退前、503 等退避重试前后检查；`CorrectionCancelled` 直接传播，不进入普通错误重试。`extract_audio` 不再创建父目录，父目录不存在时直接报错。
- 回归：`test_cancellation_blocks_retry_after_503`、`test_cancellation_blocks_response_format_fallback_after_400` 均断言删除后只发出 1 次请求；`test_extraction_delete_window_leaves_no_directory` 断言提取删除窗口结束后目录不存在；`test_extract_audio_does_not_recreate_deleted_parent` 断言不会创建父目录。
- 限制：已经发出的 HTTP 请求和已经运行的 ffmpeg/模型计算不能撤回；本修复保证不再发起后续重试/回退，且不再重建任务目录。

### 剩余限制

- 已进入 `mlx-whisper`、说话人分离或外部 HTTP 请求的计算不能强制中断；删除只保证结果不再写入，不能撤回已经发送的数据。

## A05 异步响应导致任务错位

### 根因

- 原生 `AppModel` 的保存、分章、撤销等方法在 await 后直接写全局 `job`，只检查了 `loadJob`。
- Web 界面的保存、分章、删除等也有同样的“响应到达即写当前状态”问题。

### 修复

- 原生 `AppModel` 引入任务切换代数、详情加载代数和更新代数；所有异步响应必须同时满足：
  - 请求对应的任务仍是当前选中任务；
  - 没有更新的详情加载或人工更新。
- `saveSpeakers`、`saveTranscript`、`saveChapters`、`basicChapters`、`autoChapters`、`undoCorrection`、`deleteCurrentJob` 都改为捕获目标任务 ID，只在仍选中时更新详情。
- 切换任务会清空草稿并取消轮询；分章忙碌状态改为任务级 `generatingChaptersJobId`。
- 删除 A 期间切到 B，删除完成后不会清空 B。
- Web 界面增加 `jobRequest`、`jobsRequest`、`chapterRequest` 计数器和任务 ID 校验；旧响应不会覆盖新任务，旧分章请求不会重置新任务按钮。
- 保留的 Web 界面保存正文、说话人、章节、分章、删除均做相同保护。

### 验证

- `./tests/run_native_probes.sh`：A 分章期间切到 B，A 返回后仍是 B；保存说话人、撤销、删除同样保持 B。
- 原生探针还验证了 16 MB 上传请求体不进入 `httpBody`，且 API Key 通过请求头发送。
- `node --check app/static/app.js` 通过。

### 第二轮复审补修（R02）

- 根因：`saveTranscript` 只比较任务 ID；同一任务两次保存乱序时，旧响应仍会 `adopt` 并清空草稿。切换任务后旧详情仍保留，删除等操作可从旧 `job.id` 取到 A。
- 修复：`selectJob` 立即清空详情并递增选择/保存/章节代数；`beginMutation()` + `canApply()` 统一约束所有 mutation 响应；`saveTranscript` 增加保存请求代数和草稿版本，只有最新保存且草稿未变化时才退出编辑/清空草稿；`saveSpeakers`、`saveChapters`、分章、撤销、删除均校验代数；`deleteCurrentJob` 要求 `job.id == selectedJobId`。
- Web：增加 `saveRequest`、`speakerSaveRequest`、`chapterSaveRequest`、`draftVersion` 和任务 ID 校验；`renderJob` 在编辑中不重绘 transcript，避免覆盖草稿；切换加载期间将 `state.job` 置空，保存/删除操作直接返回。
- 回归：原生探针新增 `sameJobReorderScenario`（旧保存响应不覆盖 newer、不清空新草稿）、`singleSaveDraftScenario`（单次保存返回时保留请求后新草稿）、`switchWhileLoadingScenario`（加载 B 期间删除不产生 DELETE A）。浏览器 `evaluate` 实测：旧保存响应后仍显示 newer、编辑和草稿保留，加载 B 期间删除请求为空。
- 第三轮复审补修：分章与正文保存重叠导致有效分章响应因 mutation 代数过期被丢弃；现在过期响应会 `refreshIfSuperseded` 重新读取最新详情和列表。章节忙碌状态改为 `generatingChaptersJobs` 集合 + 每任务操作 UUID，请求所有权与当前选择解耦，A→B→A 后原请求完成仍会解除该任务的忙碌状态。Web 增加全局 `mutationVersion`，所有修改迟到响应先安全重取最新 `GET /api/jobs/{id}`，编辑中不重绘 transcript；新增正式 `tests/web_state_probe.js` 覆盖姓名旧响应和分章/正文重叠。
- 第三轮回归：`Review2NativeProbe.swift` 输出显示分章成功后章节数 `1`、忙碌 `false`、完成后出现 `GET /api/jobs/A`；A→B→A 后 `still_busy=false`。`tests/web_state_probe.js` 输出显示姓名旧响应后正文仍为 `newer saved text`，章节重叠后显示 `1` 个章节。
- 限制：后端没有正式版本号/ETag；采用客户端请求所有权 + 安全刷新协调，不是服务端 CAS；跨进程并发仍不支持。
- 第四轮复审补修：修改响应额外校验 `jobUpdateGeneration`；Web 增加 `snapshotVersion`，修改、GET 刷新和轮询统一比较快照版本。`ChapterRefreshOrderProbe` 断言刷新显示章节后迟到保存不能回退，`web_state_probe.js` 断言迟到 GET 不能覆盖新正文。

### 剩余限制

- 同一任务的连续轮询与人工保存通过代数避免旧响应覆盖，但后端没有正式版本号/ETag；跨进程并发仍不支持。

## A06 本地接口缺少访问保护

### 根因

- 所有任务接口无认证，Host 和 Origin 不校验。
- CORS 不能替代有副作用请求的认证。

### 修复

- 新增 `app/security.py`：后端启动生成随机 token，写入 `data/.session-token`，权限 `0600`。
- 所有 `/api/*`（除 `/api/health`）要求 token：
  - 原生 App：`X-Transcriptor-Token` 请求头，由 `BackendManager` 读取 token 文件后注入 `APIClient`。
  - 开发 Web：`GET /` 设置 `HttpOnly`、`SameSite=Strict` cookie，同源 fetch 自动携带。
- Host 只允许 `127.0.0.1`、`localhost`、`::1`（带端口时正确解析）。
- 浏览器请求带 Origin 时，Origin 的 host 和 port 必须与当前 Host 一致；无 Origin 的原生/脚本请求不被误拒绝，但仍必须带 token。
- 未认证、不可信 Host、不可信 Origin、正常原生请求、正常浏览器 cookie 请求均有测试。
- token 不写入任务 JSON、导出、URL 或日志；DeepSeek API Key 上传改用请求头，不进入磁盘 multipart 文件。

### 验证

- `test_api_requires_token`、`test_native_header_authenticates_without_origin`、`test_anonymous_root_does_not_issue_session`、`test_session_login_with_token_sets_cookie`。
- `test_untrusted_host_is_rejected`、`test_untrusted_origin_is_rejected`、`test_allowed_origin_is_accepted`。
- `test_token_file_is_private_and_not_exposed`：token 文件权限不含组/其他位，config 和 job JSON 不含 token。
- 实际启动 App：未认证 `/api/jobs` 返回 401，带 token 返回 200。

### 第二轮复审补修（R03）

- 根因：中间件对匿名 `GET /` 无条件设置全局会话 cookie，任何本地 HTTP 客户端都能通过一次首页请求取得完整 API 权限。
- 修复：移除匿名首页 cookie 发放；新增 `POST /api/session`，要求请求头或 JSON body 中提供有效 token，成功后设置 `HttpOnly`、`SameSite=Strict` cookie；新增 `DELETE /api/session` 清理会话。Web 首页改为登录引导，用户输入 `data/.session-token` 内容后换取 cookie。
- 回归：`test_anonymous_root_does_not_issue_session`、`test_anonymous_root_cannot_delete_via_cookie` 断言匿名首页后 API 仍 401 且任务未被删除；`test_session_login_with_token_sets_cookie`、`test_session_login_with_header_also_works`、`test_session_login_rejects_wrong_token` 覆盖登录路径；浏览器实测匿名首页显示登录门、登录后 `/api/jobs` 200。
- 限制：同一 macOS 用户下能读取 token 文件的进程仍可登录；这仍是应用层认证的边界，不是系统级隔离。

### 剩余限制

- 能读取 `data/.session-token` 的同一 macOS 用户进程仍可访问服务；这不能被应用层 token 完全防御。需要更强隔离时应使用独立系统用户、沙箱或钥匙串代理。

## A07 大文件上传完整占用内存

### 根因

- 原生 `APIClient.createJob` 用 `Data(contentsOf:)` 读取整个文件，再拼接到一个完整 multipart `Data`，最后赋给 `httpBody`。
- API Key 也在 multipart 字段中，临时文件方案若不调整会把密钥落盘。

### 修复

- 原生客户端改为：
  - 上传前读取文件大小并拒绝空文件 / 超过 4 GB 的文件。
  - 将 multipart 写入临时文件，按 1 MB 分块读取源文件并写入，不再构造完整内存请求体。
  - 使用 `URLSession.upload(for:fromFile:)` 从临时文件流式上传；失败、取消或完成时通过 `defer` 删除临时文件。
  - DeepSeek API Key 不再写入 multipart，改用 `X-Transcriptor-DeepSeek-Key` 请求头；后端只在内存 `JOB_SECRETS` 中保存。
- 服务端在 Content-Length 阶段提前拒绝超过 4 GB + multipart 余量的请求；原有分块写入和失败清理保留。
- 服务端上传接口移除 `deepseek_api_key` 表单字段，避免 Starlette multipart 临时文件包含明文密钥。

### 验证

- `test_upload_uses_header_key_and_never_writes_it`：任务 JSON 不含 dummy key，密钥只在内存。
- `test_server_rejects_oversized_content_length_before_reading`：小上限 + 3 MB 合成文件返回 413，未创建任务目录。
- 原生探针用 16 MB 合成文件调用真实 `APIClient.createJob`，URLProtocol 观察到 `httpBody == nil`，请求头携带 dummy key。
- 现有 68 个 Python 测试中的字幕、上传和任务测试全部通过。

### 剩余限制

- `URLSession` 的上传进度尚未在原生界面展示；临时文件会占用与文件大小相近的磁盘空间，但不会占用同等内存。
- 4 GB 是产品配置上限；没有进行实际 4 GB 压测，使用 16 MB 合成文件和代码路径确认。

## A08 单个损坏任务阻断服务

### 根因

- `load_job` 直接 `json.load`，启动扫描和列表扫描没有逐任务隔离。
- 非法结构也会在后续代码中造成 500。

### 修复

- `load_job` 捕获 JSON 解析/IO 错误并校验顶层结构、ID、数组/对象字段和段落 text 类型；抛出 `JobDataError`。
- 新增 `load_job_safe`：任务列表逐任务隔离，损坏任务以 `status=error`、`stage=任务文件损坏`、`corrupt=true`、`error=...` 返回，健康任务继续加载。
- 启动扫描跳过损坏任务并记录路径和错误，不覆盖、不删除文件。
- 详情接口返回 422 和明确错误；删除接口允许用户显式删除损坏任务。
- 原生侧 `JobSummary` 增加 `corrupt` / `error`，侧栏显示“任务文件损坏”；Web 侧也显示损坏标记。

### 验证

- `test_list_jobs_isolates_corrupt_entries`：同时存在健康、坏 JSON、非法结构任务，列表返回 3 项，坏文件内容保持不变。
- `test_corrupt_job_does_not_block_startup`：带损坏任务启动 TestClient，`/api/health` 返回 200。
- `test_legacy_job_does_not_claim_raw_transcript`：旧任务加载迁移不会伪造原始文本。
- `test_delete_corrupt_job_removes_files`：显式删除损坏任务成功。

### 第二轮复审补修（R05）

- 根因：加载器只校验容器类型和部分段落字段；`created_at=123` 进入列表排序会抛类型错误，`title` 为数组会导致实际 Swift `JobSummary` 整个列表解码失败。
- 修复：`_validate_job_data` 覆盖 `title`、时间字段、进度、音频、设置、统计、段落、章节、说话人、导出、分段等字段类型；`title` 缺失时使用安全默认值，类型错误则视为损坏；损坏任务在列表中转换为 `corrupt=true` 的安全摘要（`title` 字符串、`created_at:null`、`status:error`），详情 422，原文件不覆盖。
- 回归：`test_metadata_type_errors_are_isolated` 同时放入健康任务、`created_at=123` 和 `title=["invalid"]` 任务，断言列表 API 200、两个损坏任务被标记、详情 422、原文件未修改；原生探针用实际 `JobSummary` 解码损坏摘要，确认健康摘要可解码。
- 限制：损坏文件不会自动修复；用户可显式删除或从备份恢复。Swift `JobSummary` 以服务端返回的安全摘要为输入；没有为任意恶意 JSON 增加客户端宽容解码，避免掩盖接口错误。
- 第三轮复审补修：`_is_number` 对 `float(value)` 的 `OverflowError` 做稳定失败；`_optional_int` 限制到客户端 int64；`progress` 限制 `0–100` 有限数；`stats`、`audio.size`、`settings.num_speakers`、`segment_indices`、章节时间、段落范围按接口契约校验；`correction` 校验 `enabled`、`errors`、`applied/rejected/batches/skipped_manual` 等嵌套字段。
- 第三轮回归：`test_metadata_type_errors_are_isolated` 新增 `progress=10**400`、`stats.paragraphs=2**63`、`correction={"enabled":"yes","errors":"bad"}`；全部 loader 抛 `JobDataError`，启动成功，列表 200 且 `corrupt=true`，详情 422，原文件保留。实际 API 输出交给真实 Swift `JobSummary` / `Job` 解码：列表 `accepted 3 jobs`，健康详情 `accepted`。
- 限制：数值范围按当前 Swift 接口契约（Int64、Double 有限值）设定；没有静默截断或任意类型转换，超出范围即视为损坏。
- 第四轮复审补修：统一时间上限为 30 天（后端 `MAX_TIME_SECONDS`，Swift `Format.maxDisplaySeconds`）；`audio.duration`、段落、章节和分段超出范围即损坏；Swift `Format.timestamp`/`duration` 在非有限、负值或超上限时安全返回 `00:00`/`未知时长`。
- 第四轮回归：`review3_numeric_probe.py` 的 duration/paragraph/chapter 三组 1e20 样例均为 `JobDataError`、启动正常、列表 200/corrupt、详情 422、原文件不变。`FormatSafetyProbe` 从真实 API 列表/详情解码后调用实际 `Format`，并通过 1e20、`infinity`、`nan`、30 天上界、上界附近取整、正常小数和旧 `"00:00"` 断言。

### 剩余限制

- 损坏任务不会自动修复；用户可查看错误并显式删除，或从备份恢复。没有自动隔离/重命名机制，避免静默移动用户数据。

## 文件与兼容性

- 新增：`app/security.py`、`tests/test_formatting.py`、`tests/test_jobs.py`、`tests/test_security.py`、`tests/support.py`、`tests/native/AppModelRaceProbe.swift`、`tests/run_native_probes.sh`。
- 修改：`app/utils.py`、`app/main.py`、`app/pipeline.py`、`app/formatting.py`、`app/correction.py`、`app/exporters.py`、`app/config.py`、`.gitignore`、原生 Swift 客户端、Web 界面、README。
- YouTube 下载与 SRT/VTT 字幕共享代码保持兼容：字幕测试、YouTube URL 校验测试通过；另用本地 HTTP 音频手动跑通 URL 下载 → 转写 → SRT/VTT 导出，确认 `raw_segments`、`raw_text` 和字幕导出同时正常。

## 未执行/明确限制

- 未读取真实任务、真实 API Key、付费 DeepSeek 服务或下载模型。
- 未做真实 4 GB 上传压测；使用合成文件和代码路径验证。
- 未做浏览器实际跨站攻击演示；使用 TestClient 验证 Host / Origin / 认证边界。
- 未做多进程后端并发支持；当前仍是单机单进程队列。
- 旧任务无法恢复真正的原始 ASR 文本；新任务才保存 `raw_segments` / `raw_text`。
