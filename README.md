# ARStream: iPhone → Mac streaming for VSLAM

Streams rear-camera RGB + intrinsics + ARKit 6-DoF pose from an iPhone to the Mac,
over USB (default) or Wi-Fi. [VSLAM-LAB](https://github.com/VSLAM-LAB/VSLAM-LAB) turns a
recording into a sequence of its `iphone` dataset with `pixi run record-iphone`.

```
ios/ARStream/      iOS app (ARKit world tracking → JPEG + JSON header → TCP server on port 5555)
python/arstream/   Mac client: USB via macOS usbmuxd, viewer (rerun), recorder
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
See `ios/ARStream/ARStream/FrameStreamer.swift`.
