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
    @Published private(set) var generatingChaptersJobs: Set<String> = []
    @Published private(set) var translatingJobs: Set<String> = []

    @Published var editingTranscript = false
    @Published var draftParagraphs: [Paragraph] = [] {
        didSet { draftVersion += 1 }
    }
    @Published var draftSpeakerIds: [String] = [] {
        didSet { draftVersion += 1 }
    }

    var isGeneratingChapters: Bool {
        guard let id = selectedJobId else { return false }
        return generatingChaptersJobs.contains(id)
    }

    var generatingChaptersJobId: String? {
        isGeneratingChapters ? selectedJobId : nil
    }

    var isTranslating: Bool {
        guard let id = selectedJobId else { return false }
        return translatingJobs.contains(id)
    }

    let backend = BackendManager()
    private var api = APIClient()
    private var pollTask: Task<Void, Never>?
    private var cancellables = Set<AnyCancellable>()
    private var jobsRequestID = 0
    private var jobFetchGeneration = 0
    private var jobUpdateGeneration = 0
    private var mutationGeneration = 0
    private var transcriptSaveGeneration = 0
    private var chapterOperations: [String: UUID] = [:]
    private var draftVersion = 0

    init() {
        backend.$state
            .receive(on: RunLoop.main)
            .sink { [weak self] state in self?.backendState = state }
            .store(in: &cancellables)
    }

    func bootstrap() async {
        await backend.ensureRunning()
        guard backend.state == .ready else { return }
        api.token = backend.sessionToken
        await loadConfig()
        await loadJobs()
    }

    func loadConfig() async {
        do { config = try await api.config() } catch { /* optional */ }
    }

    func loadJobs() async {
        jobsRequestID += 1
        let requestID = jobsRequestID
        do {
            let loaded = try await api.listJobs()
            guard requestID == jobsRequestID else { return }
            jobs = loaded
            if let selected = selectedJobId,
               !jobs.contains(where: { $0.id == selected }) {
                selectedJobId = nil
                job = nil
                editingTranscript = false
                draftParagraphs = []
                draftSpeakerIds = []
                mutationGeneration += 1
                transcriptSaveGeneration += 1
            }
            if selectedJobId == nil, let first = jobs.first(where: { $0.corrupt != true }) {
                selectJob(first.id)
            }
        } catch {
            guard requestID == jobsRequestID else { return }
            errorMessage = error.localizedDescription
        }
    }

    func selectJob(_ id: String?) {
        selectedJobId = id
        pollTask?.cancel()
        pollTask = nil
        jobFetchGeneration += 1
        jobUpdateGeneration += 1
        mutationGeneration += 1
        transcriptSaveGeneration += 1
        draftVersion += 1
        editingTranscript = false
        draftParagraphs = []
        draftSpeakerIds = []
        guard let id else {
            job = nil
            return
        }
        // Do not leave the previous job's detail visible while the new
        // selection is loading: operations based on `job.id` would target A
        // even though the sidebar already points at B.
        job = nil
        Task { await loadJob(id) }
    }

    private func beginMutation() -> Int {
        mutationGeneration += 1
        return mutationGeneration
    }

    private func canApply(_ generation: Int, for jobID: String) -> Bool {
        selectedJobId == jobID && generation == mutationGeneration
    }

    private func refreshIfSuperseded(
        _ mutation: Int,
        update: Int,
        jobID: String
    ) async {
        guard selectedJobId == jobID,
              mutation != mutationGeneration || update != jobUpdateGeneration else {
            return
        }
        await loadJob(jobID)
        await loadJobs()
    }

    private func beginChapterOperation(_ jobID: String) -> UUID {
        let token = UUID()
        chapterOperations[jobID] = token
        generatingChaptersJobs.insert(jobID)
        return token
    }

    private func endChapterOperation(_ jobID: String, token: UUID) {
        guard chapterOperations[jobID] == token else { return }
        chapterOperations.removeValue(forKey: jobID)
        generatingChaptersJobs.remove(jobID)
    }

    func markDraftChanged() {
        draftVersion += 1
    }

    private func adopt(_ updated: Job, for jobID: String) {
        guard selectedJobId == jobID else { return }
        jobUpdateGeneration += 1
        job = updated
    }

    func loadJob(_ id: String) async {
        jobFetchGeneration += 1
        let fetchGeneration = jobFetchGeneration
        let updateGeneration = jobUpdateGeneration
        let mutation = mutationGeneration
        do {
            let loaded = try await api.getJob(id)
            guard selectedJobId == id,
                  fetchGeneration == jobFetchGeneration,
                  updateGeneration == jobUpdateGeneration,
                  mutation == mutationGeneration else { return }
            adopt(loaded, for: id)
            if loaded.statusValue == .queued || loaded.statusValue == .running {
                startPolling(id)
            }
        } catch {
            guard selectedJobId == id,
                  fetchGeneration == jobFetchGeneration,
                  mutation == mutationGeneration else { return }
            errorMessage = error.localizedDescription
            if let apiError = error as? APIError,
               apiError.message.contains("损坏") || apiError.message.contains("不存在") {
                selectedJobId = nil
                job = nil
                editingTranscript = false
                draftParagraphs = []
                draftSpeakerIds = []
            }
        }
    }

    private func startPolling(_ id: String) {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_200_000_000)
                guard let self, !Task.isCancelled else { return }
                let updateGeneration = self.jobUpdateGeneration
                do {
                    let updated = try await self.api.getJob(id)
                    guard self.selectedJobId == id,
                          updateGeneration == self.jobUpdateGeneration else { return }
                    self.adopt(updated, for: id)
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
        glossary: Bool,
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
            glossary: glossary,
            deepseekBaseURL: deepseekBaseURL,
            deepseekModel: deepseekModel,
            deepseekAPIKey: deepseekAPIKey
        )
        await loadJobs()
        selectJob(id)
    }

    func createJobFromURL(
        url: String,
        title: String,
        speakers: String,
        numSpeakers: Int,
        language: String,
        model: String,
        initialPrompt: String,
        aiCorrection: Bool,
        chapters: Bool,
        glossary: Bool,
        deepseekBaseURL: String,
        deepseekModel: String,
        deepseekAPIKey: String
    ) async throws {
        isBusy = true
        defer { isBusy = false }
        let id = try await api.createJobFromURL(
            url: url,
            title: title,
            speakers: speakers,
            numSpeakers: numSpeakers,
            language: language,
            model: model,
            initialPrompt: initialPrompt,
            aiCorrection: aiCorrection,
            chapters: chapters,
            glossary: glossary,
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
        guard let id = job?.id, selectedJobId == id else { return }
        let mutation = beginMutation()
        let updateGeneration = jobUpdateGeneration
        do {
            let updated = try await api.updateSpeakers(id, names: names)
            guard canApply(mutation, for: id),
                  updateGeneration == jobUpdateGeneration else {
                await refreshIfSuperseded(
                    mutation,
                    update: updateGeneration,
                    jobID: id
                )
                return
            }
            adopt(updated, for: id)
            await loadJobs()
        } catch {
            if selectedJobId == id { errorMessage = error.localizedDescription }
        }
    }

    func beginEditing() {
        guard let job, selectedJobId == job.id else { return }
        draftParagraphs = job.paragraphs
        draftSpeakerIds = job.paragraphs.map(\.speakerId)
        editingTranscript = true
        markDraftChanged()
    }

    func cancelEditing() {
        editingTranscript = false
        draftParagraphs = []
        draftSpeakerIds = []
        markDraftChanged()
    }

    func saveTranscript() async {
        guard let id = job?.id, selectedJobId == id else { return }
        let mutation = beginMutation()
        let updateGeneration = jobUpdateGeneration
        transcriptSaveGeneration += 1
        let saveGeneration = transcriptSaveGeneration
        let draftVersionAtStart = draftVersion
        let texts = draftParagraphs.map(\.text)
        let speakerIds = draftSpeakerIds
        do {
            let updated = try await api.updateParagraphs(id, texts: texts, speakerIds: speakerIds)
            guard selectedJobId == id,
                  mutation == mutationGeneration,
                  updateGeneration == jobUpdateGeneration,
                  saveGeneration == transcriptSaveGeneration else {
                await refreshIfSuperseded(
                    mutation,
                    update: updateGeneration,
                    jobID: id
                )
                return
            }
            adopt(updated, for: id)
            if draftVersion == draftVersionAtStart {
                editingTranscript = false
                draftParagraphs = []
                draftSpeakerIds = []
            }
            await loadJobs()
        } catch {
            if selectedJobId == id { errorMessage = error.localizedDescription }
        }
    }

    func saveChapters(_ chapters: [Chapter]) async {
        guard let id = job?.id, selectedJobId == id else { return }
        let mutation = beginMutation()
        let updateGeneration = jobUpdateGeneration
        do {
            let updated = try await api.updateChapters(id, chapters: chapters)
            guard canApply(mutation, for: id),
                  updateGeneration == jobUpdateGeneration else {
                await refreshIfSuperseded(
                    mutation,
                    update: updateGeneration,
                    jobID: id
                )
                return
            }
            adopt(updated, for: id)
            await loadJobs()
        } catch {
            if selectedJobId == id { errorMessage = error.localizedDescription }
        }
    }

    func basicChapters() async {
        guard let id = job?.id, selectedJobId == id else { return }
        let mutation = beginMutation()
        let updateGeneration = jobUpdateGeneration
        let chapterOperation = beginChapterOperation(id)
        defer { endChapterOperation(id, token: chapterOperation) }
        do {
            let updated = try await api.basicChapters(id)
            guard canApply(mutation, for: id),
                  updateGeneration == jobUpdateGeneration else {
                await refreshIfSuperseded(
                    mutation,
                    update: updateGeneration,
                    jobID: id
                )
                return
            }
            adopt(updated, for: id)
            await loadJobs()
        } catch {
            if selectedJobId == id { errorMessage = error.localizedDescription }
        }
    }

    func autoChapters(
        endpoint: String,
        model: String,
        titleLanguage: String,
        apiKey: String = "",
        instructions: String = ""
    ) async {
        guard let id = job?.id, selectedJobId == id else { return }
        let mutation = beginMutation()
        let updateGeneration = jobUpdateGeneration
        let chapterOperation = beginChapterOperation(id)
        defer { endChapterOperation(id, token: chapterOperation) }
        do {
            let updated = try await api.autoChapters(
                id,
                endpoint: endpoint,
                model: model,
                titleLanguage: titleLanguage,
                apiKey: apiKey,
                instructions: instructions
            )
            guard canApply(mutation, for: id),
                  updateGeneration == jobUpdateGeneration else {
                await refreshIfSuperseded(
                    mutation,
                    update: updateGeneration,
                    jobID: id
                )
                return
            }
            adopt(updated, for: id)
            await loadJobs()
        } catch {
            if selectedJobId == id { errorMessage = error.localizedDescription }
        }
    }

    func deleteCurrentJob() async {
        guard let id = job?.id, selectedJobId == id else { return }
        let mutation = beginMutation()
        do {
            try await api.deleteJob(id)
            guard selectedJobId == id, mutation == mutationGeneration else {
                await loadJobs()
                return
            }
            selectedJobId = nil
            job = nil
            editingTranscript = false
            draftParagraphs = []
            draftSpeakerIds = []
            jobUpdateGeneration += 1
            jobFetchGeneration += 1
            mutationGeneration += 1
            chapterOperations.removeValue(forKey: id)
            generatingChaptersJobs.remove(id)
            await loadJobs()
        } catch {
            if selectedJobId == id { errorMessage = error.localizedDescription }
        }
    }

    func translate(
        targetLanguage: String,
        apiKey: String,
        baseURL: String,
        model: String
    ) async {
        guard let id = job?.id, selectedJobId == id,
              !translatingJobs.contains(id) else {
            return
        }
        translatingJobs.insert(id)
        defer { translatingJobs.remove(id) }
        let mutation = beginMutation()
        let updateGeneration = jobUpdateGeneration
        do {
            let updated = try await api.translate(
                id,
                targetLanguage: targetLanguage,
                apiKey: apiKey,
                baseURL: baseURL,
                model: model
            )
            guard canApply(mutation, for: id),
                  updateGeneration == jobUpdateGeneration else {
                await refreshIfSuperseded(
                    mutation,
                    update: updateGeneration,
                    jobID: id
                )
                return
            }
            adopt(updated, for: id)
        } catch {
            if selectedJobId == id {
                errorMessage = error.localizedDescription
            }
        }
    }

    func undoCorrection() async {
        guard let id = job?.id, selectedJobId == id else { return }
        let mutation = beginMutation()
        let updateGeneration = jobUpdateGeneration
        do {
            let updated = try await api.undoCorrection(id)
            guard canApply(mutation, for: id),
                  updateGeneration == jobUpdateGeneration else {
                await refreshIfSuperseded(
                    mutation,
                    update: updateGeneration,
                    jobID: id
                )
                return
            }
            adopt(updated, for: id)
            await loadJobs()
        } catch {
            if selectedJobId == id { errorMessage = error.localizedDescription }
        }
    }

    func exportData(_ ext: String) async throws -> Data {
        guard let id = job?.id, selectedJobId == id else {
            throw APIError(message: "没有选中的任务。")
        }
        return try await api.export(id, extension: ext)
    }

    func saveExport(_ data: Data, suggestedName: String, ext: String) throws {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "\(suggestedName).\(ext)"
        panel.canCreateDirectories = true
        panel.isExtensionHidden = false
        // Keep AppKit's standard panel size.  Forcing a narrower content
        // size clips the Save button and makes the dialog look broken.
        panel.styleMask.insert(.resizable)
        if ext == "md" {
            panel.allowedContentTypes = [.plainText]
        } else if ext == "docx" {
            if let type = UTType(filenameExtension: "docx") {
                panel.allowedContentTypes = [type]
            }
        } else if ext == "srt" || ext == "vtt" {
            if let type = UTType(filenameExtension: ext) {
                panel.allowedContentTypes = [type]
            } else {
                panel.allowedContentTypes = [.plainText]
            }
        }
        guard panel.runModal() == .OK, let url = panel.url else { return }
        try data.write(to: url, options: .atomic)
    }
}
