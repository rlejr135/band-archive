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
}

private final class TestCredentials: UploadCredentialStoring { var values:[String:String]=[:]; func save(_ token:String,uploadID:String)throws{values[uploadID]=token};func read(uploadID:String)throws->String?{values[uploadID]};func delete(uploadID:String)throws{values.removeValue(forKey:uploadID)} }
private final class TestClient: MultipartHTTPClient { var responses:[[String:Any]]; var requests:[URLRequest]=[]; init(responses:[[String:Any]]){self.responses=responses}; func json(_ request:URLRequest) async throws -> [String:Any] { requests.append(request); return responses.removeFirst() } }
