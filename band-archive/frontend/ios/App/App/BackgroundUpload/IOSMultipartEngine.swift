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
    func activeAttemptPaths() -> Set<String> {
        lock.lock(); defer { lock.unlock() }
        return Set(active.values.map { $0.temporaryURL.path })
    }
    func releaseAll(uploadID: String) -> [ActivePart] {
        lock.lock(); defer { lock.unlock() }
        let matching = active.values.filter { $0.descriptor.uploadID == uploadID }
        for entry in matching {
            active.removeValue(forKey: entry.descriptor)
            if let taskIdentifier = entry.taskIdentifier { byTaskIdentifier.removeValue(forKey: taskIdentifier); etags.removeValue(forKey: taskIdentifier) }
        }
        return matching
    }
    func hasReconciling() -> Bool { lock.lock(); defer { lock.unlock() }; return !reconciling.isEmpty }
}

/// Coordinates iOS's background-session completion handoff with synchronous
/// delegate-to-SQLite commits. Network reconciliation is deliberately bounded;
/// pending ACK/complete intent remains durable for the next foreground pump.
final class BackgroundEventDrainState {
    private struct Cycle {
        let generation: Int
        var handlers: [() -> Void]
        var finishObserved: Bool
        var draining: Bool
    }
    private let lock = NSLock()
    private var cycles: [Cycle] = []
    private var delegateWrites = 0
    private var revision = 0
    private var nextGeneration = 1
    // URLSession can deliver its finish delegate before AppDelegate's handoff on
    // cold launch, or while a preceding generation is still draining. Retain one
    // such next-generation signal only in those two cases; an unmatched finish
    // after every cycle was consumed is a stale duplicate and is discarded.
    private var unboundFinishForNextCycle = false
    private var hasAttachedAtLeastOnce = false

    /// Creates an isolated handoff generation. A prior completed generation cannot
    /// make this handler runnable.
    func append(_ handler: @escaping () -> Void) -> Int {
        lock.lock(); defer { lock.unlock() }
        let generation = nextGeneration; nextGeneration += 1
        let observed = unboundFinishForNextCycle
        if observed { unboundFinishForNextCycle = false }
        hasAttachedAtLeastOnce = true
        cycles.append(Cycle(generation: generation, handlers: [handler], finishObserved: observed, draining: false))
        return generation
    }
    /// Returns the generation which became finish-observed, if it has an attached
    /// AppDelegate completion. An unmatched finish is remembered only for an
    /// initial attach or a handler racing a still-draining prior generation.
    func markFinishObserved() -> Int? {
        lock.lock(); defer { lock.unlock() }
        if let index = cycles.firstIndex(where: { !$0.finishObserved }) {
            cycles[index].finishObserved = true
            return cycles[index].generation
        }
        if !hasAttachedAtLeastOnce || cycles.contains(where: { $0.draining }) {
            unboundFinishForNextCycle = true
        }
        return nil
    }
    func beginDelegateWrite() { lock.lock(); delegateWrites += 1; revision += 1; lock.unlock() }
    func endDelegateWrite() { lock.lock(); delegateWrites = max(0, delegateWrites - 1); revision += 1; lock.unlock() }
    func beginDrain(generation: Int) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard let index = cycles.firstIndex(where: { $0.generation == generation }), cycles[index].finishObserved, !cycles[index].draining else { return false }
        cycles[index].draining = true; return true
    }
    func snapshot() -> (writes: Int, revision: Int) { lock.lock(); defer { lock.unlock() }; return (delegateWrites, revision) }
    func finishIfStable(generation: Int, revision expected: Int) -> [() -> Void]? {
        lock.lock(); defer { lock.unlock() }
        guard let index = cycles.firstIndex(where: { $0.generation == generation }), cycles[index].draining,
              cycles[index].finishObserved, delegateWrites == 0, revision == expected else { return nil }
        return cycles.remove(at: index).handlers
    }
}

enum BackgroundEventDrainPolicy {
    /// Network work is opportunistic during an iOS wake; durable DB intent is the correctness boundary.
    static let networkBudget: TimeInterval = 2
}

