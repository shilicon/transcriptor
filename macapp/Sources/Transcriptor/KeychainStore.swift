import Foundation
import Security

enum KeychainStore {
    static let service = "com.shilicon.transcriptor"
    static let deepSeekAccount = "deepseek-api-key"

    // SwiftUI evaluates computed properties such as NewJobView's
    // `hasDeepSeekCredentials` on every body render.  Reading the Keychain
    // each time can show the access prompt repeatedly, so keep the value in
    // memory for this process after the first successful read.
    private static let cacheLock = NSLock()
    private static var cache: [String: String] = [:]

    static func save(_ value: String, account: String) {
        guard let data = value.data(using: .utf8) else { return }
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
        if !value.isEmpty {
            var attributes = query
            attributes[kSecValueData as String] = data
            attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
            SecItemAdd(attributes as CFDictionary, nil)
        }
        cacheLock.lock()
        cache[account] = value
        cacheLock.unlock()
    }

    static func load(account: String) -> String {
        cacheLock.lock()
        if let cached = cache[account] {
            cacheLock.unlock()
            return cached
        }
        cacheLock.unlock()

        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        let value: String
        if status == errSecSuccess,
           let data = result as? Data,
           let stored = String(data: data, encoding: .utf8) {
            value = stored
        } else {
            value = ""
        }

        cacheLock.lock()
        cache[account] = value
        cacheLock.unlock()
        return value
    }

    static func delete(account: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
        cacheLock.lock()
        cache[account] = ""
        cacheLock.unlock()
    }
}
