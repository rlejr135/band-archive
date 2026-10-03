import Foundation
import Security

/// The complete Supabase session (including the refresh token) is one Keychain
/// value. Keeping rotation in one record prevents a crash from persisting a new
/// access token with an old refresh token, and avoids preferences/task JSON.
enum SecureAuthSessionError: LocalizedError, Equatable {
    case unavailable, malformed, accountMismatch, refreshFailed
    var errorDescription: String? {
        switch self {
        case .unavailable: return "The secure sign-in session is unavailable."
        case .malformed: return "The secure sign-in session is invalid."
        case .accountMismatch: return "The upload belongs to a different account."
        case .refreshFailed: return "The sign-in session could not be refreshed."
        }
    }
}

final class SecureAuthSessionStore {
    typealias RefreshTransport = (URLRequest) async throws -> (Data, URLResponse)
    private struct RefreshSnapshot {
        let accountID: String
        let session: String
        let generation: UInt64
        let refreshToken: String
        let url: URL
        let anon: String
    }
    private static let mutationLock = NSLock()
    private static var generation: UInt64 = 0
    private static var inFlight: [String: Task<String, Error>] = [:]
    private let service: String
    private let sessionAccount = "supabase-session"
    private let configAccount = "supabase-config"
    private let accessibility = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
    private let refreshTransport: RefreshTransport

    init(service: String = "com.deutteun.archive.secure-auth.v1", refreshTransport: @escaping RefreshTransport = { request in try await URLSession.shared.data(for: request) }) {
        self.service = service; self.refreshTransport = refreshTransport
    }

    func setSession(_ value: String, supabaseURL: String, anonKey: String) throws {
        _ = try sessionObject(value) // reject malformed data before replacing a valid session
        Self.mutationLock.lock(); defer { Self.mutationLock.unlock() }
        try replace(value, account: sessionAccount)
        // URL and anon key are not user credentials, but are kept together with
        // the native secure transport configuration rather than preferences.
        try replace(try configData(url: supabaseURL, anonKey: anonKey), account: configAccount)
        Self.generation &+= 1
    }

    func session() throws -> String? { try read(account: sessionAccount) }
    func removeSession() throws {
        Self.mutationLock.lock(); defer { Self.mutationLock.unlock() }
        try remove(account: sessionAccount); try remove(account: configAccount); Self.generation &+= 1
    }

    func accountID() throws -> String? {
        guard let value = try session() else { return nil }
        return try accountID(from: sessionObject(value))
    }

    /// Returns a current bearer token and refreshes at most once per caller when
    /// it expires within the next minute. The refreshed full session is written
    /// atomically after verifying its user id still owns the queued task.
    func bearerToken(for accountID: String, forceRefresh: Bool = false) async throws -> String {
        Self.mutationLock.lock()
        let snapshot: RefreshSnapshot
        do {
            guard let value = try session() else { throw SecureAuthSessionError.unavailable }
            let current = try sessionObject(value)
            guard try self.accountID(from: current) == accountID else { throw SecureAuthSessionError.accountMismatch }
            if !forceRefresh, let access = current["access_token"] as? String, expiresAt(current) > Date().timeIntervalSince1970 + 60 {
                Self.mutationLock.unlock(); return access
            }
            guard let refresh = current["refresh_token"] as? String,
                  let config = try configObject(), let urlText = config["url"] as? String,
                  let anon = config["anon_key"] as? String,
                  let base = URL(string: urlText) else { throw SecureAuthSessionError.unavailable }
            snapshot = RefreshSnapshot(accountID: accountID, session: value, generation: Self.generation, refreshToken: refresh, url: base, anon: anon)
            Self.mutationLock.unlock()
        } catch {
            Self.mutationLock.unlock(); throw error
        }
        let key = "\(snapshot.accountID):\(snapshot.generation)"
        Self.mutationLock.lock()
        if let existing = Self.inFlight[key] { Self.mutationLock.unlock(); return try await existing.value }
        let task = Task { [weak self] () throws -> String in
            defer {
                Self.mutationLock.lock(); Self.inFlight.removeValue(forKey: key); Self.mutationLock.unlock()
            }
            guard let self else { throw SecureAuthSessionError.unavailable }
            return try await self.refresh(snapshot)
        }
        Self.inFlight[key] = task
        Self.mutationLock.unlock()
        return try await task.value
    }

