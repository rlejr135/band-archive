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

struct PickedVideo: Equatable {
    /// This URL is only an internal, short-lived NSItemProvider handle; callers must durable-copy it before scheduling.
    let handle: URL
    let filename: String
    let contentType: String
    let declaredBytes: Int64?
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

    static func copyDurably(_ picked: PickedVideo, uploadID: String = UUID().uuidString, fileManager: FileManager = .default) async throws -> DurableUploadFile {
        let root = try directory(fileManager: fileManager)
        guard picked.declaredBytes.map({ $0 <= maximumBytes }) ?? true else { throw BackgroundUploadError.fileTooLarge }
        let available = try root.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]).volumeAvailableCapacityForImportantUsage
        let required = (picked.declaredBytes ?? 0) + freeSpaceHeadroom
        guard (available ?? 0) >= required else { throw BackgroundUploadError.insufficientSpace }

        return try await Task.detached(priority: .userInitiated) {
            let destination = root.appendingPathComponent(uploadID, isDirectory: false)
            let temporary = root.appendingPathComponent("\(uploadID).tmp", isDirectory: false)
            guard canonicalChild(destination, of: root) != nil, canonicalChild(temporary, of: root) != nil else { throw BackgroundUploadError.unsafePath }
            defer { try? fileManager.removeItem(at: temporary) }
            let secured = picked.handle.startAccessingSecurityScopedResource()
            defer { if secured { picked.handle.stopAccessingSecurityScopedResource() } }
            guard fileManager.fileExists(atPath: picked.handle.path) else { throw BackgroundUploadError.unavailableFile }
            let input = try FileHandle(forReadingFrom: picked.handle)
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
            guard picked.declaredBytes == nil || picked.declaredBytes == total else { throw BackgroundUploadError.unavailableFile }
            // Same-directory move is atomic on the app volume; upload IDs make a destination collision invalid.
            try fileManager.moveItem(at: temporary, to: destination)
            let fingerprint = digest.finalize().map { String(format: "%02x", $0) }.joined()
            return DurableUploadFile(uploadID: uploadID, path: destination.path, filename: picked.filename, contentType: picked.contentType, bytes: total, sha256: fingerprint)
        }.value
    }
}

@MainActor
final class VideoPicker: NSObject, PHPickerViewControllerDelegate {
    typealias Completion = ([Result<PickedVideo, Error>]) -> Void
    private var completion: Completion?

    func present(from controller: UIViewController, completion: @escaping Completion) {
        var config = PHPickerConfiguration(photoLibrary: .shared())
        config.filter = .videos
        config.selectionLimit = 0
        config.preferredAssetRepresentationMode = .current
        self.completion = completion
        controller.present(PHPickerViewController(configuration: config), animated: true)
    }

