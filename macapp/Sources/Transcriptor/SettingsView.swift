import SwiftUI

struct SettingsView: View {
    @AppStorage("deepseek.baseURL") private var baseURL = "https://api.deepseek.com"
    @AppStorage("deepseek.model") private var deepseekModel = "deepseek-flash"
    @AppStorage("aiCorrectionDefault") private var aiCorrectionDefault = true
    @AppStorage("chaptersDefault") private var chaptersDefault = true

    @State private var apiKey = ""
    @State private var saved = false

    var body: some View {
        Form {
            Section {
                VStack(alignment: .leading, spacing: 4) {
                    Text("AI 校正")
                        .font(.headline)
                    Text("默认使用 DeepSeek V4.1 Flash 修正逐字稿里的明显识别错误。只做局部校正，不改写、不润色、不补充内容。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 2)

                SecureField("DeepSeek API Key", text: $apiKey)
                    .textFieldStyle(.roundedBorder)
                TextField("Base URL", text: $baseURL)
                    .textFieldStyle(.roundedBorder)
                TextField("模型 ID", text: $deepseekModel)
                    .textFieldStyle(.roundedBorder)

                HStack {
                    Button(saved ? "已保存" : "保存 API Key") {
                        KeychainStore.save(apiKey, account: KeychainStore.deepSeekAccount)
                        saved = true
                        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                            saved = false
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(transcriptorAccent)

                    Button("清除") {
                        apiKey = ""
                        KeychainStore.delete(account: KeychainStore.deepSeekAccount)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.red)
                    Spacer()
                }

                Text("API Key 只保存在 macOS 钥匙串。校正时，逐字稿文本会发送到 DeepSeek API；音频不会离开本机。")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            Section("默认工作流") {
                Toggle("新建任务默认启用 AI 校正", isOn: $aiCorrectionDefault)
                Toggle("新建任务默认自动分章", isOn: $chaptersDefault)
                Text("AI 校正失败时不会回退到低能力模型，任务会保留原始转写并提示失败。")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding(20)
        .frame(width: 560, height: 470)
        .onAppear {
            apiKey = KeychainStore.load(account: KeychainStore.deepSeekAccount)
            if baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                baseURL = "https://api.deepseek.com"
            }
            if deepseekModel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                deepseekModel = "deepseek-flash"
            }
        }
        .onDisappear {
            KeychainStore.save(apiKey, account: KeychainStore.deepSeekAccount)
        }
    }
}
