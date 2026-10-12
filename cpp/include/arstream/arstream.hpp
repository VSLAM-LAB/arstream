// ARStream C++ subscriber: frames relayed by `arstream publish` (ZeroMQ PUB/SUB, one ARS1 message per
// ZeroMQ message). Header-only; needs cppzmq, nlohmann_json and OpenCV (imgcodecs).
//
//   arstream::Subscriber sub("tcp://localhost:5556");
//   arstream::Frame frame;
//   while (sub.next(frame))           // false at the publisher's end-of-stream message (or after stop())
//       slam.Track(frame.image, frame.timestamp);
//
// ARS1 message, little-endian: "ARS1" | u32 header_len | u32 payload_len | JSON header | JPEG.
// Header: seq, timestamp (s, phone clock), width, height, K (3x3 row-major), T_world_camera (4x4 row-major,
// ARKit camera axes), tracking, exposure_duration. A header with "event" ("end") is a control message.
#pragma once

#include <atomic>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>

#include <nlohmann/json.hpp>
#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>
#include <zmq.hpp>

namespace arstream {

inline constexpr const char* kDefaultUrl = "tcp://localhost:5556";
inline constexpr const char* kMagic = "ARS1";
inline constexpr size_t kPrefixSize = 12;

struct Frame {
    uint64_t seq = 0;
    double timestamp = 0.0;           // seconds, phone clock
    int width = 0, height = 0;
    cv::Mat image;                    // BGR 8-bit
    cv::Matx33d K;                    // intrinsics of `image`
    cv::Matx44d T_world_camera;       // camera-to-world, OpenCV camera axes (x right, y down, z forward)
    std::string tracking;             // "normal" when ARKit's pose is reliable
    nlohmann::json header;            // the full JSON header
};

struct Message {
    nlohmann::json header;
    const uint8_t* payload = nullptr;
    size_t payload_size = 0;
    bool is_event() const { return header.contains("event"); }
};

inline uint32_t readU32(const uint8_t* p) {
    return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

// Parses one ARS1 message held in memory (the payload is not copied).
inline Message decode(const uint8_t* data, size_t size) {
    if (size < kPrefixSize || std::memcmp(data, kMagic, 4) != 0)
        throw std::runtime_error("arstream: not an ARS1 message");
    const uint32_t headerLen = readU32(data + 4);
    const uint32_t payloadLen = readU32(data + 8);
    if (size != kPrefixSize + headerLen + payloadLen)
        throw std::runtime_error("arstream: ARS1 message length mismatch");
    Message m;
    m.header = nlohmann::json::parse(data + kPrefixSize, data + kPrefixSize + headerLen);
    m.payload = data + kPrefixSize + headerLen;
    m.payload_size = payloadLen;
    return m;
}

// ARKit cameras look down -z with y up; OpenCV cameras look down +z with y down.
inline cv::Matx44d arkitToOpenCV(const cv::Matx44d& T) {
    const cv::Matx44d flip = cv::Matx44d::diag(cv::Vec4d(1.0, -1.0, -1.0, 1.0));
    return T * flip;
}

inline Frame toFrame(const Message& m) {
    Frame f;
    f.header = m.header;
    f.seq = m.header.value("seq", uint64_t(0));
    f.timestamp = m.header.at("timestamp").get<double>();
    f.width = m.header.at("width").get<int>();
    f.height = m.header.at("height").get<int>();
    const auto K = m.header.at("K").get<std::vector<double>>();
    const auto T = m.header.at("T_world_camera").get<std::vector<double>>();
    if (K.size() != 9 || T.size() != 16)
        throw std::runtime_error("arstream: K must have 9 and T_world_camera 16 values");
    f.K = cv::Matx33d(K.data());
    f.T_world_camera = arkitToOpenCV(cv::Matx44d(T.data()));
    f.tracking = m.header.value("tracking", "");
    const cv::Mat jpeg(1, int(m.payload_size), CV_8UC1, const_cast<uint8_t*>(m.payload));
    f.image = cv::imdecode(jpeg, cv::IMREAD_COLOR);
    if (f.image.empty())
        throw std::runtime_error("arstream: could not decode the JPEG payload");
    return f;
}

class Subscriber {
public:
    // conflate: keep only the newest frame (a slow consumer skips frames instead of lagging); otherwise
    // up to `queue` frames are buffered before the oldest are dropped.
    explicit Subscriber(const std::string& url = kDefaultUrl, bool conflate = true, int queue = 30, int pollMs = 100)
        : context_(1), socket_(context_, zmq::socket_type::sub), pollMs_(pollMs) {
        if (conflate)
            socket_.set(zmq::sockopt::conflate, 1);
        else
            socket_.set(zmq::sockopt::rcvhwm, queue);
        socket_.set(zmq::sockopt::linger, 0);
        socket_.set(zmq::sockopt::subscribe, "");
        socket_.connect(url);
    }

    // Next frame; false once the end-of-stream message arrived or stop() was called. Control messages
    // other than "end" are skipped.
    bool next(Frame& frame) {
        zmq::message_t message;
        while (!stop_.load()) {
            if (!receive(message))
                continue;
            const Message m = decode(static_cast<const uint8_t*>(message.data()), message.size());
            if (m.is_event()) {
                if (m.header["event"] == "end") {
                    ended_ = true;
                    return false;
                }
                continue;
            }
            frame = toFrame(m);
            return true;
        }
        return false;
    }

    // Makes next() return false; safe to call from a signal handler's flag check or another thread.
    void stop() { stop_.store(true); }
    bool ended() const { return ended_; }

private:
    bool receive(zmq::message_t& message) {
        zmq::pollitem_t item{socket_.handle(), 0, ZMQ_POLLIN, 0};
        try {
            if (zmq::poll(&item, 1, std::chrono::milliseconds(pollMs_)) == 0)
                return false;
            return socket_.recv(message, zmq::recv_flags::dontwait).has_value();
        } catch (const zmq::error_t& e) {
            if (e.num() == EINTR)  // interrupted by a signal: let the caller's stop() decide
                return false;
            throw;
        }
    }

    zmq::context_t context_;
    zmq::socket_t socket_;
    int pollMs_;
    std::atomic<bool> stop_{false};
    bool ended_ = false;
};

}  // namespace arstream
