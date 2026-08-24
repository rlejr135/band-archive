import Foundation
import Security
import XCTest
@testable import App

final class IOSLifecycleNotificationTests: XCTestCase {
    func testNotificationPolicyIsRedactedAndWorkScoped() {
        let file = DurableUploadFile(uploadID: "upload", path: "/private/video.mov", filename: "private-video.mov", contentType: "video/mp4", bytes: 1, sha256: "hash")
        let task = IOSUploadTask(uploadID: "upload", workID: 42, createdAt: Date(), file: file, api: "https://secret.invalid", targetKind: "member_id", targetID: "1", sessionID: "session", partSize: 1, state: .failed, progress: 10, error: "credential_lost", result: nil, leaseOwner: nil, leaseExpiresAt: nil, updatedAt: Date())
        XCTAssertEqual(BackgroundUploadNotificationPolicy.identifier(for: 42), "background-upload-42")
        XCTAssertFalse(BackgroundUploadNotificationPolicy.body(for: task).contains("private-video"))
        XCTAssertFalse(BackgroundUploadNotificationPolicy.body(for: task).contains("secret"))
    }

    func testBackgroundSessionIdentifierIsFixed() {
        XCTAssertEqual(IOSMultipartEngine.backgroundSessionIdentifier, "com.deutteun.archive.background-upload.v1")
    }

    func testBackgroundSessionCompletionFIFOHandlesBothArrivalOrders() {
        let state = BackgroundEventDrainState(); var calls: [String] = []
        state.append { calls.append("first") }
        state.markFinishObserved()
        XCTAssertTrue(state.beginDrain())
        let firstSnapshot = state.snapshot()
        state.finishIfStable(revision: firstSnapshot.revision)?()

        // A later wake may report finish before the next AppDelegate callback.
        // Its FIFO credit is consumed once, not inherited from the first wake.
        state.markFinishObserved()
        state.append { calls.append("second") }
        XCTAssertTrue(state.beginDrain())
        let secondSnapshot = state.snapshot()
        state.finishIfStable(revision: secondSnapshot.revision)?()
        XCTAssertEqual(calls, ["first", "second"])
    }

    func testContinuousHandoffsAndDrainRaceStayFIFO() {
        let state = BackgroundEventDrainState()
        var calls: [Int] = []
        state.append { calls.append(1) }; state.markFinishObserved(); XCTAssertTrue(state.beginDrain())
        // A new finish/handler pair arrives while the first durable barrier drains.
        state.markFinishObserved(); state.append { calls.append(2) }
        let first = state.snapshot(); state.finishIfStable(revision: first.revision)?()
        XCTAssertTrue(state.beginDrain())
        let second = state.snapshot(); state.finishIfStable(revision: second.revision)?()
        XCTAssertEqual(calls, [1, 2])
    }

    func testUnknownBackgroundSessionIsNotClaimed() {
        XCTAssertFalse(IOSMultipartEngine.shared?.attachBackgroundEvents(identifier: "other.sdk.session") {} ?? false)
    }

    func testDrainWaitsForDurableDelegateBarrierAndDrainsMultipleHandoffsOnce() {
        XCTAssertGreaterThan(BackgroundEventDrainPolicy.networkBudget, 0)
        XCTAssertLessThanOrEqual(BackgroundEventDrainPolicy.networkBudget, 5)
        let state = BackgroundEventDrainState()
        var calls = 0
        state.append { calls += 1 }
        state.append { calls += 1 }
        state.markFinishObserved()
        state.markFinishObserved()
        state.beginDelegateWrite()
        XCTAssertTrue(state.beginDrain())
        let beforeCommit = state.snapshot()
        XCTAssertNil(state.finishIfStable(revision: beforeCommit.revision))
        // Production didCompleteWithError stores pending ACK before ending this barrier.
        state.endDelegateWrite()
        let committed = state.snapshot()
        let handler = state.finishIfStable(revision: committed.revision)
        handler?()
        XCTAssertEqual(calls, 1)
        XCTAssertTrue(state.beginDrain())
        let secondHandler = state.finishIfStable(revision: committed.revision)
        secondHandler?()
        XCTAssertEqual(calls, 2)
    }

    func testKeychainReadPolicyKeepsLockedDeviceRetryable() {
        XCTAssertEqual(UploadCredentialPolicy.readError(for: errSecItemNotFound), .lost)
        XCTAssertEqual(UploadCredentialPolicy.readError(for: errSecInteractionNotAllowed), .unavailable)
        XCTAssertEqual(UploadCredentialPolicy.readError(for: errSecNotAvailable), .unavailable)
    }

    func testEngineProviderRetriesAfterProtectedDataFailure() {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let credentials = RecoveryCredentials()
        var attempts = 0; var notifications = 0
        let provider = IOSMultipartEngineProvider(factory: {
            attempts += 1
            if attempts == 1 { throw BackgroundUploadError.database("protected_data_unavailable") }
            return try IOSMultipartEngine(store: IOSUploadStore(root: root, credentials: credentials), credentials: credentials, client: RecoveryClient())
        })
        let token = provider.observe { _ in notifications += 1 }
        XCTAssertNil(provider.acquire())
        let recovered = provider.acquire()
        XCTAssertNotNil(recovered)
        XCTAssertTrue(provider.acquire() === recovered)
        XCTAssertEqual(attempts, 2)
        XCTAssertEqual(notifications, 1)
        provider.removeObserver(token)
    }
}

private final class RecoveryCredentials: UploadCredentialStoring {
    func save(_ token: String, uploadID: String) throws {}
    func read(uploadID: String) throws -> String? { nil }
    func delete(uploadID: String) throws {}
}
private final class RecoveryClient: MultipartHTTPClient {
    func json(_ request: URLRequest) async throws -> [String: Any] { [:] }
}
