# REVIEW-3 补修独立复核

复核日期：2026-11-12。为保留审计链，材料继续放在原 audit/2026-11-11 目录。对象是当前未提交代码，HEAD 和 15 个关键源码/正式测试文件的 SHA-256 见 [review4-source.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-source.json)。复核前后的这 15 个文件和 HEAD 一致。

**结论：REVIEW-3 的原生快照倒退、Web 旧刷新覆盖新正文、极端时间格式化崩溃，均已通过独立复现验证修复。但 R02、R05 各剩一个 P2，暂不整体关闭。** R02 剩余问题是 Web 内部状态正确而章节区域未更新；R05 剩余问题是时间范围只约束读取，章节写入仍可把健康任务变为无法打开的损坏任务。

| 项目 | 本次状态 |
|---|---|
| R02 / A05 原生快照及忙碌状态 | 本次目标用例通过，未复现 REVIEW-3 的倒退 |
| R02 / A05 Web 快照倒退 | REVIEW-3 原用例通过；章节/正文反向响应顺序下仍遗漏界面更新，P2 未关闭 |
| R05 / A08 已损坏文件隔离及 Swift 格式化 | 本次极端值、范围边界及实际解码/格式化通过 |
| R05 / A08 时间范围写入一致性 | 章节保存仍返回 200 并持久化越界时间，下一次读取返回 422，P2 未关闭 |
| A01、A03、A07、R01、R03、R04 | 维持此前审计范围内关闭；本轮没有重新做完整审计 |

本次只新增审计文件，没有修改产品实现、正式测试或历史报告。没有读取真实任务/密钥、调用外部服务、下载模型、启动真实 App、替换安装的 App、提交、推送或发布。YouTube 下载及字幕新功能仍不属于审计对象；完整测试中的本地相关测试通过不代表审计了该功能。

## R02 / A05 · P2：章节状态已更新，但 Web 界面仍显示旧章节

位置：[app.js:318](/Users/skmacair/work/transcriptor/app/static/app.js:318)、[app.js:538](/Users/skmacair/work/transcriptor/app/static/app.js:538)、[app.js:498](/Users/skmacair/work/transcriptor/app/static/app.js:498)。

新增 snapshotVersion 修复了旧快照覆盖问题。但 refreshJobState 接受最新结果后，使用 `if (!state.editing) renderJob(job)` 跳过整个界面更新。正文保存发现自己的快照代数已失效时，刷新后立即返回，未处理本次保存的完成状态。两者叠加会使章节区域持续停留在旧内容，即使没有产生新的未保存草稿。

复现步骤：

1. 显示任务 A，章节区域为空，发起自动分章。
2. 进入正文编辑，输入 `newer saved text`，发起保存。之后没有再编辑草稿。
3. 服务器先保存正文，保存响应暂缓；随后分章提交并保留新正文，返回包含 1 个章节的结果。
4. 分章响应触发刷新。`state.job.chapters.length` 变为 1，但因为编辑状态仍为 true，不刷新章节区域。
5. 释放无章节的旧正文保存响应。新保护正确拒绝旧快照并再次刷新，随后提前返回。
6. 所有请求结束，后台及内部状态为 1 个章节；实际 renderChapters 所构建的章节区域仍为 0 行。分章按钮已解除忙碌。

独立观察：

```json
{
  "state_text": "newer saved text",
  "state_chapters": 1,
  "rendered_chapter_rows": 0,
  "editing": true,
  "draft_text": "newer saved text",
  "chapter_busy": false
}
```

再次显式调用 refreshJobState 仍因编辑状态而跳过渲染。点击“退出编辑”只调用 renderTranscript，章节编辑区域依然没有章节。重新选择任务可重绘，服务器数据没有丢失。本次没有把“仍处于编辑状态”本身判为数据丢失，问题是已经完成的章节没有显示。

