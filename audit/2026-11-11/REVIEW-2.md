# R01–R05 补修独立复审

日期：2026-11-11。对象：HEAD `90b7a3eba399a5c3058a82d242ad35f497fb4200` 上当前未提交的修复。关键源文件 SHA-256 见 [review2-source.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/review2-source.json)。这是对第二轮补修的复审，不替代历史报告。

**结论：上一轮的具体失败样例已修复；R01、R03、R04 在本次验证范围内可关闭。R02、R05 各有一组可复现的 P2 遗漏，暂不全部验收。** 上一轮 R02 的旧响应清空新草稿、切换加载期间删除旧任务等 P1 路径，本次未再复现；不能把本轮剩余问题继续笼统标成 P1。

本次仅新增审计材料，没有修改产品实现或已有测试，没有回退、提交、推送或发布。原审计、第一次复审及修复记录全部保留。继续排除 YouTube 下载与字幕功能实现；重跑整套测试包含其纯本地单元测试，不代表已审计该新功能。补充验证使用临时目录、合成任务和 HTTP 替身，没有读取真实任务或真实密钥，没有调用付费服务、下载模型、启动真实 App 或替换已安装的应用。

| 编号 | 本轮状态 | 独立验证结果 |
|---|---|---|
| A01、A03、A07 | 维持原范围内关闭 | 本轮没有提出重开证据；不代表扩大了此前验收范围，A07 的真实 4 GB 压测限制仍保留 |
| R01 / A02 | 可关闭原问题 | 独立中文“不”删除样例均拒绝；现有回归覆盖英文弯引号否定及合法局部校正，全部通过 |
| R02 / A05 | 部分修复，剩余 P2 | 同任务保存乱序与新草稿保护、切换加载期间操作对象错位已修复；跨操作响应与章节忙碌状态仍有遗漏 |
| R03 / A06 | 可关闭原问题 | 匿名首页不发 cookie，随后读写 API 仍返回 401；合法 token 登录和错误凭据拒绝的回归通过 |
| R04 / A04 | 可关闭原问题 | 503 重试、400 回退在删除后均未发送第二次请求；原提取音轨窗口未再重建空目录 |
| R05 / A08 | 部分修复，剩余 P2 | 原 created_at/title 类型样例已被隔离；极端数字与部分嵌套字段仍能绕过或打断校验 |

## R02 / A05 · P2：跨操作完成后的显示与忙碌状态仍不一致

### 原生：正文保存使有效分章结果被丢弃

位置：[AppModel.swift:111](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/AppModel.swift:111)、[AppModel.swift:367](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/AppModel.swift:367)。不同修改共用 mutationGeneration；自动分章返回时若期间启动过正文保存，就直接放弃整个分章响应，也没有重新获取最新详情。

使用实际 AppModel、APIClient 和模型类型，通过 URLProtocol 控制合成响应时序：

1. 在 A 启动自动分章，暂缓响应。
2. 保存 A 的正文，接受含新正文、尚无章节的保存响应。
3. 完成分章，返回“新正文 + 已生成章节”。该响应模拟后端在最新任务快照上仅更新章节的正确行为。
4. AppModel 显示章节数仍为 0，忙碌标志已清除，完成后没有发起刷新请求。
5. 手动刷新后章节数变成 1。

这证明客户端静默丢弃有效完成结果。探针没有证明服务器丢失正文或章节；原 A03 的服务器事务修复仍维持关闭。

### 原生：分章期间切走再切回，完成后仍显示“生成中”

位置：[AppModel.swift:95](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/AppModel.swift:95)、[AppModel.swift:355](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/AppModel.swift:355)。选择任务会增加 chapterGeneration，分章请求的 defer 却只有在该代数未变化时才清除 generatingChaptersJobId。

实测顺序：启动 A 的自动分章 → 选择 B 并完成加载 → 重新选择 A 并完成加载 → 原分章请求成功结束。最终 `selected=A`、`request_finished=true`、`generatingChaptersJobId=A`、`isGeneratingChapters=true`。

