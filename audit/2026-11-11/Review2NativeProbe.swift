// Uses the actual AppModel with synthetic HTTP responses only.
import Foundation

final class Review2Protocol: URLProtocol {
    static let gate = NSLock()
    static var pending: Review2Protocol?
    static var holdPath: String?
    static var jobs: [String: String] = [:]
    static var calls: [String] = []
    static func locked<T>(_ body: () -> T) -> T {
        gate.lock(); defer { gate.unlock() }; return body()
    }
    static func job(_ id: String, text: String = "initial text", chapter: Bool = false) -> String {
        let chapters = chapter ? "[{\"start\":0,\"title\":\"Generated\"}]" : "[]"
        return """
        {"id":"\(id)","title":"Synthetic \(id)","status":"done","speakers":[{"id":"S1","name":"Test"}],"paragraphs":[{"start":0,"text":"\(text)","speaker_id":"S1"}],"chapters":\(chapters)}
        """
    }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let path = request.url!.path
        let hold = Self.locked {
            Self.calls.append("\(request.httpMethod ?? "GET") \(path)")
            if path == Self.holdPath {
                Self.holdPath = nil
                Self.pending = self
                return true
            }
            return false
        }
        if hold { return }
        if path == "/api/jobs" {
            complete("[{\"id\":\"A\",\"title\":\"A\",\"status\":\"done\"},{\"id\":\"B\",\"title\":\"B\",\"status\":\"done\"}]")
        } else if path.hasSuffix("/paragraphs") {
            let json = Self.job("A", text: "newer saved text")
            Self.locked { Self.jobs["A"] = json }
            complete(json)
        } else {
            let id = request.url!.lastPathComponent
            complete(Self.locked { Self.jobs[id] ?? Self.job(id) })
        }
    }
    override func stopLoading() {}
    func complete(_ json: String) {
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(json.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
}

@main
struct Review2NativeProbe {
    @MainActor
    static func waitFor(_ condition: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(5)
        while !condition() {
            precondition(Date() < deadline, "probe timed out")
            try await Task.sleep(nanoseconds: 1_000_000)
        }
    }
    @MainActor
    static func holdChapters(_ model: AppModel) async throws -> (Task<Void, Never>, Review2Protocol) {
        Review2Protocol.locked {
            Review2Protocol.pending = nil
            Review2Protocol.holdPath = "/api/jobs/A/chapters/auto"
        }
        let task = Task { await model.autoChapters(endpoint: "http://audit.invalid", model: "mock", titleLanguage: "zh") }
        try await waitFor { Review2Protocol.locked { Review2Protocol.pending != nil } }
        return (task, Review2Protocol.locked { Review2Protocol.pending! })
    }
    @MainActor
    static func main() async throws {
        URLCache.shared = URLCache(memoryCapacity: 0, diskCapacity: 0, diskPath: nil)
        URLProtocol.registerClass(Review2Protocol.self)
        var result: [String: Any] = [:]
        let model = AppModel()
        model.selectJob("A")
        try await waitFor { model.job?.id == "A" }
        let (chapters, response) = try await holdChapters(model)
        model.beginEditing()
        model.draftParagraphs[0].text = "newer saved text"
        await model.saveTranscript()
        // Matches the server's correct behavior: chapters commit onto the latest text.
        let completed = Review2Protocol.job("A", text: "newer saved text", chapter: true)
        Review2Protocol.locked { Review2Protocol.jobs["A"] = completed; Review2Protocol.calls = [] }
        response.complete(completed)
        await chapters.value
        result["chapter_during_text_save"] = [
            "displayed_chapters_after_success": model.job!.chapters.count,
            "calls_after_chapter_response": Review2Protocol.locked { Review2Protocol.calls },
            "busy_after_success": model.isGeneratingChapters,
        ]
        await model.refreshCurrentJob()
        result["chapters_after_manual_refresh"] = model.job!.chapters.count

        let (switchTask, switchResponse) = try await holdChapters(model)
        model.selectJob("B")
        try await waitFor { model.job?.id == "B" }
        model.selectJob("A")
        try await waitFor { model.job?.id == "A" }
        switchResponse.complete(completed)
        await switchTask.value
        result["chapter_switch_away_and_back"] = [
            "selected": model.selectedJobId!,
            "request_finished": true,
            "still_busy": model.isGeneratingChapters,
            "busy_job": model.generatingChaptersJobId ?? "nil",
        ]
        if CommandLine.arguments.count > 1 {
            let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            do {
                let jobs = try decoder.decode([JobSummary].self, from: data)
                result["actual_backend_stats_list_decode"] = "accepted \(jobs.count) jobs"
            } catch {
                result["actual_backend_stats_list_decode"] = "entire-list-rejected"
            }
        }
        if CommandLine.arguments.count > 2 {
            let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[2]))
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            do {
                _ = try decoder.decode(Job.self, from: data)
                result["actual_backend_correction_detail_decode"] = "accepted"
            } catch {
                result["actual_backend_correction_detail_decode"] = "job-detail-rejected"
            }
        }
        print(String(data: try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]), encoding: .utf8)!)
    }
}
