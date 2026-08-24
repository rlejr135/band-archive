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

    func testBackgroundSessionCompletionIsDrainedExactlyOncePerGeneration() {
        let state = BackgroundEventDrainState(); var calls: [String] = []
        let first = state.append { calls.append("first") }
        XCTAssertEqual(state.markFinishObserved(), first)
        XCTAssertTrue(state.beginDrain(generation: first))
        let firstSnapshot = state.snapshot()
        state.finishIfStable(generation: first, revision: firstSnapshot.revision)?.forEach { $0() }

        // A stale finish from the consumed generation must not make this new
        // AppDelegate completion runnable before its own finish callback.
        XCTAssertNil(state.markFinishObserved())
        let second = state.append { calls.append("second") }
        XCTAssertFalse(state.beginDrain(generation: second))
        XCTAssertEqual(state.markFinishObserved(), second)
        XCTAssertTrue(state.beginDrain(generation: second))
        let secondSnapshot = state.snapshot()
        state.finishIfStable(generation: second, revision: secondSnapshot.revision)?.forEach { $0() }
        XCTAssertEqual(calls, ["first", "second"])
    }

    func testFinishBeforeFirstAttachBindsOnlyThatGeneration() {
        let state = BackgroundEventDrainState()
        XCTAssertNil(state.markFinishObserved())
        let first = state.append {}
        XCTAssertTrue(state.beginDrain(generation: first))
        let snapshot = state.snapshot()
        XCTAssertNotNil(state.finishIfStable(generation: first, revision: snapshot.revision))
        let second = state.append {}
        XCTAssertFalse(state.beginDrain(generation: second))
    }

    func testFinishAndNextAttachRaceWhilePreviousGenerationDrains() {
        let state = BackgroundEventDrainState()
        let first = state.append {}
        XCTAssertEqual(state.markFinishObserved(), first)
        XCTAssertTrue(state.beginDrain(generation: first))
        // The next URLSession finish can precede its AppDelegate callback while
        // the previous completion is still awaiting the durable barrier.
        XCTAssertNil(state.markFinishObserved())
        let second = state.append {}
        XCTAssertTrue(state.beginDrain(generation: second))
    }

    func testUnknownBackgroundSessionIsNotClaimed() {
        XCTAssertFalse(IOSMultipartEngine.shared?.attachBackgroundEvents(identifier: "other.sdk.session") {} ?? false)
    }

    func testDrainWaitsForDurableDelegateBarrierAndDrainsMultipleHandoffsOnce() {
        XCTAssertGreaterThan(BackgroundEventDrainPolicy.networkBudget, 0)
        XCTAssertLessThanOrEqual(BackgroundEventDrainPolicy.networkBudget, 5)
        let state = BackgroundEventDrainState()
        var calls = 0
        let first = state.append { calls += 1 }
        let second = state.append { calls += 1 }
        XCTAssertEqual(state.markFinishObserved(), first)
        XCTAssertEqual(state.markFinishObserved(), second)
        state.beginDelegateWrite()
        XCTAssertTrue(state.beginDrain(generation: first))
        let beforeCommit = state.snapshot()
        XCTAssertNil(state.finishIfStable(generation: first, revision: beforeCommit.revision))
        // Production didCompleteWithError stores pending ACK before ending this barrier.
        state.endDelegateWrite()
        let committed = state.snapshot()
        let handlers = state.finishIfStable(generation: first, revision: committed.revision)
        handlers?.forEach { $0() }
        XCTAssertEqual(calls, 1)
        XCTAssertTrue(state.beginDrain(generation: second))
        let secondHandlers = state.finishIfStable(generation: second, revision: committed.revision)
        secondHandlers?.forEach { $0() }
        XCTAssertEqual(calls, 2)
    }

    func testKeychainReadPolicyKeepsLockedDeviceRetryable() {
        XCTAssertEqual(UploadCredentialPolicy.readError(for: errSecItemNotFound), .lost)
        XCTAssertEqual(UploadCredentialPolicy.readError(for: errSecInteractionNotAllowed), .unavailable)
        XCTAssertEqual(UploadCredentialPolicy.readError(for: errSecNotAvailable), .unavailable)
    }

    func testEngineProviderRetriesAfterProtectedDataFailure() {
        var attempts = 0
        let provider = IOSMultipartEngineProvider(factory: {
            attempts += 1
            throw BackgroundUploadError.database("protected_data_unavailable")
        })
        XCTAssertNil(provider.acquire())
        // A second foreground/protected-data notification gets another factory
        // attempt rather than inheriting a static `nil` from the first call.
        XCTAssertNil(provider.acquire())
        XCTAssertEqual(attempts, 2)
    }
}
