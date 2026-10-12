// Smoke test and example: prints one line per second until the end-of-stream message.
//   arstream_subscribe [tcp://localhost:5556] [--queue]
#include <chrono>
#include <csignal>
#include <iostream>
#include <string>

#include <arstream/arstream.hpp>

static arstream::Subscriber* g_subscriber = nullptr;
static void onSignal(int) { if (g_subscriber) g_subscriber->stop(); }

int main(int argc, char** argv) {
    std::string url = arstream::kDefaultUrl;
    bool conflate = true;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--queue") conflate = false;
        else if (arg == "--help") { std::cout << "usage: arstream_subscribe [URL] [--queue]\n"; return 0; }
        else url = arg;
    }

    arstream::Subscriber subscriber(url, conflate);
    g_subscriber = &subscriber;
    std::signal(SIGINT, onSignal);
    std::signal(SIGTERM, onSignal);

    std::cout << "Subscribed to " << url << (conflate ? " (newest frame)" : " (queued)") << std::endl;
    arstream::Frame frame;
    size_t count = 0;
    auto window = std::chrono::steady_clock::now();
    size_t inWindow = 0;
    while (subscriber.next(frame)) {
        ++count;
        ++inWindow;
        const auto now = std::chrono::steady_clock::now();
        const double elapsed = std::chrono::duration<double>(now - window).count();
        if (elapsed >= 1.0) {
            std::cout << "[" << count << " frames] " << inWindow / elapsed << " fps  " << frame.width << "x" << frame.height
                      << "  fx=" << frame.K(0, 0) << "  t=" << frame.timestamp << "  " << frame.tracking << std::endl;
            window = now;
            inWindow = 0;
        }
    }
    std::cout << (subscriber.ended() ? "End of stream" : "Stopped") << " after " << count << " frames" << std::endl;
    return 0;
}
