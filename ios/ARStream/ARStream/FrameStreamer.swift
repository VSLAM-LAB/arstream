import ARKit
import CoreImage
import ImageIO
import UniformTypeIdentifiers

/// Encodes ARFrames and sends them through a `StreamServer`.
///
/// Wire format (all integers little-endian), one message per frame:
///   magic "ARS1" | u32 header_len | u32 payload_len | header (UTF-8 JSON) | payload
///
/// Header fields:
///   seq, timestamp (s, device uptime clock), width, height,
///   K (3x3 row-major, for the transmitted image),
///   T_world_camera (4x4 row-major, ARKit convention: camera x right, y up, z backward;
///                   world gravity-aligned, y up),
///   tracking (string), exposure_duration (s)
///
/// The payload is the JPEG. On LiDAR devices with depth enabled it is followed by the depth map
/// (16-bit grayscale PNG, millimetres, 0 = no measurement, ARKit's native 256x192 aligned with the
/// image) and ARKit's confidence map (8-bit PNG, 0 low / 1 medium / 2 high); the header then
/// carries `jpeg_size` and `depth: {width, height, encoding: "png16", unit: "mm", size,
/// confidence_encoding: "png8", confidence_size}` so clients can split the payload.
///
/// Only one frame is in flight at a time: frames arriving while the previous one is still
/// being encoded or sent are dropped, which keeps latency low when the link is slow.
final class FrameStreamer: NSObject, ARSessionDelegate {
    struct Stats {
        var sentFPS: Double = 0
        var droppedFPS: Double = 0
        var megabitsPerSecond: Double = 0
        var tracking: String = "-"
        var resolution: String = "-"
        var depth: String = "-"
    }

    private struct EncodedDepth {
        let depthPNG: Data
        let confidencePNG: Data
        let width: Int
        let height: Int
    }

    var scale: Double = 0.5
    var jpegQuality: Double = 0.8
    var maxFPS: Double = 30
    var depthEnabled = true   // sends frame.sceneDepth when the session provides it (LiDAR devices)
    var onStats: ((Stats) -> Void)?

    private let server: StreamServer
    private let ciContext = CIContext(options: [.cacheIntermediates: false])
    private let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!
    private let encodeQueue = DispatchQueue(label: "arstream.encode", qos: .userInitiated)

    private let lock = NSLock()
    private var busy = false                 // guarded by lock
    private var sentCount = 0                // guarded by lock
    private var sentBytes = 0                // guarded by lock

    // Main-thread state (ARSession delivers delegate calls on the main queue).
    private var seq: UInt64 = 0
    private var lastSentTimestamp: TimeInterval = 0
    private var droppedCount = 0
    private var stats = Stats()
    private var statsWindowStart = Date()

    init(server: StreamServer) {
        self.server = server
    }

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        stats.tracking = Self.describe(frame.camera.trackingState)
        publishStatsIfDue()

        guard server.hasClient else { return }
        if frame.timestamp - lastSentTimestamp < 1.0 / maxFPS - 0.002 { return }

        lock.lock()
        if busy {
            lock.unlock()
            droppedCount += 1
            return
        }
        busy = true
        lock.unlock()
        lastSentTimestamp = frame.timestamp
        seq += 1

        // Copy out what we need so the ARFrame itself is not retained by the encode queue.
        let pixelBuffer = frame.capturedImage
        let camera = frame.camera
        let fullW = CVPixelBufferGetWidth(pixelBuffer)
        let fullH = CVPixelBufferGetHeight(pixelBuffer)
        let outW = Int((Double(fullW) * scale).rounded())
        let outH = Int((Double(fullH) * scale).rounded())
        var header: [String: Any] = [
            "seq": seq,
            "timestamp": frame.timestamp,
            "width": outW,
            "height": outH,
            "K": Self.scaledIntrinsics(camera.intrinsics,
                                       sx: Double(outW) / Double(fullW),
                                       sy: Double(outH) / Double(fullH)),
            "T_world_camera": Self.rowMajor(camera.transform),
            "tracking": Self.describe(camera.trackingState),
            "exposure_duration": camera.exposureDuration,
        ]
        stats.resolution = "\(outW)x\(outH)"
        let quality = jpegQuality
        // ARDepthData holds its own pixel buffers, so keeping it does not retain the ARFrame.
        let depthData = depthEnabled ? frame.sceneDepth : nil
        if let depthData {
            stats.depth = "\(CVPixelBufferGetWidth(depthData.depthMap))x\(CVPixelBufferGetHeight(depthData.depthMap))"
        } else {
            stats.depth = depthEnabled ? "none" : "off"
        }

