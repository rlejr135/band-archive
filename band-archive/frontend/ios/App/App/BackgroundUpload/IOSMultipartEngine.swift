import Foundation
import Darwin

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

/// Opaque, validated metadata only: no source paths, URLs, or credentials are ever placed in URLSession task descriptions.
struct MultipartPartDescriptor: Hashable {
    let uploadID: String
    let part: Int
    let attemptID: String

    var encoded: String { "\(uploadID)|\(part)|\(attemptID)" }

    static func parse(_ value: String?) -> MultipartPartDescriptor? {
        guard let value else { return nil }
        let fields = value.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
        guard fields.count == 3, UUID(uuidString: fields[0]) != nil, let part = Int(fields[1]), part > 0, UUID(uuidString: fields[2]) != nil else { return nil }
        return MultipartPartDescriptor(uploadID: fields[0], part: part, attemptID: fields[2])
    }
}

enum MultipartPartPlanner {
    static func missingParts(total: Int, remoteAcknowledged: Set<Int>, localAcknowledged: Set<Int>) -> [Int] {
        guard total > 0 else { return [] }
        return (1...total).filter { !remoteAcknowledged.contains($0) && !localAcknowledged.contains($0) }
    }
}

enum MultipartPartFilePolicy {
    static func partURL(root: URL, descriptor: MultipartPartDescriptor) throws -> URL {
        let candidate = root.appendingPathComponent("\(descriptor.uploadID).\(descriptor.part).\(descriptor.attemptID).part")
        guard let canonical = BackgroundUploadFiles.canonicalChild(candidate, of: root) else { throw BackgroundUploadError.unsafePath }
        return canonical
    }
}

struct ActivePart {
    let descriptor: MultipartPartDescriptor
    let temporaryURL: URL
    var taskIdentifier: Int?
}

/// The only mutable session state. Its lock serializes reconcile single-flight,
/// ETag callbacks, and recovered URLSession tasks across delegate queues.
final class IOSUploadCoordinator {
    private let lock = NSLock()
    private var reconciling: Set<String> = []
    private var pendingReconcile: Set<String> = []
    private var active: [MultipartPartDescriptor: ActivePart] = [:]
    private var byTaskIdentifier: [Int: MultipartPartDescriptor] = [:]
    private var etags: [Int: String] = [:]

