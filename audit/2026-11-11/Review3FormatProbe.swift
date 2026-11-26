// Decode actual synthetic API output, then invoke the actual UI formatters.
import Foundation

@main struct Review3FormatProbe {
    static func main() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[2]))
        switch CommandLine.arguments[1] {
        case "duration":
            let jobs = try decoder.decode([JobSummary].self, from: data)
            let damaged = jobs.first { $0.id == "damaged" }!
            print(Format.duration(damaged.duration))
        case "paragraph":
            let job = try decoder.decode(Job.self, from: data)
            print(Format.timestamp(job.paragraphs[0].start))
        case "chapter":
            let job = try decoder.decode(Job.self, from: data)
            print(Format.timestamp(job.chapters[0].start))
        default:
            let job = try decoder.decode(Job.self, from: data)
            print(Format.duration(job.duration))
            print(Format.timestamp(job.paragraphs[0].start))
        }
    }
}
