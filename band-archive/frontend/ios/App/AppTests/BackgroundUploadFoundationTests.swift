import Foundation
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

    func testFreshStoreRunsV1MigrationAndStoresAcknowledgedParts() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let credentials = FakeCredentials(); let store = try IOSUploadStore(root: root, credentials: credentials)
        let file = DurableUploadFile(uploadID: "one", path: root.appendingPathComponent("one").path, filename: "one.mov", contentType: "video/quicktime", bytes: 1, sha256: "hash")
        let task = IOSUploadTask(uploadID: "one", workID: 1, createdAt: Date(), file: file, api: "https://example.invalid", targetKind: "media", targetID: "1", sessionID: nil, partSize: nil, state: .queued, progress: 0, error: nil, result: nil, leaseOwner: nil, leaseExpiresAt: nil, updatedAt: Date())
        try store.insert(task, capability: "secret")
        try store.saveAck(uploadID: "one", part: 2, etag: "etag", bytes: 1)
        XCTAssertEqual(try store.acknowledgedParts(uploadID: "one"), [2])
        XCTAssertEqual(try credentials.read(uploadID: "one"), "secret")
    }

    func testCredentialProtocolCanBeFakedWithoutPlaintextPersistence() throws {
        let credentials = FakeCredentials(); try credentials.save("token", uploadID: "upload")
        XCTAssertEqual(try credentials.read(uploadID: "upload"), "token")
        try credentials.delete(uploadID: "upload")
        XCTAssertNil(try credentials.read(uploadID: "upload"))
    }
}

private final class FakeCredentials: UploadCredentialStoring {
    private var values: [String: String] = [:]
    func save(_ token: String, uploadID: String) throws { values[uploadID] = token }
    func read(uploadID: String) throws -> String? { values[uploadID] }
    func delete(uploadID: String) throws { values.removeValue(forKey: uploadID) }
}
