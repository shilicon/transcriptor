import SwiftUI

let transcriptorAccent = Color(red: 0.06, green: 0.46, blue: 0.43)
let transcriptorWarm = Color(red: 0.71, green: 0.33, blue: 0.04)

extension Color {
    init(hex: String) {
        let cleaned = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        var value: UInt64 = 0
        Scanner(string: cleaned).scanHexInt64(&value)
        let r = Double((value >> 16) & 0xFF) / 255
        let g = Double((value >> 8) & 0xFF) / 255
        let b = Double(value & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}

struct Card<Content: View>: View {
    let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        content
            .padding(20)
            .background(.regularMaterial)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(Color.primary.opacity(0.08))
            )
    }
}

struct StatusBadge: View {
    let status: JobStatus

    var body: some View {
        Text(status.label)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 9)
            .padding(.vertical, 4)
            .foregroundStyle(foreground)
            .background(background)
            .clipShape(Capsule())
    }

    private var foreground: Color {
        switch status {
        case .done: return Color(red: 0.09, green: 0.40, blue: 0.20)
        case .running, .queued: return Color(red: 0.55, green: 0.32, blue: 0.02)
        case .error: return Color(red: 0.65, green: 0.12, blue: 0.10)
        }
    }

    private var background: Color {
        switch status {
        case .done: return Color(red: 0.86, green: 0.95, blue: 0.89)
        case .running, .queued: return Color(red: 1.0, green: 0.94, blue: 0.82)
        case .error: return Color(red: 1.0, green: 0.89, blue: 0.88)
        }
    }
}

struct SectionTitle: View {
    let eyebrow: String
    let title: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(eyebrow.uppercased())
                .font(.caption2.weight(.bold))
                .tracking(1.2)
                .foregroundStyle(transcriptorAccent)
            Text(title)
                .font(.title3.weight(.semibold))
        }
    }
}

struct EmptyStateView: View {
    var body: some View {
        VStack(spacing: 18) {
            ZStack {
                Circle()
                    .fill(
                        RadialGradient(
                            colors: [transcriptorAccent.opacity(0.20), transcriptorAccent.opacity(0.02)],
                            center: .center,
                            startRadius: 4,
                            endRadius: 90
                        )
                    )
                    .frame(width: 140, height: 140)
                Image(systemName: "waveform.circle")
                    .font(.system(size: 58, weight: .light))
                    .foregroundStyle(transcriptorAccent)
            }
            Text("把访谈变成可编辑的逐字稿")
                .font(.system(size: 30, weight: .bold, design: .rounded))
            Text("上传音频后，Transcriptor 会在本机完成转写、说话人分离、段落整理和章节生成。")
                .font(.body)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 520)
            HStack(spacing: 8) {
                Label("本地处理", systemImage: "lock.shield")
                Label("Markdown / Word", systemImage: "doc.text")
                Label("可编辑章节", systemImage: "text.append")
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(40)
    }
}