    func beginReconcile(_ uploadID: String) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard !reconciling.contains(uploadID) else { pendingReconcile.insert(uploadID); return false }
        reconciling.insert(uploadID); return true
    }
    /// Returns true when a request arrived while this run was active and one follow-up run must start.
    func finishReconcile(_ uploadID: String) -> Bool {
        lock.lock(); defer { lock.unlock() }
        reconciling.remove(uploadID)
        guard pendingReconcile.remove(uploadID) != nil else { return false }
        reconciling.insert(uploadID); return true
    }
    func reserve(_ descriptor: MultipartPartDescriptor, temporaryURL: URL) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard !active.keys.contains(where: { $0.uploadID == descriptor.uploadID && $0.part == descriptor.part }) else { return false }
        active[descriptor] = ActivePart(descriptor: descriptor, temporaryURL: temporaryURL, taskIdentifier: nil)
        return true
    }
    func attach(_ descriptor: MultipartPartDescriptor, taskIdentifier: Int) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard var entry = active[descriptor], entry.taskIdentifier == nil, byTaskIdentifier[taskIdentifier] == nil else { return false }
        entry.taskIdentifier = taskIdentifier; active[descriptor] = entry; byTaskIdentifier[taskIdentifier] = descriptor
        return true
    }
    func recover(_ attempt: IOSPartAttempt, descriptor: MultipartPartDescriptor, taskIdentifier: Int, temporaryURL: URL) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard attempt.uploadID == descriptor.uploadID, attempt.part == descriptor.part, attempt.attemptID == descriptor.attemptID,
              !active.keys.contains(where: { $0.uploadID == descriptor.uploadID && $0.part == descriptor.part }), byTaskIdentifier[taskIdentifier] == nil else { return false }
        active[descriptor] = ActivePart(descriptor: descriptor, temporaryURL: temporaryURL, taskIdentifier: taskIdentifier)
        byTaskIdentifier[taskIdentifier] = descriptor
        return true
    }
    func release(_ descriptor: MultipartPartDescriptor) -> ActivePart? {
        lock.lock(); defer { lock.unlock() }
        guard let entry = active.removeValue(forKey: descriptor) else { return nil }
        if let identifier = entry.taskIdentifier { byTaskIdentifier.removeValue(forKey: identifier); etags.removeValue(forKey: identifier) }
        return entry
    }
    /// A stale delegate callback cannot remove a newer attempt because task identifier and descriptor must both match.
    func finish(taskIdentifier: Int, descriptor: MultipartPartDescriptor) -> (ActivePart, String?)? {
        lock.lock(); defer { lock.unlock() }
        guard byTaskIdentifier[taskIdentifier] == descriptor, let entry = active[descriptor], entry.taskIdentifier == taskIdentifier else { return nil }
        active.removeValue(forKey: descriptor); byTaskIdentifier.removeValue(forKey: taskIdentifier)
        return (entry, etags.removeValue(forKey: taskIdentifier))
    }
    func recordETag(_ value: String, taskIdentifier: Int, descriptor: MultipartPartDescriptor) {
        lock.lock(); defer { lock.unlock() }
        guard byTaskIdentifier[taskIdentifier] == descriptor else { return }
        etags[taskIdentifier] = value
    }
    func isCurrent(taskIdentifier: Int, descriptor: MultipartPartDescriptor) -> Bool {
        lock.lock(); defer { lock.unlock() }
        return byTaskIdentifier[taskIdentifier] == descriptor
    }
    func hasActive(uploadID: String, part: Int? = nil) -> Bool {
        lock.lock(); defer { lock.unlock() }
        return active.keys.contains { $0.uploadID == uploadID && (part == nil || $0.part == part) }
    }
    func activeCount(uploadID: String) -> Int {
        lock.lock(); defer { lock.unlock() }
        return active.keys.filter { $0.uploadID == uploadID }.count
    }
}

/// Shared background-session multipart executor. It stores no credentials or presigned URLs in SQLite/task descriptions.
final class IOSMultipartEngine: NSObject, URLSessionTaskDelegate, URLSessionDataDelegate {
    static let backgroundSessionIdentifier = "com.deutteun.archive.background-upload.v1"
    static let shared = IOSMultipartEngine()
    private let owner = UUID().uuidString
    private let store: IOSUploadStore
    private let credentials: UploadCredentialStoring
    private let client: MultipartHTTPClient
    private let pumpQueue = DispatchQueue(label: "com.deutteun.archive.background-upload.pump")
    private let coordinator = IOSUploadCoordinator()
    private var session: URLSession!
    var event: ((IOSUploadTask) -> Void)?
    private let lifecycleLock = NSLock()
    private var backgroundCompletions: [() -> Void] = []

    override convenience init() { try! self.init(store: IOSUploadStore(), credentials: KeychainUploadCredentialStore(), client: URLSessionMultipartHTTPClient()) }
    init(store: IOSUploadStore, credentials: UploadCredentialStoring, client: MultipartHTTPClient) throws {
        self.store = store; self.credentials = credentials; self.client = client
        super.init()
        let configuration = URLSessionConfiguration.background(withIdentifier: Self.backgroundSessionIdentifier)
        configuration.sessionSendsLaunchEvents = true
        configuration.isDiscretionary = false
        session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        recoverBackgroundTasks {}
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
        recoverBackgroundTasks { [weak self] in
            guard let self else { return }
            self.pumpQueue.async {
                let tasks = (try? self.store.retainedTasks()) ?? []
                for task in tasks where (uploadID == nil || task.uploadID == uploadID) && task.state.isRunnable { self.beginReconcile(task.uploadID) }
            }
        }
    }
    private func beginReconcile(_ uploadID: String) {
        guard coordinator.beginReconcile(uploadID) else { return }
        Task { [weak self] in
            guard let self else { return }
            await self.reconcile(uploadID)
            if self.coordinator.finishReconcile(uploadID) { self.startFollowupReconcile(uploadID) }
        }
    }
    private func startFollowupReconcile(_ uploadID: String) {
        Task { [weak self] in
            guard let self else { return }
            await self.reconcile(uploadID)
            if self.coordinator.finishReconcile(uploadID) { self.startFollowupReconcile(uploadID) }
        }
    }

