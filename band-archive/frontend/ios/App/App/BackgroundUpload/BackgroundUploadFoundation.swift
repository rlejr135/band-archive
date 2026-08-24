import CryptoKit
import Foundation
import PhotosUI
import Security
import SQLite3
import UniformTypeIdentifiers
import UIKit

private let SQLITE_TRANSIENT = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

// This foundation is deliberately not a Capacitor plugin yet. Web calls continue to use the web transport.
enum BackgroundUploadState: String, Codable, CaseIterable {
    case preparing, queued, uploading, retryWait = "retry_wait", completing, processing, completed, failed, cancelled

    var isTerminal: Bool { self == .completed || self == .failed || self == .cancelled }
    var isRunnable: Bool { [.preparing, .queued, .uploading, .retryWait, .completing].contains(self) }
}

enum BackgroundUploadError: LocalizedError, Equatable {
    case fileTooLarge, insufficientSpace, unsafePath, unavailableFile, database(String), keychain(OSStatus)

    var errorDescription: String? {
        switch self {
        case .fileTooLarge: return "Videos larger than 1 GiB are not supported."
        case .insufficientSpace: return "There is not enough device storage to prepare this video."
        case .unsafePath: return "The selected file is outside the app upload directory."
        case .unavailableFile: return "The selected video is no longer available."
        case .database: return "The upload queue could not be stored."
        case .keychain: return "The upload credential could not be stored securely."
        }
    }
}

/// Missing capability is terminal for this upload; temporary Keychain access failures are retryable.
enum UploadCredentialError: Error, Equatable {
    case lost
    case unavailable
}

enum UploadCredentialPolicy {
    static func readError(for status: OSStatus) -> UploadCredentialError {
        // Item-not-found is the only terminal credential state. Device-lock and security-daemon availability
        // errors occur before first unlock or during a background relaunch and must remain retryable.
        status == errSecItemNotFound ? .lost : .unavailable
    }
}

enum UploadEngineError: Error {
    case ownershipLost
}

struct DurableUploadFile: Codable, Equatable {
    let uploadID: String
    let path: String
    let filename: String
    let contentType: String
    let bytes: Int64
    let sha256: String
}

enum BackgroundUploadFiles {
    static let maximumBytes: Int64 = 1_024 * 1_024 * 1_024
    static let freeSpaceHeadroom: Int64 = 16 * 1_024 * 1_024

    static func directory(fileManager: FileManager = .default) throws -> URL {
        let support = try fileManager.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        let directory = support.appendingPathComponent("background_uploads", isDirectory: true)
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory.resolvingSymlinksInPath()
    }

    static func canonicalChild(_ candidate: URL, of root: URL) -> URL? {
        let canonicalRoot = root.resolvingSymlinksInPath().standardizedFileURL
        let canonicalCandidate = candidate.resolvingSymlinksInPath().standardizedFileURL
        let prefix = canonicalRoot.path.hasSuffix("/") ? canonicalRoot.path : canonicalRoot.path + "/"
        guard canonicalCandidate.path.hasPrefix(prefix) else { return nil }
        return canonicalCandidate
    }

