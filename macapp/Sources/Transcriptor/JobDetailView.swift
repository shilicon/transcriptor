import SwiftUI

private enum DetailTab: String, CaseIterable {
    case transcript = "逐字稿"
    case chapters = "章节"
}

struct JobDetailView: View {
    let job: Job
    @EnvironmentObject private var model: AppModel

    @State private var selectedTab: DetailTab = .transcript
    @State private var speakerNames: [String: String] = [:]
    @State private var showDeleteConfirm = false
    @State private var showUndoConfirm = false

    private var current: Job { model.job ?? job }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header
                if current.statusValue == .queued || current.statusValue == .running {
                    progressCard
                }
                if current.statusValue == .error {
                    errorCard
                }
                if let exportError = current.exportError, !exportError.isEmpty {
                    warningCard(exportError)
                }
                if current.isDone {
                    if let correction = current.correction, correction.status != "disabled" {
                        correctionCard(correction)
                    }
                    speakersCard
                    contentCard
                }
            }
            .frame(maxWidth: 1040, alignment: .leading)
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .top)
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear(perform: syncSpeakerNames)
        .onChange(of: current.speakers) { _, _ in syncSpeakerNames() }
        .alert("删除这个任务？", isPresented: $showDeleteConfirm) {
            Button("删除", role: .destructive) {
                Task { await model.deleteCurrentJob() }
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("音频、逐字稿和导出文件都会从磁盘删除。")
        }
        .alert("撤销 AI 校正？", isPresented: $showUndoConfirm) {
            Button("撤销", role: .destructive) {
                Task { await model.undoCorrection() }
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("所有段落会恢复为 ASR 原始转写。")
        }
    }

    private var header: some View {
        Card {
            HStack(alignment: .top, spacing: 18) {
                VStack(alignment: .leading, spacing: 8) {
                    StatusBadge(status: current.statusValue)
                    Text(current.title)
                        .font(.system(size: 27, weight: .bold, design: .rounded))
                        .lineLimit(2)
                    HStack(spacing: 14) {
                        if let duration = current.duration {
                            Label(Format.duration(duration), systemImage: "clock")
                        }
                        Label("\(current.stats?.speakers ?? current.speakers.count) 位说话人", systemImage: "person.2")
                        Label("\(current.stats?.paragraphs ?? current.paragraphs.count) 段", systemImage: "text.alignleft")
                        if let modelName = current.settings?.model {
                            Label(modelName, systemImage: "waveform")
                        }
                        if let source = current.audio?.sourceUrl, let url = URL(string: source) {
                            Link(destination: url) {
                                Label("YouTube 来源", systemImage: "link")
                            }
                        }
                        if let correction = current.correction {
                            Label(correctionLabel(correction), systemImage: "checkmark.seal")
                        }
                        if current.settings?.chapters == false {
                            Label("自动分章已关闭", systemImage: "list.bullet")
                        }
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }

                Spacer(minLength: 12)

                VStack(alignment: .trailing, spacing: 8) {
                    HStack(spacing: 7) {
                        Button {
                            export("md")
                        } label: {
                            Label("Markdown", systemImage: "doc.text")
                        }
                        Button {
                            export("docx")
                        } label: {
                            Label("Word", systemImage: "doc.richtext")
                        }
                    }
                    .buttonStyle(.bordered)
                    .disabled(!current.isDone)
                    HStack(spacing: 7) {
                        Button {
                            export("srt")
                        } label: {
                            Label("SRT 字幕", systemImage: "captions.bubble")
                        }
                        Button {
                            export("vtt")
                        } label: {
                            Label("VTT 字幕", systemImage: "text.bubble")
                        }
                    }
                    .buttonStyle(.bordered)
                    .disabled(!current.isDone)

                    Button(role: .destructive) {
                        showDeleteConfirm = true
                    } label: {
                        Label("删除任务", systemImage: "trash")
                    }
                    .buttonStyle(.plain)
                    .font(.caption)
                    .foregroundStyle(.red)
                }
            }
        }
    }

    private var progressCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text(current.stage ?? "处理中")
                        .font(.headline)
                    Spacer()
                    Text("\(Int(current.progress ?? 0))%")
                        .font(.system(.body, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
                ProgressView(value: max(0, min(100, current.progress ?? 0)), total: 100)
                    .tint(transcriptorAccent)
                Text("转写时间取决于音频长度和模型。页面可以保持打开，也可以稍后回来查看。")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var errorCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                Label("处理失败", systemImage: "exclamationmark.triangle")
                    .font(.headline)
                    .foregroundStyle(.red)
                Text(current.error ?? "未知错误")
                    .font(.caption)
                    .textSelection(.enabled)
            }
        }
    }

    private func warningCard(_ text: String) -> some View {
        Card {
            VStack(alignment: .leading, spacing: 6) {
                Label("导出提示", systemImage: "exclamationmark.circle")
                    .font(.headline)
                    .foregroundStyle(.orange)
                Text(text)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
        }
    }

    private func correctionCard(_ correction: CorrectionInfo) -> some View {
        Card {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: correction.status == "failed" ? "exclamationmark.triangle" : "checkmark.seal")
                    .font(.title2)
                    .foregroundStyle(correction.status == "failed" ? .orange : transcriptorAccent)
                VStack(alignment: .leading, spacing: 4) {
                    Text(correction.status == "failed" ? "AI 校正失败" : "AI 校正")
                        .font(.headline)
                    Text(correctionDetail(correction))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                Spacer()
                if correction.status == "applied" || correction.status == "partial" {
                    Button("撤销 AI 校正") {
                        showUndoConfirm = true
                    }
                    .buttonStyle(.bordered)
                }
            }
        }
    }

    private func correctionLabel(_ correction: CorrectionInfo) -> String {
        switch correction.status {
        case "applied": return "AI 校正 \(correction.applied ?? 0) 处"
        case "partial": return "AI 校正 \(correction.applied ?? 0) 处（部分失败）"
        case "failed": return "AI 校正失败"
        case "unchanged": return "AI 校正：无明显错误"
        case "undone": return "AI 校正已撤销"
        default: return "AI 校正：关闭"
        }
    }

    private func correctionDetail(_ correction: CorrectionInfo) -> String {
        switch correction.status {
        case "applied":
            return "已自动应用 \(correction.applied ?? 0) 处局部修正（\(correction.model ?? "DeepSeek")）。只修正明显识别错误；原始转写保留，可撤销。"
        case "partial":
            return "已应用 \(correction.applied ?? 0) 处修正，部分批次失败。原始转写保留。\(correction.error ?? "")"
        case "failed":
            return "已保留原始转写。\(correction.error ?? "")"
        case "unchanged":
            return "DeepSeek 没有发现需要自动修正的明显错误。"
        case "undone":
            return "已恢复为 ASR 原始转写。"
        default:
            return "AI 校正未启用。"
        }
    }

    private var speakersCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 3) {
                        SectionTitle(eyebrow: "说话人", title: "确认说话人姓名")
                        Text("自动按首次发言顺序分配。如果顺序反了，直接改名即可。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("保存姓名") {
                        Task { await model.saveSpeakers(speakerNames) }
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(transcriptorAccent)
                }

                HStack(spacing: 14) {
                    ForEach(current.speakers.sorted { $0.id < $1.id }) { speaker in
                        VStack(alignment: .leading, spacing: 6) {
                            HStack(spacing: 6) {
                                Circle()
                                    .fill(Color(hex: speaker.color ?? "#0f766e"))
                                    .frame(width: 8, height: 8)
                                Text(speaker.id)
                                    .font(.caption2.weight(.bold))
                                    .foregroundStyle(.secondary)
                            }
                            TextField(
                                "说话人姓名",
                                text: Binding(
                                    get: { speakerNames[speaker.id] ?? speaker.name },
                                    set: { speakerNames[speaker.id] = $0 }
                                )
                            )
                            .textFieldStyle(.roundedBorder)
                        }
                    }
                }
            }
        }
    }

    private var contentCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Picker("", selection: $selectedTab) {
                        ForEach(DetailTab.allCases, id: \.self) { tab in
                            Text(tab.rawValue).tag(tab)
                        }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .frame(width: 220)

                    Spacer()

                    if selectedTab == .transcript {
                        if model.editingTranscript {
                            Button("取消") { model.cancelEditing() }
                            Button("保存逐字稿") {
                                Task { await model.saveTranscript() }
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(transcriptorAccent)
                        } else {
                            Button("编辑文字") { model.beginEditing() }
                        }
                    }
                }

                Divider()

                switch selectedTab {
                case .transcript:
                    TranscriptView(
                        job: current,
                        editing: model.editingTranscript,
                        draftParagraphs: $model.draftParagraphs,
                        draftSpeakerIds: $model.draftSpeakerIds
                    )
                case .chapters:
                    ChaptersView(job: current)
                }
            }
        }
    }

    private func syncSpeakerNames() {
        speakerNames = Dictionary(
            uniqueKeysWithValues: current.speakers.map { ($0.id, $0.name) }
        )
    }

    private func export(_ ext: String) {
        Task {
            do {
                let data = try await model.exportData(ext)
                try model.saveExport(data, suggestedName: current.title, ext: ext)
            } catch {
                model.errorMessage = error.localizedDescription
            }
        }
    }
}
