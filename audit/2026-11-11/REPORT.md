# Transcriptor 独立审计报告

审计日期：2026-11-11（Asia/Shanghai）。

结论：现有核心功能可以编译，已提供的校正测试通过，但目前存在可稳定复现的文本失真、已保存编辑丢失、任务删除失效和界面任务错位问题。适合作为有人复核的开发阶段工具；在用于正式逐字稿或扩大分发之前，建议先修复下列 3 项 P1，再处理 5 项 P2。

**范围与方法**

- 范围：本地音视频转写流程、说话人分离实现、文本整理、AI 校正、章节、Markdown/Word 导出、任务存储、原生 macOS 客户端及保留的 Web 接口。
- 排除：YouTube 下载、平台链接处理、SRT/VTT 字幕及其新功能测试，按用户要求未审计。
- 复现基准：审计开始时最后一个提交 `b547b15d13cd73df2cca4299d25bf48711b63ff3`，使用隔离副本。审计期间项目另行提交了 `90b7a3e`（YouTube/字幕功能）；已对照该提交，本文引用的旧功能问题仍在。下方源码链接的行号对应审计结束时的工作区。
- 使用合成文本、独立临时任务目录和受控的模型/网络替身；未读取用户真实逐字稿、未访问钥匙串、未调用付费 AI 服务、未操作正在运行的应用后端。
- 除本目录的审计材料外，没有修改项目源代码。复现脚本展示问题的实际行为，不应作为“以错误行为为正确结果”的回归测试直接合入。
- P1：应优先修复的结果完整性问题。P2：应在稳定发布前修复的可靠性、安全边界或资源问题。

| 编号 | 级别 | 发现 | 证据性质 |
|---|---|---|---|
| A01 | P1 | 固定术语替换损坏普通英文，关闭 AI 校正仍发生 | 实测 |
| A02 | P1 | 校正校验器接受数字颠倒、否定词删除和中文整句改意 | 实测校验器，未断言真实模型已产生这些输出 |
| A03 | P1 | 自动分章覆盖期间已保存的人工修改 | 实测并发时序 |
| A04 | P2 | 删除运行中任务后，任务和转写文件被后台重新创建 | 实测工作线程 |
| A05 | P2 | 切换任务后，迟到的响应覆盖当前详情，操作对象错位 | 编译实际 Swift 客户端后实测 |
| A06 | P2 | 本地接口没有会话认证与 Host/Origin 校验 | 实测服务端接收行为，未做浏览器攻击演示 |
| A07 | P2 | 原生上传先在内存中拼出整个文件请求体 | 静态确认，未做 4 GB 压测 |
| A08 | P2 | 一个损坏任务文件可阻断整个服务启动和任务列表 | 实测 |

**A01 · 固定术语替换会损坏普通词，且不受 AI 校正开关控制**

位置：[app/formatting.py:13](/Users/skmacair/work/transcriptor/app/formatting.py:13)、[app/formatting.py:78](/Users/skmacair/work/transcriptor/app/formatting.py:78)、[app/pipeline.py:131](/Users/skmacair/work/transcriptor/app/pipeline.py:131)。

`cades` 等规则没有单词边界，并无条件应用于所有访谈。实测经 `build_paragraphs` 处理：

```text
输入：We have decades of experience.
输出：We have deCadence of experience.
```

这个过程在 AI 校正开关判断之前执行。`original_text` 又在整理之后赋值，因此应用中的“撤销 AI 校正”也恢复不了被术语替换改坏的文本。在审计基准版本里，原始分段没有持久化，需回到音频重做识别；新功能增加的分段存储不改变现有整理与撤销路径的问题。

建议：把领域术语作为可选配置，匹配完整词或明确词组；在任何清理之前保留不可变原始转写，基础格式处理与内容替换分别记录。至少加入 `decades`、普通语境下的 `cadence`、不同领域音频的回归样例。

**A02 · 校正校验不足以保证“不改数字、不改内容”**