    /// This is intentionally synchronous. NSItemProvider deletes a loadFileRepresentation URL when its
    /// completion handler returns, so callers must finish this copy before returning from that callback.
    static func copyDurably(from source: URL, filename: String, contentType: String, declaredBytes: Int64?, uploadID: String = UUID().uuidString, root explicitRoot: URL? = nil, fileManager: FileManager = .default) throws -> DurableUploadFile {
        let root: URL
        if let explicitRoot {
            root = explicitRoot
        } else {
            root = try directory(fileManager: fileManager)
        }
        try fileManager.createDirectory(at: root, withIntermediateDirectories: true)
        let canonicalRoot = root.resolvingSymlinksInPath()
        guard declaredBytes.map({ $0 <= maximumBytes }) ?? true else { throw BackgroundUploadError.fileTooLarge }
        let available = try canonicalRoot.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]).volumeAvailableCapacityForImportantUsage
        let required = (declaredBytes ?? 0) + freeSpaceHeadroom
        guard (available ?? 0) >= required else { throw BackgroundUploadError.insufficientSpace }
        let destination = canonicalRoot.appendingPathComponent(uploadID, isDirectory: false)
        let temporary = canonicalRoot.appendingPathComponent("\(uploadID).tmp", isDirectory: false)
        guard canonicalChild(destination, of: canonicalRoot) != nil, canonicalChild(temporary, of: canonicalRoot) != nil else { throw BackgroundUploadError.unsafePath }
        defer { try? fileManager.removeItem(at: temporary) }
        guard fileManager.fileExists(atPath: source.path) else { throw BackgroundUploadError.unavailableFile }
        let input = try FileHandle(forReadingFrom: source)
        fileManager.createFile(atPath: temporary.path, contents: nil)
        let output = try FileHandle(forWritingTo: temporary)
        defer { try? input.close(); try? output.close() }
        var total: Int64 = 0
        var digest = SHA256()
        while true {
            let chunk = try input.read(upToCount: 64 * 1_024) ?? Data()
            if chunk.isEmpty { break }
            total += Int64(chunk.count)
            guard total <= maximumBytes else { throw BackgroundUploadError.fileTooLarge }
            digest.update(data: chunk)
            try output.write(contentsOf: chunk)
        }
        try output.synchronize()
        guard declaredBytes == nil || declaredBytes == total else { throw BackgroundUploadError.unavailableFile }
        // Same-directory move is atomic on the app volume; upload IDs make a destination collision invalid.
        try fileManager.moveItem(at: temporary, to: destination)
        let fingerprint = digest.finalize().map { String(format: "%02x", $0) }.joined()
        return DurableUploadFile(uploadID: uploadID, path: destination.path, filename: filename, contentType: contentType, bytes: total, sha256: fingerprint)
    }
}

enum VideoPickerPolicy {
    static func selectionLimit(multiple: Bool) -> Int { multiple ? 0 : 1 }

    static func mimeType(filename: String, fallback: UTType = .movie) -> String {
        let extensionType = UTType(filenameExtension: URL(fileURLWithPath: filename).pathExtension)
        if let mime = extensionType?.preferredMIMEType, mime.hasPrefix("video/") { return mime }
        if let mime = fallback.preferredMIMEType, mime.hasPrefix("video/") { return mime }
        return "video/mp4"
    }
}

enum VideoPickerLoader {
    static func loadDurably(_ provider: NSItemProvider) async -> Result<DurableUploadFile, Error> {
        let type = UTType.movie.identifier
        guard provider.hasItemConformingToTypeIdentifier(type) else { return .failure(BackgroundUploadError.unavailableFile) }
        return await withCheckedContinuation { continuation in
            provider.loadFileRepresentation(forTypeIdentifier: type) { url, error in
                if let error { continuation.resume(returning: .failure(error)); return }
                guard let url else { continuation.resume(returning: .failure(BackgroundUploadError.unavailableFile)); return }
                do {
                    let values = try? url.resourceValues(forKeys: [.fileSizeKey])
                    let declaredBytes = values?.fileSize.map(Int64.init)
                    let suggested = provider.suggestedName?.trimmingCharacters(in: .whitespacesAndNewlines)
                    let filename = Self.filename(suggested: suggested, source: url)
                    // The durable copy completes synchronously before this NSItemProvider callback returns.
                    let durable = try BackgroundUploadFiles.copyDurably(from: url, filename: filename, contentType: VideoPickerPolicy.mimeType(filename: filename), declaredBytes: declaredBytes)
                    continuation.resume(returning: .success(durable))
                } catch {
                    continuation.resume(returning: .failure(error))
                }
            }
        }
    }

    private static func filename(suggested: String?, source: URL) -> String {
        let base = (suggested?.isEmpty == false ? suggested! : source.lastPathComponent)
        guard URL(fileURLWithPath: base).pathExtension.isEmpty, !source.pathExtension.isEmpty else { return base }
        return "\(base).\(source.pathExtension)"
    }
}

