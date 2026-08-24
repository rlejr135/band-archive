import Foundation
import XCTest
@testable import App

final class IOSMultipartEngineTests: XCTestCase {
    func testEnqueueUsesInitiateAndStoresOnlyKeychainCapability() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let fileURL = root.appendingPathComponent("video"); try Data([1]).write(to: fileURL)
        let credentials = TestCredentials(); let store = try IOSUploadStore(root: root, credentials: credentials)
        let client = TestClient(responses: [["session_id":"s","part_size":NSNumber(value:16),"upload_capability_token":"capability"]])
        let engine = try IOSMultipartEngine(store: store, credentials: credentials, client: client)
        let file = DurableUploadFile(uploadID:"u",path:fileURL.path,filename:"video.mp4",contentType:"video/mp4",bytes:1,sha256:"x")
        let task = IOSUploadTask(uploadID:"u",workID:1,createdAt:Date(),file:file,api:"https://example.invalid",targetKind:"song_id",targetID:"1",sessionID:nil,partSize:nil,state:.preparing,progress:0,error:nil,result:nil,leaseOwner:nil,leaseExpiresAt:nil,updatedAt:Date())
        try await engine.enqueue(task)
        XCTAssertEqual(try store.task("u")?.sessionID, "s")
        XCTAssertEqual(try credentials.read(uploadID:"u"), "capability")
        XCTAssertFalse(client.requests.first?.value(forHTTPHeaderField:"X-Upload-Capability") != nil)
    }

    func testCoordinatorCoalescesConcurrentPumpsForOneUpload() {
        let coordinator = IOSUploadCoordinator()
        XCTAssertTrue(coordinator.beginReconcile("upload"))
        XCTAssertFalse(coordinator.beginReconcile("upload"))
        XCTAssertTrue(coordinator.finishReconcile("upload"))
        XCTAssertFalse(coordinator.finishReconcile("upload"))
    }

    func testPlannerSkipsRecoveredAndAcknowledgedParts() {
        XCTAssertEqual(MultipartPartPlanner.missingParts(total: 5, remoteAcknowledged: [1, 4], localAcknowledged: [2]), [3, 5])
    }

    func testDescriptorAndRegistryRejectDuplicatePartAndStaleCallback() {
        let upload = UUID().uuidString.lowercased()
        let first = MultipartPartDescriptor(uploadID: upload, part: 1, attemptID: UUID().uuidString.lowercased())
        let replacement = MultipartPartDescriptor(uploadID: upload, part: 1, attemptID: UUID().uuidString.lowercased())
        XCTAssertEqual(MultipartPartDescriptor.parse(first.encoded), first)
        XCTAssertNil(MultipartPartDescriptor.parse("not-a-task-description"))

        let coordinator = IOSUploadCoordinator()
        let root = FileManager.default.temporaryDirectory
        XCTAssertTrue(coordinator.reserve(first, temporaryURL: root.appendingPathComponent("first.part")))
        XCTAssertFalse(coordinator.reserve(replacement, temporaryURL: root.appendingPathComponent("replacement.part")))
        XCTAssertTrue(coordinator.attach(first, taskIdentifier: 11))
        XCTAssertNil(coordinator.finish(taskIdentifier: 12, descriptor: first))
        XCTAssertNotNil(coordinator.finish(taskIdentifier: 11, descriptor: first))
        XCTAssertTrue(coordinator.reserve(replacement, temporaryURL: root.appendingPathComponent("replacement.part")))
    }

    func testPartAttemptPathsAreUniqueAndCanonicalChildren() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let upload = UUID().uuidString
        let first = MultipartPartDescriptor(uploadID: upload, part: 1, attemptID: UUID().uuidString)
        let second = MultipartPartDescriptor(uploadID: upload, part: 1, attemptID: UUID().uuidString)
        let firstURL = try MultipartPartFilePolicy.partURL(root: root, descriptor: first)
        let secondURL = try MultipartPartFilePolicy.partURL(root: root, descriptor: second)
        XCTAssertNotEqual(firstURL, secondURL)
        XCTAssertNotNil(BackgroundUploadFiles.canonicalChild(firstURL, of: root))
    }

    func testAttemptMappingPersistsForBackgroundTaskRecovery() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let credentials = TestCredentials(); let store = try IOSUploadStore(root: root, credentials: credentials)
        let upload = UUID().uuidString.lowercased(); let attempt = UUID().uuidString.lowercased()
        let source = root.appendingPathComponent(upload); try Data([1]).write(to: source)
        let task = IOSUploadTask(uploadID: upload, workID: 1, createdAt: Date(), file: DurableUploadFile(uploadID: upload, path: source.path, filename: "video.mp4", contentType: "video/mp4", bytes: 1, sha256: "x"), api: "https://example.invalid", targetKind: "song_id", targetID: "1", sessionID: "session", partSize: 1, state: .queued, progress: 0, error: nil, result: nil, leaseOwner: nil, leaseExpiresAt: nil, updatedAt: Date())
        try store.insert(task, capability: "capability")
        let expected = IOSPartAttempt(uploadID: upload, part: 2, attemptID: attempt, temporaryPath: root.appendingPathComponent("part").path, taskIdentifier: 41)
        XCTAssertTrue(try store.acquire(upload, owner: "engine"))
        XCTAssertTrue(try store.savePartAttempt(expected, owner: "engine"))
        XCTAssertEqual(try store.partAttempt(uploadID: upload, part: 2, attemptID: attempt), expected)

        let coordinator = IOSUploadCoordinator()
        let descriptor = MultipartPartDescriptor(uploadID: upload, part: 2, attemptID: attempt)
        XCTAssertTrue(coordinator.recover(expected, descriptor: descriptor, taskIdentifier: 41, temporaryURL: URL(fileURLWithPath: expected.temporaryPath)))
        XCTAssertTrue(coordinator.isCurrent(taskIdentifier: 41, descriptor: descriptor))
        XCTAssertFalse(coordinator.recover(expected, descriptor: descriptor, taskIdentifier: 41, temporaryURL: URL(fileURLWithPath: expected.temporaryPath)))
    }
}

private final class TestCredentials: UploadCredentialStoring { var values:[String:String]=[:]; func save(_ token:String,uploadID:String)throws{values[uploadID]=token};func read(uploadID:String)throws->String?{values[uploadID]};func delete(uploadID:String)throws{values.removeValue(forKey:uploadID)} }
private final class TestClient: MultipartHTTPClient { var responses:[[String:Any]]; var requests:[URLRequest]=[]; init(responses:[[String:Any]]){self.responses=responses}; func json(_ request:URLRequest) async throws -> [String:Any] { requests.append(request); return responses.removeFirst() } }