位置：[app/correction.py:115](/Users/skmacair/work/transcriptor/app/correction.py:115)、[app/correction.py:158](/Users/skmacair/work/transcriptor/app/correction.py:158)、[app/correction.py:164](/Users/skmacair/work/transcriptor/app/correction.py:164)。

把模型可能返回的修改项直接交给实际 `_apply_edits`，以下三项均被接受并自动应用：

```text
数字顺序：We increased from 10 to 20 units.
      → We increased from 20 to 10 units.

否定词：This change is not safe for customers.
    → This change is safe for customers.

中文整句：我们认为这个方案不能上线，因为目前还有严重风险。
      → 我们认为这个方案可以上线，因为目前已经没有风险。
```

原因分别是：数字比较使用集合，丢掉了顺序及次数；`duplicate` 只要求替换文本是原文的子串，没有确认存在重复；局部修改的长度限制按空格分词，整句中文也只有一个“词”，字符相似度又不能约束语义。这里证明的是保护规则存在漏洞，不代表本次调用真实模型观察到了这些错误。由于通过校验的结果会直接覆盖正文，风险高于单纯的校正建议质量问题。

建议：比较数字的有序序列并明确保护符号、单位；重复删除必须能验证为相邻重复片段；对中文使用字符/词跨度上限。语义性质难以程序化保证的修改应进入待确认建议，不能仅凭模型自报置信度自动应用。

**A03 · 自动分章会回写旧任务快照，抹掉已保存的编辑**

位置：[app/main.py:501](/Users/skmacair/work/transcriptor/app/main.py:501)、[app/main.py:516](/Users/skmacair/work/transcriptor/app/main.py:516)、[app/utils.py:34](/Users/skmacair/work/transcriptor/app/utils.py:34)。

实测时序：启动 AI 分章 → 暂停模型响应 → 保存人工修改且接口返回成功 → 恢复分章响应。磁盘正文由 `Saved human correction.` 退回 `Original approved text.`。

原因是分章在调用模型前读取整份 `job`，等待结束后又把该旧对象整体保存。JSON 原子替换只能避免文件写到一半，不能避免过期快照覆盖新内容。其他读—改—整份写入操作，以及 `_refresh_exports` 再次保存，也缺少统一并发控制。原生界面只禁用分章按钮，没有阻止用户在等待时编辑正文或姓名。

建议：引入每任务的版本号和事务式修改；模型返回后在锁内重新读取最新任务，仅更新章节字段，并处理版本冲突。导出应使用明确的任务版本，不能用旧快照回写其他字段。验收必须覆盖“分章期间编辑正文/姓名/撤销校正”。

**A04 · 删除运行中任务不取消工作，后台会将其重新创建**

位置：[app/main.py:417](/Users/skmacair/work/transcriptor/app/main.py:417)、[app/pipeline.py:27](/Users/skmacair/work/transcriptor/app/pipeline.py:27)、[app/utils.py:36](/Users/skmacair/work/transcriptor/app/utils.py:36)。

实测：暂停正在转写的工作线程，调用删除接口得到 `{"deleted":true}`，目录确实消失；恢复工作线程后，任务目录和导出文件重新出现，状态为 `done`。

删除仅移除目录，没有取消令牌或持久删除标记。工作线程继续持有任务对象，后续保存自动创建目录。实际删除时机不同，也可能重新出现为 `error`。删除发生在转写或校正阶段时，后台处理仍可能继续，不能把“界面删除成功”理解为停止后续数据处理。

建议：建立取消/删除状态，工作线程在各阶段及保存前检查；删除流程与任务写入共享同步机制。先阻止后续写入及外部调用，再清理目录。对已发出的外部请求，应明确取消能力边界。

**A05 · 原生界面接收旧任务响应后，侧栏选择与操作对象不一致**

位置：[macapp/Sources/Transcriptor/AppModel.swift:245](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/AppModel.swift:245)、[macapp/Sources/Transcriptor/AppModel.swift:258](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/AppModel.swift:258)。

