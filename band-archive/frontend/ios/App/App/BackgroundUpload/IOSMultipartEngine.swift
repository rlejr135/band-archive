import Foundation

protocol MultipartHTTPClient {
    func json(_ request: URLRequest) async throws -> [String: Any]
}

final class URLSessionMultipartHTTPClient: MultipartHTTPClient {
    func json(_ request: URLRequest) async throws -> [String: Any] {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode), let body = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw URLError(.badServerResponse) }
        return body
    }
}

/// Shared background-session multipart executor. It stores no credentials or presigned URLs in SQLite/task descriptions.
final class IOSMultipartEngine: NSObject, URLSessionTaskDelegate, URLSessionDataDelegate {
    static let shared = IOSMultipartEngine()
    private let owner = UUID().uuidString
    private let store: IOSUploadStore
    private let credentials: UploadCredentialStoring
    private let client: MultipartHTTPClient
    private let queue = DispatchQueue(label: "com.deutteun.archive.background-upload")
    private var etags: [Int: String] = [:]
    private var active: Set<String> = []
    private var session: URLSession!
    var event: ((IOSUploadTask) -> Void)?

    override convenience init() { try! self.init(store: IOSUploadStore(), credentials: KeychainUploadCredentialStore(), client: URLSessionMultipartHTTPClient()) }
    init(store: IOSUploadStore, credentials: UploadCredentialStoring, client: MultipartHTTPClient) throws {
        self.store = store; self.credentials = credentials; self.client = client
        super.init()
        let configuration = URLSessionConfiguration.background(withIdentifier: "com.deutteun.archive.background-upload.v1")
        configuration.sessionSendsLaunchEvents = true
        configuration.isDiscretionary = false
        session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
    }

    func enqueue(_ task: IOSUploadTask) async throws {
        var payload: [String: Any] = ["filename": task.file.filename, "content_type": task.file.contentType, "declared_bytes": task.file.bytes]
        if task.targetKind == "media", let data = task.targetID.data(using: .utf8), let media = try? JSONSerialization.jsonObject(with: data) as? [String: Any] { payload.merge(media) { _, newer in newer } }
        else { payload[task.targetKind] = Int(task.targetID) ?? task.targetID }
        let response = try await request(task.api + "/uploads/multipart/initiate", method: "POST", body: payload, capability: nil)
        guard let sessionID = response["session_id"] as? String, let partSize = (response["part_size"] as? NSNumber)?.int64Value, let capability = response["upload_capability_token"] as? String else { throw URLError(.cannotParseResponse) }
        var persisted = task; persisted.sessionID = sessionID; persisted.partSize = partSize; persisted.state = .queued; persisted.updatedAt = Date()
        try store.insert(persisted, capability: capability)
        publish(persisted); pump(persisted.uploadID)
    }

    func pump(_ uploadID: String? = nil) {
        queue.async { [weak self] in
            guard let self else { return }
            let tasks = (try? self.store.retainedTasks()) ?? []
            for task in tasks where (uploadID == nil || task.uploadID == uploadID) && task.state.isRunnable { Task { await self.reconcile(task.uploadID) } }
        }
    }