    private func refresh(_ snapshot: RefreshSnapshot) async throws -> String {
        guard var components = URLComponents(url: snapshot.url.appendingPathComponent("auth/v1/token"), resolvingAgainstBaseURL: false) else { throw SecureAuthSessionError.malformed }
        components.queryItems = [URLQueryItem(name: "grant_type", value: "refresh_token")]
        guard let refreshURL = components.url else { throw SecureAuthSessionError.malformed }
        var request = URLRequest(url: refreshURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(snapshot.anon, forHTTPHeaderField: "apikey")
        request.setValue("Bearer \(snapshot.anon)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["refresh_token": snapshot.refreshToken])
        let (data, response) = try await refreshTransport(request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode),
              var rotated = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw SecureAuthSessionError.refreshFailed }
        if rotated["expires_at"] == nil, let seconds = (rotated["expires_in"] as? NSNumber)?.doubleValue { rotated["expires_at"] = Date().timeIntervalSince1970 + seconds }
        guard try self.accountID(from: rotated) == snapshot.accountID,
              let encoded = String(data: try JSONSerialization.data(withJSONObject: rotated), encoding: .utf8),
              let access = rotated["access_token"] as? String else { throw SecureAuthSessionError.accountMismatch }
        Self.mutationLock.lock(); defer { Self.mutationLock.unlock() }
        guard Self.generation == snapshot.generation, try session() == snapshot.session,
              let current = try session(), try self.accountID(from: sessionObject(current)) == snapshot.accountID else { throw SecureAuthSessionError.accountMismatch }
        try replace(encoded, account: sessionAccount)
        Self.generation &+= 1
        return access
    }

    private func configData(url: String, anonKey: String) throws -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: ["url": url, "anon_key": anonKey]), let text = String(data: data, encoding: .utf8) else { throw SecureAuthSessionError.malformed }
        return text
    }
    private func configObject() throws -> [String: Any]? {
        guard let value = try read(account: configAccount) else { return nil }
        return try sessionObject(value)
    }
    private func sessionObject(_ value: String) throws -> [String: Any] {
        guard let data = value.data(using: .utf8), let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw SecureAuthSessionError.malformed }
        return object
    }
    private func accountID(from object: [String: Any]) throws -> String {
        guard let user = object["user"] as? [String: Any], let id = user["id"] as? String, !id.isEmpty else { throw SecureAuthSessionError.malformed }
        return id
    }
    private func expiresAt(_ object: [String: Any]) -> TimeInterval {
        if let value = (object["expires_at"] as? NSNumber)?.doubleValue { return value }
        if let value = Double(object["expires_at"] as? String ?? "") { return value }
        return 0
    }
    private func read(account: String) throws -> String? {
        let query: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: account, kSecReturnData: true, kSecMatchLimit: kSecMatchLimitOne]
        var result: CFTypeRef?; let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data, let value = String(data: data, encoding: .utf8) else { throw SecureAuthSessionError.unavailable }
        return value
    }
    private func replace(_ value: String, account: String) throws {
        let query: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: account]
        let values: [CFString: Any] = [kSecValueData: Data(value.utf8), kSecAttrAccessible: accessibility]
        let status = SecItemUpdate(query as CFDictionary, values as CFDictionary)
        if status == errSecSuccess { return }
        if status != errSecItemNotFound { throw SecureAuthSessionError.unavailable }
        var add = query; add[kSecValueData] = Data(value.utf8); add[kSecAttrAccessible] = accessibility
        let addStatus = SecItemAdd(add as CFDictionary, nil)
        if addStatus == errSecDuplicateItem { try replace(value, account: account); return }
        guard addStatus == errSecSuccess else { throw SecureAuthSessionError.unavailable }
    }
    private func remove(account: String) throws {
        let query: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: account]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw SecureAuthSessionError.unavailable }
    }
}
