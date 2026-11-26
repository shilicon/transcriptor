import Foundation

struct APIClient {
    let baseURL = URL(string: "http://127.0.0.1:8765/api")!
    var token: String?

    private var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }

    private var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .useDefaultKeys
        return encoder
    }

    private func request(path: String, method: String = "GET", timeout: TimeInterval = 30) -> URLRequest {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = timeout
        if let token, !token.isEmpty {
            request.setValue(token, forHTTPHeaderField: "X-Transcriptor-Token")
        }
        return request
    }

    private func check(_ response: URLResponse, _ data: Data) throws {
        guard let http = response as? HTTPURLResponse else {
            throw APIError(message: "没有收到服务器响应。")
        }
        guard (200..<300).contains(http.statusCode) else {
            if let apiError = try? decoder.decode(APIErrorResponse.self, from: data),
               let detail = apiError.detail {
                throw APIError(message: detail)
            }
            let text = String(data: data, encoding: .utf8) ?? ""
            throw APIError(message: text.isEmpty ? "HTTP \(http.statusCode)" : text)
        }
    }

    private func get<T: Decodable>(_ path: String, timeout: TimeInterval = 30) async throws -> T {
        let (data, response) = try await URLSession.shared.data(for: request(path: path, timeout: timeout))
        try check(response, data)
        return try decoder.decode(T.self, from: data)
    }

    private func send<T: Decodable, U: Encodable>(
        _ path: String,
        method: String,
        body: U,
        timeout: TimeInterval = 60
    ) async throws -> T {
        var req = request(path: path, method: method, timeout: timeout)
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try encoder.encode(body)
        let (data, response) = try await URLSession.shared.data(for: req)
        try check(response, data)
        return try decoder.decode(T.self, from: data)
    }

    func health() async -> Bool {
        do {
            var req = request(path: "health", timeout: 1.2)
            req.cachePolicy = .reloadIgnoringLocalCacheData
            let (_, response) = try await URLSession.shared.data(for: req)
            return (response as? HTTPURLResponse)?.statusCode == 200
        } catch {
            return false
        }
    }

    func config() async throws -> AppConfig {
        try await get("config")
    }

    func listJobs() async throws -> [JobSummary] {
        try await get("jobs")
    }

    func getJob(_ id: String) async throws -> Job {
        try await get("jobs/\(id)")
    }

    func deleteJob(_ id: String) async throws {
        let (data, response) = try await URLSession.shared.data(for: request(path: "jobs/\(id)", method: "DELETE"))
        try check(response, data)
    }

    func updateSpeakers(_ id: String, names: [String: String]) async throws -> Job {
        let payload = SpeakersPayload(
            speakers: names.map { SpeakersPayload.Item(id: $0.key, name: $0.value) }
        )
        return try await send("jobs/\(id)/speakers", method: "PUT", body: payload)
    }

    func updateParagraphs(_ id: String, texts: [String], speakerIds: [String]) async throws -> Job {
        let payload = ParagraphsPayload(texts: texts, speakerIds: speakerIds)
        return try await send("jobs/\(id)/paragraphs", method: "PUT", body: payload)
    }

    func updateChapters(_ id: String, chapters: [Chapter]) async throws -> Job {
        let payload = ChaptersPayload(
            chapters: chapters.map {
                ChaptersPayload.Item(start: Format.timestamp($0.start), title: $0.title)
            }
        )
        return try await send("jobs/\(id)/chapters", method: "PUT", body: payload)
    }

    func basicChapters(_ id: String) async throws -> Job {
        var req = request(path: "jobs/\(id)/chapters/basic", method: "POST", timeout: 60)
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data("{}".utf8)
        let (data, response) = try await URLSession.shared.data(for: req)
        try check(response, data)
        return try decoder.decode(Job.self, from: data)
    }

    func autoChapters(
        _ id: String,
        endpoint: String,
        model: String,
        titleLanguage: String,
        apiKey: String = "",
        instructions: String = ""
    ) async throws -> Job {
        let payload = AutoChaptersPayload(
            endpoint: endpoint,
            model: model,
            titleLanguage: titleLanguage,
            apiKey: apiKey,
            instructions: instructions
        )
        return try await send(
            "jobs/\(id)/chapters/auto",
            method: "POST",
            body: payload,
            timeout: 300
        )
    }

    func translate(
        _ id: String,
        targetLanguage: String,
        apiKey: String,
        baseURL: String,
        model: String
    ) async throws -> Job {
        let payload = TranslatePayload(
            targetLanguage: targetLanguage,
            apiKey: apiKey,
            baseURL: baseURL,
            model: model
        )
        return try await send(
            "jobs/\(id)/translate",
            method: "POST",
            body: payload,
            timeout: 900
        )
    }

    func undoCorrection(_ id: String) async throws -> Job {
        var req = request(path: "jobs/\(id)/correction/undo", method: "POST", timeout: 60)
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data("{}".utf8)
        let (data, response) = try await URLSession.shared.data(for: req)
        try check(response, data)
        return try decoder.decode(Job.self, from: data)
    }

    func export(_ id: String, extension ext: String) async throws -> Data {
        let req = request(path: "jobs/\(id)/export.\(ext)", timeout: 120)
        let (data, response) = try await URLSession.shared.data(for: req)
        try check(response, data)
        return data
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
    ) async throws -> String {
        // URLs returned by SwiftUI's fileImporter can be security-scoped.
        // Keep access alive while size checking and streaming the file.
        let accessedSecurityScopedResource = fileURL.startAccessingSecurityScopedResource()
        defer {
            if accessedSecurityScopedResource {
                fileURL.stopAccessingSecurityScopedResource()
            }
        }

        let fileSize = (try? fileURL.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
        let maxUploadBytes = 4 * 1024 * 1024 * 1024
        if fileSize <= 0 {
            throw APIError(message: "文件为空或无法读取。")
        }
        if fileSize > maxUploadBytes {
            throw APIError(message: "文件超过 4 GB 限制。")
        }

        let boundary = "Boundary-\(UUID().uuidString)"
        let tempURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("transcriptor-upload-\(UUID().uuidString).multipart")
        guard FileManager.default.createFile(atPath: tempURL.path, contents: nil) else {
            throw APIError(message: "无法创建上传临时文件。")
        }
        defer { try? FileManager.default.removeItem(at: tempURL) }

        let handle = try FileHandle(forWritingTo: tempURL)
        do {
            func append(_ string: String) throws {
                try handle.write(contentsOf: Data(string.utf8))
            }

            func appendField(_ name: String, _ value: String) throws {
                try append("--\(boundary)\r\n")
                try append("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n")
                try append("\(value)\r\n")
            }

            let filename = fileURL.lastPathComponent
            let ext = fileURL.pathExtension.lowercased()
            let mime: String
            switch ext {
            case "mp3": mime = "audio/mpeg"
            case "wav": mime = "audio/wav"
            case "m4a", "aac": mime = "audio/mp4"
            case "mp4", "m4v": mime = "video/mp4"
            case "mov": mime = "video/quicktime"
            default: mime = "application/octet-stream"
            }

            try appendField("title", title)
            try appendField("speakers", speakers)
            try appendField("num_speakers", String(numSpeakers))
            try appendField("language", language)
            try appendField("model", model)
            try appendField("initial_prompt", initialPrompt)
            try appendField("ai_correction", aiCorrection ? "true" : "false")
            try appendField("chapters", chapters ? "true" : "false")
            try appendField("glossary", glossary ? "true" : "false")
            try appendField("deepseek_base_url", deepseekBaseURL)
            try appendField("deepseek_model", deepseekModel)

            try append("--\(boundary)\r\n")
            try append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n")
            try append("Content-Type: \(mime)\r\n\r\n")

            let input = try FileHandle(forReadingFrom: fileURL)
            defer { try? input.close() }
            while let chunk = try input.read(upToCount: 1024 * 1024), !chunk.isEmpty {
                try handle.write(contentsOf: chunk)
            }
            try append("\r\n--\(boundary)--\r\n")
            try handle.close()
        } catch {
            try? handle.close()
            throw error
        }

        var req = request(path: "jobs", method: "POST", timeout: 600)
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        if !deepseekAPIKey.isEmpty {
            // Keep the API key out of the on-disk multipart body.  The backend
            // accepts it through a request header and keeps it in memory only.
            req.setValue(deepseekAPIKey, forHTTPHeaderField: "X-Transcriptor-DeepSeek-Key")
        }
        let (data, response) = try await URLSession.shared.upload(for: req, fromFile: tempURL)
        try check(response, data)
        return try decoder.decode(CreateJobResponse.self, from: data).id
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
    ) async throws -> String {
        let payload = URLJobPayload(
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
            deepseekBaseUrl: deepseekBaseURL,
            deepseekModel: deepseekModel,
            deepseekApiKey: deepseekAPIKey
        )
        let response: CreateJobResponse = try await send(
            "jobs/url",
            method: "POST",
            body: payload,
            timeout: 120
        )
        return response.id
    }
}
