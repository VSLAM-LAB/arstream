import ARKit
import SwiftUI

/// Owns the AR session, the TCP server and the streamer, and exposes their state to the UI.
final class StreamController: ObservableObject {
    static let port: UInt16 = 5555

    @Published var serverStatus = "Starting…"
    @Published var stats = FrameStreamer.Stats()
    @Published var autoFocus = false { didSet { runSession() } }
    @Published var scale = 0.5 { didSet { streamer.scale = scale } }
    @Published var maxFPS = 30.0 { didSet { streamer.maxFPS = maxFPS } }

    let session = ARSession()
    private let server = StreamServer(port: StreamController.port)
    private lazy var streamer = FrameStreamer(server: server)

    var wifiAddress: String { NetworkInfo.wifiIPv4Address() ?? "no Wi-Fi" }

    func start() {
        server.onStatusChange = { [weak self] in self?.serverStatus = $0 }
        streamer.onStats = { [weak self] in self?.stats = $0 }
        streamer.scale = scale
        streamer.maxFPS = maxFPS
        session.delegate = streamer
        do {
            try server.start()
        } catch {
            serverStatus = "Server error: \(error)"
        }
        runSession()
    }

    private func runSession() {
        let config = ARWorldTrackingConfiguration()
        config.worldAlignment = .gravity
        // Fixed focus keeps the intrinsics stable, which most SLAM systems assume.
        config.isAutoFocusEnabled = autoFocus
        session.run(config)
    }
}

struct ContentView: View {
    @StateObject private var controller = StreamController()

    var body: some View {
        ZStack(alignment: .bottom) {
            ARPreview(session: controller.session)
                .ignoresSafeArea()

            VStack(alignment: .leading, spacing: 8) {
                Text(controller.serverStatus).font(.headline)
                Text("USB: port \(StreamController.port)   Wi-Fi: \(controller.wifiAddress):\(StreamController.port)")
                    .font(.caption.monospaced())
                Text(String(format: "%@  %.1f fps sent  %.1f dropped  %.1f Mbit/s",
                            controller.stats.resolution,
                            controller.stats.sentFPS,
                            controller.stats.droppedFPS,
                            controller.stats.megabitsPerSecond))
                    .font(.caption.monospaced())
                Text("Tracking: \(controller.stats.tracking)")
                    .font(.caption.monospaced())
                    .foregroundStyle(controller.stats.tracking == "normal" ? .green : .orange)

                Picker("Resolution", selection: $controller.scale) {
                    Text("1920×1440").tag(1.0)
                    Text("960×720").tag(0.5)
                    Text("640×480").tag(1.0 / 3.0)
                }
                .pickerStyle(.segmented)

                Picker("Max FPS", selection: $controller.maxFPS) {
                    Text("10 fps").tag(10.0)
                    Text("30 fps").tag(30.0)
                    Text("60 fps").tag(60.0)
                }
                .pickerStyle(.segmented)

                Toggle("Autofocus", isOn: $controller.autoFocus)
            }
            .padding()
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
            .padding()
        }
        .onAppear { controller.start() }
    }
}

/// Camera preview that shares the controller's ARSession.
struct ARPreview: UIViewRepresentable {
    let session: ARSession

    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView()
        view.session = session
        view.automaticallyUpdatesLighting = false
        return view
    }

    func updateUIView(_ uiView: ARSCNView, context: Context) {}
}

enum NetworkInfo {
    /// IPv4 address of the Wi-Fi interface (en0), if any.
    static func wifiIPv4Address() -> String? {
        var ifaddr: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&ifaddr) == 0, let first = ifaddr else { return nil }
        defer { freeifaddrs(ifaddr) }
        for ptr in sequence(first: first, next: { $0.pointee.ifa_next }) {
            let iface = ptr.pointee
            guard let addr = iface.ifa_addr, addr.pointee.sa_family == UInt8(AF_INET),
                  String(cString: iface.ifa_name) == "en0" else { continue }
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            if getnameinfo(addr, socklen_t(addr.pointee.sa_len), &host, socklen_t(host.count),
                           nil, 0, NI_NUMERICHOST) == 0 {
                return String(cString: host)
            }
        }
        return nil
    }
}
