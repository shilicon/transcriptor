import SwiftUI
import UniformTypeIdentifiers

private enum SourceMode: String, CaseIterable, Identifiable {
    case file = "本地文件"
    case youtube = "YouTube 链接"

    var id: String { rawValue }
}

struct NewJobView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var sourceMode: SourceMode = .file
    @State private var fileURL: URL?
    @State private var youtubeURL = ""
    @State private var title = ""
    @State private var speakers = "说话人 1, 说话人 2"
    @State private var numSpeakers = 2
    @State private var language = "auto"
    @State private var modelKey = "large-v3"
    @State private var initialPrompt = ""
    @State private var showImporter = false
    @State private var isDropTarget = false
    @State private var isStarting = false
    @State private var localError: String?

    @AppStorage("aiCorrectionDefault") private var aiCorrectionDefault = true
    @AppStorage("chaptersDefault") private var chaptersDefault = true
    @AppStorage("deepseek.baseURL") private var deepseekBaseURL = "https://api.deepseek.com"
    @AppStorage("deepseek.model") private var deepseekModel = "deepseek-flash"

    @State private var aiCorrection = true
    @State private var chapters = true
    @Environment(\.openSettings) private var openSettings

    private var allowedTypes: [UTType] {
        var types: [UTType] = [.audio, .movie]
        for ext in ["mp3", "wav", "m4a", "aac", "flac", "ogg", "mp4", "mov", "mkv", "webm"] {
            if let type = UTType(filenameExtension: ext), !types.contains(type) {
                types.append(type)
            }
        }
        return types
    }

    private var hasDeepSeekCredentials: Bool {
        !KeychainStore.load(account: KeychainStore.deepSeekAccount).isEmpty
            || (model.config?.deepseekConfigured ?? false)
    }

    private var availableModels: [WhisperModel] {
        model.config?.whisperModels.filter(\.available) ?? [
            WhisperModel(key: "large-v3", available: true)
        ]
    }

    private var normalizedYouTubeURL: String {
        youtubeURL.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var isValidYouTubeURL: Bool {
        guard let components = URLComponents(string: normalizedYouTubeURL),
              let scheme = components.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let host = components.host?.lowercased() else {
            return false
        }
        let isYouTubeHost = host == "youtu.be"
            || host == "youtube.com"
            || host == "youtube-nocookie.com"
            || host.hasSuffix(".youtube.com")
            || host.hasSuffix(".youtube-nocookie.com")
        guard isYouTubeHost else { return false }

        let path = components.path
        if host == "youtu.be" {
            return !path.trimmingCharacters(in: CharacterSet(charactersIn: "/")).isEmpty
        }
        if path == "/watch" || path == "/watch/" {
            return components.queryItems?.contains {
                $0.name == "v" && !($0.value ?? "").isEmpty
            } ?? false
        }
        return ["/shorts/", "/live/", "/embed/", "/v/", "/clip/"].contains {
            path.hasPrefix($0) && path.count > $0.count
        }
    }

    private var canStart: Bool {
        switch sourceMode {
        case .file:
            return fileURL != nil
        case .youtube:
            return isValidYouTubeURL
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                SectionTitle(eyebrow: "新建任务", title: "添加一次访谈")
                Spacer()
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
            }
            .padding(22)

            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Picker("", selection: $sourceMode) {
                        ForEach(SourceMode.allCases) { mode in
                            Text(mode.rawValue).tag(mode)
                        }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()

                    if sourceMode == .file {
                        dropZone
                    } else {
                        youtubeField
                    }

                    VStack(alignment: .leading, spacing: 14) {
                        field("标题") {
                            TextField(
                                sourceMode == .file ? "默认使用文件名" : "默认使用视频标题",
                                text: $title
                            )
                            .textFieldStyle(.roundedBorder)
                        }

                        field("说话人姓名") {
                            VStack(alignment: .leading, spacing: 4) {
                                TextField("按首次发言顺序，用逗号分隔", text: $speakers)
                                    .textFieldStyle(.roundedBorder)
                                Text("第一个名字会分配给最先发言的声音。完成后也可以在结果页修改。")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }

                        HStack(alignment: .top, spacing: 14) {
                            field("说话人数") {
                                Picker("", selection: $numSpeakers) {
                                    ForEach(1...4, id: \.self) { count in
                                        Text("\(count) 人").tag(count)
                                    }
                                }
                                .labelsHidden()
                                .pickerStyle(.menu)
                            }
                            field("语言") {
                                Picker("", selection: $language) {
                                    Text("自动识别").tag("auto")
                                    Text("English").tag("en")
                                    Text("中文").tag("zh")
                                }
                                .labelsHidden()
                                .pickerStyle(.menu)
                            }
                        }

                        field("语音模型") {
                            Picker("", selection: $modelKey) {
                                ForEach(availableModels) { item in
                                    Text(item.key == "large-v3" ? "large-v3（推荐）" : "turbo（更快）")
                                        .tag(item.key)
                                }
                            }
                            .labelsHidden()
                            .pickerStyle(.menu)
                        }

                        VStack(alignment: .leading, spacing: 8) {
                            Toggle("AI 校正", isOn: $aiCorrection)
                                .toggleStyle(.switch)
                            Text("使用 DeepSeek V4.1 Flash 修正明显识别错误，不改写、不润色。默认开启。")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                            Toggle("自动分章", isOn: $chapters)
                                .toggleStyle(.switch)
                            Text("默认开启；关闭后仍可稍后手动分章。")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        .padding(10)
                        .background(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .fill(Color.primary.opacity(0.025))
                        )

                        if aiCorrection && !hasDeepSeekCredentials {
                            HStack(spacing: 8) {
                                Image(systemName: "key")
                                    .foregroundStyle(.orange)
                                Text("还没有配置 DeepSeek API Key。可以先打开设置，或关闭 AI 校正。")
                                    .font(.caption)
                                Spacer()
                                Button("打开设置") { openSettings() }
                                    .font(.caption)
                            }
                            .padding(10)
                            .background(
                                RoundedRectangle(cornerRadius: 9, style: .continuous)
                                    .fill(Color.orange.opacity(0.08))
                            )
                        }

                        DisclosureGroup("高级设置") {
                            field("初始提示词") {
                                TextField("可选，例如人名、产品名", text: $initialPrompt)
                                    .textFieldStyle(.roundedBorder)
                            }
                            .padding(.top, 8)
                        }
                        .font(.caption)
                    }

                    if let localError {
                        Text(localError)
                            .font(.caption)
                            .foregroundStyle(.red)
                            .textSelection(.enabled)
                    }
                }
                .padding(22)
            }

            Divider()

            HStack {
                Text(sourceMode == .file ? "音频只在本机处理。" : "音频下载后只在本机处理。")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("取消") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button {
                    start()
                } label: {
                    if isStarting {
                        ProgressView().controlSize(.small)
                    } else {
                        Text(sourceMode == .file ? "开始转写" : "下载并转写")
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(transcriptorAccent)
                .keyboardShortcut(.defaultAction)
                .disabled(!canStart || isStarting)
            }
            .padding(18)
        }
        .frame(width: 580, height: 660)
        .fileImporter(
            isPresented: $showImporter,
            allowedContentTypes: allowedTypes,
            allowsMultipleSelection: false
        ) { result in
            if case .success(let urls) = result, let url = urls.first {
                choose(url)
            }
        }
        .onAppear {
            if let defaultModel = model.config?.defaultModel {
                modelKey = defaultModel
            }
            aiCorrection = aiCorrectionDefault
            chapters = chaptersDefault
        }
        .onChange(of: sourceMode) { _, _ in
            localError = nil
        }
    }

    private var dropZone: some View {
        Button {
            showImporter = true
        } label: {
            VStack(spacing: 7) {
                Image(systemName: "waveform.badge.plus")
                    .font(.system(size: 28, weight: .light))
                    .foregroundStyle(transcriptorAccent)
                Text(fileURL?.lastPathComponent ?? "拖入文件或点击选择")
                    .font(.system(size: 14, weight: .semibold))
                    .lineLimit(1)
                Text(fileURL.map(fileDescription) ?? "MP3 / WAV / M4A / MP4 / MOV")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 24)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(isDropTarget ? transcriptorAccent.opacity(0.10) : Color.primary.opacity(0.025))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(
                        isDropTarget ? transcriptorAccent : Color.primary.opacity(0.16),
                        style: StrokeStyle(lineWidth: 1.2, dash: [5, 4])
                    )
            )
        }
        .buttonStyle(.plain)
        .onDrop(of: [.fileURL], isTargeted: $isDropTarget) { providers in
            guard let provider = providers.first else { return false }
            _ = provider.loadObject(ofClass: URL.self) { url, _ in
                guard let url else { return }
                DispatchQueue.main.async { choose(url) }
            }
            return true
        }
    }

    private var youtubeField: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("YouTube 视频链接", systemImage: "link")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(transcriptorAccent)
            TextField("https://www.youtube.com/watch?v=...", text: $youtubeURL)
                .textFieldStyle(.roundedBorder)
            Text("仅下载音频，不使用 YouTube 自带字幕。支持 youtube.com、youtu.be、Shorts 和直播回放链接。")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(16)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Color.primary.opacity(0.025))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.12), lineWidth: 1)
        )
    }

    private func field<Content: View>(_ label: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            content()
        }
    }

    private func choose(_ url: URL) {
        fileURL = url
        if title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            title = url.deletingPathExtension().lastPathComponent
        }
        localError = nil
    }

    private func fileDescription(_ url: URL) -> String {
        if let values = try? url.resourceValues(forKeys: [.fileSizeKey]),
           let size = values.fileSize {
            return "\(url.pathExtension.uppercased()) · \(ByteCountFormatter.string(fromByteCount: Int64(size), countStyle: .file))"
        }
        return url.pathExtension.uppercased()
    }

    private func start() {
        let apiKey = KeychainStore.load(account: KeychainStore.deepSeekAccount)
        if aiCorrection && apiKey.isEmpty && !(model.config?.deepseekConfigured ?? false) {
            localError = "请先在设置中填写 DeepSeek API Key，或关闭 AI 校正。"
            return
        }

        switch sourceMode {
        case .file:
            guard let fileURL else { return }
            isStarting = true
            localError = nil
            Task {
                do {
                    try await model.createJob(
                        fileURL: fileURL,
                        title: title,
                        speakers: speakers,
                        numSpeakers: numSpeakers,
                        language: language,
                        model: modelKey,
                        initialPrompt: initialPrompt,
                        aiCorrection: aiCorrection,
                        chapters: chapters,
                        deepseekBaseURL: deepseekBaseURL,
                        deepseekModel: deepseekModel,
                        deepseekAPIKey: apiKey
                    )
                    dismiss()
                } catch {
                    localError = error.localizedDescription
                    isStarting = false
                }
            }
        case .youtube:
            guard isValidYouTubeURL else {
                localError = "请输入有效的 YouTube 视频链接。"
                return
            }
            isStarting = true
            localError = nil
            Task {
                do {
                    try await model.createJobFromURL(
                        url: normalizedYouTubeURL,
                        title: title,
                        speakers: speakers,
                        numSpeakers: numSpeakers,
                        language: language,
                        model: modelKey,
                        initialPrompt: initialPrompt,
                        aiCorrection: aiCorrection,
                        chapters: chapters,
                        deepseekBaseURL: deepseekBaseURL,
                        deepseekModel: deepseekModel,
                        deepseekAPIKey: apiKey
                    )
                    dismiss()
                } catch {
                    localError = error.localizedDescription
                    isStarting = false
                }
            }
        }
    }
}