    private func reconcile(_ id: String) async {
        guard var task = try? store.task(id), !task.state.isTerminal, try! store.acquire(id, owner: owner) else { return }
        guard let sessionID = task.sessionID else { return } // A capability/session is never regenerated after durable enqueue.
        guard let capability = try? credentials.read(uploadID: id), let capability else { task.state = .failed; task.error = "credential_lost"; task.updatedAt = Date(); try? store.updateLocal(task); publish(task); return }
        do {
            for (part, etag, bytes) in try store.pendingAcks(uploadID: id) { _ = try await request(task.api + "/uploads/multipart/\(sessionID)/parts/\(part)/ack", method: "POST", body: ["etag": etag, "bytes": bytes], capability: capability); try store.saveAck(uploadID: id, part: part, etag: etag, bytes: bytes); try store.clearPendingAck(uploadID: id, part: part) }
            let remote = try await request(task.api + "/uploads/multipart/\(sessionID)", method: "GET", body: nil, capability: capability)
            let remoteState = remote["status"] as? String ?? ""
            if remoteState == "completed" { try finish(&task, response: remote); return }
            if ["expired", "aborted", "failed"].contains(remoteState) { task.state = .failed; task.error = "session_\(remoteState)"; task.updatedAt = Date(); try store.updateLocal(task); publish(task); return }
            let partSize = (remote["part_size"] as? NSNumber)?.int64Value ?? task.partSize ?? 0
            guard partSize > 0 else { throw URLError(.cannotParseResponse) }
            let remoteAcked = Set(((remote["parts"] as? [[String: Any]]) ?? []).compactMap { ($0["status"] as? String) == "acknowledged" ? ($0["part_number"] as? NSNumber)?.intValue : nil })
            let localAcked = try store.acknowledgedParts(uploadID: id)
            let count = Int((task.file.bytes + partSize - 1) / partSize)
            let missing = (1...count).filter { !remoteAcked.contains($0) && !localAcked.contains($0) }
            if missing.isEmpty && (try store.pendingAcks(uploadID: id)).isEmpty && !hasActive(id) { task.state = .completing; task.updatedAt = Date(); try store.updateLocal(task); let result = try await request(task.api + "/uploads/multipart/\(sessionID)/complete", method: "POST", body: [:], capability: capability); try finish(&task, response: result); return }
            for part in missing where active.filter({ $0.hasPrefix(id + "|") }).count < 2 { try await schedulePart(task, part: part, partSize: partSize, capability: capability) }
        } catch {
            task.state = .retryWait; task.error = "network_retry"; task.updatedAt = Date(); try? store.updateForOwner(task, owner: owner); publish(task)
        }
    }

    private func schedulePart(_ task: IOSUploadTask, part: Int, partSize: Int64, capability: String) async throws {
        guard let sessionID = task.sessionID, !hasActive("\(task.uploadID)|\(part)") else { return }
        let issued = try await request(task.api + "/uploads/multipart/\(sessionID)/parts", method: "POST", body: ["part_number": part], capability: capability)
        guard let text = issued["upload_url"] as? String, let url = URL(string: text) else { throw URLError(.badURL) }
        let offset = Int64(part - 1) * partSize, length = min(partSize, task.file.bytes - offset)
        let partFile = try makePartFile(task: task, part: part, offset: offset, length: length)
        var request = URLRequest(url: url); request.httpMethod = "PUT"; request.setValue(task.file.contentType, forHTTPHeaderField: "Content-Type")
        let upload = session.uploadTask(with: request, fromFile: partFile)
        upload.taskDescription = "\(task.uploadID)|\(part)" // only opaque IDs, never URL/token.
        active.insert(upload.taskDescription!); upload.resume()
    }

