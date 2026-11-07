import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @State private var showNewJob = false

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 270, ideal: 300, max: 340)
        } detail: {
            detail
        }
        .navigationSplitViewStyle(.balanced)
        .sheet(isPresented: $showNewJob) {
            NewJobView()
                .environmentObject(model)
        }
        .onReceive(NotificationCenter.default.publisher(for: .newTranscript)) { _ in
            showNewJob = true
        }
        .alert(
            "出错了",
            isPresented: Binding(
                get: { model.errorMessage != nil },
                set: { if !$0 { model.errorMessage = nil } }
            )
        ) {
            Button("好", role: .cancel) { model.errorMessage = nil }
        } message: {
            Text(model.errorMessage ?? "")
        }
        .task {
            await model.bootstrap()
        }
    }

    private var sidebar: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 10) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(transcriptorAccent)
                            .frame(width: 34, height: 34)
                        Text("T")
                            .font(.system(size: 19, weight: .bold, design: .rounded))
                            .foregroundStyle(.white)
                    }
                    VStack(alignment: .leading, spacing: 1) {
                        Text("Transcriptor")
                            .font(.system(size: 18, weight: .bold, design: .rounded))
                        Text("访谈音频转逐字稿")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }

                Button {
                    showNewJob = true
                } label: {
                    Label("新建转写", systemImage: "plus")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(transcriptorAccent)
                .controlSize(.large)
            }
            .padding(16)

            Divider()

            HStack {
                Text("最近任务")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                Button {
                    Task { await model.loadJobs() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.plain)
                .help("刷新")
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)

            List(
                selection: Binding(
                    get: { model.selectedJobId },
                    set: { model.selectJob($0) }
                )
            ) {
                ForEach(model.jobs) { job in
                    JobRow(job: job)
                        .tag(job.id)
                }
            }
            .listStyle(.sidebar)
            .overlay {
                if model.jobs.isEmpty {
                    Text("还没有任务\n点击“新建转写”开始")
                        .font(.caption)
                        .multilineTextAlignment(.center)
                        .foregroundStyle(.tertiary)
                        .padding()
                }
            }

            Divider()
            HStack(spacing: 6) {
                Image(systemName: "lock.shield")
                Text("本地处理 · 音频不上传云端")
            }
            .font(.caption2)
            .foregroundStyle(.secondary)
            .padding(12)
        }
        .background(.thinMaterial)
    }

    @ViewBuilder
    private var detail: some View {
        switch model.backendState {
        case .idle, .starting:
            StartupView()
        case .failed(let message):
            BackendErrorView(message: message) {
                Task { await model.bootstrap() }
            }
        case .ready:
            if let job = model.job {
                JobDetailView(job: job)
                    .id(job.id)
            } else if model.selectedJobId != nil {
                ProgressView("正在加载任务…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                EmptyStateView()
            }
        }
    }
}

private struct JobRow: View {
    let job: JobSummary

    var body: some View {
        HStack(alignment: .top, spacing: 9) {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)
                .padding(.top, 5)
            VStack(alignment: .leading, spacing: 2) {
                Text(job.title)
                    .font(.system(size: 13, weight: .semibold))
                    .lineLimit(1)
                HStack(spacing: 5) {
                    Text(job.statusValue.label)
                    if let duration = job.duration {
                        Text("·")
                        Text(Format.duration(duration))
                    }
                    if let paragraphs = job.stats?.paragraphs {
                        Text("· \(paragraphs) 段")
                    }
                }
                .font(.caption2)
                .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 3)
    }

    private var color: Color {
        switch job.statusValue {
        case .done: return transcriptorAccent
        case .running, .queued: return transcriptorWarm
        case .error: return .red
        }
    }
}

private struct StartupView: View {
    var body: some View {
        VStack(spacing: 14) {
            ProgressView()
                .controlSize(.large)
            Text("正在启动本地转写服务…")
                .font(.headline)
            Text("首次启动需要加载 Python 环境，请稍候。")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct BackendErrorView: View {
    let message: String
    let retry: () -> Void

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 42))
                .foregroundStyle(.orange)
            Text("本地转写服务启动失败")
                .font(.title2.weight(.semibold))
            Text(message)
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .textSelection(.enabled)
                .frame(maxWidth: 620)
            Button("重试", action: retry)
                .buttonStyle(.borderedProminent)
                .tint(transcriptorAccent)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