@MainActor
final class VideoPicker: NSObject, PHPickerViewControllerDelegate {
    typealias Completion = ([Result<DurableUploadFile, Error>]) -> Void
    private var completion: Completion?

    func present(from controller: UIViewController, multiple: Bool, completion: @escaping Completion) {
        var config = PHPickerConfiguration(photoLibrary: .shared())
        config.filter = .videos
        config.selectionLimit = VideoPickerPolicy.selectionLimit(multiple: multiple)
        config.preferredAssetRepresentationMode = .current
        self.completion = completion
        controller.present(PHPickerViewController(configuration: config), animated: true)
    }

    func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
        picker.dismiss(animated: true)
        let completion = self.completion
        self.completion = nil
        Task {
            let selections = await withTaskGroup(of: Result<DurableUploadFile, Error>.self) { group in
                for result in results { group.addTask { await VideoPickerLoader.loadDurably(result.itemProvider) } }
                var values: [Result<DurableUploadFile, Error>] = []
                for await value in group { values.append(value) }
                return values
            }
            completion?(selections)
        }
    }

}

protocol UploadCredentialStoring {
    func save(_ token: String, uploadID: String) throws
    func read(uploadID: String) throws -> String?
    func delete(uploadID: String) throws
}

final class KeychainUploadCredentialStore: UploadCredentialStoring {
    private let service = "com.deutteun.archive.background-upload"
    // After first device unlock, URLSession background relaunches may read credentials; ThisDeviceOnly prevents backup migration.
    private let accessibility = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly

    func save(_ token: String, uploadID: String) throws {
        try delete(uploadID: uploadID)
        let query: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: uploadID,
                                       kSecAttrAccessible: accessibility, kSecValueData: Data(token.utf8)]
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else { throw BackgroundUploadError.keychain(status) }
    }

    func read(uploadID: String) throws -> String? {
        let query: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: uploadID,
                                       kSecReturnData: true, kSecMatchLimit: kSecMatchLimitOne]
        var result: CFTypeRef?; let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { throw UploadCredentialPolicy.readError(for: status) }
        if status == errSecInteractionNotAllowed || status == errSecNotAvailable { throw UploadCredentialPolicy.readError(for: status) }
        guard status == errSecSuccess, let data = result as? Data, let token = String(data: data, encoding: .utf8) else { throw UploadCredentialPolicy.readError(for: status) }
        return token
    }

    func delete(uploadID: String) throws {
        let query: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: uploadID]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw BackgroundUploadError.keychain(status) }
    }
}

struct IOSUploadTask: Codable, Equatable {
    let uploadID: String; let workID: Int64; let createdAt: Date
    var file: DurableUploadFile; var api: String; var targetKind: String; var targetID: String
    var sessionID: String?; var partSize: Int64?; var state: BackgroundUploadState; var progress: Int
    var error: String?; var result: String?; var leaseOwner: String?; var leaseExpiresAt: Date?; var updatedAt: Date
}

/// A durable association between an opaque background URLSession task and its
/// one-use on-disk part file. Presigned URLs and capabilities are deliberately
/// not persisted here.
struct IOSPartAttempt: Equatable {
    let uploadID: String
    let part: Int
    let attemptID: String
    let temporaryPath: String
    let taskIdentifier: Int?
}

final class IOSUploadStore {
    private var db: OpaquePointer?
    private let root: URL
    private let credentials: UploadCredentialStoring
    private static let retention: TimeInterval = 7 * 24 * 60 * 60

