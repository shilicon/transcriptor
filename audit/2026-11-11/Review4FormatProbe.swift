// Current actual API summaries/details, then actual formatters; no native App.
import Foundation

@main struct Review4FormatProbe {
    static func main() throws {
        let dir = URL(fileURLWithPath: CommandLine.arguments[1])
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let list = try decoder.decode([JobSummary].self, from: Data(contentsOf: dir.appendingPathComponent("list.json")))
        precondition(list.count == 37)
        precondition(list.filter { $0.corrupt == true }.count == 25)
        for item in list { _ = Format.duration(item.duration) }
        var details = 0
        for url in try FileManager.default.contentsOfDirectory(at:dir,includingPropertiesForKeys:nil)
            where url.lastPathComponent.hasSuffix("-detail.json") && !url.lastPathComponent.hasPrefix("write-") {
            let job = try decoder.decode(Job.self, from:Data(contentsOf:url))
            _ = Format.duration(job.duration)
            for p in job.paragraphs { _ = Format.timestamp(p.start); _ = Format.timestamp(p.end) }
            for c in job.chapters { _ = Format.timestamp(c.start) }
            details += 1
        }
        precondition(details == 12)
        let max = Format.maxDisplaySeconds
        for value in [Double.greatestFiniteMagnitude, 1e20, Double.infinity, -Double.infinity, Double.nan, -1, max.nextUp] {
            precondition(Format.timestamp(value) == "00:00")
            precondition(Format.duration(value) == "未知时长")
        }
        precondition(Format.duration(max.nextDown) == "720 小时 0 分 0 秒")
        precondition(Format.timestamp(max.nextDown) == "43199:59")
        precondition(Format.timestamp(max) == "43200:00")
        precondition(Format.duration(10.5) == "11 秒")
        precondition(Format.timestamp(90.5) == "01:30")
        print("{\"decoded_summaries\":37,\"safe_corrupt_summaries\":25,\"decoded_and_formatted_details\":12,\"invalid_values_and_rounding\":\"PASS\"}")
    }
}