[ChaptersView.swift:107](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/ChaptersView.swift:107) 据此显示进度，并禁用自动分章和基础分章按钮。该请求的完成不会解除忙碌状态。基础分章具有相同的清理结构，但本次独立运行的时序使用自动分章。

### Web：姓名保存的迟到响应覆盖较新的正文显示

位置：[app.js:581](/Users/skmacair/work/transcriptor/app/static/app.js:581)。saveSpeakers 仅比较自己的请求计数器，然后用整个返回对象覆盖 state.job；正文保存使用另一个计数器，不能阻止姓名保存的旧快照覆盖新正文。

在 Node 中执行实际 app.js 修改处理函数，仅替换网络、DOM 控件和渲染观察器：姓名保存先提交但暂缓旧快照的响应 → 正文保存完成并显示 `newer saved text` → 释放姓名响应 → state.job 和传给 renderJob 的正文均退回 `original text`。

实际 [renderJob](/Users/skmacair/work/transcriptor/app/static/app.js:330) 在非编辑状态重绘正文，而正文保存成功会结束该编辑状态，因此源码与状态探针共同支持旧正文会被重绘。**本轮没有进行真实浏览器视觉复现。** 没有观察到服务器持久化文本在此时序被覆盖；风险是显示落后，以及用户随后基于旧显示继续编辑。

**修复与验收建议：** 按任务和操作管理在途请求，并对重叠修改完成后的最新状态进行协调。不能仅丢弃旧请求的整个响应而不获取最终状态，也不能无条件接受整个快照。可选择安全刷新或按字段应用，但必须保留请求发出后产生的草稿，并防止刷新响应再倒退。忙碌状态的结束应绑定请求所有权，与当前选择代数解耦，避免旧请求误清新请求状态。将以上三种时序加入正式回归，并继续保留原同任务保存乱序、草稿和加载期间操作保护测试。

## R05 / A08 · P2：数值边界与嵌套类型仍未完整隔离

位置：[app/utils.py:56](/Users/skmacair/work/transcriptor/app/utils.py:56)、[app/utils.py:72](/Users/skmacair/work/transcriptor/app/utils.py:72)、[app/utils.py:216](/Users/skmacair/work/transcriptor/app/utils.py:216)。_is_number 中的 float(value) 可能抛出 OverflowError；_optional_int 接受超出客户端整数范围的 Python 整数；correction 仅验证部分字段。校验在 JSON 解析的异常捕获之后执行，load_job_safe 只捕获 JobDataError。

每个样例均使用一个健康任务与一个合成损坏任务，未改动真实数据：

| 损坏内容 | 实测结果 | 影响边界 |
|---|---|---|
| `progress = 10**400`，写入 JSON 为 401 位整数 | load_job 与应用启动均抛出 OverflowError；单独绕过启动执行 HTTP 时，列表和详情均为 500 | 一个损坏任务仍能阻断启动；服务已启动时也可能阻断整个列表 |
| `stats.paragraphs = 2**63` | 加载器接受，列表 200 且 corrupt=false；实际 API 列表交给实际 Swift JobSummary 解码器后，整个列表解码失败 | 健康任务也不能正常更新显示 |
| `correction = {"enabled":"yes","errors":"bad"}` | 加载器接受，详情 200；实际 API 详情交给实际 Swift Job 解码器后失败 | 该损坏任务未被正确标记，详情不可用；此样例没有阻断健康任务列表 |

这属于损坏存储数据隔离不完整；没有证明正常操作会生成这些数据，也没有证明远程攻击路径。超大整数仍是可解析的 JSON，不能以“JSON 解析成功”替代接口数值范围验证。

Swift [Stats](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/Models.swift:19) 使用 Int，[CorrectionInfo](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/Models.swift:45) 的 enabled 是 Bool、errors 是字符串数组。服务端应按实际接口契约校验，而非依靠客户端宽容解码掩盖问题。

