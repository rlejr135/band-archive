import Capacitor

/// Registers app-owned plugins on the Capacitor 8 bridge instance exactly once.
final class BridgeViewController: CAPBridgeViewController {
    private var didRegisterBackgroundUpload = false

    override func capacitorDidLoad() {
        super.capacitorDidLoad()
        guard !didRegisterBackgroundUpload, let bridge else { return }
        didRegisterBackgroundUpload = true
        bridge.registerPluginInstance(BackgroundUploadPlugin())
    }
}
