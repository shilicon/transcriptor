import Foundation

final class MockURLProtocol: URLProtocol {
    static let gate = NSLock()
    static var pending: MockURLProtocol?
    static var pendingMatcher: ((String) -> Bool)?
    static var calls: [String] = []
    static var uploadBodyBytes: Int?
    static var uploadHeaderKey: String?
    static var savedText = "newer saved text"
    static var jobs: [String: String] = [:]

    static func locked<T>(_ body: () -> T) -> T {
        gate.lock()
        defer { gate.unlock() }
        return body()
    }

    static func jobJSON(_ id: String, chapter: String = "", text: String? = nil) -> String {
        let chapters = chapter.isEmpty ? "[]" : "[{\"start\":0,\"title\":\"\(chapter)\"}]"
        let paragraphText = text ?? "\(id) text"
        return """
        {"id":"\(id)","title":"Audit \(id)","status":"done","speakers":[{"id":"S1","name":"Test"}],"paragraphs":[{"start":0,"text":"\(paragraphText)","speaker_id":"S1"}],"chapters":\(chapters)}
        """
    }

    static func waitForPending(timeout: TimeInterval = 5) async -> MockURLProtocol? {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            let value = locked { pending }
            if value != nil { return value }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        return nil
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let path = request.url!.path
        MockURLProtocol.gate.lock()
        MockURLProtocol.calls.append("\(request.httpMethod ?? "GET") \(path)")
        if request.httpMethod == "POST", path == "/api/jobs" {
            MockURLProtocol.uploadBodyBytes = request.httpBody?.count
            MockURLProtocol.uploadHeaderKey = request.value(forHTTPHeaderField: "X-Transcriptor-DeepSeek-Key")
        }
        if let matcher = MockURLProtocol.pendingMatcher, matcher(path) {
            MockURLProtocol.pending = self
            MockURLProtocol.pendingMatcher = nil
            MockURLProtocol.gate.unlock()
            return
        }
        MockURLProtocol.gate.unlock()

        if request.httpMethod == "POST", path == "/api/jobs" {
            complete("{\"id\":\"uploaded\"}")
        } else if request.httpMethod == "DELETE" {
            complete("{\"deleted\":true}")
        } else if path == "/api/jobs" {
            complete("[{\"id\":\"A\",\"title\":\"Audit A\",\"status\":\"done\"},{\"id\":\"B\",\"title\":\"Audit B\",\"status\":\"done\"}]")
        } else if path.hasSuffix("/paragraphs") {
            let jobID = request.url!.deletingLastPathComponent().lastPathComponent
            let json = MockURLProtocol.jobJSON(jobID, text: MockURLProtocol.savedText)
            MockURLProtocol.locked { MockURLProtocol.jobs[jobID] = json }
            complete(json)
        } else {
            let jobID = request.url!.lastPathComponent
            complete(MockURLProtocol.locked { MockURLProtocol.jobs[jobID] ?? MockURLProtocol.jobJSON(jobID) })
        }
    }

