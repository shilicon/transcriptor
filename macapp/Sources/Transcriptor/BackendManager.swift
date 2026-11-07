import Foundation
import SwiftUI

@MainActor
final class BackendManager: ObservableObject {
    enum State: Equatable {
        case idle
        case starting
        case ready
        case failed(String)
    }

    @Published var state: State = .idle

    private let api = APIClient()
    private var process: Process?
    private var ownsProcess = false
    private var logHandle: FileHandle?
    private(set) var repoRoot: URL?

    func ensureRunning() async {
        if state == .ready { return }
        if await api.health() {
            state = .ready
            return
        }

        state = .starting
        guard let root = findRepoRoot() else {
            state = .failed("找不到 Transcriptor 项目目录（需要 app/main.py 和 .venv）。")
            return
        }
        repoRoot = root

        let python = root.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            state = .failed("找不到 Python 环境：\(python.path)")
            return
        }

        do {
            try launchBackend(root: root, python: python)
        } catch {
            state = .failed("启动本地转写服务失败：\(error.localizedDescription)")
            return
        }

        for _ in 0..<60 {
            if await api.health() {
                state = .ready
                return
            }
            try? await Task.sleep(nanoseconds: 400_000_000)
        }

        let detail = tailLog(root: root)
        state = .failed("本地转写服务没有在预期时间内启动。\n\(detail)")
    }

    func stop() {
        guard ownsProcess, let process else { return }
        process.terminate()
        self.process = nil
        ownsProcess = false
        try? logHandle?.close()
        logHandle = nil
    }

    private func launchBackend(root: URL, python: URL) throws {
        let dataDir = root.appendingPathComponent("data", isDirectory: true)
        try FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)
        let logURL = dataDir.appendingPathComponent("macapp-backend.log")
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: logURL)
        try handle.seekToEnd()
        logHandle = handle

        let process = Process()
        process.executableURL = python
        process.arguments = [
            "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1",
            "--port", "8765",
        ]
        process.currentDirectoryURL = root
        var env = ProcessInfo.processInfo.environment
        env["TRANSCRIPTOR_DATA_DIR"] = dataDir.path
        env["TRANSCRIPTOR_MODELS_DIR"] = root.appendingPathComponent("models").path
        env["TRANSCRIPTOR_PARENT_PID"] = String(ProcessInfo.processInfo.processIdentifier)
        env["PYTHONUNBUFFERED"] = "1"
        // Finder-launched apps get a minimal PATH; ffmpeg is usually in
        // Homebrew on Apple Silicon.
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        process.environment = env
        process.standardOutput = handle
        process.standardError = handle
        process.terminationHandler = { [weak self] proc in
            Task { @MainActor in
                guard let self, self.ownsProcess else { return }
                if self.state == .ready {
                    self.state = .failed("本地转写服务意外退出（code \(proc.terminationStatus)）。")
                }
            }
        }
        try process.run()
        self.process = process
        ownsProcess = true
    }

    private func findRepoRoot() -> URL? {
        var candidates: [URL] = []
        if let env = ProcessInfo.processInfo.environment["TRANSCRIPTOR_ROOT"] {
            candidates.append(URL(fileURLWithPath: env, isDirectory: true))
        }

        var cursor = Bundle.main.bundleURL
        for _ in 0..<8 {
            candidates.append(cursor)
            cursor.deleteLastPathComponent()
        }
        candidates.append(URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true))
        candidates.append(URL(fileURLWithPath: "/Users/skmacair/work/transcriptor", isDirectory: true))

        for candidate in candidates {
            let main = candidate.appendingPathComponent("app/main.py")
            let python = candidate.appendingPathComponent(".venv/bin/python")
            if FileManager.default.fileExists(atPath: main.path),
               FileManager.default.isExecutableFile(atPath: python.path) {
                return candidate
            }
        }
        return nil
    }

    private func tailLog(root: URL) -> String {
        let logURL = root.appendingPathComponent("data/macapp-backend.log")
        guard let data = try? Data(contentsOf: logURL),
              let text = String(data: data, encoding: .utf8) else {
            return ""
        }
        return String(text.suffix(1800))
    }
}
