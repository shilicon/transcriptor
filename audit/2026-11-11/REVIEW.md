# A01–A08 修复独立复审

日期：2026-11-11。对象：`90b7a3e` 上当前未提交的修复，精确 HEAD 与关键源文件 SHA-256 见 [reaudit-source.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/reaudit-source.json)。

**结论：不能验收为“A01–A08 全部完成”。A01、A03、A07 的原问题在本次验证范围内可关闭；A02、A04、A05、A06、A08 仍为部分修复。** 49 项现有 Python 测试、原生探针与原生编译确实通过，但独立补充探针复现了下列遗漏。

本次仅新增审计材料，没有修改产品实现或已有测试，没有回退、提交、推送或发布。原始 REPORT.md、REMEDIATION.md 和旧复现证据保留。继续排除 YouTube 下载和字幕功能实现；重跑现有整套测试包含其纯本地单元测试，不代表完成了该新功能审计。未读取真实任务或密钥；补充探针使用临时任务、合成文本、HTTP 替身；未调用付费服务或下载模型。

| 原编号 | 复审状态 | 本次结果 |
|---|---|---|
| A01 | 原问题可关闭 | `decades` 完整词边界、术语默认关闭、原始文本与整理文本分开保存均有对应实现和测试；旧任务不伪造 raw_text |
| A02 | 部分修复，P1 | 数字顺序和原有重复删除样例已挡住；独立的中文“不”仍可被删除并自动应用 |
| A03 | 原问题可关闭 | 自动分章仅更新章节，事务式修改读取最新任务；分章期间正文与姓名修改的回归通过 |
| A04 | 部分修复，P2 | 转写结束后回写、分章结束后回写已受墓碑保护；请求内部重试仍会在删除后发送文本，提取音轨也能重建空目录 |
| A05 | 部分修复，新增 P1 路径 | 原有 A→B 迟到响应探针通过；同一任务旧保存响应仍可覆盖新显示并清空新草稿，切换加载期间也仍有操作对象错位 |
| A06 | 部分修复，P2 | Host/Origin 和直接访问 API 的校验有效；无凭据 GET 首页会自行获得完整认证 cookie |
| A07 | 原问题可关闭，保留验证限度 | 上传使用 1 MB 分块构建磁盘文件和 upload(fromFile:)，原生 16 MB 探针通过；没有进行 4 GB 峰值内存压测 |
| A08 | 部分修复，P2 | 无效 JSON 和部分结构错误已隔离；未覆盖的元数据类型仍会拖垮整个列表 |

**R01 / A02 · P1：中文否定仍可被自动改成肯定**

位置：[app/correction.py:31](/Users/skmacair/work/transcriptor/app/correction.py:31)。`NEGATION_RE` 包含“不能”“不要”“不是”等词，遗漏了单独的“不”。短语修改又能通过跨度和相似度检查。

独立探针对实际 `_apply_edits` 传入 `type=homophone, confidence=0.99`：

```text
我不赞成这个方案。 → 我赞成这个方案。     accepted=1
这个操作不安全。   → 这个操作安全。       accepted=1
This change is not safe for customers.  accepted=0（英文对照）
```

这证明校验器接受并应用否定反转，不代表真实模型在本次审计生成了这些错误。建议补齐中文否定及英语撇号变体等回归，保留合法局部校正；对无法可靠判定的语义变化采用保守拒绝或待确认策略。有限的否定词表和字符相似度不能证明语义不变。

**R02 / A05 · P1：旧保存响应会覆盖新界面并清空新的人工草稿**

位置：[AppModel.swift:250](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/AppModel.swift:250)、[AppModel.swift:92](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/AppModel.swift:92)。`saveTranscript` 返回时只比较任务 ID，没有校验发起时的更新代数或草稿版本。`adopt` 只在接受后递增代数，不会拒绝旧的修改响应。

编译实际 AppModel/APIClient/Models/BackendManager，仅由 URLProtocol 控制响应顺序，实测：

1. 在 A 保存旧文本，延迟该请求的响应；同一任务再保存新文本，新响应先到。
2. 页面已显示 `newer saved text`，用户开始下一轮编辑，草稿为 `new unsaved draft`。
3. 第一份保存的旧响应到达。
4. 页面退回 `older saved text`，`editingTranscript=false`，草稿数量变成 0。

探针证明客户端状态和新草稿丢失；没有据此声称服务器在该时序已丢失第二份保存。若继续基于旧显示编辑并保存，则还可能把旧正文写回。

另一个实测窗口发生在选择 B、GET B 尚未完成时：[AppModel.swift:76](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/AppModel.swift:76) 没有清空 A 的详情，[AppModel.swift:314](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/AppModel.swift:314) 又从 `job.id` 获取删除对象。此时 `selected=B, displayed=A`，删除请求为 `DELETE /api/jobs/A`。界面仍显示 A 的标题，不能描述为完全不可见的误删，但选择与操作对象确实不一致。

建议对保存、分章、撤销、加载、删除统一使用任务 ID、选择代数、请求序号及草稿版本约束；接受响应时不能清空请求发起后产生的草稿。切换详情期间清空旧详情或禁止针对不一致对象的操作。复查 Web 的保存响应：目前也主要按任务 ID 接受结果，不能仅凭已有 chapterRequest/jobRequest 推断所有更新都受保护。Web 同类乱序本次只做源码检查，未单独运行浏览器复现。

**R03 / A06 · P2：匿名访问首页即可获得全部 API 权限**

位置：[app/main.py:143](/Users/skmacair/work/transcriptor/app/main.py:143)。首页无条件把全局会话 TOKEN 放进 cookie，不要求任何预先持有的凭据。