    func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
        picker.dismiss(animated: true)
        let completion = self.completion
        self.completion = nil
        Task {
            let selections = await withTaskGroup(of: Result<PickedVideo, Error>.self) { group in
                for result in results { group.addTask { await Self.load(result.itemProvider) } }
                var values: [Result<PickedVideo, Error>] = []
                for await value in group { values.append(value) }
                return values
            }
            completion?(selections)
        }
    }

    private static func load(_ provider: NSItemProvider) async -> Result<PickedVideo, Error> {
        let type = UTType.movie.identifier
        guard provider.hasItemConformingToTypeIdentifier(type) else { return .failure(BackgroundUploadError.unavailableFile) }
        return await withCheckedContinuation { continuation in
            provider.loadFileRepresentation(forTypeIdentifier: type) { url, error in
                if let error { continuation.resume(returning: .failure(error)); return }
                guard let url else { continuation.resume(returning: .failure(BackgroundUploadError.unavailableFile)); return }
                let values = try? url.resourceValues(forKeys: [.fileSizeKey])
                let filename = provider.suggestedName ?? url.lastPathComponent
                continuation.resume(returning: .success(PickedVideo(handle: url, filename: filename, contentType: type, declaredBytes: values?.fileSize.map { Int64($0) })))
            }
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
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data, let token = String(data: data, encoding: .utf8) else { throw BackgroundUploadError.keychain(status) }
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
    func acquire(_ id: String, owner: String, now: Date = Date()) throws -> Bool { try update("UPDATE tasks SET lease_owner=?,lease_expires_at=? WHERE upload_id=? AND state NOT IN ('completed','failed','cancelled') AND (lease_owner IS NULL OR lease_expires_at<? OR lease_owner=?)", [owner, stamp(now.addingTimeInterval(120)), id, stamp(now), owner]) }
    /// Engine writes are compare-and-set: a cancelled/terminal row or different lease owner cannot be overwritten.
    func updateForOwner(_ task: IOSUploadTask, owner: String, now: Date = Date()) throws -> Bool {
        guard !task.state.isTerminal else { return false }
        return try update("UPDATE tasks SET session_id=?,part_size=?,state=?,progress=?,error=?,result=?,lease_expires_at=?,updated_at=? WHERE upload_id=? AND lease_owner=? AND lease_expires_at>=? AND state NOT IN ('completed','failed','cancelled')", [task.sessionID, task.partSize, task.state.rawValue, task.progress, task.error, task.result, stamp(now.addingTimeInterval(120)), stamp(now), task.uploadID, owner, stamp(now)])
    }
    func cancel(_ id: String) throws -> Bool { try transaction { try update("UPDATE tasks SET state='cancelled',lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE upload_id=? AND state NOT IN ('completed','failed','cancelled')", [stamp(Date()), id]) } }
    func acknowledge(_ id: String) throws -> Bool { try transaction { let changed = try update("DELETE FROM tasks WHERE upload_id=? AND state IN ('completed','failed','cancelled')", [id]); if changed { try credentials.delete(uploadID: id) }; return changed } }
    func saveAck(uploadID: String, part: Int, etag: String, bytes: Int64) throws { try execute("INSERT OR REPLACE INTO part_acks(upload_id,part_number,etag,bytes) VALUES(?,?,?,?)", values: [uploadID, part, etag, bytes]) }
    func acknowledgedParts(uploadID: String) throws -> Set<Int> {
        let values = try query("SELECT part_number FROM part_acks WHERE upload_id=?", [uploadID]).compactMap { Int($0[0] ?? "") }
        return Set(values)
    }
    func garbageCollect(now: Date = Date(), fileManager: FileManager = .default) throws {
        let stale = stamp(now.addingTimeInterval(-Self.retention)); let rows = try query("SELECT upload_id,path FROM tasks WHERE state IN ('completed','failed','cancelled') AND updated_at<=? AND (lease_expires_at IS NULL OR lease_expires_at<?)", [stale, stamp(now)])
        for row in rows { guard let id=row[0], let path=row[1] else { continue }; if let file=BackgroundUploadFiles.canonicalChild(URL(fileURLWithPath: path), of: root) { try? fileManager.removeItem(at: file) }; _ = try acknowledge(id) }
    }

    private func migrate() throws {
        let version = Int(try query("PRAGMA user_version", []).first?.first ?? "0") ?? 0
        if version < 1 { try transaction { try execute("CREATE TABLE IF NOT EXISTS tasks(upload_id TEXT PRIMARY KEY,work_id INTEGER NOT NULL UNIQUE CHECK(work_id>0),created_at REAL NOT NULL,path TEXT NOT NULL,name TEXT NOT NULL,bytes INTEGER NOT NULL,type TEXT NOT NULL,hash TEXT NOT NULL,api TEXT NOT NULL,target_kind TEXT NOT NULL,target_id TEXT NOT NULL,session_id TEXT,part_size INTEGER,state TEXT NOT NULL,progress INTEGER NOT NULL,error TEXT,result TEXT,lease_owner TEXT,lease_expires_at REAL,updated_at REAL NOT NULL)"); try execute("CREATE TABLE IF NOT EXISTS part_acks(upload_id TEXT NOT NULL REFERENCES tasks(upload_id) ON DELETE CASCADE,part_number INTEGER NOT NULL,etag TEXT NOT NULL,bytes INTEGER NOT NULL,PRIMARY KEY(upload_id,part_number))"); try execute("PRAGMA user_version=1") } }
    }
    private func transaction<T>(_ block: () throws -> T) throws -> T { try execute("BEGIN IMMEDIATE"); do { let result=try block(); try execute("COMMIT"); return result } catch { try? execute("ROLLBACK"); throw error } }
    private func execute(_ sql: String, values: [Any?] = []) throws { guard let db else { throw BackgroundUploadError.database("closed") }; var statement: OpaquePointer?; guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK else { throw BackgroundUploadError.database("prepare") }; defer { sqlite3_finalize(statement) }; try bind(values, to: statement); guard sqlite3_step(statement) == SQLITE_DONE else { throw BackgroundUploadError.database("step") } }
    private func update(_ sql: String, _ values: [Any?]) throws -> Bool { guard let db else { throw BackgroundUploadError.database("closed") }; var statement: OpaquePointer?; guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK else { throw BackgroundUploadError.database("prepare") }; defer { sqlite3_finalize(statement) }; try bind(values, to: statement); guard sqlite3_step(statement) == SQLITE_DONE else { throw BackgroundUploadError.database("step") }; return sqlite3_changes(db)>0 }
    private func query(_ sql: String, _ values: [Any?]) throws -> [[String?]] { guard let db else { throw BackgroundUploadError.database("closed") }; var statement: OpaquePointer?; guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK else { throw BackgroundUploadError.database("prepare") }; defer { sqlite3_finalize(statement) }; try bind(values, to: statement); var rows:[[String?]]=[]; while sqlite3_step(statement)==SQLITE_ROW { rows.append((0..<sqlite3_column_count(statement)).map { sqlite3_column_text(statement,$0).map { String(cString:$0) } }) }; return rows }
    private func bind(_ values: [Any?], to statement: OpaquePointer?) throws { for (index,value) in values.enumerated() { let i=Int32(index+1); switch value { case nil: sqlite3_bind_null(statement,i); case let v as String: sqlite3_bind_text(statement,i,v,-1,SQLITE_TRANSIENT); case let v as Int: sqlite3_bind_int64(statement,i,sqlite3_int64(v)); case let v as Int64: sqlite3_bind_int64(statement,i,sqlite3_int64(v)); case let v as Double: sqlite3_bind_double(statement,i,v); default: throw BackgroundUploadError.database("bind") } } }
    private func stamp(_ date: Date) -> Double { date.timeIntervalSince1970 }
}
