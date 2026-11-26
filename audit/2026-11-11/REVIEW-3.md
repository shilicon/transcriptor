# R02、R05 第三轮补修独立复审

日期：2026-11-11。审查对象：当前未提交补修。HEAD、关键源码与正式测试的 SHA-256 见 [review3-source.json](/Users/skmacair/work/transcriptor/audit/2026-11-11/review3-source.json)。

**结论：REVIEW-2.md 中的具体失败时序和损坏样例均已修复，但 R02、R05 仍各有一组可复现的 P2 遗漏，暂不关闭。** 本轮证据分别是“刷新与修改响应互相覆盖”以及“通过校验和解码的时间值触发原生格式化崩溃”。不能用现有回归全部通过推导这两个保护边界已完整。

仅新增审计材料，没有修改产品代码、正式测试或历史报告，没有提交、推送、发布。没有读取真实任务或真实密钥、调用付费服务、下载模型、启动真实 App 或替换已安装应用。继续排除 YouTube 下载与字幕功能实现；重跑完整测试包含其纯本地单元测试，不代表审计该新功能。

| 项目 | 本次结论 |
|---|---|
| A01、A03、A07 | 维持此前范围内关闭，原验证限制不变 |
| R01 / A02、R03 / A06、R04 / A04 | 维持关闭；重跑历史否定、匿名首页认证、删除重试与提取目录窗口探针未回退 |
| R02 / A05 | 上轮具体样例已修复；不同响应顺序仍造成最终显示倒退，P2 未关闭 |
| R05 / A08 | 上轮三个损坏样例已隔离；有限但超出显示转换范围的时间值仍触发原生崩溃，P2 未关闭 |

## R02 / A05 · P2：请求发起顺序不能保证返回快照的新旧

### 原生：刷新已显示章节，迟到的正文保存响应又清掉章节

位置：[AppModel.swift:125](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/AppModel.swift:125)、[AppModel.swift:327](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/AppModel.swift:327)。refreshIfSuperseded 可以正确读取最新状态；但 saveTranscript 接受响应时只比较 mutationGeneration 和保存请求代数，没有防止该响应覆盖请求期间已接受的更新快照。adopt 会增加 jobUpdateGeneration，正文保存的响应校验并未使用该值。

独立探针编译实际 AppModel、APIClient、Models、BackendManager，通过 URLProtocol 模拟符合当前后端事务行为的响应：

1. 启动 A 的自动分章。
2. 启动 A 的正文保存，服务器先提交新正文，此时尚无章节；暂缓这份保存响应。
3. 分章随后提交，正确保留新正文并加入章节；分章响应先到达客户端。
4. 分章的 mutation 已过期，因此触发 GET 刷新，客户端成功显示 1 个章节。
5. 释放步骤 2 的正文保存响应。它仍是最后发起的 mutation，通过校验，整个快照被接受，章节数退回 0。
6. 所有操作结束后仍显示 0；手动刷新又显示 1。

结果摘录：

```json
{
  "chapters_after_reconciliation": 1,
  "chapters_after_delayed_save": 0,
  "chapters_after_manual_refresh": 1,
  "busy_after_all_responses": false
}
```

本次不是忙碌状态未清理，也不是后端丢失章节；忙碌状态修复已通过。这里证明的是最终界面快照仍可能落后。探针中的网络和服务器状态为合成替身，未调用真实分章服务。

### Web：迟到的“安全刷新”本身覆盖新保存结果

位置：[app.js:293](/Users/skmacair/work/transcriptor/app/static/app.js:293)、[app.js:524](/Users/skmacair/work/transcriptor/app/static/app.js:524)。refreshJobState 捕获 mutationVersion 和 refreshRequest，但在保存请求已发起、尚未完成时启动的 GET，与随后成功的保存响应具有同一 mutationVersion。保存响应被接受也不使该 GET 失效。

独立探针执行实际 Web 修改处理函数，控制以下顺序：

