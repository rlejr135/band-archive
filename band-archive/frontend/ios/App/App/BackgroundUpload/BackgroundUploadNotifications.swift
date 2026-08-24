import Foundation
import UserNotifications
import UIKit

enum BackgroundUploadNotificationPolicy {
    static func identifier(for workID: Int64) -> String { "background-upload-\(workID)" }
    static func shouldNotify(_ task: IOSUploadTask, applicationState: UIApplication.State) -> Bool {
        applicationState != .active && [BackgroundUploadState.completed, .failed].contains(task.state)
    }
    static func body(for task: IOSUploadTask) -> String {
        task.state == .completed ? "Your upload is ready to review." : "An upload needs your attention."
    }
}

final class BackgroundUploadNotifier {
    static let shared = BackgroundUploadNotifier()
    func requestPermission(completion: @escaping (Bool) -> Void) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { granted, _ in DispatchQueue.main.async { completion(granted) } }
    }
    func publishIfNeeded(_ task: IOSUploadTask) {
        // A bridge object can remain allocated after the app is backgrounded; use application state rather than
        // event-closure presence so terminal transfers still alert the user while JavaScript is suspended.
        guard BackgroundUploadNotificationPolicy.shouldNotify(task, applicationState: UIApplication.shared.applicationState) else { return }
        let content = UNMutableNotificationContent(); content.title = task.state == .completed ? "Upload completed" : "Upload needs attention"; content.body = BackgroundUploadNotificationPolicy.body(for: task); content.sound = .default
        let identifier = BackgroundUploadNotificationPolicy.identifier(for: task.workID)
        UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: [identifier])
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: identifier, content: content, trigger: nil))
    }
    func clear(_ task: IOSUploadTask) { let identifier = BackgroundUploadNotificationPolicy.identifier(for: task.workID); UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: [identifier]); UNUserNotificationCenter.current().removeDeliveredNotifications(withIdentifiers: [identifier]) }
}
