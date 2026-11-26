import Foundation

@main
struct FormatSafetyProbe {
    static func main() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let listURL = URL(fileURLWithPath: CommandLine.arguments[1])
        let detailURL = URL(fileURLWithPath: CommandLine.arguments[2])
        let list = try decoder.decode(
            [JobSummary].self,
            from: Data(contentsOf: listURL)
        )
        precondition(list.count == 2)
        let healthySummary = list.first { $0.id == "healthy" }!
        let damagedSummary = list.first { $0.id == "damaged" }!
        precondition(healthySummary.corrupt != true)
        precondition(damagedSummary.corrupt == true)
        precondition(Format.duration(healthySummary.duration) == "11 秒")
        precondition(Format.duration(damagedSummary.duration) == "未知时长")

        let job = try decoder.decode(Job.self, from: Data(contentsOf: detailURL))
        precondition(job.id == "healthy")
        precondition(Format.duration(job.duration) == "11 秒")
        precondition(Format.timestamp(job.paragraphs[0].start) == "00:01")
        precondition(Format.timestamp(job.chapters[0].start) == "00:03")

        // Defense in depth: even if a stale/third-party API sends an extreme
        // finite value, the formatter must never trap in Int conversion.
        precondition(Format.timestamp(1e20) == "00:00")
        precondition(Format.duration(1e20) == "未知时长")
        precondition(Format.timestamp(.infinity) == "00:00")
        precondition(Format.duration(.nan) == "未知时长")
        precondition(Format.timestamp(0.9) == "00:00")
        precondition(Format.timestamp(1.9) == "00:01")
        precondition(Format.duration(3599.9) == "1 小时 0 分 0 秒")
        precondition(Format.duration(Format.maxDisplaySeconds) == "720 小时 0 分 0 秒")
        precondition(Format.duration(Format.maxDisplaySeconds + 0.4) == "未知时长")
        precondition(Format.timestamp(Format.maxDisplaySeconds + 1) == "00:00")

        print("format probes: PASS")
    }
}
