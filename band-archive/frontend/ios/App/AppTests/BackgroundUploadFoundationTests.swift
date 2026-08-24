import Foundation
import SQLite3
import XCTest
@testable import App

final class BackgroundUploadFoundationTests: XCTestCase {
    func testStatePolicySeparatesTerminalAndRunnableStates() {
        XCTAssertTrue(BackgroundUploadState.completed.isTerminal)
        XCTAssertTrue(BackgroundUploadState.cancelled.isTerminal)
        XCTAssertFalse(BackgroundUploadState.processing.isTerminal)
        XCTAssertTrue(BackgroundUploadState.retryWait.isRunnable)
        XCTAssertFalse(BackgroundUploadState.processing.isRunnable)
    }

    func testCanonicalChildRejectsTraversalAndAcceptsDurableChild() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        XCTAssertNotNil(BackgroundUploadFiles.canonicalChild(root.appendingPathComponent("safe.mov"), of: root))
        XCTAssertNil(BackgroundUploadFiles.canonicalChild(root.appendingPathComponent("../outside.mov"), of: root))
    }

    func testSizeBoundaryIsOneGiB() {
        XCTAssertEqual(BackgroundUploadFiles.maximumBytes, 1_024 * 1_024 * 1_024)
        XCTAssertGreaterThan(BackgroundUploadFiles.freeSpaceHeadroom, 0)
    }

    func testSynchronousProviderCopyRemainsAfterTheTemporarySourceIsRemoved() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let temporaryProviderFile = root.appendingPathComponent("provider.mov")
        try Data([1, 2, 3]).write(to: temporaryProviderFile)
        let durable = try BackgroundUploadFiles.copyDurably(from: temporaryProviderFile, filename: "provider.mov", contentType: "video/quicktime", declaredBytes: 3, uploadID: "durable", root: root)
        try FileManager.default.removeItem(at: temporaryProviderFile)
        XCTAssertTrue(FileManager.default.fileExists(atPath: durable.path))
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: durable.path)), Data([1, 2, 3]))
    }

    func testVideoPickerPolicyUsesVideoMIMEAndHonorsMultipleChoice() {
        XCTAssertEqual(VideoPickerPolicy.mimeType(filename: "clip.mov"), "video/quicktime")
        XCTAssertEqual(VideoPickerPolicy.mimeType(filename: "clip.mp4"), "video/mp4")
        XCTAssertEqual(VideoPickerPolicy.selectionLimit(multiple: true), 0)
        XCTAssertEqual(VideoPickerPolicy.selectionLimit(multiple: false), 1)
    }

    func testFreshStoreRunsV1MigrationAndStoresAcknowledgedParts() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let credentials = FakeCredentials(); let store = try IOSUploadStore(root: root, credentials: credentials)
        let file = DurableUploadFile(uploadID: "one", path: root.appendingPathComponent("one").path, filename: "one.mov", contentType: "video/quicktime", bytes: 1, sha256: "hash")
        let task = IOSUploadTask(uploadID: "one", workID: 1, createdAt: Date(), file: file, api: "https://example.invalid", targetKind: "media", targetID: "1", sessionID: nil, partSize: nil, state: .queued, progress: 0, error: nil, result: nil, leaseOwner: nil, leaseExpiresAt: nil, updatedAt: Date())
        try store.insert(task, capability: "secret")
        XCTAssertTrue(try store.acquire("one", owner: "engine"))
        XCTAssertTrue(try store.saveAck(uploadID: "one", part: 2, etag: "etag", bytes: 1, owner: "engine"))
        XCTAssertEqual(try store.acknowledgedParts(uploadID: "one"), [2])
        XCTAssertEqual(try credentials.read(uploadID: "one"), "secret")
    }

    func testCredentialProtocolCanBeFakedWithoutPlaintextPersistence() throws {
        let credentials = FakeCredentials(); try credentials.save("token", uploadID: "upload")
        XCTAssertEqual(try credentials.read(uploadID: "upload"), "token")
        try credentials.delete(uploadID: "upload")
        XCTAssertNil(try credentials.read(uploadID: "upload"))
    }

    func testOwnerCASDeniesStaleWritesAndAckAfterCancel() throws {
        let (store, task) = try makeStoreTask()
        XCTAssertTrue(try store.acquire(task.uploadID, owner: "owner-a"))
        XCTAssertTrue(try store.cancel(task.uploadID))
        XCTAssertFalse(try store.renew(task.uploadID, owner: "owner-a"))
        var stale = task; stale.state = .retryWait; stale.error = "stale"; stale.updatedAt = Date()
        XCTAssertFalse(try store.updateForOwner(stale, owner: "owner-a"))
        XCTAssertFalse(try store.savePendingAck(uploadID: task.uploadID, part: 1, etag: "etag", bytes: 1, owner: "owner-a"))
        XCTAssertEqual(try store.task(task.uploadID)?.state, .cancelled)
    }

    func testOwnerMismatchTerminalOverwriteAndLeaseExpiryTakeover() throws {
        let (store, task) = try makeStoreTask()
        let now = Date(timeIntervalSince1970: 1_000)
        XCTAssertTrue(try store.acquire(task.uploadID, owner: "owner-a", now: now))
        var changed = task; changed.state = .uploading; changed.updatedAt = now
        XCTAssertFalse(try store.updateForOwner(changed, owner: "owner-b", now: now))
        XCTAssertTrue(try store.acquire(task.uploadID, owner: "owner-b", now: now.addingTimeInterval(121)))
        changed.state = .completed
        XCTAssertTrue(try store.updateForOwner(changed, owner: "owner-b", now: now.addingTimeInterval(121)))
        changed.state = .retryWait
        XCTAssertFalse(try store.updateForOwner(changed, owner: "owner-b", now: now.addingTimeInterval(122)))
    }

    func testPersistentWorkIDsArePositiveUniqueAndSurviveReopen() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let first = try IOSUploadStore(root: root, credentials: FakeCredentials())
        XCTAssertEqual(try first.allocateWorkID(), 1)
        XCTAssertEqual(try first.allocateWorkID(), 2)
        let reopened = try IOSUploadStore(root: root, credentials: FakeCredentials())
        XCTAssertEqual(try reopened.allocateWorkID(), 3)
    }

    func testV3MigrationSeedsPersistentWorkIDAllocator() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let path = root.appendingPathComponent("background_upload.sqlite").path
        var database: OpaquePointer?
        XCTAssertEqual(sqlite3_open(path, &database), SQLITE_OK)
        defer { sqlite3_close(database) }
        XCTAssertEqual(sqlite3_exec(database, "CREATE TABLE tasks(work_id INTEGER NOT NULL UNIQUE CHECK(work_id>0)); INSERT INTO tasks(work_id) VALUES(41); PRAGMA user_version=3;", nil, nil, nil), SQLITE_OK)
        let store = try IOSUploadStore(root: root, credentials: FakeCredentials())
        XCTAssertEqual(try store.allocateWorkID(), 42)
    }

    func testPendingAckIsDurableBeforeBackgroundDrainCompletion() throws {
        let (store, task) = try makeStoreTask()
        XCTAssertTrue(try store.acquire(task.uploadID, owner: "engine"))
        let drain = BackgroundEventDrainState(); var completionCalls = 0
        drain.append { completionCalls += 1 }; drain.beginDelegateWrite(); XCTAssertTrue(drain.beginDrain())
        XCTAssertTrue(try store.savePendingAck(uploadID: task.uploadID, part: 1, etag: "etag", bytes: 1, owner: "engine"))
        drain.endDelegateWrite()
        let stable = drain.snapshot(); drain.finishIfStable(revision: stable.revision)?.forEach { $0() }
        XCTAssertEqual(try store.pendingAcks(uploadID: task.uploadID).count, 1)
        XCTAssertEqual(completionCalls, 1)
    }

    private func makeStoreTask() throws -> (IOSUploadStore, IOSUploadTask) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let store = try IOSUploadStore(root: root, credentials: FakeCredentials())
        let id = UUID().uuidString; let path = root.appendingPathComponent(id)
        try Data([1]).write(to: path)
        let task = IOSUploadTask(uploadID: id, workID: try store.allocateWorkID(), createdAt: Date(), file: DurableUploadFile(uploadID: id, path: path.path, filename: "video.mp4", contentType: "video/mp4", bytes: 1, sha256: "x"), api: "https://example.invalid", targetKind: "member_id", targetID: "1", sessionID: "session", partSize: 1, state: .queued, progress: 0, error: nil, result: nil, leaseOwner: nil, leaseExpiresAt: nil, updatedAt: Date())
        try store.insert(task, capability: "secret")
        return (store, task)
    }
}

private final class FakeCredentials: UploadCredentialStoring {
    private var values: [String: String] = [:]
    func save(_ token: String, uploadID: String) throws { values[uploadID] = token }
    func read(uploadID: String) throws -> String? { values[uploadID] }
    func delete(uploadID: String) throws { values.removeValue(forKey: uploadID) }
}