    func complete(_ json: String) {
        MockURLProtocol.locked {
            if MockURLProtocol.pending === self {
                MockURLProtocol.pending = nil
            }
        }
        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: 200,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "application/json"]
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(json.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

@main
struct NativeRaceProbe {
    @MainActor
    static func main() async throws {
        URLProtocol.registerClass(MockURLProtocol.self)
        let model = AppModel()
        MockURLProtocol.locked {
            MockURLProtocol.jobs = ["A": MockURLProtocol.jobJSON("A"), "B": MockURLProtocol.jobJSON("B")]
        }

        try await switchScenario(model: model, action: {
            await model.autoChapters(
                endpoint: "http://audit.invalid/v1",
                model: "mock",
                titleLanguage: "zh"
            )
        }, pendingSuffix: "/chapters/auto", pendingResponse: MockURLProtocol.jobJSON("A", chapter: "Generated"))

        try await switchScenario(model: model, action: {
            await model.saveSpeakers(["S1": "Renamed"])
        }, pendingSuffix: "/speakers", pendingResponse: MockURLProtocol.jobJSON("A"))

        try await switchScenario(model: model, action: {
            await model.undoCorrection()
        }, pendingSuffix: "/correction/undo", pendingResponse: MockURLProtocol.jobJSON("A"))

        model.selectJob("A")
        try await waitUntil { model.job?.id == "A" }
        MockURLProtocol.locked {
            MockURLProtocol.pendingMatcher = { $0 == "/api/jobs/A" }
        }
        let deleteTask = Task { await model.deleteCurrentJob() }
        guard let deletePending = await MockURLProtocol.waitForPending() else {
            fatalError("delete request did not become pending")
        }
        model.selectJob("B")
        try await waitUntil { model.job?.id == "B" }
        deletePending.complete("{\"deleted\":true}")
        await deleteTask.value
        precondition(model.selectedJobId == "B", "delete response cleared the newly selected job")
        precondition(model.job?.id == "B", "delete response replaced the newly selected job")
        let deleteCall = MockURLProtocol.locked {
            MockURLProtocol.calls.contains("DELETE /api/jobs/A")
        }
        precondition(deleteCall, "delete did not target job A")

        try await sameJobReorderScenario(model: model)
        try await singleSaveDraftScenario(model: model)
        try await chapterDuringTextSaveScenario(model: model)
        try await chapterSwitchAwayBackScenario(model: model)
        try await switchWhileLoadingScenario(model: model)

        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let corruptList = """
        [{"id":"healthy","title":"Healthy","status":"done","progress":100,"created_at":"2026-11-11T00:00:00+08:00","duration":10,"stats":{"segments":1,"paragraphs":1,"speakers":1}},
         {"id":"bad","title":"损坏的任务","status":"error","progress":0,"stage":"任务文件损坏","created_at":null,"duration":null,"stats":{},"error":"synthetic corrupt job","corrupt":true}]
        """
        let decoded = try decoder.decode([JobSummary].self, from: Data(corruptList.utf8))
        precondition(decoded.count == 2)
        precondition(decoded[0].id == "healthy")
        precondition(decoded[1].corrupt == true)
        precondition(decoded[1].statusValue == .error)

        let upload = try await uploadScenario()
        print("native probes: PASS")
        print("upload body bytes in URLProtocol: \(upload.bodyBytes.map(String.init) ?? "nil")")
        print("upload header key present: \(upload.headerKey == "dummy-key")")
    }

    @MainActor
    static func sameJobReorderScenario(model: AppModel) async throws {
        model.selectJob("A")
        try await waitUntil { model.job?.id == "A" }
        model.beginEditing()
        model.draftParagraphs[0].text = "older saved text"
        MockURLProtocol.locked {
            MockURLProtocol.pendingMatcher = { $0.hasSuffix("/paragraphs") }
        }
        let earlierSave = Task { await model.saveTranscript() }
        guard let earlierPending = await MockURLProtocol.waitForPending() else {
            fatalError("first save did not become pending")
        }
        model.draftParagraphs[0].text = "newer saved text"
        await model.saveTranscript()
        precondition(model.job?.paragraphs.first?.text == "newer saved text", "newer save was not displayed")
        model.beginEditing()
        model.draftParagraphs[0].text = "new unsaved draft"
        earlierPending.complete(MockURLProtocol.jobJSON("A", text: "older saved text"))
        await earlierSave.value
        precondition(model.job?.id == "A")
        precondition(model.job?.paragraphs.first?.text == "newer saved text", "old response replaced newer state")
        precondition(model.editingTranscript, "old response cleared the new editing session")
        precondition(model.draftParagraphs.count == 1, "old response cleared the new draft")
        precondition(model.draftParagraphs.first?.text == "new unsaved draft", "new draft text was lost")
    }

    @MainActor
    static func singleSaveDraftScenario(model: AppModel) async throws {
        model.selectJob("A")
        try await waitUntil { model.job?.id == "A" }
        model.beginEditing()
        model.draftParagraphs[0].text = "saved text"
        MockURLProtocol.locked {
            MockURLProtocol.pendingMatcher = { $0.hasSuffix("/paragraphs") }
        }
        let saveTask = Task { await model.saveTranscript() }
        guard let pending = await MockURLProtocol.waitForPending() else {
            fatalError("single save did not become pending")
        }
        model.draftParagraphs[0].text = "new unsaved draft"
        pending.complete(MockURLProtocol.jobJSON("A", text: "saved text"))
        await saveTask.value
        precondition(model.job?.id == "A")
        precondition(model.editingTranscript, "save response cleared a draft created after the request")
        precondition(model.draftParagraphs.count == 1, "save response cleared the newer draft")
        precondition(model.draftParagraphs.first?.text == "new unsaved draft", "newer draft text was lost")
    }

    @MainActor
    static func chapterDuringTextSaveScenario(model: AppModel) async throws {
        model.selectJob("A")
        try await waitUntil { model.job?.id == "A" }
        MockURLProtocol.locked {
            MockURLProtocol.jobs["A"] = MockURLProtocol.jobJSON("A")
            MockURLProtocol.pendingMatcher = { $0 == "/api/jobs/A/chapters/auto" }
        }
        let chapterTask = Task {
            await model.autoChapters(
                endpoint: "http://audit.invalid/v1",
                model: "mock",
                titleLanguage: "zh"
            )
        }
        guard let chapterPending = await MockURLProtocol.waitForPending() else {
            fatalError("chapter request did not become pending")
        }
        model.beginEditing()
        model.draftParagraphs[0].text = "newer saved text"
        await model.saveTranscript()
        let completed = MockURLProtocol.jobJSON("A", chapter: "Generated", text: "newer saved text")
        MockURLProtocol.locked {
            MockURLProtocol.jobs["A"] = completed
            MockURLProtocol.calls = []
        }
        chapterPending.complete(completed)
        await chapterTask.value
        precondition(model.job?.id == "A")
        precondition(model.job?.paragraphs.first?.text == "newer saved text")
        precondition(model.job?.chapters.count == 1, "valid chapter result was not reconciled")
        precondition(!model.isGeneratingChapters, "chapter busy state was not cleared")
        let refreshCalls = MockURLProtocol.locked { MockURLProtocol.calls }
        precondition(refreshCalls.contains("GET /api/jobs/A"), "stale chapter response did not refresh latest state")
    }

    @MainActor
    static func chapterSwitchAwayBackScenario(model: AppModel) async throws {
        model.selectJob("A")
        try await waitUntil { model.job?.id == "A" }
        MockURLProtocol.locked {
            MockURLProtocol.jobs["A"] = MockURLProtocol.jobJSON("A")
            MockURLProtocol.pendingMatcher = { $0 == "/api/jobs/A/chapters/auto" }
        }
        let task = Task {
            await model.autoChapters(
                endpoint: "http://audit.invalid/v1",
                model: "mock",
                titleLanguage: "zh"
            )
        }
        guard let pending = await MockURLProtocol.waitForPending() else {
            fatalError("chapter request did not become pending")
        }
        model.selectJob("B")
        try await waitUntil { model.job?.id == "B" }
        model.selectJob("A")
        try await waitUntil { model.job?.id == "A" }
        let completed = MockURLProtocol.jobJSON("A", chapter: "Generated")
        MockURLProtocol.locked { MockURLProtocol.jobs["A"] = completed }
        pending.complete(completed)
        await task.value
        precondition(model.selectedJobId == "A")
        precondition(!model.isGeneratingChapters, "A→B→A left chapter busy state stuck")
        precondition(model.generatingChaptersJobId == nil)
        precondition(model.job?.chapters.count == 1, "A→B→A did not display completed chapters")
    }

    @MainActor
    static func switchWhileLoadingScenario(model: AppModel) async throws {
        MockURLProtocol.locked {
            MockURLProtocol.calls = []
            MockURLProtocol.pendingMatcher = { $0 == "/api/jobs/B" }
        }
        model.selectJob("B")
        try await waitUntil {
            MockURLProtocol.locked { MockURLProtocol.pending != nil }
        }
        precondition(model.selectedJobId == "B")
        precondition(model.job == nil, "previous job remained operable while B was loading")
        await model.deleteCurrentJob()
        let deleteCalls = MockURLProtocol.locked {
            MockURLProtocol.calls.filter { $0.hasPrefix("DELETE") }
        }
        precondition(deleteCalls.isEmpty, "delete targeted the previous job while B was loading")
        guard let pendingB = await MockURLProtocol.waitForPending() else {
            fatalError("B detail response disappeared")
        }
        pendingB.complete(MockURLProtocol.jobJSON("B"))
        try await waitUntil { model.job?.id == "B" }
    }

    @MainActor
    static func switchScenario(
        model: AppModel,
        action: @escaping @MainActor () async -> Void,
        pendingSuffix: String,
        pendingResponse: String
    ) async throws {
        model.selectJob("A")
        try await waitUntil { model.job?.id == "A" }
        MockURLProtocol.locked {
            MockURLProtocol.pendingMatcher = { $0.hasSuffix(pendingSuffix) }
        }
        let task = Task { await action() }
        guard let pending = await MockURLProtocol.waitForPending() else {
            fatalError("request \(pendingSuffix) did not become pending")
        }
        model.selectJob("B")
        try await waitUntil { model.job?.id == "B" }
        pending.complete(pendingResponse)
        await task.value
        precondition(model.selectedJobId == "B", "\(pendingSuffix): selection changed to A")
        precondition(model.job?.id == "B", "\(pendingSuffix): stale response replaced B")
    }

    @MainActor
    static func uploadScenario() async throws -> (bodyBytes: Int?, headerKey: String?) {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("transcriptor-upload-probe-\(UUID().uuidString).wav")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        defer { try? FileManager.default.removeItem(at: url) }
        let handle = try FileHandle(forWritingTo: url)
        let chunk = Data(count: 1024 * 1024)
        for _ in 0..<16 {
            try handle.write(contentsOf: chunk)
        }
        try handle.close()

        MockURLProtocol.locked {
            MockURLProtocol.uploadBodyBytes = nil
            MockURLProtocol.uploadHeaderKey = nil
        }

        let api = APIClient()
        let id = try await api.createJob(
            fileURL: url,
            title: "Upload probe",
            speakers: "A, B",
            numSpeakers: 2,
            language: "en",
            model: "large-v3",
            initialPrompt: "",
            aiCorrection: false,
            chapters: false,
            glossary: false,
            deepseekBaseURL: "https://api.deepseek.com",
            deepseekModel: "deepseek-flash",
            deepseekAPIKey: "dummy-key"
        )
        precondition(id == "uploaded")
        let values = MockURLProtocol.locked {
            (MockURLProtocol.uploadBodyBytes, MockURLProtocol.uploadHeaderKey)
        }
        let bodyBytes = values.0
        let headerKey = values.1
        precondition(bodyBytes == nil, "upload request retained a full in-memory body")
        precondition(headerKey == "dummy-key", "API key header was not sent")
        return (bodyBytes, headerKey)
    }

    @MainActor
    static func waitUntil(timeout: TimeInterval = 5, condition: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return }
            try await Task.sleep(nanoseconds: 1_000_000)
        }
        fatalError("condition timed out")
    }
}
