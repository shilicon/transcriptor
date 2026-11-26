// Independent audit observations. All HTTP is intercepted; no real backend is used.
import Foundation

final class ReauditProtocol: URLProtocol {
    static let gate = NSLock()
    static var pending: ReauditProtocol?
    static var pendingPath: String?
    static var calls: [String] = []

    static func locked<T>(_ body: () -> T) -> T {
        gate.lock()
        defer { gate.unlock() }
        return body()
    }

    static func job(_ id: String, text: String = "initial text") -> String {
        """
        {"id":"\(id)","title":"Synthetic \(id)","status":"done","speakers":[{"id":"S1","name":"Test"}],"paragraphs":[{"start":0,"text":"\(text)","speaker_id":"S1"}],"chapters":[]}
        """
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let path = request.url!.path
        let shouldHold = Self.locked {
            Self.calls.append("\(request.httpMethod ?? "GET") \(path)")
            if path == Self.pendingPath {
                Self.pendingPath = nil
                Self.pending = self
                return true
            }
            return false
        }
        if shouldHold { return }
        if request.httpMethod == "DELETE" {
            complete("{\"deleted\":true}")
        } else if path == "/api/jobs" {
            complete("[{\"id\":\"A\",\"title\":\"Synthetic A\",\"status\":\"done\"},{\"id\":\"B\",\"title\":\"Synthetic B\",\"status\":\"done\"}]")
        } else if path.hasSuffix("/paragraphs") {
            complete(Self.job("A", text: "newer saved text"))
        } else {
            complete(Self.job(request.url!.lastPathComponent))
        }
    }
    override func stopLoading() {}

    func complete(_ json: String) {
        let response = HTTPURLResponse(url: request.url!, statusCode: 200,
            httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(json.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
}

@main
struct ReauditNativeProbe {
    @MainActor
    static func waitFor(_ condition: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(5)
        while !condition() {
            precondition(Date() < deadline, "probe timed out")
            try await Task.sleep(nanoseconds: 1_000_000)
        }
    }

    @MainActor
    static func main() async throws {
        URLProtocol.registerClass(ReauditProtocol.self)
        var results: [String: Any] = [:]
        let model = AppModel()
        model.selectJob("A")
        try await waitFor { model.job?.id == "A" }
        model.beginEditing()
        model.draftParagraphs[0].text = "older saved text"
        ReauditProtocol.locked {
            ReauditProtocol.pending = nil
            ReauditProtocol.pendingPath = "/api/jobs/A/paragraphs"
        }
        let earlierSave = Task { await model.saveTranscript() }
        try await waitFor { ReauditProtocol.locked { ReauditProtocol.pending != nil } }
        let pendingSave = ReauditProtocol.locked { ReauditProtocol.pending! }
        model.draftParagraphs[0].text = "newer saved text"
        await model.saveTranscript()
        let before = model.job!.paragraphs[0].text
        model.beginEditing()
        model.draftParagraphs[0].text = "new unsaved draft"
        pendingSave.complete(ReauditProtocol.job("A", text: "older saved text"))
        await earlierSave.value
        results["same_job_reordered_save"] = [
            "display_before_old_response": before,
            "display_after_old_response": model.job!.paragraphs[0].text,
            "editing_after_old_response": model.editingTranscript,
            "draft_count_after_old_response": model.draftParagraphs.count,
        ]

        ReauditProtocol.locked {
            ReauditProtocol.pending = nil
            ReauditProtocol.pendingPath = "/api/jobs/B"
            ReauditProtocol.calls = []
        }
        model.selectJob("B")
        try await waitFor { ReauditProtocol.locked { ReauditProtocol.pending != nil } }
        let selectedBeforeDelete = model.selectedJobId!
        let displayedBeforeDelete = model.job!.id
        await model.deleteCurrentJob()
        results["switch_while_detail_loading"] = [
            "selected_before_delete": selectedBeforeDelete,
            "displayed_before_delete": displayedBeforeDelete,
            "delete_calls": ReauditProtocol.locked { ReauditProtocol.calls.filter { $0.hasPrefix("DELETE") } },
        ]
        ReauditProtocol.locked { ReauditProtocol.pending! }.complete(ReauditProtocol.job("B"))
        try await waitFor { model.job?.id == "B" }

        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let malformedList = "[{\"id\":\"healthy\",\"title\":\"Healthy\",\"status\":\"done\"},{\"id\":\"bad\",\"title\":[\"invalid-title\"],\"status\":\"done\"}]"
        do {
            _ = try decoder.decode([JobSummary].self, from: Data(malformedList.utf8))
            results["A08_list_decode"] = "accepted"
        } catch {
            results["A08_list_decode"] = "entire-list-rejected"
        }
        let output = try JSONSerialization.data(withJSONObject: results, options: [.prettyPrinted, .sortedKeys])
        print(String(data: output, encoding: .utf8)!)
    }
}
