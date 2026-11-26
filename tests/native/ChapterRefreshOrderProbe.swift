import Foundation

// Formal regression for REVIEW-3 order: chapter response + refresh happens
// before the delayed transcript-save response, which must not roll chapters back.
final class ChapterRefreshOrderProtocol: URLProtocol {
    static let lock = NSLock()
    static var pending: [String: ChapterRefreshOrderProtocol] = [:]
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
}

@main
struct ChapterRefreshOrderProbe {
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
        URLCache.shared = URLCache(memoryCapacity: 0, diskCapacity: 0, diskPath: nil)
        URLProtocol.registerClass(ChapterRefreshOrderProtocol.self)
        let model = AppModel()
        model.selectJob("A")
        try await waitFor { model.job?.id == "A" }

        let chapterTask = Task {
            await model.autoChapters(
                endpoint: "http://audit.invalid",
                model: "mock",
                titleLanguage: "zh"
            )
        }
        try await waitFor {
            ChapterRefreshOrderProtocol.locked {
                ChapterRefreshOrderProtocol.pending["/api/jobs/A/chapters/auto"] != nil
            }
        }

        model.beginEditing()
        model.draftParagraphs[0].text = "newer saved text"
        let textTask = Task { await model.saveTranscript() }
        try await waitFor {
            ChapterRefreshOrderProtocol.locked {
                ChapterRefreshOrderProtocol.pending["/api/jobs/A/paragraphs"] != nil
            }
        }

        let textSnapshot = ChapterRefreshOrderProtocol.json(chapters: false)
        let finalSnapshot = ChapterRefreshOrderProtocol.json(chapters: true)
        ChapterRefreshOrderProtocol.locked {
            ChapterRefreshOrderProtocol.current = finalSnapshot
        }
        ChapterRefreshOrderProtocol.locked {
            ChapterRefreshOrderProtocol.pending["/api/jobs/A/chapters/auto"]!
        }.complete(finalSnapshot)
        await chapterTask.value
        precondition(model.job?.chapters.count == 1, "chapter refresh did not display chapters")

        ChapterRefreshOrderProtocol.locked {
            ChapterRefreshOrderProtocol.pending["/api/jobs/A/paragraphs"]!
        }.complete(textSnapshot)
        await textTask.value

        precondition(model.job?.chapters.count == 1, "delayed save rolled chapters back")
        precondition(model.job?.paragraphs.first?.text == "newer saved text")
        precondition(!model.isGeneratingChapters)
        print("chapter refresh order probe: PASS")
    }
}
