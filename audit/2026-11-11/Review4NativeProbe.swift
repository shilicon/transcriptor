// Actual AppModel/APIClient; synthetic server snapshots and controlled response order.
import Foundation

final class Review4Protocol: URLProtocol {
    static let lock = NSLock()
    static var pending: [String: Review4Protocol] = [:]
    static var current = json(chapters: false)
    static var calls: [String] = []
    static var failNextRead = false
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
        if path == "/api/jobs/A", Self.locked({
            let fail = Self.failNextRead; Self.failNextRead = false; return fail
        }) {
            client?.urlProtocol(self, didFailWithError: URLError(.timedOut))
            return
        }
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

@main struct Review4NativeProbe {
    @MainActor static func waitFor(_ condition: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(5)
        while !condition() {
            precondition(Date() < deadline, "probe timed out")
            try await Task.sleep(nanoseconds: 1_000_000)
        }
    }
    @MainActor static func main() async throws {
        URLCache.shared = URLCache(memoryCapacity: 0, diskCapacity: 0, diskPath: nil)
        URLProtocol.registerClass(Review4Protocol.self)
        let mode = CommandLine.arguments[1]
        let model = AppModel()
        model.selectJob("A")
        try await waitFor { model.job?.id == "A" }
        let c = Task { await model.autoChapters(endpoint:"http://audit.invalid",model:"mock",titleLanguage:"zh") }
        try await waitFor { Review4Protocol.locked { Review4Protocol.pending["/api/jobs/A/chapters/auto"] != nil } }
        model.beginEditing()
        model.draftParagraphs[0].text = "newer saved text"
        let t = Task { await model.saveTranscript() }
        try await waitFor { Review4Protocol.locked { Review4Protocol.pending["/api/jobs/A/paragraphs"] != nil } }
        let final = Review4Protocol.json(chapters:true)
        Review4Protocol.locked { Review4Protocol.current = final }
        Review4Protocol.locked { Review4Protocol.pending["/api/jobs/A/chapters/auto"]! }.complete(final)
        await c.value
        if mode == "draft" { model.draftParagraphs[0].text = "unsaved during refresh" }
        if mode == "failure" { Review4Protocol.locked { Review4Protocol.failNextRead = true } }
        Review4Protocol.locked { Review4Protocol.pending["/api/jobs/A/paragraphs"]! }.complete(Review4Protocol.json(chapters:false))
        await t.value
        func observation() -> [String:Any] {
            ["chapters":model.job!.chapters.count,"text":model.job!.paragraphs[0].text,
             "editing":model.editingTranscript,"draft":model.draftParagraphs.first?.text ?? "",
             "busy":model.isGeneratingChapters,"error":model.errorMessage ?? ""]
        }
        let before = observation()
        await model.refreshCurrentJob()
        precondition(model.job!.chapters.count == 1)
        precondition(!model.isGeneratingChapters)
        if mode == "draft" { precondition(model.draftParagraphs.first?.text == "unsaved during refresh") }
        let result: [String:Any] = ["mode":mode,"after_all_responses":before,"after_manual_refresh":observation(),
                                  "calls":Review4Protocol.locked { Review4Protocol.calls }]
        print(String(data:try JSONSerialization.data(withJSONObject:result,options:[.prettyPrinted,.sortedKeys]),encoding:.utf8)!)
    }
}
