import SwiftUI

struct TranscriptView: View {
    let job: Job
    let editing: Bool
    @Binding var draftParagraphs: [Paragraph]
    @Binding var draftSpeakerIds: [String]

    private struct Block: Identifiable {
        let id: Int
        let title: String
        let indices: [Int]
    }

    private var blocks: [Block] {
        let chapters = job.chapters.sorted { $0.start < $1.start }
        guard !chapters.isEmpty else {
            return [Block(id: 0, title: "全文", indices: Array(job.paragraphs.indices))]
        }
        var result: [Block] = []
        for i in chapters.indices {
            let start = chapters[i].start
            let end = i + 1 < chapters.count ? chapters[i + 1].start : .infinity
            var indices = job.paragraphs.indices.filter {
                job.paragraphs[$0].start >= start && job.paragraphs[$0].start < end
            }
            if i == 0 {
                indices = job.paragraphs.indices.filter { job.paragraphs[$0].start < end }
            }
            if !indices.isEmpty {
                result.append(Block(id: i, title: chapters[i].title, indices: indices))
            }
        }
        return result.isEmpty
            ? [Block(id: 0, title: "全文", indices: Array(job.paragraphs.indices))]
            : result
    }

    var body: some View {
        LazyVStack(alignment: .leading, spacing: 0) {
            ForEach(Array(blocks.enumerated()), id: \.element.id) { blockIndex, block in
                VStack(alignment: .leading, spacing: 16) {
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text(String(format: "%02d", blockIndex + 1))
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(transcriptorAccent)
                            .tracking(0.8)
                        Text(block.title)
                            .font(.headline)
                    }
                    .padding(.top, blockIndex == 0 ? 0 : 26)

                    Divider()

                    ForEach(block.indices, id: \.self) { index in
                        paragraphView(index: index)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func paragraphView(index: Int) -> some View {
        let paragraph = job.paragraphs[index]
        let speaker = job.speakers.first { $0.id == paragraph.speakerId }
        let color = Color(hex: speaker?.color ?? "#0f766e")

        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .center, spacing: 8) {
                if editing && index < draftSpeakerIds.count {
                    Picker("", selection: $draftSpeakerIds[index]) {
                        ForEach(job.speakers) { item in
                            Text(item.name).tag(item.id)
                        }
                    }
                    .labelsHidden()
                    .pickerStyle(.menu)
                    .controlSize(.small)
                    .frame(maxWidth: 180)
                } else {
                    Text(speaker?.name ?? "说话人")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(color)
                }

                Text(Format.timestamp(paragraph.start))
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)

                Spacer(minLength: 0)
            }

            if editing && index < draftParagraphs.count {
                TextField(
                    "",
                    text: Binding(
                        get: { draftParagraphs[index].text },
                        set: { draftParagraphs[index].text = $0 }
                    ),
                    axis: .vertical
                )
                .textFieldStyle(.plain)
                .lineLimit(2...12)
                .font(.body)
                .lineSpacing(3)
                .padding(8)
                .background(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(Color.primary.opacity(0.035))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .strokeBorder(Color.primary.opacity(0.10))
                )
            } else {
                Text(paragraph.text)
                    .font(.body)
                    .lineSpacing(4)
                    .textSelection(.enabled)
            }
        }
        .padding(.bottom, 18)
    }
}