测试编译了实际 `AppModel`、`APIClient`、`Models`、`BackendManager`，仅用 `URLProtocol` 接管网络响应，未连接真实服务。结果：

```text
任务 A 分章尚未返回，切换到 B：selected=B, displayed=B
A 的分章返回之后：           selected=B, displayed=A
随后调用删除当前任务：        DELETE /api/jobs/A
```

`loadJob` 检查了 `selectedJobId`，但分章、保存、撤销等方法在等待后直接把响应赋给全局 `job`。侧栏仍指向 B，而正文和之后的操作指向 A。界面会显示 A 的标题，故并非无提示地删除 B，但这种状态错位仍容易导致误操作。

建议：捕获请求对应的任务 ID，返回时只有在其仍为当前选中任务时才更新详情；对草稿、忙碌状态和删除后的选择也采用任务级状态。统一处理所有异步更新入口，而不只修补分章。

**A06 · 本地服务缺少自己的认证与来源防线**

位置：[app/main.py:93](/Users/skmacair/work/transcriptor/app/main.py:93)、[app/main.py:279](/Users/skmacair/work/transcriptor/app/main.py:279)、[app/main.py:522](/Users/skmacair/work/transcriptor/app/main.py:522)。

服务绑定 `127.0.0.1` 是正确的，但全部任务接口没有会话令牌，也不检查 Host/Origin。隔离 API 测试确认：

- 使用 `Host: untrusted.invalid:8765` 仍可获得任务列表，返回 200。
- 带外站 Origin、`text/plain` 的无正文 POST 可执行撤销校正，返回 200。
- 带外站 Origin 的 multipart 上传被接收并调度任务，返回 200；测试中调度器已替换，不执行真实转写。

因此任何能访问该回环服务的本地进程都可调用任务接口；在浏览器允许访问本地网络的环境下，还缺少抵御跨站提交与 DNS rebinding 的应用层防线。浏览器的本地网络权限、私有网络保护和同源策略会影响实际网站攻击条件；本次未证明某一浏览器中可完成全文窃取，也没有发现默认监听公网的配置。CORS 不允许读取响应不能替代对有副作用请求的认证。

建议：由应用生成随机会话凭据，通过受控渠道交给后端；所有敏感接口验证凭据，并限制 Host、校验浏览器请求来源。调试 Web 界面需同步设计会话机制。

**A07 · 大文件上传在原生客户端先完整驻留内存**

位置：[macapp/Sources/Transcriptor/APIClient.swift:210](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/APIClient.swift:210)。

客户端先用 `Data(contentsOf:)` 读取文件，再 append 到一个 multipart `Data`，最后赋给 `httpBody`。`.mappedIfSafe` 不能让追加后的请求体变成流式传输。因此内存随整个文件大小增长；面对项目声称支持的 4 GB 文件，会先构造约该量级的完整请求体，再开始上传。上传和已有任务的模型处理也可能重叠，增加内存压力。

这是代码路径确认的资源问题；本次未用 4 GB 文件压测，也不声称已观察到崩溃。服务器的大小检查发生在客户端构造及发送之后，不能保护客户端内存。

建议：上传前检查文件大小，使用磁盘临时 multipart 文件配合 `URLSession.upload(fromFile:)`，或提供受控流式上传；加入进度和取消。以大文件验证峰值内存基本不随文件大小线性增加。

**A08 · 单个损坏任务文件会阻断整个服务**

位置：[app/utils.py:26](/Users/skmacair/work/transcriptor/app/utils.py:26)、[app/main.py:79](/Users/skmacair/work/transcriptor/app/main.py:79)、[app/main.py:390](/Users/skmacair/work/transcriptor/app/main.py:390)。

向独立测试数据目录放入一个内容为 `{broken` 的 `job.json` 后：任务列表返回 500；重新进入服务启动流程抛出 `JSONDecodeError`。其他健康任务因此也无法正常使用。