1. 启动姓名保存，再启动正文保存。
2. 姓名响应到达，触发 GET 刷新；此时正文请求尚未提交，GET 读取旧正文，暂缓返回。
3. 正文保存提交并返回，界面显示 `newer saved text`，编辑状态结束。
4. 释放 GET 的旧正文响应，校验仍通过，state.job 和渲染参数退回 `original text`。

全部请求已结束，合成服务器保留 `newer saved text`，客户端最终显示 `original text`。本轮使用 Node 状态及渲染调用观察，不是完整浏览器视觉测试；未观察到服务端已保存正文被覆盖。

### 修复与验收边界

不能把“较晚发起的请求”等同于“较新的服务器快照”，也不能把 GET 刷新视为天然安全。修改响应和刷新响应需共享一致的新旧判定，尤其要覆盖刷新期间已有请求完成、刷新完成后已有请求才返回两种方向。

可采用服务器资源版本，或在客户端按任务协调在途修改、使被后续更新超越的读取失效，并在重叠操作全部结束后可靠获取最终状态；不要求为此支持多个后端进程。选择实现时仍须保护新草稿、选择对象与请求所有权，避免为了防止倒退而再次静默丢掉有效更新。

验收至少包含：上轮时序、本轮两种反向时序、刷新期间建立新草稿、刷新失败后可恢复。需要断言所有响应完成后的最终状态，而不只断言某一次响应刚结束时的状态。若采用重取策略，最终应显示最新正文和章节，同时保留未保存草稿。

证据：[原生结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review3-native-results.json)、[原生探针](/Users/skmacair/work/transcriptor/audit/2026-11-11/Review3NativeProbe.swift)、[Web 结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review3-web-results.json)、[Web 探针](/Users/skmacair/work/transcriptor/audit/2026-11-11/review3_web_probe.js)。

## R05 / A08 · P2：有限时间值仍能触发 Swift 整数转换崩溃

后端位置：[app/utils.py:154](/Users/skmacair/work/transcriptor/app/utils.py:154)、[app/utils.py:187](/Users/skmacair/work/transcriptor/app/utils.py:187)、[app/utils.py:220](/Users/skmacair/work/transcriptor/app/utils.py:220)。时长和时间戳验证有限、非负等条件，却没有上界。它们可以通过 Double 解码，但客户端随后会把它们转换成 Int。

客户端位置：[Models.swift:217](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/Models.swift:217)、[Models.swift:223](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/Models.swift:223)。Format.timestamp 调用 `Int(seconds)`，Format.duration 调用 `Int(seconds.rounded())`。有限数值不保证这两个转换不会溢出。

分别向三个独立临时目录写入一个健康任务与一个合成损坏任务：

| 合成损坏字段 | 服务端观察 | 实际 Swift 解码与格式化 |
|---|---|---|
| audio.duration = 1e20 | loader 接受，启动正常，列表与详情 200，corrupt=false | 列表解码成功后，Format.duration 触发 fatal error |
| paragraphs[0].start/end = 1e20 | 同上 | 详情解码成功后，Format.timestamp 触发 fatal error |
| chapters[0].start = 1e20 | 同上 | 详情解码成功后，Format.timestamp 触发 fatal error |

三次独立子进程都以 SIGTRAP 退出，记录的诊断为：

```text
Fatal error: Double value cannot be converted to Int because the result would be greater than Int.max
```

健康任务对照正常退出，输出 `10 秒`、`00:00`；所有合成任务原文件均未被改写。

[ContentView.swift:163](/Users/skmacair/work/transcriptor/macapp/Sources/Transcriptor/ContentView.swift:163) 在列表行直接调用 Format.duration，正文和章节视图也调用上述时间格式化函数。因此单个这样的损坏任务仍有使原生界面崩溃的路径，健康任务也可能无法正常浏览。**本轮实测的是实际 Swift 类型与格式化函数组成的隔离可执行程序，不是启动真实 App 的视觉复现。** 没有证据表明正常操作会产生这些极端数据，也没有证明远程攻击路径。

### 修复与验收边界

