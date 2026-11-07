// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Transcriptor",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "Transcriptor", targets: ["Transcriptor"])
    ],
    targets: [
        .executableTarget(
            name: "Transcriptor",
            path: "Sources/Transcriptor"
        )
    ],
    swiftLanguageModes: [.v5]
)