`load_job` 直接解析 JSON，启动扫描和列表扫描均未隔离单任务错误。当前原子替换降低了正常写入产生残缺 JSON 的概率，但不能覆盖损坏备份、外部修改或非法数据结构。这里没有断言正常关闭应用必然损坏文件。

建议：逐任务捕获解析及结构校验错误；隔离损坏文件并显示可恢复错误，让健康任务继续加载。避免对损坏文件静默覆盖或直接删除。

**其他观察，不计入上述缺陷数量**

- “撤销 AI 校正”实际会把所有段落恢复为整理后的 `original_text`，包括没有被 AI 修改、后来人工修正的段落。已复现人工修改被清除。原生确认框明确写有“所有段落会恢复为 ASR 原始转写”，因此没有将其作为隐瞒行为单独列为缺陷；仍建议保存撤销前版本，并明确人工编辑也会丢失。该“原始”文本还受 A01 的影响。
- 所审计版本只有 6 项测试，集中在校正函数；没有覆盖任务生命周期、并发保存、原生状态、格式整理等关键边界。新字幕测试按范围排除。
- 直接 Python 依赖有版本固定，但没有完整传递依赖锁文件。已安装的 78 个包兼容性检查通过；未做在线漏洞库扫描，不能由兼容性通过推断依赖没有已知漏洞。
- 正向设计：默认只监听回环地址；任务 JSON 采用临时文件加原子替换；API Key 不写入任务 JSON；Web 正文主要使用 `textContent`；AI 校正错误不会使整个转写任务失败。这些措施有价值，但分别不能替代接口认证、并发控制及内容保护。

**验证记录与复现材料**

- 基准版本 Python 测试：`unittest discover -s tests -v`，6/6 通过。
- 基准版本原生应用：隔离目录 `swift build -c debug` 编译成功；未启动真实应用或重签现有应用包。
- Python 审计探针：12 条行为记录，见 [python-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/python-results.json)。包含辅助观察；不是 12 个独立缺陷。
- Swift 任务切换探针：见 [native-results.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/native-results.json)。全部 HTTP 响应均由测试替身返回。
- Python 复现脚本：[reproduce.py](/Users/skmacair/work/transcriptor/audit/2026-11-11/reproduce.py)。可传入已检出的项目源码目录；不传参数时针对当前工作区。每次建立独立临时数据目录。
- Swift 复现源文件：[NativeRaceProbe.swift](/Users/skmacair/work/transcriptor/audit/2026-11-11/NativeRaceProbe.swift)。与被测版本的上述四个 Swift 源文件一起编译，使用 Swift 5 语言模式。

在仓库根目录重跑 Python 探针：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python audit/2026-11-11/reproduce.py /path/to/audit-source
```

Swift 探针的编译与运行方式：

```bash
audit_source=/path/to/audit-source/macapp/Sources/Transcriptor
audit_build=$(mktemp -d /private/tmp/transcriptor-native-audit.XXXXXX)
swiftc -swift-version 5 -parse-as-library \
  -module-cache-path "$audit_build/module-cache" \
  "$audit_source/AppModel.swift" "$audit_source/APIClient.swift" \
  "$audit_source/Models.swift" "$audit_source/BackendManager.swift" \
  audit/2026-11-11/NativeRaceProbe.swift -o "$audit_build/probe"
"$audit_build/probe"
```

本次未执行真实 Whisper/声纹模型推理，不能据此评估识别准确率和说话人分离质量；也未做 4 GB 压测、实际浏览器攻击或在线依赖漏洞扫描。模型调用与音频处理在并发探针中被替换，存储、路由、整理与客户端状态代码则实际执行。

建议修复顺序：先处理 A01–A03 的文本完整性问题，再处理 A04–A06 的任务与接口边界，最后补齐 A07–A08 的大文件与损坏数据恢复能力。每项修复应以本文触发条件改写为期望安全行为的回归测试。