统一存储、接口和展示的时间值契约，设置可安全处理的范围；把不合法的任务标记为损坏摘要、详情返回 422，保留原文件。对原生格式化中的数值转换加防护，使意外输入也不会触发进程级崩溃；不能仅特殊拒绝 1e20。

补充从真实 API 输出到实际 Swift 解码、再到真实格式化函数的回归。覆盖正常小数时间、合法旧时间字符串、极大有限值及安全上界附近的取整行为，注意 Double 表示和 rounded() 可能将边界推到 Int 无法表示的值。单纯断言 JSONDecoder 成功不足以验收这个边界。

证据：[后端结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review3-numeric-results.json)、[后端探针](/Users/skmacair/work/transcriptor/audit/2026-11-11/review3_numeric_probe.py)、[Swift 运行结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review3-format-results.json)、[实际格式化探针](/Users/skmacair/work/transcriptor/audit/2026-11-11/Review3FormatProbe.swift)、[隔离运行脚本](/Users/skmacair/work/transcriptor/audit/2026-11-11/review3_format_runner.py)。API 合成输出保存在 [review3-fixtures](/Users/skmacair/work/transcriptor/audit/2026-11-11/review3-fixtures/duration-list.json) 所在目录。

## 已确认的修复与验证

- **Python 62/62 通过**，数据和模型目录均为临时目录：[日志](/Users/skmacair/work/transcriptor/audit/2026-11-11/review3-python-tests.log)。
- **正式 Web 状态回归通过**，覆盖上轮姓名旧响应和分章/正文重叠时序：[结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review3-web-tests.log)。
- **正式原生探针通过**，含新草稿保护、同任务保存乱序、切换加载期间操作保护、分章重叠、A→B→A 及合成上传：[日志](/Users/skmacair/work/transcriptor/audit/2026-11-11/review3-native-tests.log)。
- **隔离 Swift debug 编译通过**，使用临时 scratch/cache 路径，未运行 build_app.sh：[日志](/Users/skmacair/work/transcriptor/audit/2026-11-11/review3-swift-build.log)。
- 重跑历史独立 Python 探针，原 created_at/title、progress=10**400、stats.paragraphs=2**63、correction 类型样例均为 JobDataError、启动正常、列表 200、详情 422：[结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review3-previous-python-results.json)。
- 重跑 Review2NativeProbe，原分章/正文时序显示 1 个章节并刷新，A→B→A 后 busy=false；服务端安全摘要交给真实 Swift 列表类型可解码：[结果](/Users/skmacair/work/transcriptor/audit/2026-11-11/review3-previous-native-results.json)。该历史探针保留了字段名 actual_backend_correction_detail_decode，本轮此字段输入是健康任务详情，结果 accepted；损坏 correction 的详情已是 422，不应把错误响应拿来当正常 Job 解码。
- `node --check app/static/app.js`、`git diff --check` 通过。

现有回归通过与新增边界失败并不矛盾：前者的响应顺序与数值样例不同。以上新增脚本记录观察结果；脚本正常结束不代表问题修复，尤其格式化运行脚本会捕获子进程失败并保存证据。

## 重跑方法与范围

在仓库根目录执行 Web 探针即可观察 R02 的 Web 路径。原生探针需与当前 AppModel.swift、APIClient.swift、Models.swift、BackendManager.swift 一起使用 `swiftc -swift-version 5 -parse-as-library` 编译，并指定临时 module-cache-path 与可执行文件路径。

R05 先运行 review3_numeric_probe.py，唯一参数为新临时输出目录；再将 Models.swift 与 Review3FormatProbe.swift 编译为临时可执行程序。运行 review3_format_runner.py，依次传入该可执行程序、API 输出目录、新日志目录，即可观察健康对照和三种极端时间样例。不要覆盖历史结果。运行脚本禁用 core dump，未使用真实任务或网络服务。

没有执行真实浏览器跨站攻击、在线依赖漏洞扫描、4 GB 上传压测、真实识别准确率评测。此次结论限于已审查的修复边界，不能视为整个项目无缺陷的保证。