private final class LeaseHeartbeat {
    private let store: IOSUploadStore
    private let uploadID: String
    private let owner: String
    private let onLost: () -> Void
    private let lock = NSLock()
    private var lost = false
    private var stopped = false
    private var task: Task<Void, Never>?

    init(store: IOSUploadStore, uploadID: String, owner: String, onLost: @escaping () -> Void) {
        self.store = store; self.uploadID = uploadID; self.owner = owner; self.onLost = onLost
    }
    func start() {
        task = Task { [weak self] in
            while let self, !self.isStopped {
                try? await Task.sleep(nanoseconds: 30_000_000_000)
                guard !self.isStopped else { return }
                guard (try? self.store.renew(self.uploadID, owner: self.owner)) == true else { self.markLost(); return }
            }
        }
    }
    func isValid() -> Bool { lock.lock(); defer { lock.unlock() }; return !lost && !stopped }
    func stop() { lock.lock(); stopped = true; lock.unlock(); task?.cancel() }
    private var isStopped: Bool { lock.lock(); defer { lock.unlock() }; return stopped }
    private func markLost() {
        lock.lock(); guard !lost else { lock.unlock(); return }; lost = true; lock.unlock()
        onLost()
    }
}

/// Keeps a successfully-created engine for the process, but never memoizes an
/// initialization failure. This is important when Application Support or the
/// Keychain is temporarily unavailable before first device unlock.
final class IOSMultipartEngineProvider {
    typealias Factory = () throws -> IOSMultipartEngine
    private let lock = NSLock()
    private var instance: IOSMultipartEngine?
    private let factory: Factory

    init(factory: @escaping Factory = {
        try IOSMultipartEngine(store: IOSUploadStore(), credentials: KeychainUploadCredentialStore(), client: URLSessionMultipartHTTPClient())
    }) { self.factory = factory }

    func acquire() -> IOSMultipartEngine? {
        lock.lock(); defer { lock.unlock() }
        if let instance { return instance }
        guard let created = try? factory() else { return nil }
        instance = created
        return created
    }
}

/// Shared background-session multipart executor. It stores no credentials or presigned URLs in SQLite/task descriptions.
final class IOSMultipartEngine: NSObject, URLSessionTaskDelegate, URLSessionDataDelegate {
    static let backgroundSessionIdentifier = "com.deutteun.archive.background-upload.v1"
    /// Protected-data availability can change after process launch, so creation is
    /// retryable instead of permanently caching a failed `try?` result.
    static let provider = IOSMultipartEngineProvider()
    static var shared: IOSMultipartEngine? { provider.acquire() }
    private let owner = UUID().uuidString
    private let store: IOSUploadStore
    private let credentials: UploadCredentialStoring
    private let client: MultipartHTTPClient
    private let pumpQueue = DispatchQueue(label: "com.deutteun.archive.background-upload.pump")
    private let coordinator = IOSUploadCoordinator()
    private let drainState = BackgroundEventDrainState()
    private var session: URLSession!
    var event: ((IOSUploadTask) -> Void)?

    init(store: IOSUploadStore, credentials: UploadCredentialStoring, client: MultipartHTTPClient) throws {
        self.store = store; self.credentials = credentials; self.client = client
        super.init()
        let configuration = URLSessionConfiguration.background(withIdentifier: Self.backgroundSessionIdentifier)
        configuration.sessionSendsLaunchEvents = true
        configuration.isDiscretionary = false
        session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        recoverBackgroundTasks {}
    }