新探针使用实际 saveTranscript、runAutoChapters、refreshJobState、renderJob、renderTranscript 和 renderChapters，仅替代网络和最小 DOM。它观察实际渲染生成的章节行；不是完整浏览器视觉测试。正式 Web 测试中的分章重叠用例起始 editing=false，并把 renderJob 替换为记录函数，因此没有覆盖这个条件。

**修复要求：** 接受新任务状态后，应更新章节等不依赖正文草稿的区域；为当前未保存的草稿保留编辑内容。过期保存响应的快照不能直接采用，但仍应按保存请求、草稿版本判断该次保存是否完成。不要通过统一清空草稿解决显示问题，也不要仅修改测试的期望值。

**验收：** 在 editing=true 的真实处理顺序下，覆盖“章节先响应、正文后响应”和相反顺序；全部响应结束后最新正文与章节都可见。没有新草稿时正常确认保存；出现新草稿时保留它并更新章节。补测刷新失败后再次刷新；断言实际章节区域和草稿，而不只断言 state.job。

证据：[独立 Web 探针](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4_web_probe.js)、[结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-web-results.json)。同一结果也确认刷新期间产生的新草稿被保留、失败刷新后内部状态能恢复；当前遗漏位于界面更新。

## R05 / A08 · P2：章节保存可写入超过新上限的时间，导致健康任务变为损坏

位置：[main.py:622](/Users/skmacair/work/transcriptor/app/main.py:622)、[main.py:630](/Users/skmacair/work/transcriptor/app/main.py:630)、[utils.py:224](/Users/skmacair/work/transcriptor/app/utils.py:224)、[utils.py:360](/Users/skmacair/work/transcriptor/app/utils.py:360)。

新的读取校验正确拒绝超过 2,592,000 秒（30 天）的章节时间，但 update_chapters 仅解析输入，没有应用同一范围校验。mutate_job 校验的是修改前的任务；修改后直接保存。保存路径还在写入 job.json 之前生成导出文件。

通过真实 TestClient API，在隔离目录里创建健康任务和已有导出，提交两个章节（第一个 0 秒，第二个越界，避免首章自动归零掩盖测试）：

```json
{
  "chapters": [
    {"start": 0, "title": "Opening"},
    {"start": "43200:01", "title": "Outside time range"}
  ]
}
```

`43200:01` 是 Web 章节输入框接受的“分:秒”格式，比 30 天上限多 1 秒。观察到：

| 第二章输入 | PUT 保存 | 随后 GET 详情 | 列表摘要 | 磁盘最终时间 |
|---|---:|---:|---|---:|
| 2592000（上界对照） | 200 | 200 | corrupt=false | 2592000 |
| 2592001 | 200 | 422 | corrupt=true | 2592001 |
| `"43200:01"` | 200 | 422 | corrupt=true | 2592001 |
| 1e20 | 200 | 422 | corrupt=true | 1e20 |

三种越界请求都改写了原健康任务，返回成功；同目录另一个健康任务仍能正常打开。此处没有手工破坏 job.json，也没有真实超长录音，而是已有章节编辑 API 能产生其自身拒绝读取的数据。30 天作为产品约束可以保留，但应在写入前拒绝不符合约束的输入。

**修复要求：** 解析后、修改任务及生成导出前，统一校验章节时间的有限性和范围。越界请求返回明确 4xx，原任务和原导出保持不变。不能只在 load_job 加限制，也不能只在 save_job 写盘前验证，因为导出副作用已经发生。检查相同时间契约在相关写入路径的一致性，无需为此改动 YouTube 功能。

**验收：** 对正常值、恰好上界、超过上界的数字和时间字符串、极大有限值分别经过真实保存 API；无效输入被拒绝，文件哈希保持不变，原任务详情仍为 200。保留对已存在损坏文件的安全摘要/422/原文件不变验证，以及 Swift 解码与真实格式化回归。

证据：[独立时间及写入探针](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4_time_probe.py)、[完整结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-time-results.json)、[越界保存实际响应](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-fixtures/write-ui-time-string-response.json)、[随后详情响应](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-fixtures/write-ui-time-string-detail.json)。

