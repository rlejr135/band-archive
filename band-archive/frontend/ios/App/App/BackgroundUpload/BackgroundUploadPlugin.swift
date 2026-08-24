import Capacitor
import Foundation

@objc(BackgroundUploadPlugin)
public final class BackgroundUploadPlugin: CAPPlugin, CAPBridgedPlugin {
    public static let identifier = "BackgroundUploadPlugin"
    public static let jsName = "BackgroundUpload"
    public static let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "pickFiles", returnType: CAPPluginReturnPromise), CAPPluginMethod(name: "requestNotificationPermission", returnType: CAPPluginReturnPromise), CAPPluginMethod(name: "enqueue", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "resume", returnType: CAPPluginReturnPromise), CAPPluginMethod(name: "retry", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "cancel", returnType: CAPPluginReturnPromise), CAPPluginMethod(name: "listPending", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "acknowledge", returnType: CAPPluginReturnPromise), CAPPluginMethod(name: "syncProcessingStatus", returnType: CAPPluginReturnPromise), CAPPluginMethod(name: "delete", returnType: CAPPluginReturnPromise)
    ]
    private var engine: IOSMultipartEngine? { IOSMultipartEngine.shared }
    private let picker = VideoPicker()

    public override func load() { engine?.event = { [weak self] task in self?.notifyListeners("state", data: Self.json(task), retainUntilConsumed: true) }; engine?.pump() }

    @objc func requestNotificationPermission(_ call: CAPPluginCall) { BackgroundUploadNotifier.shared.requestPermission { granted in call.resolve(["granted":granted,"backgroundLimited":!granted,"message":granted ? "" : "Notifications are off; background completion alerts will be unavailable.","forceQuitStopsTransfers":true]) } }

    @objc func pickFiles(_ call: CAPPluginCall) {
        guard let controller = bridge?.viewController else { call.reject("Picker is unavailable"); return }
        picker.present(from: controller, multiple: call.getBool("multiple", true)) { results in
            var files:[[String:Any]]=[]; var errors:[[String:String]]=[]
            for result in results { do { let durable=try result.get(); files.append(["id":durable.uploadID,"uri":URL(fileURLWithPath:durable.path).absoluteString,"name":durable.filename,"mimeType":durable.contentType,"size":durable.bytes,"fingerprint":durable.sha256,"nativeVideo":true]) } catch { errors.append(["message":error.localizedDescription]) } }
            call.resolve(["files":files,"errors":errors])
        }
    }

    @objc func enqueue(_ call: CAPPluginCall) {
        guard let id=call.getString("fileId"), let uri=call.getString("uri"), let api=call.getString("apiUrl"), let target=call.getObject("target"), let source=URL(string:uri), let root=try? BackgroundUploadFiles.directory(), let safe=BackgroundUploadFiles.canonicalChild(source,of:root), let size=call.getInt("size").map(Int64.init), size>0, size<=BackgroundUploadFiles.maximumBytes else { call.reject("A durable video file and target are required"); return }
        let keys=["song_id","rehearsal_id","member_id"]; guard let key=keys.first(where:{target[$0] != nil}), let value=target[key] else { call.reject("A multipart target is required"); return }
        let kind: String; let targetID: String
        if key == "member_id" { kind = key; targetID = String(describing: value) }
        else { kind = "media"; let media = target.filter { ["song_id", "rehearsal_id"].contains($0.key) }; guard let data = try? JSONSerialization.data(withJSONObject: media), let encoded = String(data: data, encoding: .utf8) else { call.reject("Invalid media target"); return }; targetID = encoded }
        guard let engine else { call.reject("The upload queue is temporarily unavailable."); return }
        let workID: Int64
        do { workID = try engine.allocateWorkID() } catch { call.reject("The upload queue could not allocate work."); return }
        let file=DurableUploadFile(uploadID:id,path:safe.path,filename:call.getString("name") ?? "upload",contentType:call.getString("mimeType") ?? "video/mp4",bytes:size,sha256:call.getString("fingerprint") ?? "")
        let task=IOSUploadTask(uploadID:id,workID:workID,createdAt:Date(),file:file,api:api,targetKind:kind,targetID:targetID,sessionID:nil,partSize:nil,state:.preparing,progress:0,error:nil,result:nil,leaseOwner:nil,leaseExpiresAt:nil,updatedAt:Date())
        Task { do { try await engine.enqueue(task); call.resolve(["id":id,"state":"queued"]) } catch { call.reject(error.localizedDescription) } }
    }
    @objc func resume(_ call: CAPPluginCall) { engine?.pump(call.getString("id")); call.resolve(["available":engine != nil]) }
    @objc func retry(_ call: CAPPluginCall) { guard let id=call.getString("id"), let engine else {call.resolve(["changed":false]);return}; call.resolve(["changed":engine.retry(id)]) }
    @objc func cancel(_ call: CAPPluginCall) { guard let id=call.getString("id"), let engine else {call.reject("The upload queue is temporarily unavailable.");return}; engine.cancel(id); call.resolve() }
    @objc func acknowledge(_ call: CAPPluginCall) { terminalDelete(call) }
    @objc func delete(_ call: CAPPluginCall) { terminalDelete(call) }
    @objc func syncProcessingStatus(_ call: CAPPluginCall) { guard let id=call.getString("id"), let state=call.getString("state"), let next=BackgroundUploadState(rawValue:state), next == .completed || next == .failed, let engine else {call.resolve(["changed":false]);return}; call.resolve(["changed":engine.syncProcessing(id,state:next,result:call.getString("result"),error:call.getString("error"))]) }
    @objc func updateProcessing(_ call: CAPPluginCall) { syncProcessingStatus(call) }
    @objc func listPending(_ call: CAPPluginCall) { let tasks=(try? IOSUploadStore().retainedTasks()) ?? []; call.resolve(["items":tasks.map(Self.json),"supportsBackground":true,"forceQuitStopsTransfers":true,"backgroundNotice":"Force-quitting the app cancels iOS background transfers; reopen and retry when needed."]) }
    private func terminalDelete(_ call:CAPPluginCall){guard let id=call.getString("id"),let engine else{call.resolve(["changed":false]);return};call.resolve(["changed":engine.acknowledge(id)])}
    private static func json(_ task:IOSUploadTask)->[String:Any]{ let target: [String:Any] = task.targetKind == "media" ? ((try? JSONSerialization.jsonObject(with: Data(task.targetID.utf8))) as? [String:Any] ?? [:]) : [task.targetKind:task.targetID]; return ["id":task.uploadID,"workId":task.workID,"uri":URL(fileURLWithPath:task.file.path).absoluteString,"name":task.file.filename,"mimeType":task.file.contentType,"size":task.file.bytes,"fingerprint":task.file.sha256,"apiUrl":task.api,"target":target,"state":task.state.rawValue,"progress":task.progress,"error":task.error as Any,"result":task.result as Any] }
}