        encodeQueue.async {
            guard let jpeg = self.encodeJPEG(pixelBuffer, width: outW, height: outH, quality: quality) else {
                self.finishSend(bytes: 0)
                return
            }
            var payload = jpeg
            if let depthData, let depth = Self.encodeDepth(depthData) {
                header["jpeg_size"] = jpeg.count
                header["depth"] = [
                    "width": depth.width,
                    "height": depth.height,
                    "encoding": "png16",
                    "unit": "mm",
                    "size": depth.depthPNG.count,
                    "confidence_encoding": "png8",
                    "confidence_size": depth.confidencePNG.count,
                ]
                payload.append(depth.depthPNG)
                payload.append(depth.confidencePNG)
            }
            guard let message = Self.makeMessage(header: header, payload: payload) else {
                self.finishSend(bytes: 0)
                return
            }
            self.server.send(message) { self.finishSend(bytes: message.count) }
        }
    }

    private func finishSend(bytes: Int) {
        lock.lock()
        busy = false
        if bytes > 0 {
            sentCount += 1
            sentBytes += bytes
        }
        lock.unlock()
    }

    private func encodeJPEG(_ pixelBuffer: CVPixelBuffer, width: Int, height: Int, quality: Double) -> Data? {
        var image = CIImage(cvPixelBuffer: pixelBuffer)
        let fullW = image.extent.width, fullH = image.extent.height
        if width != Int(fullW) || height != Int(fullH) {
            let sy = CGFloat(height) / fullH
            let sx = CGFloat(width) / fullW
            image = image
                .applyingFilter("CILanczosScaleTransform", parameters: [
                    kCIInputScaleKey: sy,
                    kCIInputAspectRatioKey: sx / sy,
                ])
                .cropped(to: CGRect(x: 0, y: 0, width: width, height: height))
        }
        let options = [CIImageRepresentationOption(rawValue: kCGImageDestinationLossyCompressionQuality as String): quality]
        return ciContext.jpegRepresentation(of: image, colorSpace: colorSpace, options: options)
    }

    /// Depth as a 16-bit grayscale PNG in millimetres (0 where ARKit has no measurement) and the confidence
    /// map as an 8-bit PNG, both at ARKit's native depth resolution.
    private static func encodeDepth(_ depth: ARDepthData) -> EncodedDepth? {
        let map = depth.depthMap
        guard CVPixelBufferGetPixelFormatType(map) == kCVPixelFormatType_DepthFloat32 else { return nil }
        let width = CVPixelBufferGetWidth(map), height = CVPixelBufferGetHeight(map)

        CVPixelBufferLockBaseAddress(map, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(map, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(map) else { return nil }
        let bytesPerRow = CVPixelBufferGetBytesPerRow(map)
        var millimetres = [UInt16](repeating: 0, count: width * height)
        for y in 0..<height {
            let row = base.advanced(by: y * bytesPerRow).assumingMemoryBound(to: Float32.self)
            for x in 0..<width {
                let metres = row[x]
                if metres.isFinite && metres > 0 {
                    millimetres[y * width + x] = UInt16(min(metres * 1000, 65535)).bigEndian  // PNG samples are big-endian
                }
            }
        }
        let depthBytes = millimetres.withUnsafeBufferPointer { Data(buffer: $0) }
        guard let depthPNG = Self.encodePNG(depthBytes, width: width, height: height, bitsPerComponent: 16) else { return nil }

        var confidencePNG = Data()
        if let confidence = depth.confidenceMap, CVPixelBufferGetPixelFormatType(confidence) == kCVPixelFormatType_OneComponent8 {
            CVPixelBufferLockBaseAddress(confidence, .readOnly)
            defer { CVPixelBufferUnlockBaseAddress(confidence, .readOnly) }
            if let confBase = CVPixelBufferGetBaseAddress(confidence) {
                let confBytesPerRow = CVPixelBufferGetBytesPerRow(confidence)
                var levels = Data(count: width * height)
                for y in 0..<height {
                    levels.replaceSubrange(y * width..<(y + 1) * width,
                                           with: Data(bytes: confBase.advanced(by: y * confBytesPerRow), count: width))
                }
                confidencePNG = Self.encodePNG(levels, width: width, height: height, bitsPerComponent: 8) ?? Data()
            }
        }
        return EncodedDepth(depthPNG: depthPNG, confidencePNG: confidencePNG, width: width, height: height)
    }

    /// Grayscale PNG (8 or 16 bits per sample) from packed samples, via ImageIO.
    private static func encodePNG(_ samples: Data, width: Int, height: Int, bitsPerComponent: Int) -> Data? {
        guard let provider = CGDataProvider(data: samples as CFData) else { return nil }
        let byteOrder: CGBitmapInfo = bitsPerComponent == 16 ? .byteOrder16Big : []
        guard let image = CGImage(width: width, height: height, bitsPerComponent: bitsPerComponent,
                                  bitsPerPixel: bitsPerComponent, bytesPerRow: width * bitsPerComponent / 8,
                                  space: CGColorSpaceCreateDeviceGray(),
                                  bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue | byteOrder.rawValue),
                                  provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
        else { return nil }
        let out = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(out, UTType.png.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { return nil }
        return out as Data
    }

    private func publishStatsIfDue() {
        let elapsed = Date().timeIntervalSince(statsWindowStart)
        guard elapsed >= 1 else { return }
        lock.lock()
        let sent = sentCount, bytes = sentBytes
        sentCount = 0
        sentBytes = 0
        lock.unlock()
        stats.sentFPS = Double(sent) / elapsed
        stats.droppedFPS = Double(droppedCount) / elapsed
        stats.megabitsPerSecond = Double(bytes) * 8 / 1e6 / elapsed
        droppedCount = 0
        statsWindowStart = Date()
        onStats?(stats)
    }

    // MARK: - Serialization helpers

    private static func makeMessage(header: [String: Any], payload: Data) -> Data? {
        guard let headerData = try? JSONSerialization.data(withJSONObject: header) else { return nil }
        var message = Data(capacity: 12 + headerData.count + payload.count)
        message.append(contentsOf: Array("ARS1".utf8))
        withUnsafeBytes(of: UInt32(headerData.count).littleEndian) { message.append(contentsOf: $0) }
        withUnsafeBytes(of: UInt32(payload.count).littleEndian) { message.append(contentsOf: $0) }
        message.append(headerData)
        message.append(payload)
        return message
    }

    /// Rescales full-resolution intrinsics to a resized image (pixel-centre convention).
    private static func scaledIntrinsics(_ k: simd_float3x3, sx: Double, sy: Double) -> [Double] {
        // simd matrices are column-major: k[column][row].
        let fx = Double(k[0][0]) * sx
        let fy = Double(k[1][1]) * sy
        let cx = (Double(k[2][0]) + 0.5) * sx - 0.5
        let cy = (Double(k[2][1]) + 0.5) * sy - 0.5
        return [fx, 0, cx,
                0, fy, cy,
                0, 0, 1]
    }

    private static func rowMajor(_ m: simd_float4x4) -> [Double] {
        var out = [Double]()
        out.reserveCapacity(16)
        for row in 0..<4 {
            for col in 0..<4 {
                out.append(Double(m[col][row]))
            }
        }
        return out
    }

    static func describe(_ state: ARCamera.TrackingState) -> String {
        switch state {
        case .normal:
            return "normal"
        case .notAvailable:
            return "not_available"
        case .limited(let reason):
            switch reason {
            case .initializing: return "limited_initializing"
            case .excessiveMotion: return "limited_excessive_motion"
            case .insufficientFeatures: return "limited_insufficient_features"
            case .relocalizing: return "limited_relocalizing"
            @unknown default: return "limited"
            }
        @unknown default:
            return "unknown"
        }
    }
}
