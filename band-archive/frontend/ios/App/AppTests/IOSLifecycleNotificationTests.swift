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

    func testBackgroundSessionCompletionIsDrainedExactlyOnce() {
        guard let engine = IOSMultipartEngine.shared else { XCTFail("background engine unavailable"); return }
        let completion = expectation(description: "background completion")
        XCTAssertTrue(engine.attachBackgroundEvents(identifier: IOSMultipartEngine.backgroundSessionIdentifier) {
            completion.fulfill()
        })
        engine.urlSessionDidFinishEvents(forBackgroundURLSession: .shared)
        wait(for: [completion], timeout: 1)

        let secondCompletion = expectation(description: "no duplicate completion")
        secondCompletion.isInverted = true
        engine.urlSessionDidFinishEvents(forBackgroundURLSession: .shared)
        wait(for: [secondCompletion], timeout: 0.1)
    }

    func testUnknownBackgroundSessionIsNotClaimed() {
        XCTAssertFalse(IOSMultipartEngine.shared?.attachBackgroundEvents(identifier: "other.sdk.session") {} ?? false)
    }
}