**修复与验收建议：** 数字校验应对非法或超范围输入稳定产生 JobDataError，覆盖有限数值、客户端整数范围及字段业务范围；检查客户端实际解码的嵌套字段，保留合法旧记录兼容。每个坏任务应返回安全摘要与详情 422，原文件保持不变，健康任务仍可启动、列出并由真实 Swift 类型解码。避免仅特殊处理本次三个常量，或通过静默数值截断、任意类型转换隐藏损坏。

## 已通过的验证与证据范围

- Python 现有测试 **62/62 通过**，使用独立临时数据与模型目录，见 [review2-python-tests.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/review2-python-tests.log)。
- 现有原生探针通过，见 [review2-native-tests.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/review2-native-tests.log)。16 MB 合成上传探针不等价于 4 GB 压测。
- 原生应用在临时构建与缓存目录中的 Swift debug 编译通过，见 [review2-swift-build.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/review2-swift-build.log)。本轮没有运行会替换应用产物的 build_app.sh，也没有启动真实 App。
- `node --check app/static/app.js` 和 `git diff --check` 通过。
- 原同任务保存乱序探针现在保留新正文、编辑状态和新草稿；加载 B 时旧详情为 nil，未发送删除 A 请求，见 [review2-previous-native-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/review2-previous-native-results.json)。其中 `A08_list_decode` 是旧探针直接喂给解码器的固定非法列表，绕过服务端验证；它不是 title 修复失败的证据，不参与本轮未关闭结论。
- 原 created_at/title 失败样例现在都返回列表 200、corrupt=true、详情 422，启动正常。匿名首页后读写仍为 401，删除重试探针仅记录删除前的一次 HTTP 请求，提取窗口不再产生目录。见 [review2-python-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/review2-python-results.json)。

## 独立补充探针

| 探针 | 结果 / 输入 |
|---|---|
| [review2_reproduce.py](/Users/skmacair/work/transcriptor/audit/2026-11-11/review2_reproduce.py) | [Python 结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review2-python-results.json)、[预期异常日志](/Users/skmacair/work/transcriptor/audit/2026-11-11/review2-probe-stderr.log)；复用历史 reaudit_reproduce.py 中的隔离环境及探针 |
| [Review2NativeProbe.swift](/Users/skmacair/work/transcriptor/audit/2026-11-11/Review2NativeProbe.swift) | [原生结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review2-native-results.json)；真实 API 输出输入为 [stats 列表](/Users/skmacair/work/transcriptor/audit/2026-11-11/review2-stats-list.json)、[correction 详情](/Users/skmacair/work/transcriptor/audit/2026-11-11/review2-correction-detail.json) |
| [review2_web_probe.js](/Users/skmacair/work/transcriptor/audit/2026-11-11/review2_web_probe.js) | [Web 状态结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review2-web-results.json)；真实处理函数配合替身，不是完整浏览器测试 |
| [Review2PreviousNativeProbe.swift](/Users/skmacair/work/transcriptor/audit/2026-11-11/Review2PreviousNativeProbe.swift) | 原历史探针的兼容副本；旧详情改为可空观察并关闭缓存，历史原文件未修改 |

Python 探针接收一个临时输出文件路径，写出实际 API 列表，并在同目录写出 correction-detail.json。Swift 探针与当前 AppModel.swift、APIClient.swift、Models.swift、BackendManager.swift 一起，以 `swiftc -swift-version 5 -parse-as-library` 编译，使用临时 module-cache-path 与可执行文件路径；运行时传入这两份 JSON。Node 探针可直接用 Node 执行。所有命令应在仓库根目录运行，输出到临时目录，不覆盖本次历史结果。

这些探针记录观察结果，不是以安全行为为断言的正式回归测试。修复时应把触发条件转成正确行为断言，不能把“探针正常退出”当作问题已解决。本轮未进行真实浏览器跨站攻击、在线依赖漏洞扫描、真实识别准确率评测或 4 GB 上传压测；有限否定词表也不能证明任意改写的语义等价。
