import AppKit
import Combine
import Foundation
import UniformTypeIdentifiers

@MainActor
final class AppModel: ObservableObject {
    static let shared = AppModel()

    @Published var backendState: BackendManager.State = .idle
    @Published var jobs: [JobSummary] = []
    @Published var selectedJobId: String?
    @Published var job: Job?
    @Published var config: AppConfig?
    @Published var errorMessage: String?
    @Published var isBusy = false
    @Published var isGeneratingChapters = false

    @Published var editingTranscript = false
    @Published var draftParagraphs: [Paragraph] = []
    @Published var draftSpeakerIds: [String] = []

    let backend = BackendManager()
    private let api = APIClient()
    private var pollTask: Task<Void, Never>?
    private var cancellables = Set<AnyCancellable>()

    init() {
        backend.$state
            .receive(on: RunLoop.main)
            .sink { [weak self] state in self?.backendState = state }
            .store(in: &cancellables)
    }

    func bootstrap() async {
        await backend.ensureRunning()
        guard backend.state == .ready else { return }
        await loadConfig()
        await loadJobs()
    }

    func loadConfig() async {
        do { config = try await api.config() } catch { /* optional */ }
    }

    func loadJobs() async {
        do {
            jobs = try await api.listJobs()
            if let selected = selectedJobId,
               !jobs.contains(where: { $0.id == selected }) {
                selectedJobId = nil
                job = nil
            }
            if selectedJobId == nil, let first = jobs.first {
                selectJob(first.id)
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func selectJob(_ id: String?) {
        selectedJobId = id
        pollTask?.cancel()
        pollTask = nil
        guard let id else {
            job = nil
            editingTranscript = false
            return
        }
        editingTranscript = false
        Task { await loadJob(id) }
    }

    func loadJob(_ id: String) async {
        do {
            let loaded = try await api.getJob(id)
            guard selectedJobId == id else { return }
            job = loaded
            if loaded.statusValue == .queued || loaded.statusValue == .running {
                startPolling(id)
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func startPolling(_ id: String) {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_200_000_000)
                guard let self, !Task.isCancelled else { return }
                do {
                    let updated = try await self.api.getJob(id)
                    guard self.selectedJobId == id else { return }
                    self.job = updated
                    if updated.statusValue != .queued && updated.statusValue != .running {
                        await self.loadJobs()
                        return
                    }
                } catch {
                    // Keep polling; transient errors are common while the
                    // backend is busy writing job.json.
                }
            }
        }
    }

    func createJob(
        fileURL: URL,
        title: String,
        speakers: String,
        numSpeakers: Int,
        language: String,
        model: String,
        initialPrompt: String,
        aiCorrection: Bool,
        chapters: Bool,
        deepseekBaseURL: String,
        deepseekModel: String,
        deepseekAPIKey: String
    ) async throws {
        isBusy = true
        defer { isBusy = false }
        let id = try await api.createJob(
            fileURL: fileURL,
            title: title,
            speakers: speakers,
            numSpeakers: numSpeakers,
            language: language,
            model: model,
            initialPrompt: initialPrompt,
            aiCorrection: aiCorrection,
            chapters: chapters,
            deepseekBaseURL: deepseekBaseURL,
            deepseekModel: deepseekModel,
            deepseekAPIKey: deepseekAPIKey
        )
        await loadJobs()
        selectJob(id)
    }

    func refreshCurrentJob() async {
        guard let id = job?.id else { return }
        await loadJob(id)
    }

    func saveSpeakers(_ names: [String: String]) async {
        guard let id = job?.id else { return }
        do {
            job = try await api.updateSpeakers(id, names: names)
            await loadJobs()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func beginEditing() {
        guard let job else { return }
        draftParagraphs = job.paragraphs
        draftSpeakerIds = job.paragraphs.map(\.speakerId)
        editingTranscript = true
    }

    func cancelEditing() {
        editingTranscript = false
        draftParagraphs = []
        draftSpeakerIds = []
    }

    func saveTranscript() async {
        guard let id = job?.id else { return }
        do {
            let texts = draftParagraphs.map(\.text)
            job = try await api.updateParagraphs(id, texts: texts, speakerIds: draftSpeakerIds)
            editingTranscript = false
            await loadJobs()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func saveChapters(_ chapters: [Chapter]) async {
        guard let id = job?.id else { return }
        do {
            job = try await api.updateChapters(id, chapters: chapters)
            await loadJobs()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func basicChapters() async {
        guard let id = job?.id else { return }
        isGeneratingChapters = true
        defer { isGeneratingChapters = false }
        do {
            job = try await api.basicChapters(id)
            await loadJobs()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func autoChapters(endpoint: String, model: String, titleLanguage: String) async {
        guard let id = job?.id else { return }
        isGeneratingChapters = true
        defer { isGeneratingChapters = false }
        do {
            job = try await api.autoChapters(
                id,
                endpoint: endpoint,
                model: model,
                titleLanguage: titleLanguage
            )
            await loadJobs()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func deleteCurrentJob() async {
        guard let id = job?.id else { return }
        do {
            try await api.deleteJob(id)
            selectedJobId = nil
            job = nil
            editingTranscript = false
            await loadJobs()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func undoCorrection() async {
        guard let id = job?.id else { return }
        do {
            job = try await api.undoCorrection(id)
            await loadJobs()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func exportData(_ ext: String) async throws -> Data {
        guard let id = job?.id else { throw APIError(message: "没有选中的任务。") }
        return try await api.export(id, extension: ext)
    }

    func saveExport(_ data: Data, suggestedName: String, ext: String) throws {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "\(suggestedName).\(ext)"
        panel.canCreateDirectories = true
        panel.isExtensionHidden = false
        if ext == "md" {
            panel.allowedContentTypes = [.plainText]
        } else if ext == "docx" {
            if let type = UTType(filenameExtension: "docx") {
                panel.allowedContentTypes = [type]
            }
        }
        guard panel.runModal() == .OK, let url = panel.url else { return }
        try data.write(to: url, options: .atomic)
    }
}