    private func reconcile(_ id: String) async {
        guard var task = try? store.task(id), !task.state.isTerminal, try! store.acquire(id, owner: owner) else { return }
        guard let sessionID = task.sessionID else { return }
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
            let missing = MultipartPartPlanner.missingParts(total: count, remoteAcknowledged: remoteAcked, localAcknowledged: localAcked)
            if missing.isEmpty && (try store.pendingAcks(uploadID: id)).isEmpty && !coordinator.hasActive(uploadID: id) { task.state = .completing; task.updatedAt = Date(); try store.updateLocal(task); let result = try await request(task.api + "/uploads/multipart/\(sessionID)/complete", method: "POST", body: [:], capability: capability); try finish(&task, response: result); return }
            for part in missing {
                guard coordinator.activeCount(uploadID: id) < 2 else { break }
                try await schedulePart(task, part: part, partSize: partSize, capability: capability)
            }
        } catch {
            task.state = .retryWait; task.error = "network_retry"; task.updatedAt = Date(); try? store.updateForOwner(task, owner: owner); publish(task)
        }
    }

    private func schedulePart(_ task: IOSUploadTask, part: Int, partSize: Int64, capability: String) async throws {
        guard let sessionID = task.sessionID, !coordinator.hasActive(uploadID: task.uploadID, part: part) else { return }
        let descriptor = MultipartPartDescriptor(uploadID: task.uploadID, part: part, attemptID: UUID().uuidString)
        guard MultipartPartDescriptor.parse(descriptor.encoded) != nil else { throw BackgroundUploadError.unavailableFile }
        let root = try BackgroundUploadFiles.directory()
        let partFile = try MultipartPartFilePolicy.partURL(root: root, descriptor: descriptor)
        guard coordinator.reserve(descriptor, temporaryURL: partFile) else { return }
        do {
            let issued = try await request(task.api + "/uploads/multipart/\(sessionID)/parts", method: "POST", body: ["part_number": part], capability: capability)
            guard let text = issued["upload_url"] as? String, let url = URL(string: text) else { throw URLError(.badURL) }
            let offset = Int64(part - 1) * partSize, length = min(partSize, task.file.bytes - offset)
            try makePartFile(task: task, offset: offset, length: length, output: partFile, root: root)
            var request = URLRequest(url: url); request.httpMethod = "PUT"; request.setValue(task.file.contentType, forHTTPHeaderField: "Content-Type")
            let upload = session.uploadTask(with: request, fromFile: partFile)
            upload.taskDescription = descriptor.encoded
            try store.savePartAttempt(IOSPartAttempt(uploadID: descriptor.uploadID, part: descriptor.part, attemptID: descriptor.attemptID, temporaryPath: partFile.path, taskIdentifier: upload.taskIdentifier))
            guard coordinator.attach(descriptor, taskIdentifier: upload.taskIdentifier) else { throw BackgroundUploadError.database("active_part") }
            upload.resume()
        } catch {
            let entry = coordinator.release(descriptor)
            if let entry { try? FileManager.default.removeItem(at: entry.temporaryURL) }
            try? store.removePartAttempt(uploadID: descriptor.uploadID, part: descriptor.part, attemptID: descriptor.attemptID)
            throw error
        }
    }

    private func makePartFile(task: IOSUploadTask, offset: Int64, length: Int64, output: URL, root: URL) throws {
        let source = URL(fileURLWithPath: task.file.path)
        let sourceValues = try source.resourceValues(forKeys: [.isRegularFileKey])
        guard BackgroundUploadFiles.canonicalChild(source, of: root) != nil,
              sourceValues.isRegularFile == true,
              BackgroundUploadFiles.canonicalChild(output, of: root) != nil else { throw BackgroundUploadError.unsafePath }
        let inputHandle = try FileHandle(forReadingFrom: source); defer { try? inputHandle.close() }
        try inputHandle.seek(toOffset: UInt64(offset))
        let fileDescriptor = Darwin.open(output.path, O_WRONLY | O_CREAT | O_EXCL, S_IRUSR | S_IWUSR)
        guard fileDescriptor >= 0 else { throw BackgroundUploadError.unavailableFile }
        let out = FileHandle(fileDescriptor: fileDescriptor, closeOnDealloc: true)
        var complete = false
        defer { try? out.close(); if !complete { try? FileManager.default.removeItem(at: output) } }
        var left = length
        while left > 0 {
            let data = try inputHandle.read(upToCount: Int(min(left, 64 * 1024))) ?? Data()
            guard !data.isEmpty else { throw BackgroundUploadError.unavailableFile }
            try out.write(contentsOf: data); left -= Int64(data.count)
        }
        try out.synchronize(); complete = true
    }

    private func recoverBackgroundTasks(completion: @escaping () -> Void) {
        session.getAllTasks { [weak self] tasks in
            guard let self else { completion(); return }
            for task in tasks {
                guard let descriptor = MultipartPartDescriptor.parse(task.taskDescription) else { task.cancel(); continue }
                // getAllTasks may be requested by launch and foreground pump at the same time.
                if self.coordinator.isCurrent(taskIdentifier: task.taskIdentifier, descriptor: descriptor) { continue }
                guard let attempt = try? self.store.partAttempt(uploadID: descriptor.uploadID, part: descriptor.part, attemptID: descriptor.attemptID),
                      let attempt,
                      let safe = self.safeRegularPartFile(attempt.temporaryPath),
                      self.coordinator.recover(attempt, descriptor: descriptor, taskIdentifier: task.taskIdentifier, temporaryURL: safe) else {
                    task.cancel()
                    // Never delete a mapping used by the task that won this part's recovery race.
                    if !self.coordinator.hasActive(uploadID: descriptor.uploadID, part: descriptor.part) { try? self.store.removePartAttempt(uploadID: descriptor.uploadID, part: descriptor.part, attemptID: descriptor.attemptID) }
                    continue
                }
            }
            completion()
        }
    }
    private func safeRegularPartFile(_ path: String) -> URL? {
        guard let root = try? BackgroundUploadFiles.directory(), let file = BackgroundUploadFiles.canonicalChild(URL(fileURLWithPath: path), of: root), let values = try? file.resourceValues(forKeys: [.isRegularFileKey]), values.isRegularFile == true else { return nil }
        return file
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didReceive response: URLResponse, completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        if let descriptor = MultipartPartDescriptor.parse(task.taskDescription), let http = response as? HTTPURLResponse, let etag = http.value(forHTTPHeaderField: "ETag") { coordinator.recordETag(etag.trimmingCharacters(in: CharacterSet(charactersIn: "\"")), taskIdentifier: task.taskIdentifier, descriptor: descriptor) }
        completionHandler(.allow)
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let descriptor = MultipartPartDescriptor.parse(task.taskDescription), let finished = coordinator.finish(taskIdentifier: task.taskIdentifier, descriptor: descriptor) else { return }
        defer { try? FileManager.default.removeItem(at: finished.0.temporaryURL); try? store.removePartAttempt(uploadID: descriptor.uploadID, part: descriptor.part, attemptID: descriptor.attemptID) }
        guard error == nil, let response = task.response as? HTTPURLResponse, (200..<300).contains(response.statusCode), let etag = finished.1 ?? response.value(forHTTPHeaderField: "ETag")?.trimmingCharacters(in: CharacterSet(charactersIn: "\"")), let upload = try? store.task(descriptor.uploadID) else { pump(descriptor.uploadID); return }
        let size = min(upload.partSize ?? upload.file.bytes, upload.file.bytes - Int64(descriptor.part - 1) * (upload.partSize ?? upload.file.bytes))
        try? store.savePendingAck(uploadID: descriptor.uploadID, part: descriptor.part, etag: etag, bytes: size)
        pump(descriptor.uploadID)
    }
    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        pump()
        lifecycleLock.lock(); let completions = backgroundCompletions; backgroundCompletions.removeAll(); lifecycleLock.unlock()
        DispatchQueue.main.async { completions.forEach { $0() } }
    }
    /// Returns false for unknown sessions so AppDelegate can safely leave other SDK handlers untouched.
    func attachBackgroundEvents(identifier: String, completion: @escaping () -> Void) -> Bool {
        guard identifier == Self.backgroundSessionIdentifier else { return false }
        lifecycleLock.lock(); backgroundCompletions.append(completion); lifecycleLock.unlock(); pump(); return true
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64, totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        guard let descriptor = MultipartPartDescriptor.parse(task.taskDescription), coordinator.isCurrent(taskIdentifier: task.taskIdentifier, descriptor: descriptor), var upload = try? store.task(descriptor.uploadID) else { return }
        let sent = Int64(descriptor.part - 1) * (upload.partSize ?? upload.file.bytes) + totalBytesSent
        upload.progress = max(upload.progress, min(99, Int(sent * 100 / max(1, upload.file.bytes)))); upload.state = .uploading; upload.updatedAt = Date()
        try? store.updateLocal(upload); publish(upload)
    }

    func cancel(_ id: String) {
        session.getAllTasks { tasks in tasks.filter { MultipartPartDescriptor.parse($0.taskDescription)?.uploadID == id }.forEach { $0.cancel() } }
        if let task = try? store.task(id), let sessionID = task.sessionID, let capability = try? credentials.read(uploadID: id), let capability { Task { _ = try? await self.request(task.api + "/uploads/multipart/\(sessionID)/abort", method: "POST", body: [:], capability: capability) } }
        _ = try? store.cancel(id); if let task = try? store.task(id) { publish(task) }
    }
    func acknowledge(_ id: String) -> Bool { guard let task = try? store.task(id) else { return false }; BackgroundUploadNotifier.shared.clear(task); return (try? store.acknowledge(id)) ?? false }
    func retry(_ id: String) -> Bool { guard let task = try? store.task(id), task.state == .retryWait, !coordinator.hasActive(uploadID: id) else { return false }; pump(id); return true }
    func syncProcessing(_ id: String, state: BackgroundUploadState, result: String?, error: String?) -> Bool {
        guard state == .completed || state == .failed, var task = try? store.task(id), task.state == .processing else { return false }
        task.state = state; task.result = result ?? task.result; task.error = error ?? task.error; task.updatedAt = Date(); try? store.updateLocal(task); publish(task); return true
    }

    private func request(_ path: String, method: String, body: [String: Any]?, capability: String?) async throws -> [String: Any] { guard let url = URL(string: path) else { throw URLError(.badURL) }; var request = URLRequest(url: url); request.httpMethod = method; if let capability { request.setValue(capability, forHTTPHeaderField: "X-Upload-Capability") }; if let body { request.setValue("application/json", forHTTPHeaderField: "Content-Type"); request.httpBody = try JSONSerialization.data(withJSONObject: body) }; return try await client.json(request) }
    private func finish(_ task: inout IOSUploadTask, response: [String: Any]) throws { let entity = ((response["result"] as? [String: Any]) ?? response); let item = (entity["media"] as? [String: Any]) ?? (entity["personal_log"] as? [String: Any]) ?? entity; let status = (item["transcoding_status"] as? String) ?? (item["status"] as? String) ?? "processing"; task.result = String(data: try JSONSerialization.data(withJSONObject: ["id": item["id"] as Any, "status": status, "transcoding_status": status]), encoding: .utf8); task.state = status == "completed" ? .completed : (status == "failed" ? .failed : .processing); task.progress = 100; task.updatedAt = Date(); try store.updateLocal(task); if task.state == .processing || task.state == .completed { try? FileManager.default.removeItem(at: URL(fileURLWithPath: task.file.path)) }; publish(task) }
    private func publish(_ task: IOSUploadTask) { event?(task); BackgroundUploadNotifier.shared.publishIfNeeded(task) }
}
