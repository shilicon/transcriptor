// Actual AppModel/APIClient; synthetic server snapshots and controlled response order.
import Foundation

final class Review3Protocol: URLProtocol {
    static let lock = NSLock()
    static var pending: [String: Review3Protocol] = [:]
    static var current = json(chapters: false)
    static var calls: [String] = []
    static func locked<T>(_ body: () -> T) -> T {
        lock.lock(); defer { lock.unlock() }; return body()
    }
    static func json(chapters: Bool, text: String = "newer saved text") -> String {
        let items = chapters ? "[{\"start\":0,\"title\":\"Generated\"}]" : "[]"
        return """
        {"id":"A","title":"Synthetic A","status":"done","speakers":[{"id":"S1","name":"Test"}],"paragraphs":[{"start":0,"text":"\(text)","speaker_id":"S1"}],"chapters":\(items)}
        """
    }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let path = request.url!.path
        let held = Self.locked {
            Self.calls.append("\(request.httpMethod ?? "GET") \(path)")
            if path.hasSuffix("/chapters/auto") || path.hasSuffix("/paragraphs") {
                Self.pending[path] = self
                return true
            }
            return false
        }
        if held { return }
        if path == "/api/jobs" {
            complete("[{\"id\":\"A\",\"title\":\"Synthetic A\",\"status\":\"done\"}]")
        } else {
            complete(Self.locked { Self.current })
        }
    }
    override func stopLoading() {}
    func complete(_ json: String) {
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200,
            httpVersion: "HTTP/1.1", headerFields: ["Content-Type":"application/json"])!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(json.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
}

@main
struct Review3NativeProbe {
    @MainActor static func waitFor(_ condition: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(5)
        while !condition() {
            precondition(Date() < deadline, "probe timed out")
            try await Task.sleep(nanoseconds: 1_000_000)
        }
    }
    @MainActor static func main() async throws {
        URLCache.shared = URLCache(memoryCapacity: 0, diskCapacity: 0, diskPath: nil)
        URLProtocol.registerClass(Review3Protocol.self)
        let model = AppModel()
        model.selectJob("A")
        try await waitFor { model.job?.id == "A" }
        let chapterTask = Task { await model.autoChapters(endpoint:"http://audit.invalid", model:"mock", titleLanguage:"zh") }
        try await waitFor { Review3Protocol.locked { Review3Protocol.pending["/api/jobs/A/chapters/auto"] != nil } }
        model.beginEditing()
        model.draftParagraphs[0].text = "newer saved text"
        let textTask = Task { await model.saveTranscript() }
        try await waitFor { Review3Protocol.locked { Review3Protocol.pending["/api/jobs/A/paragraphs"] != nil } }
        // Transcript commits first, but its response (no chapters yet) is delayed.
        let textSnapshot = Review3Protocol.json(chapters: false)
        // Chapter commit correctly preserves the new text; its response arrives first.
        let finalSnapshot = Review3Protocol.json(chapters: true)
        Review3Protocol.locked { Review3Protocol.current = finalSnapshot }
        Review3Protocol.locked { Review3Protocol.pending["/api/jobs/A/chapters/auto"]! }.complete(finalSnapshot)
        await chapterTask.value
        let afterRefresh = model.job!.chapters.count
        Review3Protocol.locked { Review3Protocol.pending["/api/jobs/A/paragraphs"]! }.complete(textSnapshot)
        await textTask.value
        let afterSave = model.job!.chapters.count
        let completedText = model.job!.paragraphs[0].text
        let completedBusy = model.isGeneratingChapters
        let completedCalls = Review3Protocol.locked { Review3Protocol.calls }
        await model.refreshCurrentJob()
        let result: [String: Any] = ["save_response_after_reconciliation": [
            "chapters_after_reconciliation": afterRefresh,
            "chapters_after_delayed_save": afterSave,
            "chapters_after_manual_refresh": model.job!.chapters.count,
            "text_after_all_responses": completedText,
            "busy_after_all_responses": completedBusy,
            "calls_before_manual_refresh": completedCalls,
        ]]
        print(String(data: try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]), encoding:.utf8)!)
    }
}