## 已验证通过的修复

- Python 正式测试 **64/64 通过**：[日志](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-python-tests.log)。运行前设置临时数据/模型目录和合成会话 token。
- 正式 Web 状态回归通过：[日志](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-web-tests.log)。
- 正式原生探针全部通过，包括 ChapterRefreshOrderProbe、FormatSafetyProbe、A→B→A 及合成上传：[日志](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-native-tests.log)。
- 隔离 Swift debug 编译通过，使用临时 scratch/cache，没有运行 build_app.sh：[日志](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-swift-build.log)。
- REVIEW-3 原生独立探针：刷新后 1 个章节，迟到正文保存后仍为 1，最终正文正确，忙碌 false：[结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-prior-native-results.json)。
- REVIEW-3 Web 独立探针：迟到 GET 后仍为 `newer saved text`，渲染参数没有倒退：[结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-prior-web-results.json)。
- 原生追加检查使用实际 AppModel/APIClient + URLProtocol：新草稿保留；一次刷新超时不破坏已接受的正确快照，之后手动刷新成功。没有声称会自动重试或清除旧错误提示：[新探针](/Users/skmacair/work/transcriptor/audit/2026-11-11/Review4NativeProbe.swift)、[草稿结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-native-draft-results.json)、[失败刷新结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-native-failure-results.json)。
- 原 1e20 时长/段落/章节三组样例均为 JobDataError、启动正常、列表 200、损坏摘要、详情 422、原文件不变：[结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-prior-numeric-results.json)。
- 扩展 35 个读取样例，涵盖 duration、paragraph、chapter、segments、raw_segments 的正常值、上界、上界相邻越界值、1e20、Infinity、NaN、负值；10 个正常/上界样例通过，25 个无效样例隔离，原文件全部保持不变。合法旧时间字符串正常转换为数值。JSON 中 NaN/Infinity 是故意写入的合成损坏输入，没有将它们当作合法 API JSON。
- 真实 API 输出的 **37 条列表摘要（含 25 条损坏摘要）和 12 条有效详情**交给实际 Swift JobSummary/Job 解码，再调用实际时间格式化函数，全部通过；另测极大有限值、正负无限、NaN、负值、上界相邻值及取整：[探针](/Users/skmacair/work/transcriptor/audit/2026-11-11/Review4FormatProbe.swift)、[结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-format-results.json)。
- 把 REVIEW-3 保留的原始极端值 API 合成输出直接交给当前 Swift 格式化程序，健康对照及三个过去崩溃的路径均退出 0，输出安全占位值：[结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review4-historical-format-results.json)。没有将现在的 422 响应当成正常 Job 解码。
- `node --check app/static/app.js` 与 `git diff --check` 通过。

以上通过项与剩余两项失败分别记录，不将脚本正常退出等同于观察到的问题已修复。新的 Web、写入探针包含观察结果；应阅读 JSON 字段。

## 范围与重跑

所有新增 Python 探针通过历史隔离辅助代码设置临时目录，使用合成任务和 TestClient；无真实 HTTP 外呼。Web 探针从实际 app.js 加载处理与渲染函数，跳过初始化，以最小 DOM 和受控网络 Promise 复现。原生探针编译实际模型/客户端代码，以 URLProtocol 替换网络。编译仅产生临时可执行程序。

在仓库目录运行 review4_web_probe.js；运行 review4_time_probe.py 时传入新的临时输出目录。将 Models.swift 与 Review4FormatProbe.swift 使用 swiftc 编译到临时路径，传入该输出目录即可验证真实 API 到格式化链。Review4NativeProbe.swift 与 AppModel.swift、APIClient.swift、Models.swift、BackendManager.swift 一起编译，分别以 draft 和 failure 参数运行。不要覆盖本轮历史证据。

未进行真实浏览器视觉测试、4 GB 上传压测、真实识别准确率评测、在线依赖漏洞扫描或完整新功能审计。结论只覆盖上述修复边界，不代表项目整体无缺陷。
