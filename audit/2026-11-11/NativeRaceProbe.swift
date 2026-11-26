import Foundation

final class AuditURLProtocol: URLProtocol {
    static let gate = NSLock()
    static var pending: AuditURLProtocol?
    static var calls: [String] = []
    static func pendingRequest() -> AuditURLProtocol? {
        gate.lock(); defer { gate.unlock() }
        return pending
    }
    static func jobJSON(_ id: String) -> String {
        return """
        {"id":"\(id)","title":"Audit \(id)","status":"done","speakers":[{"id":"S1","name":"Test"}],"paragraphs":[{"start":0,"text":"\(id) text","speaker_id":"S1"}],"chapters":[]}
        """
    }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let path = request.url!.path
        Self.gate.lock()
        Self.calls.append("\(request.httpMethod ?? "GET") \(path)")
        if path.hasSuffix("/chapters/auto") {
            Self.pending = self
            Self.gate.unlock()
            return
        }
        Self.gate.unlock()
        if request.httpMethod == "DELETE" {
            complete("{\"deleted\":true}")
        } else if path == "/api/jobs" {
            complete("[{\"id\":\"A\",\"title\":\"Audit A\",\"status\":\"done\"},{\"id\":\"B\",\"title\":\"Audit B\",\"status\":\"done\"}]")
        } else {
            complete(Self.jobJSON(request.url!.lastPathComponent))
        }
    }
    func complete(_ json: String) {
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: ["Content-Type":"application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(json.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

@main
struct NativeRaceProbe {
    @MainActor static func main() async throws {
        URLProtocol.registerClass(AuditURLProtocol.self)
        let model = AppModel()
        model.selectJob("A")
        for _ in 0..<1000 {
            if model.job?.id == "A" { break }
            try await Task.sleep(nanoseconds: 1_000_000)
        }
        precondition(model.job?.id == "A")
        let pending = Task { await model.autoChapters(endpoint: "http://audit.invalid/v1", model: "mock", titleLanguage: "zh") }
        for _ in 0..<1000 {
            if AuditURLProtocol.pendingRequest() != nil { break }
            try await Task.sleep(nanoseconds: 1_000_000)
        }
        precondition(AuditURLProtocol.pendingRequest() != nil)
        model.selectJob("B")
        for _ in 0..<1000 {
            if model.job?.id == "B" { break }
            try await Task.sleep(nanoseconds: 1_000_000)
        }
        precondition(model.job?.id == "B")
        let before = "selected=\(model.selectedJobId ?? "nil"), displayed=\(model.job?.id ?? "nil")"
        AuditURLProtocol.pendingRequest()!.complete(AuditURLProtocol.jobJSON("A"))
        await pending.value
        let after = "selected=\(model.selectedJobId ?? "nil"), displayed=\(model.job?.id ?? "nil")"
        await model.deleteCurrentJob()
        let output: [String: Any] = ["probe":"native_job_switch_race", "before_old_response":before, "after_old_response":after, "mock_requests": AuditURLProtocol.calls]
        let data = try JSONSerialization.data(withJSONObject: output, options: [.prettyPrinted, .sortedKeys])
        print(String(data: data, encoding: .utf8)!)
    }
}