    init(root: URL? = nil, credentials: UploadCredentialStoring = KeychainUploadCredentialStore()) throws {
        self.root = try root ?? BackgroundUploadFiles.directory(); self.credentials = credentials
        let database = self.root.appendingPathComponent("background_upload.sqlite")
        guard sqlite3_open_v2(database.path, &db, SQLITE_OPEN_CREATE | SQLITE_OPEN_READWRITE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK else { throw BackgroundUploadError.database("open") }
        try execute("PRAGMA journal_mode=WAL"); try execute("PRAGMA foreign_keys=ON"); try migrate()
    }
    deinit { sqlite3_close(db) }

    func insert(_ task: IOSUploadTask, capability: String) throws {
        try transaction {
            try credentials.save(capability, uploadID: task.uploadID)
            try execute("INSERT INTO tasks(upload_id,work_id,created_at,path,name,bytes,type,hash,api,target_kind,target_id,session_id,part_size,state,progress,error,result,lease_owner,lease_expires_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values: [task.uploadID, task.workID, stamp(task.createdAt), task.file.path, task.file.filename, task.file.bytes, task.file.contentType, task.file.sha256, task.api, task.targetKind, task.targetID, task.sessionID, task.partSize, task.state.rawValue, task.progress, task.error, task.result, task.leaseOwner, task.leaseExpiresAt.map(stamp), stamp(task.updatedAt)])
        }
    }
    func task(_ id: String) throws -> IOSUploadTask? { try tasks(where: "upload_id=?", values: [id]).first }
    func retainedTasks() throws -> [IOSUploadTask] { try tasks(where: "1=1", values: []) }
    func allocateWorkID() throws -> Int64 {
        try transaction {
            guard let text = try query("SELECT next_work_id FROM work_id_allocator WHERE singleton=1", []).first?.first,
                  let next = Int64(text), next > 0, next < Int64.max else { throw BackgroundUploadError.database("work_id") }
            guard try update("UPDATE work_id_allocator SET next_work_id=? WHERE singleton=1 AND next_work_id=?", [next + 1, next]) else { throw BackgroundUploadError.database("work_id") }
            return next
        }
    }
    func acquire(_ id: String, owner: String, now: Date = Date()) throws -> Bool { try update("UPDATE tasks SET lease_owner=?,lease_expires_at=? WHERE upload_id=? AND state NOT IN ('completed','failed','cancelled') AND (lease_owner IS NULL OR lease_expires_at<? OR lease_owner=?)", [owner, stamp(now.addingTimeInterval(120)), id, stamp(now), owner]) }
    func renew(_ id: String, owner: String, now: Date = Date()) throws -> Bool { try update("UPDATE tasks SET lease_expires_at=? WHERE upload_id=? AND lease_owner=? AND lease_expires_at>=? AND state NOT IN ('completed','failed','cancelled')", [stamp(now.addingTimeInterval(120)), id, owner, stamp(now)]) }
    func release(_ id: String, owner: String) throws -> Bool { try update("UPDATE tasks SET lease_owner=NULL,lease_expires_at=NULL WHERE upload_id=? AND lease_owner=?", [id, owner]) }
    /// Engine writes are compare-and-set: a cancelled/terminal row or different lease owner cannot be overwritten.
    func updateForOwner(_ task: IOSUploadTask, owner: String, now: Date = Date()) throws -> Bool {
        try transaction { try update("UPDATE tasks SET session_id=?,part_size=?,state=?,progress=?,error=?,result=?,lease_expires_at=?,updated_at=? WHERE upload_id=? AND lease_owner=? AND lease_expires_at>=? AND state NOT IN ('completed','failed','cancelled')", [task.sessionID, task.partSize, task.state.rawValue, task.progress, task.error, task.result, stamp(now.addingTimeInterval(120)), stamp(now), task.uploadID, owner, stamp(now)]) }
    }
    func cancel(_ id: String) throws -> Bool {
        let changed = try transaction { try update("UPDATE tasks SET state='cancelled',lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE upload_id=? AND state NOT IN ('completed','failed','cancelled')", [stamp(Date()), id]) }
        guard changed else { return false }
        // Cancellation is durable before best-effort file cleanup; a later acknowledge/GC retry can finish cleanup.
        try? cleanupArtifacts(uploadID: id, requireTerminal: true)
        return true
    }
    /// Media processing is no longer owned by the upload engine; UI polling may only consume its nonterminal processing row once.
    func syncProcessing(_ id: String, state: BackgroundUploadState, result: String?, error: String?, now: Date = Date()) throws -> Bool {
        guard state == .completed || state == .failed else { return false }
        return try update("UPDATE tasks SET state=?,result=COALESCE(?,result),error=COALESCE(?,error),lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE upload_id=? AND state='processing'", [state.rawValue, result, error, stamp(now), id])
    }
    /// Terminal-only consume sequence. Files are removed first, then Keychain capability, then the task row and
    /// its ACK/attempt children. Any earlier failure leaves the terminal row for an idempotent later retry.
    func acknowledge(_ id: String, fileManager: FileManager = .default) throws -> Bool {
        guard let task = try task(id), task.state.isTerminal else { return false }
        try cleanupArtifacts(uploadID: id, requireTerminal: true, deleteMappings: false, fileManager: fileManager)
        try credentials.delete(uploadID: id)
        return try transaction { try update("DELETE FROM tasks WHERE upload_id=? AND state IN ('completed','failed','cancelled')", [id]) }
    }
    func saveAck(uploadID: String, part: Int, etag: String, bytes: Int64, owner: String, now: Date = Date()) throws -> Bool { try ownerTransaction(uploadID: uploadID, owner: owner, now: now) { try execute("INSERT OR REPLACE INTO part_acks(upload_id,part_number,etag,bytes) VALUES(?,?,?,?)", values: [uploadID, part, etag, bytes]) } }
    func savePendingAck(uploadID: String, part: Int, etag: String, bytes: Int64, owner: String, now: Date = Date()) throws -> Bool { try ownerTransaction(uploadID: uploadID, owner: owner, now: now) { try execute("INSERT OR REPLACE INTO pending_acks(upload_id,part_number,etag,bytes) VALUES(?,?,?,?)", values: [uploadID, part, etag, bytes]) } }
    func pendingAcks(uploadID: String) throws -> [(Int, String, Int64)] { try query("SELECT part_number,etag,bytes FROM pending_acks WHERE upload_id=? ORDER BY part_number", [uploadID]).compactMap { row in guard let part=Int(row[0] ?? ""), let etag=row[1], let bytes=Int64(row[2] ?? "") else{return nil}; return (part,etag,bytes) } }
    func clearPendingAck(uploadID: String, part: Int, owner: String, now: Date = Date()) throws -> Bool { try ownerTransaction(uploadID: uploadID, owner: owner, now: now) { try execute("DELETE FROM pending_acks WHERE upload_id=? AND part_number=?", values: [uploadID, part]) } }
    func acknowledgedParts(uploadID: String) throws -> Set<Int> {
        let values = try query("SELECT part_number FROM part_acks WHERE upload_id=?", [uploadID]).compactMap { Int($0[0] ?? "") }
        return Set(values)
    }
    func savePartAttempt(_ attempt: IOSPartAttempt, owner: String, now: Date = Date()) throws -> Bool {
        try ownerTransaction(uploadID: attempt.uploadID, owner: owner, now: now) { try execute("INSERT OR REPLACE INTO part_attempts(upload_id,part_number,attempt_id,temp_path,task_identifier,created_at) VALUES(?,?,?,?,?,?)", values: [attempt.uploadID, attempt.part, attempt.attemptID, attempt.temporaryPath, attempt.taskIdentifier, stamp(Date())]) }
    }
    func partAttempt(uploadID: String, part: Int, attemptID: String) throws -> IOSPartAttempt? {
        guard let row = try query("SELECT upload_id,part_number,attempt_id,temp_path,task_identifier FROM part_attempts WHERE upload_id=? AND part_number=? AND attempt_id=?", [uploadID, part, attemptID]).first,
              let storedID = row[0], let storedPart = Int(row[1] ?? ""), let storedAttempt = row[2], let path = row[3] else { return nil }
        return IOSPartAttempt(uploadID: storedID, part: storedPart, attemptID: storedAttempt, temporaryPath: path, taskIdentifier: Int(row[4] ?? ""))
    }
    func removePartAttempt(uploadID: String, part: Int, attemptID: String, owner: String, now: Date = Date()) throws -> Bool {
        try ownerTransaction(uploadID: uploadID, owner: owner, now: now) { try execute("DELETE FROM part_attempts WHERE upload_id=? AND part_number=? AND attempt_id=?", values: [uploadID, part, attemptID]) }
    }
    /// Startup/foreground cleanup. Only canonical children of Application Support/background_uploads are touched.
    /// Active URLSession attempts and unexpired leases are caller-provided exclusions; every task cleanup is isolated.
    func garbageCollect(activeAttemptPaths: Set<String> = [], now: Date = Date(), fileManager: FileManager = .default) throws {
        let stale = stamp(now.addingTimeInterval(-Self.retention))
        let terminalIDs = try query("SELECT upload_id FROM tasks WHERE state IN ('completed','failed','cancelled') AND updated_at<=? AND (lease_expires_at IS NULL OR lease_expires_at<?)", [stale, stamp(now)]).compactMap { $0[0] }
        for id in terminalIDs { _ = try? acknowledge(id, fileManager: fileManager) }

        let referencedSources = Set(try query("SELECT path FROM tasks", []).compactMap { $0[0] })
        let attempts = try allAttempts()
        let leasedAttempts = Set(try query("SELECT p.temp_path FROM part_attempts p JOIN tasks t ON t.upload_id=p.upload_id WHERE t.lease_expires_at>=? AND t.state NOT IN ('completed','failed','cancelled')", [stamp(now)]).compactMap { $0[0] })
        let active = activeAttemptPaths.union(leasedAttempts)
        let referencedAttempts = Set(attempts.map(\.temporaryPath))
        for attempt in attempts where !active.contains(attempt.temporaryPath) {
            // A mapping without a recovered active URLSession task is stale; remove its canonical part file and row.
            try? removeCanonical(attempt.temporaryPath, fileManager: fileManager)
            try? execute("DELETE FROM part_attempts WHERE upload_id=? AND part_number=? AND attempt_id=?", values: [attempt.uploadID, attempt.part, attempt.attemptID])
        }

        let protected = Set(["background_upload.sqlite", "background_upload.sqlite-wal", "background_upload.sqlite-shm"])
        let contents = (try? fileManager.contentsOfDirectory(at: root, includingPropertiesForKeys: [.contentModificationDateKey, .isRegularFileKey], options: [.skipsHiddenFiles])) ?? []
        for candidate in contents {
            guard !protected.contains(candidate.lastPathComponent), let safe = BackgroundUploadFiles.canonicalChild(candidate, of: root) else { continue }
            let path = safe.path
            if active.contains(path) || referencedSources.contains(path) || referencedAttempts.contains(path) { continue }
            let name = safe.lastPathComponent
            if name.hasSuffix(".tmp") || name.hasSuffix(".part") { try? fileManager.removeItem(at: safe); continue }
            let modified = (try? safe.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? now
            if modified <= now.addingTimeInterval(-Self.retention) { try? fileManager.removeItem(at: safe) }
        }
    }
    /// R2 has accepted the original once the multipart complete response enters processing/completed; keeping
    /// the durable source after that only wastes device storage. Processing metadata itself remains for JS polling.
    func removeSourceAfterR2Complete(_ id: String, fileManager: FileManager = .default) throws -> Bool {
        guard let task = try task(id), task.state == .processing || task.state == .completed else { return false }
        return try removeCanonical(task.file.path, fileManager: fileManager)
    }

    private func cleanupArtifacts(uploadID: String, requireTerminal: Bool, deleteMappings: Bool = true, fileManager: FileManager = .default) throws {
        guard let task = try task(uploadID), !requireTerminal || task.state.isTerminal else { return }
        _ = try removeCanonical(task.file.path, fileManager: fileManager)
        let attempts = try allAttempts(uploadID: uploadID)
        for attempt in attempts { _ = try removeCanonical(attempt.temporaryPath, fileManager: fileManager) }
        if deleteMappings { try execute("DELETE FROM part_attempts WHERE upload_id=?", values: [uploadID]) }
    }
    private func allAttempts(uploadID: String? = nil) throws -> [IOSPartAttempt] {
        let clause = uploadID == nil ? "1=1" : "upload_id=?"
        let values: [Any?] = uploadID == nil ? [] : [uploadID!]
        return try query("SELECT upload_id,part_number,attempt_id,temp_path,task_identifier FROM part_attempts WHERE \(clause)", values).compactMap { row in
            guard let id = row[0], let part = Int(row[1] ?? ""), let attempt = row[2], let path = row[3] else { return nil }
            return IOSPartAttempt(uploadID: id, part: part, attemptID: attempt, temporaryPath: path, taskIdentifier: Int(row[4] ?? ""))
        }
    }
    @discardableResult private func removeCanonical(_ path: String, fileManager: FileManager) throws -> Bool {
        guard let safe = BackgroundUploadFiles.canonicalChild(URL(fileURLWithPath: path), of: root) else { return false }
        guard fileManager.fileExists(atPath: safe.path) else { return true }
        try fileManager.removeItem(at: safe); return true
    }

    private func migrate() throws {
        let version = Int(try query("PRAGMA user_version", []).first?.first ?? "0") ?? 0
        if version < 1 { try transaction { try execute("CREATE TABLE IF NOT EXISTS tasks(upload_id TEXT PRIMARY KEY,work_id INTEGER NOT NULL UNIQUE CHECK(work_id>0),created_at REAL NOT NULL,path TEXT NOT NULL,name TEXT NOT NULL,bytes INTEGER NOT NULL,type TEXT NOT NULL,hash TEXT NOT NULL,api TEXT NOT NULL,target_kind TEXT NOT NULL,target_id TEXT NOT NULL,session_id TEXT,part_size INTEGER,state TEXT NOT NULL,progress INTEGER NOT NULL,error TEXT,result TEXT,lease_owner TEXT,lease_expires_at REAL,updated_at REAL NOT NULL)"); try execute("CREATE TABLE IF NOT EXISTS part_acks(upload_id TEXT NOT NULL REFERENCES tasks(upload_id) ON DELETE CASCADE,part_number INTEGER NOT NULL,etag TEXT NOT NULL,bytes INTEGER NOT NULL,PRIMARY KEY(upload_id,part_number))"); try execute("PRAGMA user_version=1") } }
        if version < 2 { try transaction { try execute("CREATE TABLE IF NOT EXISTS pending_acks(upload_id TEXT NOT NULL REFERENCES tasks(upload_id) ON DELETE CASCADE,part_number INTEGER NOT NULL,etag TEXT NOT NULL,bytes INTEGER NOT NULL,PRIMARY KEY(upload_id,part_number))"); try execute("PRAGMA user_version=2") } }
        if version < 3 { try transaction { try execute("CREATE TABLE IF NOT EXISTS part_attempts(upload_id TEXT NOT NULL REFERENCES tasks(upload_id) ON DELETE CASCADE,part_number INTEGER NOT NULL,attempt_id TEXT NOT NULL,temp_path TEXT NOT NULL,task_identifier INTEGER,created_at REAL NOT NULL,PRIMARY KEY(upload_id,part_number,attempt_id))"); try execute("PRAGMA user_version=3") } }
        if version < 4 { try transaction { try execute("CREATE TABLE IF NOT EXISTS work_id_allocator(singleton INTEGER PRIMARY KEY CHECK(singleton=1),next_work_id INTEGER NOT NULL CHECK(next_work_id>0))"); try execute("INSERT OR IGNORE INTO work_id_allocator(singleton,next_work_id) SELECT 1,COALESCE(MAX(work_id),0)+1 FROM tasks"); try execute("PRAGMA user_version=4") } }
    }
    private func tasks(where clause: String, values: [Any?]) throws -> [IOSUploadTask] {
        try query("SELECT upload_id,work_id,created_at,path,name,bytes,type,hash,api,target_kind,target_id,session_id,part_size,state,progress,error,result,lease_owner,lease_expires_at,updated_at FROM tasks WHERE \(clause) ORDER BY created_at", values).compactMap { row in
            guard let id=row[0], let work=Int64(row[1] ?? ""), let created=Double(row[2] ?? ""), let path=row[3], let name=row[4], let bytes=Int64(row[5] ?? ""), let type=row[6], let hash=row[7], let api=row[8], let kind=row[9], let target=row[10], let raw=row[13], let state=BackgroundUploadState(rawValue:raw), let progress=Int(row[14] ?? ""), let updated=Double(row[19] ?? "") else{return nil}
            return IOSUploadTask(uploadID:id,workID:work,createdAt:Date(timeIntervalSince1970:created),file:DurableUploadFile(uploadID:id,path:path,filename:name,contentType:type,bytes:bytes,sha256:hash),api:api,targetKind:kind,targetID:target,sessionID:row[11],partSize:Int64(row[12] ?? ""),state:state,progress:progress,error:row[15],result:row[16],leaseOwner:row[17],leaseExpiresAt:Double(row[18] ?? "").map{Date(timeIntervalSince1970:$0)},updatedAt:Date(timeIntervalSince1970:updated))
        }
    }
    private func transaction<T>(_ block: () throws -> T) throws -> T { try execute("BEGIN IMMEDIATE"); do { let result=try block(); try execute("COMMIT"); return result } catch { try? execute("ROLLBACK"); throw error } }
    private func ownerTransaction(uploadID: String, owner: String, now: Date, _ write: () throws -> Void) throws -> Bool {
        try transaction {
            guard try ownsLease(uploadID: uploadID, owner: owner, now: now) else { return false }
            try write(); return true
        }
    }
    private func ownsLease(uploadID: String, owner: String, now: Date) throws -> Bool { !(try query("SELECT 1 FROM tasks WHERE upload_id=? AND lease_owner=? AND lease_expires_at>=? AND state NOT IN ('completed','failed','cancelled')", [uploadID, owner, stamp(now)])).isEmpty }
    private func execute(_ sql: String, values: [Any?] = []) throws { guard let db else { throw BackgroundUploadError.database("closed") }; var statement: OpaquePointer?; guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK else { throw BackgroundUploadError.database("prepare") }; defer { sqlite3_finalize(statement) }; try bind(values, to: statement); guard sqlite3_step(statement) == SQLITE_DONE else { throw BackgroundUploadError.database("step") } }
    private func update(_ sql: String, _ values: [Any?]) throws -> Bool { guard let db else { throw BackgroundUploadError.database("closed") }; var statement: OpaquePointer?; guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK else { throw BackgroundUploadError.database("prepare") }; defer { sqlite3_finalize(statement) }; try bind(values, to: statement); guard sqlite3_step(statement) == SQLITE_DONE else { throw BackgroundUploadError.database("step") }; return sqlite3_changes(db)>0 }
    private func query(_ sql: String, _ values: [Any?]) throws -> [[String?]] { guard let db else { throw BackgroundUploadError.database("closed") }; var statement: OpaquePointer?; guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK else { throw BackgroundUploadError.database("prepare") }; defer { sqlite3_finalize(statement) }; try bind(values, to: statement); var rows:[[String?]]=[]; while sqlite3_step(statement)==SQLITE_ROW { rows.append((0..<sqlite3_column_count(statement)).map { sqlite3_column_text(statement,$0).map { String(cString:$0) } }) }; return rows }
    private func bind(_ values: [Any?], to statement: OpaquePointer?) throws { for (index,value) in values.enumerated() { let i=Int32(index+1); switch value { case nil: sqlite3_bind_null(statement,i); case let v as String: sqlite3_bind_text(statement,i,v,-1,SQLITE_TRANSIENT); case let v as Int: sqlite3_bind_int64(statement,i,sqlite3_int64(v)); case let v as Int64: sqlite3_bind_int64(statement,i,sqlite3_int64(v)); case let v as Double: sqlite3_bind_double(statement,i,v); default: throw BackgroundUploadError.database("bind") } } }
    private func stamp(_ date: Date) -> Double { date.timeIntervalSince1970 }
}
