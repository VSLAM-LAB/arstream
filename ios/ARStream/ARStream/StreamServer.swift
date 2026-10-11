import Foundation
import Network

/// TCP server that pushes messages to a single client (the newest connection wins).
///
/// Works over Wi-Fi and over USB: on USB the Mac reaches this port through usbmuxd.
final class StreamServer {
    static let bonjourType = "_arstream._tcp"

    let port: UInt16
    var onStatusChange: ((String) -> Void)?

    private let queue = DispatchQueue(label: "arstream.server")
    private var listener: NWListener?
    private var connection: NWConnection?          // only touched on `queue`
    private let lock = NSLock()
    private var _hasClient = false

    init(port: UInt16) {
        self.port = port
    }

    var hasClient: Bool {
        lock.lock(); defer { lock.unlock() }
        return _hasClient
    }

    func start() throws {
        let tcp = NWProtocolTCP.Options()
        tcp.noDelay = true
        let params = NWParameters(tls: nil, tcp: tcp)
        params.allowLocalEndpointReuse = true

        let listener = try NWListener(using: params, on: NWEndpoint.Port(rawValue: port)!)
        // Advertise over Bonjour so the Mac can find the phone on Wi-Fi without typing its IP.
        listener.service = NWListener.Service(type: Self.bonjourType)
        listener.newConnectionHandler = { [weak self] conn in self?.accept(conn) }
        listener.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            switch state {
            case .ready: self.report("Waiting for client on port \(self.port)")
            case .waiting(let error): self.report("Listener waiting: \(error)")
            case .failed(let error): self.report("Listener failed: \(error)")
            default: break
            }
        }
        listener.start(queue: queue)
        self.listener = listener
    }

    /// Sends one message; `completion` runs once the data has been handed to the network stack.
    func send(_ data: Data, completion: @escaping () -> Void) {
        queue.async {
            guard let conn = self.connection else { completion(); return }
            conn.send(content: data, completion: .contentProcessed { error in
                if let error { self.report("Send failed: \(error)") }
                completion()
            })
        }
    }

    private func accept(_ conn: NWConnection) {
        connection?.cancel()
        connection = conn
        conn.stateUpdateHandler = { [weak self, weak conn] state in
            guard let self, let conn else { return }
            switch state {
            case .ready:
                self.setHasClient(true)
                self.report("Client connected")
            case .failed, .cancelled:
                if self.connection === conn {
                    self.connection = nil
                    self.setHasClient(false)
                    self.report("Waiting for client on port \(self.port)")
                }
            default:
                break
            }
        }
        // Detect client disconnects: we never expect data, so any completion means EOF or error.
        conn.receive(minimumIncompleteLength: 1, maximumLength: 1) { _, _, _, _ in conn.cancel() }
        conn.start(queue: queue)
    }

    private func setHasClient(_ value: Bool) {
        lock.lock(); _hasClient = value; lock.unlock()
    }

    private func report(_ status: String) {
        DispatchQueue.main.async { self.onStatusChange?(status) }
    }
}
