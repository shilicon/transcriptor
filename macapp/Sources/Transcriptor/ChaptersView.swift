import SwiftUI

private struct ChapterDraft: Identifiable {
    let id = UUID()
    var start: String
    var title: String
}

struct ChaptersView: View {
    let job: Job
    @EnvironmentObject private var model: AppModel

    @State private var drafts: [ChapterDraft] = []
    @AppStorage("llm.endpoint") private var endpoint = ""
    @AppStorage("llm.model") private var llmModel = ""
    @AppStorage("chapter.language") private var titleLanguage = "zh"

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            aiPanel

            VStack(spacing: 8) {
                ForEach($drafts) { $draft in
                    HStack(spacing: 8) {
                        TextField("00:00", text: $draft.start)
                            .textFieldStyle(.roundedBorder)
                            .font(.system(.body, design: .monospaced))
                            .frame(width: 78)
                        TextField("章节标题", text: $draft.title)
                            .textFieldStyle(.roundedBorder)
                        Button {
                            drafts.removeAll { $0.id == draft.id }
                        } label: {
                            Image(systemName: "xmark")
                                .foregroundStyle(.secondary)
                        }
                        .buttonStyle(.plain)
                        .help("删除章节")
                    }
                }
            }

            HStack {
                Button {
                    addChapter()
                } label: {
                    Label("添加章节", systemImage: "plus")
                }
                Spacer()
                Button("保存章节") {
                    save()
                }
                .buttonStyle(.borderedProminent)
                .tint(transcriptorAccent)
            }
        }
        .onAppear {
            syncFromJob()
            if endpoint.isEmpty {
                endpoint = model.config?.llmBaseUrl ?? "http://127.0.0.1:1234/v1"
            }
        }
        .onChange(of: job.chapters) { _, _ in
            syncFromJob()
        }
    }

    private var aiPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("自动分章")
                        .font(.headline)
                    Text("连接本地 LM Studio / Ollama 的 OpenAI 兼容接口，按内容生成章节标题。也可以先使用基础分章。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }

            HStack(spacing: 8) {
                TextField("Endpoint，例如 http://127.0.0.1:1234/v1", text: $endpoint)
                    .textFieldStyle(.roundedBorder)
                TextField("模型，留空优先非推理模型", text: $llmModel)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 190)
                Picker("", selection: $titleLanguage) {
                    Text("中文标题").tag("zh")
                    Text("English").tag("en")
                }
                .labelsHidden()
                .pickerStyle(.menu)
                .frame(width: 100)
            }

            HStack(spacing: 8) {
                Button {
                    Task {
                        await model.autoChapters(
                            endpoint: endpoint,
                            model: llmModel,
                            titleLanguage: titleLanguage
                        )
                        syncFromJob()
                    }
                } label: {
                    if model.isGeneratingChapters {
                        ProgressView().controlSize(.small)
                    } else {
                        Label("AI 自动分章", systemImage: "sparkles")
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(transcriptorAccent)
                .disabled(model.isGeneratingChapters)

                Button("基础分章") {
                    Task {
                        await model.basicChapters()
                        syncFromJob()
                    }
                }
                .disabled(model.isGeneratingChapters)

                Spacer()
                Text("建议使用 Gemma 等非推理模型")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color.primary.opacity(0.025))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.08))
        )
    }

    private func syncFromJob() {
        let chapters = model.job?.chapters ?? job.chapters
        drafts = chapters
            .sorted { $0.start < $1.start }
            .map { ChapterDraft(start: Format.timestamp($0.start), title: $0.title) }
    }

    private func addChapter() {
        let lastStart = drafts.last.flatMap { parseTimestamp($0.start) } ?? 0
        drafts.append(
            ChapterDraft(
                start: Format.timestamp(lastStart + 300),
                title: "新章节"
            )
        )
    }

    private func save() {
        let chapters = drafts.compactMap { draft -> Chapter? in
            let title = draft.title.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !title.isEmpty else { return nil }
            return Chapter(start: parseTimestamp(draft.start) ?? 0, title: title)
        }
        Task {
            await model.saveChapters(chapters)
            syncFromJob()
        }
    }

    private func parseTimestamp(_ text: String) -> Double? {
        let parts = text.split(separator: ":").map(String.init)
        if parts.count == 2,
           let minutes = Double(parts[0]),
           let seconds = Double(parts[1]) {
            return max(0, minutes * 60 + seconds)
        }
        if parts.count == 3,
           let hours = Double(parts[0]),
           let minutes = Double(parts[1]),
           let seconds = Double(parts[2]) {
            return max(0, hours * 3600 + minutes * 60 + seconds)
        }
        return Double(text)
    }
}
