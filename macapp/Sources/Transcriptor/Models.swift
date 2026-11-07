import Foundation

enum JobStatus: String, Codable {
    case queued
    case running
    case done
    case error

    var label: String {
        switch self {
        case .queued: return "排队中"
        case .running: return "处理中"
        case .done: return "已完成"
        case .error: return "失败"
        }
    }
}

struct Stats: Codable, Hashable {
    var segments: Int?
    var paragraphs: Int?
    var speakers: Int?
    var corrected: Int?
}

struct AudioInfo: Codable, Hashable {
    var filename: String?
    var duration: Double?
    var size: Int64?
}

struct JobSettings: Codable, Hashable {
    var language: String?
    var numSpeakers: Int?
    var model: String?
    var initialPrompt: String?
    var aiCorrection: Bool?
    var chapters: Bool?
    var deepseekBaseUrl: String?
    var deepseekModel: String?
}

struct CorrectionInfo: Codable, Hashable {
    var enabled: Bool?
    var status: String?
    var model: String?
    var applied: Int?
    var rejected: Int?
    var batches: Int?
    var errors: [String]?
    var error: String?
}

struct Speaker: Codable, Identifiable, Hashable {
    var id: String
    var name: String
    var color: String?
}

struct Paragraph: Codable, Hashable {
    var start: Double
    var end: Double?
    var text: String
    var speakerId: String
}

struct Chapter: Codable, Identifiable, Hashable {
    var start: Double
    var title: String

    var id: String { "\(start)-\(title)" }
}

struct JobSummary: Codable, Identifiable, Hashable {
    var id: String
    var title: String
    var status: String
    var progress: Double?
    var stage: String?
    var createdAt: String?
    var duration: Double?
    var stats: Stats?

    var statusValue: JobStatus { JobStatus(rawValue: status) ?? .queued }
}

struct Job: Codable, Identifiable {
    var id: String
    var title: String
    var status: String
    var progress: Double?
    var stage: String?
    var error: String?
    var audio: AudioInfo?
    var settings: JobSettings?
    var speakers: [Speaker]
    var paragraphs: [Paragraph]
    var chapters: [Chapter]
    var stats: Stats?
    var exports: [String: String]?
    var exportError: String?
    var correction: CorrectionInfo?
    var correctionWarning: String?

    var statusValue: JobStatus { JobStatus(rawValue: status) ?? .queued }
    var duration: Double? { audio?.duration }
    var isDone: Bool { statusValue == .done }
}

struct AppConfig: Codable {
    var llmBaseUrl: String
    var llmModel: String
    var whisperModels: [WhisperModel]
    var defaultModel: String
    var aiCorrectionDefault: Bool?
    var chaptersDefault: Bool?
    var deepseekBaseUrl: String?
    var deepseekModel: String?
    var deepseekConfigured: Bool?
}

struct WhisperModel: Codable, Identifiable, Hashable {
    var key: String
    var available: Bool
    var id: String { key }
}

struct CreateJobResponse: Codable {
    var id: String
}

struct APIErrorResponse: Codable {
    var detail: String?
}

struct SpeakersPayload: Codable {
    struct Item: Codable {
        var id: String
        var name: String
    }
    var speakers: [Item]
}

struct ParagraphsPayload: Codable {
    var texts: [String]
    var speakerIds: [String]

    enum CodingKeys: String, CodingKey {
        case texts
        case speakerIds = "speaker_ids"
    }
}

struct ChaptersPayload: Codable {
    struct Item: Codable {
        var start: String
        var title: String
    }
    var chapters: [Item]
}

struct AutoChaptersPayload: Codable {
    var endpoint: String
    var model: String
    var titleLanguage: String

    enum CodingKeys: String, CodingKey {
        case endpoint
        case model
        case titleLanguage = "title_language"
    }
}

struct APIError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

enum Format {
    static func timestamp(_ seconds: Double?) -> String {
        guard let seconds, seconds.isFinite, seconds >= 0 else { return "00:00" }
        let total = Int(seconds)
        return String(format: "%02d:%02d", total / 60, total % 60)
    }

    static func duration(_ seconds: Double?) -> String {
        guard let seconds, seconds.isFinite, seconds >= 0 else { return "未知时长" }
        let total = Int(seconds.rounded())
        let h = total / 3600
        let m = (total % 3600) / 60
        let s = total % 60
        if h > 0 { return "\(h) 小时 \(m) 分 \(s) 秒" }
        if m > 0 { return "\(m) 分 \(s) 秒" }
        return "\(s) 秒"
    }

    static func createdAt(_ raw: String?) -> String {
        guard let raw else { return "" }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = formatter.date(from: raw) ?? ISO8601DateFormatter().date(from: raw)
        guard let date else { return "" }
        let display = DateFormatter()
        display.locale = Locale(identifier: "zh_CN")
        display.dateFormat = "M月d日 HH:mm"
        return display.string(from: date)
    }
}
