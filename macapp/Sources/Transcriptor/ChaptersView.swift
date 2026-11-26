import SwiftUI

private struct ChapterDraft: Identifiable {
    let id = UUID()
    var start: String
    var title: String
}

struct ChaptersView: View {
    let job: Job
    @EnvironmentObject private var model: AppModel
    @Environment(\.openSettings) private var openSettings

    @State private var drafts: [ChapterDraft] = []
    @State private var draftsJobID: String?
    @AppStorage("llm.endpoint") private var endpoint = ""
    @AppStorage("llm.model") private var llmModel = ""
    @AppStorage("chapter.language") private var titleLanguage = "zh"
    @AppStorage("chapter.provider") private var provider = "local"
    @AppStorage("chapter.providerChosen") private var providerChosen = false
    @AppStorage("chapter.instructions") private var chapterInstructions = ""
    @AppStorage("deepseek.baseURL") private var deepseekBaseURL = "https://api.deepseek.com"
    @AppStorage("deepseek.model") private var deepseekModel = "deepseek-flash"
    @AppStorage("deepseek.keyRevision") private var keyRevision = 0
    @State private var deepseekKey = ""

    private var isDeepSeek: Bool { provider == "deepseek" }
    private var deepSeekConfigured: Bool {
        !deepseekKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

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
            deepseekKey = KeychainStore.load(account: KeychainStore.deepSeekAccount)
            if !providerChosen {
                provider = deepseekKey.isEmpty ? "local" : "deepseek"
                providerChosen = true
            }
            if endpoint.isEmpty {
                endpoint = model.config?.llmBaseUrl ?? "http://127.0.0.1:1234/v1"
            }
        }
        .onChange(of: provider) { _, _ in
            providerChosen = true
            deepseekKey = KeychainStore.load(account: KeychainStore.deepSeekAccount)
        }
        .onChange(of: keyRevision) { _, _ in
            deepseekKey = KeychainStore.load(account: KeychainStore.deepSeekAccount)
        }
        .onChange(of: job.id) { _, _ in
            syncFromJob()
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
                    Text(
                        isDeepSeek
                            ? "使用已配置的 DeepSeek API 生成章节标题。也可以先使用基础分章。"
                            : "连接本地 LM Studio / Ollama 的 OpenAI 兼容接口，按内容生成章节标题。也可以先使用基础分章。"
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                Spacer()
                Picker("", selection: $provider) {
                    Text("本地 LLM").tag("local")
                    Text("DeepSeek").tag("deepseek")
                }
                .labelsHidden()
                .pickerStyle(.segmented)
                .frame(width: 190)
            }

            if isDeepSeek {
                HStack(spacing: 8) {
                    TextField("Base URL", text: $deepseekBaseURL)
                        .textFieldStyle(.roundedBorder)
                    TextField("模型 ID", text: $deepseekModel)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 190)
                    if deepSeekConfigured {
                        Label("已配置 Key", systemImage: "checkmark.seal")
                            .font(.caption2)
                            .foregroundStyle(transcriptorAccent)
                    } else {
                        Button("配置 API Key") { openSettings() }
                            .font(.caption)
                    }
                }
            } else {
                HStack(spacing: 8) {
                    TextField("Endpoint，例如 http://127.0.0.1:1234/v1", text: $endpoint)
                        .textFieldStyle(.roundedBorder)
                    TextField("模型，留空优先非推理模型", text: $llmModel)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 190)
                }
            }

            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text("分章要求（可选）")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Text("\(chapterInstructions.count)/500")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                TextField(
                    "例如：分得更细，每 3–5 分钟一章，按技术主题细分",
                    text: $chapterInstructions,
                    axis: .vertical
                )
                .textFieldStyle(.roundedBorder)
                .lineLimit(2...4)
                .onChange(of: chapterInstructions) { _, value in
                    if value.count > 500 {
                        chapterInstructions = String(value.prefix(500))
                    }
                }
                Text("不填写时默认 6–12 章；填写后最多 30 章。")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            HStack(spacing: 8) {
                Picker("", selection: $titleLanguage) {
                    Text("中文标题").tag("zh")
                    Text("English").tag("en")
                }
                .labelsHidden()
                .pickerStyle(.menu)
                .frame(width: 100)

                Button {
                    let apiKey = isDeepSeek
                        ? KeychainStore.load(account: KeychainStore.deepSeekAccount)
                        : ""
                    if isDeepSeek
                        && apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        model.errorMessage = "还没有配置 DeepSeek API Key。请先在设置中填写。"
                        return
                    }
                    Task {
                        await model.autoChapters(
                            endpoint: isDeepSeek ? deepseekBaseURL : endpoint,
                            model: isDeepSeek ? deepseekModel : llmModel,
                            titleLanguage: titleLanguage,
                            apiKey: apiKey,
                            instructions: chapterInstructions
                                .trimmingCharacters(in: .whitespacesAndNewlines)
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
                Text(isDeepSeek ? "使用设置中的 DeepSeek Key" : "建议使用 Gemma 等非推理模型")
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
        // Never mix another job's chapters into this editor.  `model.job` can
        // briefly point at a different selection while a switch is in flight.
        let chapters = model.job?.id == job.id
            ? model.job?.chapters ?? []
            : job.chapters
        draftsJobID = job.id
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
        guard draftsJobID == job.id else {
            syncFromJob()
            return
        }
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