    func allocateWorkID() throws -> Int64 { try store.allocateWorkID() }

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
                // Recovery registers live system tasks before GC sees stale mappings/files.
                try? self.store.garbageCollect(activeAttemptPaths: self.coordinator.activeAttemptPaths())
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
        guard let loaded = try? store.task(id), var task = loaded, !task.state.isTerminal else { return }
        guard (try? store.acquire(id, owner: owner)) == true else { return }
        let lease = LeaseHeartbeat(store: store, uploadID: id, owner: owner) { [weak self] in self?.stopActive(id) }
        lease.start()
        defer { lease.stop(); _ = try? store.release(id, owner: owner) }
        guard let sessionID = task.sessionID else { return }
        let capability: String
        do { capability = try readCapability(uploadID: id) }
        catch UploadCredentialError.lost {
            task.state = .failed; task.error = "credential_lost"; task.updatedAt = Date()
            if (try? store.updateForOwner(task, owner: owner)) == true { publish(task) } else { stopActive(id) }
            return
        } catch {
            task.state = .retryWait; task.error = "keychain_unavailable"; task.updatedAt = Date()
            if (try? store.updateForOwner(task, owner: owner)) == true { publish(task) } else { stopActive(id) }
            return
        }
        do {
            for (part, etag, bytes) in try store.pendingAcks(uploadID: id) {
                guard lease.isValid else { return }
                _ = try await request(task.api + "/uploads/multipart/\(sessionID)/parts/\(part)/ack", method: "POST", body: ["etag": etag, "bytes": bytes], capability: capability)
                guard (try store.saveAck(uploadID: id, part: part, etag: etag, bytes: bytes, owner: owner)) else { stopActive(id); return }
                guard (try store.clearPendingAck(uploadID: id, part: part, owner: owner)) else { stopActive(id); return }
            }
            guard lease.isValid else { return }
            let remote = try await request(task.api + "/uploads/multipart/\(sessionID)", method: "GET", body: nil, capability: capability)
            let remoteState = remote["status"] as? String ?? ""
            if remoteState == "completed" { _ = try finish(&task, response: remote, owner: owner); return }
            if ["expired", "aborted", "failed"].contains(remoteState) {
                task.state = .failed; task.error = "session_\(remoteState)"; task.updatedAt = Date()
                if (try store.updateForOwner(task, owner: owner)) { publish(task) } else { stopActive(id) }
                return
            }
            let partSize = (remote["part_size"] as? NSNumber)?.int64Value ?? task.partSize ?? 0
            guard partSize > 0 else { throw URLError(.cannotParseResponse) }
            let remoteAcked = Set(((remote["parts"] as? [[String: Any]]) ?? []).compactMap { ($0["status"] as? String) == "acknowledged" ? ($0["part_number"] as? NSNumber)?.intValue : nil })
            let localAcked = try store.acknowledgedParts(uploadID: id)
            let count = Int((task.file.bytes + partSize - 1) / partSize)
            let missing = MultipartPartPlanner.missingParts(total: count, remoteAcknowledged: remoteAcked, localAcknowledged: localAcked)
            if missing.isEmpty && (try store.pendingAcks(uploadID: id)).isEmpty && !coordinator.hasActive(uploadID: id) {
                task.state = .completing; task.updatedAt = Date()
                guard try store.updateForOwner(task, owner: owner) else { stopActive(id); return }
                guard lease.isValid else { return }
                let result = try await request(task.api + "/uploads/multipart/\(sessionID)/complete", method: "POST", body: [:], capability: capability)
                _ = try finish(&task, response: result, owner: owner); return
            }
            for part in missing {
                guard coordinator.activeCount(uploadID: id) < 2 else { break }
                guard lease.isValid else { return }
                try await schedulePart(task, part: part, partSize: partSize, capability: capability, owner: owner)
            }
        } catch UploadEngineError.ownershipLost {
            stopActive(id)
        } catch {
            guard lease.isValid else { return }
            task.state = .retryWait; task.error = "network_retry"; task.updatedAt = Date()
            if (try? store.updateForOwner(task, owner: owner)) == true { publish(task) } else { stopActive(id) }
        }
    }

    private func schedulePart(_ task: IOSUploadTask, part: Int, partSize: Int64, capability: String, owner: String) async throws {
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
            guard try store.savePartAttempt(IOSPartAttempt(uploadID: descriptor.uploadID, part: descriptor.part, attemptID: descriptor.attemptID, temporaryPath: partFile.path, taskIdentifier: upload.taskIdentifier), owner: owner) else { throw UploadEngineError.ownershipLost }
            guard coordinator.attach(descriptor, taskIdentifier: upload.taskIdentifier) else { throw BackgroundUploadError.database("active_part") }
            upload.resume()
        } catch {
            let entry = coordinator.release(descriptor)
            if let entry { try? FileManager.default.removeItem(at: entry.temporaryURL) }
            _ = try? store.removePartAttempt(uploadID: descriptor.uploadID, part: descriptor.part, attemptID: descriptor.attemptID, owner: owner)
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
        drainState.beginDelegateWrite()
        defer { drainState.endDelegateWrite() }
        if let descriptor = MultipartPartDescriptor.parse(task.taskDescription), let http = response as? HTTPURLResponse, let etag = http.value(forHTTPHeaderField: "ETag") { coordinator.recordETag(etag.trimmingCharacters(in: CharacterSet(charactersIn: "\"")), taskIdentifier: task.taskIdentifier, descriptor: descriptor) }
        completionHandler(.allow)
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        drainState.beginDelegateWrite()
        defer { drainState.endDelegateWrite() }
        guard let descriptor = MultipartPartDescriptor.parse(task.taskDescription), let finished = coordinator.finish(taskIdentifier: task.taskIdentifier, descriptor: descriptor) else { return }
        defer { try? FileManager.default.removeItem(at: finished.0.temporaryURL) }
        guard (try? store.acquire(descriptor.uploadID, owner: owner)) == true else { stopActive(descriptor.uploadID); return }
        defer { _ = try? store.release(descriptor.uploadID, owner: owner) }
        guard error == nil, let response = task.response as? HTTPURLResponse, (200..<300).contains(response.statusCode), let etag = finished.1 ?? response.value(forHTTPHeaderField: "ETag")?.trimmingCharacters(in: CharacterSet(charactersIn: "\"")), let upload = try? store.task(descriptor.uploadID) else {
            _ = try? store.removePartAttempt(uploadID: descriptor.uploadID, part: descriptor.part, attemptID: descriptor.attemptID, owner: owner)
            pump(descriptor.uploadID); return
        }
        let size = min(upload.partSize ?? upload.file.bytes, upload.file.bytes - Int64(descriptor.part - 1) * (upload.partSize ?? upload.file.bytes))
        guard (try? store.savePendingAck(uploadID: descriptor.uploadID, part: descriptor.part, etag: etag, bytes: size, owner: owner)) == true,
              (try? store.removePartAttempt(uploadID: descriptor.uploadID, part: descriptor.part, attemptID: descriptor.attemptID, owner: owner)) == true else { stopActive(descriptor.uploadID); return }
        pump(descriptor.uploadID)
    }
    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        if let generation = drainState.markFinishObserved() { requestBackgroundDrain(generation: generation) }
    }
    /// Returns false for unknown sessions so AppDelegate can safely leave other SDK handlers untouched.
    func attachBackgroundEvents(identifier: String, completion: @escaping () -> Void) -> Bool {
        guard identifier == Self.backgroundSessionIdentifier else { return false }
        let generation = drainState.append(completion)
        pump()
        requestBackgroundDrain(generation: generation)
        return true
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64, totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        drainState.beginDelegateWrite()
        defer { drainState.endDelegateWrite() }
        guard let descriptor = MultipartPartDescriptor.parse(task.taskDescription), coordinator.isCurrent(taskIdentifier: task.taskIdentifier, descriptor: descriptor), var upload = try? store.task(descriptor.uploadID) else { return }
        let sent = Int64(descriptor.part - 1) * (upload.partSize ?? upload.file.bytes) + totalBytesSent
        upload.progress = max(upload.progress, min(99, Int(sent * 100 / max(1, upload.file.bytes)))); upload.state = .uploading; upload.updatedAt = Date()
        guard (try? store.acquire(descriptor.uploadID, owner: owner)) == true else { stopActive(descriptor.uploadID); return }
        defer { _ = try? store.release(descriptor.uploadID, owner: owner) }
        guard (try? store.updateForOwner(upload, owner: owner)) == true else { stopActive(descriptor.uploadID); return }
        publish(upload)
    }

    func cancel(_ id: String) {
        guard (try? store.cancel(id)) == true, let task = try? store.task(id) else { return }
        stopActive(id)
        if let sessionID = task.sessionID, let capability = try? credentials.read(uploadID: id), let capability { Task { _ = try? await self.request(task.api + "/uploads/multipart/\(sessionID)/abort", method: "POST", body: [:], capability: capability) } }
        publish(task)
    }
    func acknowledge(_ id: String) -> Bool { guard let task = try? store.task(id) else { return false }; BackgroundUploadNotifier.shared.clear(task); return (try? store.acknowledge(id)) ?? false }
    func retry(_ id: String) -> Bool { guard let task = try? store.task(id), task.state == .retryWait, !coordinator.hasActive(uploadID: id) else { return false }; pump(id); return true }
    func syncProcessing(_ id: String, state: BackgroundUploadState, result: String?, error: String?) -> Bool {
        guard state == .completed || state == .failed, let current = try? store.task(id), current.state == .processing,
              (try? store.syncProcessing(id, state: state, result: result, error: error)) == true,
              let task = try? store.task(id) else { return false }
        publish(task); return true
    }

    private func requestBackgroundDrain(generation: Int) {
        guard drainState.beginDrain(generation: generation) else { return }
        Task { [weak self] in await self?.drainBackgroundEvents(generation: generation) }
    }
    private func drainBackgroundEvents(generation: Int) async {
        let deadline = Date().addingTimeInterval(BackgroundEventDrainPolicy.networkBudget)
        var stableRevision: Int?
        var pumpedRevision: Int?
        while true {
            // This retries pending ACK/complete while the OS wake window remains available. Every continuation
            // is already represented by a pending ACK row or a durable completing/retry_wait task state.
            let snapshot = drainState.snapshot()
            if pumpedRevision != snapshot.revision { pump(); pumpedRevision = snapshot.revision }
            if snapshot.writes == 0 {
                if stableRevision == snapshot.revision {
                    if !coordinator.hasReconciling() || Date() >= deadline,
                        let handlers = drainState.finishIfStable(generation: generation, revision: snapshot.revision) {
                        DispatchQueue.main.async { handlers.forEach { $0() } }
                        return
                    }
                } else {
                    stableRevision = snapshot.revision
                }
            } else {
                stableRevision = nil
            }
            // Delegate writes are synchronous SQLite transactions. Waiting for them is necessary for durability;
            // only best-effort network reconciliation is bounded by the deadline above.
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
    }

    private func request(_ path: String, method: String, body: [String: Any]?, capability: String?) async throws -> [String: Any] { guard let url = URL(string: path) else { throw URLError(.badURL) }; var request = URLRequest(url: url); request.httpMethod = method; if let capability { request.setValue(capability, forHTTPHeaderField: "X-Upload-Capability") }; if let body { request.setValue("application/json", forHTTPHeaderField: "Content-Type"); request.httpBody = try JSONSerialization.data(withJSONObject: body) }; return try await client.json(request) }
    private func readCapability(uploadID: String) throws -> String {
        do {
            guard let capability = try credentials.read(uploadID: uploadID) else { throw UploadCredentialError.lost }
            return capability
        } catch let error as UploadCredentialError { throw error
        } catch { throw UploadCredentialError.unavailable }
    }
    private func finish(_ task: inout IOSUploadTask, response: [String: Any], owner: String) throws -> Bool {
        let entity = ((response["result"] as? [String: Any]) ?? response); let item = (entity["media"] as? [String: Any]) ?? (entity["personal_log"] as? [String: Any]) ?? entity; let status = (item["transcoding_status"] as? String) ?? (item["status"] as? String) ?? "processing"
        task.result = String(data: try JSONSerialization.data(withJSONObject: ["id": item["id"] as Any, "status": status, "transcoding_status": status]), encoding: .utf8); task.state = status == "completed" ? .completed : (status == "failed" ? .failed : .processing); task.progress = 100; task.updatedAt = Date()
        guard try store.updateForOwner(task, owner: owner) else { stopActive(task.uploadID); return false }
        if task.state == .processing || task.state == .completed { _ = try? store.removeSourceAfterR2Complete(task.uploadID) }
        publish(task); return true
    }
    private func stopActive(_ uploadID: String) {
        session.getAllTasks { tasks in tasks.filter { MultipartPartDescriptor.parse($0.taskDescription)?.uploadID == uploadID }.forEach { $0.cancel() } }
        coordinator.releaseAll(uploadID: uploadID).forEach { try? FileManager.default.removeItem(at: $0.temporaryURL) }
    }
    private func publish(_ task: IOSUploadTask) { event?(task); BackgroundUploadNotifier.shared.publishIfNeeded(task) }
}
