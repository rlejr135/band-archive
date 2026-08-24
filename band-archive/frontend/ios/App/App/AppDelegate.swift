import UIKit

@UIApplicationMain
class AppDelegate: UIResponder, UIApplicationDelegate {

    var window: UIWindow?
    private let handoffs = BackgroundUploadHandoffBroker()

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        _ = IOSMultipartEngine.shared
        return true
    }

    func applicationWillResignActive(_ application: UIApplication) {
        // Sent when the application is about to move from active to inactive state. This can occur for certain types of temporary interruptions (such as an incoming phone call or SMS message) or when the user quits the application and it begins the transition to the background state.
        // Use this method to pause ongoing tasks, disable timers, and invalidate graphics rendering callbacks. Games should use this method to pause the game.
    }

    func applicationDidEnterBackground(_ application: UIApplication) {
        // Use this method to release shared resources, save user data, invalidate timers, and store enough application state information to restore your application to its current state in case it is terminated later.
        // If your application supports background execution, this method is called instead of applicationWillTerminate: when the user quits.
    }

    func applicationWillEnterForeground(_ application: UIApplication) {
        // Called as part of the transition from the background to the active state; here you can undo many of the changes made on entering the background.
    }

    func applicationDidBecomeActive(_ application: UIApplication) {
        handoffs.retryPending()
        IOSMultipartEngine.shared?.pump()
    }

    func application(_ application: UIApplication, handleEventsForBackgroundURLSession identifier: String, completionHandler: @escaping () -> Void) {
        guard identifier == IOSMultipartEngine.backgroundSessionIdentifier else { DispatchQueue.main.async(execute: completionHandler); return }
        handoffs.attachOrHold(identifier: identifier, completion: completionHandler)
    }

    func applicationProtectedDataDidBecomeAvailable(_ application: UIApplication) {
        handoffs.retryPending()
        IOSMultipartEngine.shared?.pump()
    }

    func applicationWillTerminate(_ application: UIApplication) {
        // Called when the application is about to terminate. Save data if appropriate. See also applicationDidEnterBackground:.
    }

    func application(_ application: UIApplication,
                     configurationForConnecting connectingSceneSession: UISceneSession,
                     options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        let config = UISceneConfiguration(name: "Default Configuration",
                                          sessionRole: connectingSceneSession.role)
        config.delegateClass = SceneDelegate.self
        return config
    }
}

/// Holds only the iOS completion closure while protected storage is unavailable.
/// The normal path attaches immediately; the bounded fallback prevents a leaked
/// system handoff if the device never becomes available in this process.
private final class BackgroundUploadHandoffBroker {
    private struct Pending { let identifier: String; let completion: () -> Void }
    private let lock = NSLock()
    private var pending: [Pending] = []
    private var expiry: DispatchWorkItem?
    private let maximumWait: TimeInterval = 30

    func attachOrHold(identifier: String, completion: @escaping () -> Void) {
        if let engine = IOSMultipartEngine.shared, engine.attachBackgroundEvents(identifier: identifier, completion: completion) { return }
        lock.lock(); pending.append(Pending(identifier: identifier, completion: completion)); scheduleExpiryLocked(); lock.unlock()
    }

    func retryPending() {
        guard let engine = IOSMultipartEngine.shared else { return }
        lock.lock(); let handoffs = pending; pending.removeAll(); expiry?.cancel(); expiry = nil; lock.unlock()
        for handoff in handoffs where !engine.attachBackgroundEvents(identifier: handoff.identifier, completion: handoff.completion) {
            DispatchQueue.main.async(execute: handoff.completion)
        }
    }

    private func scheduleExpiryLocked() {
        guard expiry == nil else { return }
        let item = DispatchWorkItem { [weak self] in self?.expire() }
        expiry = item
        DispatchQueue.main.asyncAfter(deadline: .now() + maximumWait, execute: item)
    }
    private func expire() {
        lock.lock(); let handoffs = pending; pending.removeAll(); expiry = nil; lock.unlock()
        // No delegate can be created safely in this process; close the OS handoff
        // once bounded wait expires. Existing DB state remains retryable next launch.
        DispatchQueue.main.async { handoffs.forEach { $0.completion() } }
    }
}