    private func makePartFile(task: IOSUploadTask, part: Int, offset: Int64, length: Int64) throws -> URL {
        let root = try BackgroundUploadFiles.directory(); let source = URL(fileURLWithPath: task.file.path)
        guard BackgroundUploadFiles.canonicalChild(source, of: root) != nil else { throw BackgroundUploadError.unsafePath }
        let output = root.appendingPathComponent("\(task.uploadID).\(part).part")
        let inputHandle = try FileHandle(forReadingFrom: source); defer { try? inputHandle.close() }
        try inputHandle.seek(toOffset: UInt64(offset)); FileManager.default.createFile(atPath: output.path, contents: nil)
        let out = try FileHandle(forWritingTo: output); defer { try? out.close() }
        var left = length; while left > 0 { let data = try inputHandle.read(upToCount: Int(min(left, 64 * 1024))) ?? Data(); guard !data.isEmpty else { throw BackgroundUploadError.unavailableFile }; try out.write(contentsOf: data); left -= Int64(data.count) }; try out.synchronize(); return output
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didReceive response: URLResponse, completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) { if let http=response as? HTTPURLResponse, let etag=http.value(forHTTPHeaderField:"ETag") { etags[task.taskIdentifier]=etag.trimmingCharacters(in: CharacterSet(charactersIn:"\"")) }; completionHandler(.allow) }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let description=task.taskDescription else{return}; active.remove(description); let parts=description.split(separator:"|",maxSplits:1).map(String.init); guard parts.count==2, let part=Int(parts[1]) else{return}; defer { try? FileManager.default.removeItem(at: try BackgroundUploadFiles.directory().appendingPathComponent("\(parts[0]).\(part).part")) }
        guard error == nil, let response=task.response as? HTTPURLResponse, (200..<300).contains(response.statusCode), let etag=etags.removeValue(forKey:task.taskIdentifier), let upload=try? store.task(parts[0]) else { pump(parts[0]); return }
        let size = min(upload.partSize ?? upload.file.bytes, upload.file.bytes - Int64(part - 1) * (upload.partSize ?? upload.file.bytes)); try? store.savePendingAck(uploadID:parts[0],part:part,etag:etag,bytes:size); pump(parts[0])
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64, totalBytesSent: Int64, totalBytesExpectedToSend: Int64) { guard let description=task.taskDescription else{return}; let pieces=description.split(separator:"|",maxSplits:1).map(String.init); guard pieces.count==2, let part=Int(pieces[1]), var upload=try? store.task(pieces[0]) else{return}; let sent=Int64(part-1)*(upload.partSize ?? upload.file.bytes)+totalBytesSent; upload.progress=max(upload.progress,min(99,Int(sent*100/max(1,upload.file.bytes)))); upload.state = .uploading; upload.updatedAt=Date(); try? store.updateLocal(upload); publish(upload) }

    func cancel(_ id: String) { session.getAllTasks { tasks in tasks.filter{$0.taskDescription?.hasPrefix(id + "|") == true}.forEach{$0.cancel()} }; if let task=try? store.task(id), let sessionID=task.sessionID, let capability=try? credentials.read(uploadID:id), let capability { Task { _=try? await self.request(task.api+"/uploads/multipart/\(sessionID)/abort",method:"POST",body:[:],capability:capability) } }; _=try? store.cancel(id); if let task=try? store.task(id){publish(task)} }
    func acknowledge(_ id: String) -> Bool { (try? store.acknowledge(id)) ?? false }
    func retry(_ id: String) -> Bool { guard let task = try? store.task(id), task.state == .retryWait, !active.contains(where: { $0.hasPrefix(id + "|") }) else { return false }; pump(id); return true }
    func syncProcessing(_ id: String, state: BackgroundUploadState, result: String?, error: String?) -> Bool {
        guard state == .completed || state == .failed, var task = try? store.task(id), task.state == .processing else { return false }
        task.state = state; task.result = result ?? task.result; task.error = error ?? task.error; task.updatedAt = Date(); try? store.updateLocal(task); publish(task); return true
    }

    private func request(_ path:String, method:String, body:[String:Any]?, capability:String?) async throws -> [String:Any] { guard let url=URL(string:path) else {throw URLError(.badURL)}; var request=URLRequest(url:url); request.httpMethod=method; if let capability {request.setValue(capability,forHTTPHeaderField:"X-Upload-Capability")}; if let body {request.setValue("application/json",forHTTPHeaderField:"Content-Type");request.httpBody=try JSONSerialization.data(withJSONObject:body)}; return try await client.json(request) }
    private func finish(_ task: inout IOSUploadTask,response:[String:Any]) throws { let entity=((response["result"] as? [String:Any]) ?? response); let item=(entity["media"] as? [String:Any]) ?? (entity["personal_log"] as? [String:Any]) ?? entity; let status=(item["transcoding_status"] as? String) ?? (item["status"] as? String) ?? "processing"; task.result=String(data:try JSONSerialization.data(withJSONObject:["id":item["id"] as Any,"status":status,"transcoding_status":status]),encoding:.utf8); task.state=status=="completed" ? .completed : (status=="failed" ? .failed : .processing); task.progress=100;task.updatedAt=Date();try store.updateLocal(task);if task.state == .processing || task.state == .completed {try? FileManager.default.removeItem(at:URL(fileURLWithPath:task.file.path))};publish(task) }
    private func publish(_ task:IOSUploadTask){event?(task)}
    private func hasActive(_ value:String)->Bool{active.contains(value)}
}