独立客户端从始至终不发送 token 请求头、不读取 token 文件、不发送 Origin，实测：

```text
GET /api/jobs              → 401
GET /                     → 200，获得认证 cookie
GET /api/jobs（带该 cookie）→ 200
DELETE /api/jobs/cookie-victim → 200，合成任务目录删除
```

因此能连接回环服务的本地 HTTP 客户端不需要读取 0600 文件就能获得权限；不能把剩余风险仅归结为“同一 macOS 用户能读 token 的进程”。HttpOnly/SameSite 保护浏览器 cookie 行为，不能阻止普通 HTTP 客户端获得并使用它。Host/Origin 修复仍然有价值；本次没有证明某个真实浏览器中的跨站窃取或完整攻击链。

建议移除匿名首页直接发放凭据的路径，设计由原生应用或明确登录动作持有秘密的一次性会话引导；保留 Web 功能并验证合法登录方式。验收需包含“无凭据先 GET / 再调用读写 API 仍被拒绝”，不能保留现有测试中匿名首页自动取得权限的期望。

**R04 / A04 · P2：删除后仍会发起新的 AI 重试请求**

位置：[app/correction.py:339](/Users/skmacair/work/transcriptor/app/correction.py:339)、[app/correction.py:357](/Users/skmacair/work/transcriptor/app/correction.py:357)、[app/correction.py:405](/Users/skmacair/work/transcriptor/app/correction.py:405)。取消检查只在批次前后，未进入 `_call_chat` 的重试和兼容回退路径。

独立探针以 HTTP MockTransport 替代全部网络：第一条请求返回 503 或 400 前，调用真正的删除入口，目录删除并标记墓碑；随后真实 `_call_chat` 仍发起第二条请求，正文仍含合成逐字稿，直到第二条响应后才抛出取消异常。503 的退避重试和 400 移除 response_format 的立即重发均能复现。

这是删除后新发出的请求，不属于“已经发送的请求无法撤回”的已知限制。建议把取消状态传入请求层，每次 POST 前、回退前、退避结束后检查，并让取消异常直接传播，避免进入普通重试。

附带的文件生命周期遗漏：[app/transcribe.py:43](/Users/skmacair/work/transcriptor/app/transcribe.py:43) 在提取音轨时独立 mkdir，不参与任务锁。把工作线程暂停在提取开始、mkdir 之前，删除并恢复线程，实际 ffmpeg 路径会重建空目录，随后因输入已删除而失败。本次仅观察到空目录重现，**没有**重建 job.json、音频内容或导出文件，影响低于原始“整个任务复活”。仍建议任务工作目录与取消/清理流程统一管理，并补充该窗口的回归。

**R05 / A08 · P2：元数据类型不合法仍会阻断所有任务**

位置：[app/utils.py:55](/Users/skmacair/work/transcriptor/app/utils.py:55)、[app/main.py:519](/Users/skmacair/work/transcriptor/app/main.py:519)。结构校验没有覆盖 created_at、title 等客户端和列表所依赖的字段。

使用一个健康任务与一个合成损坏任务实测：

- 损坏任务 `created_at=123`：加载器接受，详情返回 200；列表排序遇到数字与字符串比较，整个 `/api/jobs` 返回 500。
- 损坏任务 `title=["invalid-title"]`：列表返回 200 且 `corrupt=false`；使用实际 Swift JobSummary 解码器，整个列表解码失败，健康任务也无法更新显示。

建议统一定义存储/接口结构与兼容旧任务的规则，验证必要字段、嵌套字段和有限数值；单任务校验失败时返回损坏摘要与详情 422，保留原文件。补充列表和客户端解码测试，不能只校验 JSON 可解析或容器类型。

**验证与材料**

- 现有 Python 测试：49/49 通过，见 [reaudit-python-tests.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/reaudit-python-tests.log)。数据目录设为临时目录。
- 现有原生探针：通过，16 MB 上传的 httpBody 为 nil，合成 API Key 请求头存在，见 [reaudit-native-tests.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/reaudit-native-tests.log)。该测试不等价于实际网络接收端验证完整 multipart 或峰值内存压测。
- 原生应用隔离 Swift debug 编译：通过，见 [reaudit-swift-build.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/reaudit-swift-build.log)。首次编译遇到默认缓存目录权限限制，改用临时缓存后成功；未覆盖现有 .app，未启动真实应用。
- `node --check app/static/app.js` 和 `git diff --check` 通过。
- 独立 Python 结果：[reaudit-python-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/reaudit-python-results.json)；脚本：[reaudit_reproduce.py](/Users/skmacair/work/transcriptor/audit/2026-11-11/reaudit_reproduce.py)。提取删除探针的预期失败日志另存 [reaudit-python-stderr.log](/Users/skmacair/work/transcriptor/audit/2026-11-11/reaudit-python-stderr.log)。
- 独立原生结果：[reaudit-native-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/reaudit-native-results.json)；探针：[ReauditNativeProbe.swift](/Users/skmacair/work/transcriptor/audit/2026-11-11/ReauditNativeProbe.swift)。

从仓库根目录重跑 Python 探针：

```bash
.venv/bin/python audit/2026-11-11/reaudit_reproduce.py
```

原生探针需与当前 AppModel.swift、APIClient.swift、Models.swift、BackendManager.swift 一起用 `swiftc -swift-version 5 -parse-as-library` 编译，指定临时 `-module-cache-path` 与输出路径后运行；不依赖实际后端。

补充探针记录的是审计观察，并非以安全行为为断言的正式回归测试。修复时应将这些触发条件转为正确期望的测试，保留本次结果作为修复前证据。没有执行真实识别准确率评测、4 GB 上传压测、真实浏览器攻击或在线依赖漏洞扫描。
