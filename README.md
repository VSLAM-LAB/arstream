# ARStream: iPhone → Mac streaming for VSLAM

Streams rear-camera RGB + intrinsics + ARKit 6-DoF pose from an iPhone to the Mac,
over USB (default) or Wi-Fi. [VSLAM-LAB](https://github.com/VSLAM-LAB/VSLAM-LAB) turns a
recording into a sequence of its `iphone` dataset with `pixi run record-iphone`.

```
ios/ARStream/      iOS app (ARKit world tracking → JPEG + JSON header → TCP server on port 5555)
python/arstream/   Mac client: USB via macOS usbmuxd, viewer (rerun), recorder, ZeroMQ relay + subscriber
cpp/               header-only C++ subscriber (arstream/arstream.hpp) for SLAM systems running online
```

## Phone setup (once)
1. `sudo xcodebuild -license accept`
2. Open `ios/ARStream/ARStream.xcodeproj` in Xcode → target ARStream → *Signing & Capabilities* →
   pick your Team (a free Apple ID works; add it under Xcode → Settings → Accounts).
   If the bundle id is taken, change `PRODUCT_BUNDLE_IDENTIFIER`.
3. iPhone: Settings → Privacy & Security → Developer Mode → on (reboots).
4. Plug in the phone, select it as run destination, Run (⌘R).
   First launch: Settings → General → VPN & Device Management → trust your developer certificate.

## Usage
Open the ARStream app, then on the Mac:

```sh
pixi run view                      # live 3D view over USB
pixi run view --wifi               # over Wi-Fi, phone found via Bonjour
pixi run view --host 192.168.x.y   # over Wi-Fi with an explicit IP (shown in the app)
pixi run record                    # → recordings/<date_time>/
pixi run record --view my_scan     # record and view
pixi run record --duration 30      # stop by itself after 30 s
pixi run ios-install               # build, install and launch the app on the USB iPhone
pixi run ios-launch                # bring the installed app to the foreground on the USB iPhone
pixi run ios-build                 # compile-check the iOS app (unsigned)
```

From Python:

```python
from arstream import ARStreamClient
with ARStreamClient.usb() as client:
    for frame in client:
        frame.image            # BGR uint8
        frame.K                # 3x3 intrinsics of frame.image
        frame.T_world_camera   # 4x4 camera-to-world, OpenCV camera axes (x right, y down, z forward)
        frame.timestamp        # seconds, phone clock
        frame.tracking         # "normal" when ARKit's pose is reliable
```

The app only listens while it is in the foreground on an unlocked phone (iOS suspends it otherwise,
and connections are refused). Over USB, `pixi run ios-launch` brings it to the front from the Mac.

## Streaming to SLAM systems (relay)
The phone serves one client at a time. `publish` connects to it once and re-publishes every frame
over ZeroMQ PUB/SUB, so any number of subscribers (viewer, SLAM systems, your own code) run at once:

```sh
pixi run publish                            # phone over USB → tcp://*:5556
pixi run publish --wifi --bind tcp://*:6000
pixi run publish --calibration-yaml calibration.yaml   # VSLAM-LAB calibration from the first frame, written before it is published
pixi run view --sub                         # viewer as a subscriber (default tcp://localhost:5556)
pixi run record --sub my_scan               # recorder as a subscriber
```

Each ZeroMQ message is one ARS1 message (bytes untouched, see "Wire format"); the relay sends
`{"event": "end"}` with an empty payload when it stops. Subscribers default to keeping only the
newest frame (`ZMQ_CONFLATE`): a SLAM system slower than the stream skips frames instead of lagging.

```python
from arstream import Subscriber
with Subscriber("tcp://localhost:5556") as sub:   # conflate=False, queue=30 to buffer instead
    for frame in sub:                             # ends at the publisher's end-of-stream message
        ...
```

```cpp
#include <arstream/arstream.hpp>                  // cppzmq + nlohmann_json + OpenCV
arstream::Subscriber sub("tcp://localhost:5556");
arstream::Frame frame;
while (sub.next(frame))                           // false at end of stream (or after sub.stop())
    slam.Track(frame.image, frame.timestamp);     // frame.K, frame.T_world_camera, frame.tracking
```

Conda packages `arstream` (Python) and `libarstream` (C++ header + CMake config,
`find_package(arstream)` → `arstream::arstream`) are published to prefix.dev/vslamlab on version
tags; `pixi run build-packages` builds them locally into `output/`.

## Wi-Fi notes
- Phone and Mac must be on the same network, and ARStream needs Local Network access
  (Settings → Privacy & Security → Local Network). Restart the app after granting it.
- Campus networks (eduroam) usually block device-to-device traffic. Use the phone's
  Personal Hotspot and join it from the Mac instead.
- 960×720 reached ~23 fps on Wi-Fi vs 30 fps on USB; 640×480 in the app needs less than half
  the bandwidth if you need the full rate.
- Bluetooth is not supported: BLE is far too slow (<~1–2 Mbit/s vs ~15 Mbit/s needed).

## Conventions
- World frame: ARKit, gravity-aligned, **y up**, origin where the session started.
- `T_world_camera` is converted to OpenCV camera axes; the raw ARKit matrix is in `frame.header`.
- Intrinsics are rescaled on the phone to the transmitted resolution.
- Autofocus is off by default (stable intrinsics); toggle in the app.

## Recording layout
```
rgb/<timestamp>.jpg   rgb.txt   arkit_poses.txt (TUM format)   frames.jsonl (all metadata)
```

## Wire format
Per frame, little-endian: `"ARS1" | u32 header_len | u32 jpeg_len | JSON header | JPEG`.
See `ios/ARStream/ARStream/FrameStreamer.swift`. The same message is what the relay publishes and
what `frames.jsonl` records (one header per line); a header with an `"event"` key (`"end"`) is a
control message with an empty payload.
