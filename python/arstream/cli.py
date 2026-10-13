"""Command line entry point: `arstream view`, `arstream record OUT_DIR` and `arstream publish`."""

import argparse
import time
from datetime import datetime

import numpy as np

from .client import DEFAULT_PORT, ARStreamClient, Frame
from .publisher import DEFAULT_BIND, DEFAULT_URL, Publisher, first_frame_header, write_vslamlab_calibration
from .recorder import Recorder
from .subscriber import Subscriber
from .usbmux import UsbmuxError


def open_client(args) -> ARStreamClient:
    if args.wifi:
        from .discovery import discover
        print("Looking for the phone on Wi-Fi (Bonjour)…")
        services = discover()
        if not services:
            raise SystemExit("No phone found. Same network? Local Network access allowed for ARStream? "
                             "Otherwise pass --host <IP shown in the app>.")
        if len(services) > 1:
            print("Several phones found, using the first:", [s.name for s in services])
        args.host, args.port = services[0].host, services[0].port
    try:
        if args.host:
            print(f"Connecting to {args.host}:{args.port} over Wi-Fi…")
            return ARStreamClient.tcp(args.host, args.port)
        print(f"Connecting to port {args.port} over USB…")
        return ARStreamClient.usb(args.port, args.serial)
    except (UsbmuxError, OSError) as e:
        raise SystemExit(f"Could not connect to the phone: {e}") from None


def open_source(args):
    """Frames from the phone (default) or from a relay (--sub URL)."""
    if args.sub:
        print(f"Subscribing to {args.sub}…")
        return Subscriber(args.sub)
    return open_client(args)


class RateMeter:
    def __init__(self):
        self.start = time.monotonic()
        self.count = 0

    def tick(self) -> float | None:
        """Counts a frame; returns the rate once per second, else None."""
        self.count += 1
        elapsed = time.monotonic() - self.start
        if elapsed < 1.0:
            return None
        rate = self.count / elapsed
        self.start, self.count = time.monotonic(), 0
        return rate


def status_line(frame: Frame, fps: float) -> str:
    t = frame.T_world_camera[:3, 3]
    depth = f"  depth {frame.header['depth']['width']}x{frame.header['depth']['height']}" if frame.has_depth else ""
    return (f"{fps:5.1f} fps  {frame.width}x{frame.height}{depth}  "
            f"fx={frame.K[0, 0]:.1f}  pos=[{t[0]:+.3f} {t[1]:+.3f} {t[2]:+.3f}]  {frame.tracking}")


class RerunLogger:
    def __init__(self):
        import rerun as rr
        self.rr = rr
        rr.init("arstream", spawn=True)
        rr.log("world", rr.ViewCoordinates.RIGHT_HAND_Y_UP, static=True)  # ARKit world: y up
        self.positions = []

    def log(self, frame: Frame):
        rr = self.rr
        rr.set_time("device_time", duration=frame.timestamp)
        T = frame.T_world_camera
        rr.log("world/camera", rr.Transform3D(translation=T[:3, 3], mat3x3=T[:3, :3]))
        rr.log("world/camera/image", rr.Pinhole(
            image_from_camera=frame.K,
            resolution=[frame.width, frame.height],
            camera_xyz=rr.ViewCoordinates.RDF,
            image_plane_distance=0.1,
        ))
        rr.log("world/camera/image", rr.EncodedImage(contents=frame.jpeg, media_type="image/jpeg"))
        if frame.has_depth:
            # Depth under its own pinhole at the native depth resolution (K scaled down): rerun back-projects it
            depth = frame.depth
            dh, dw = depth.shape[:2]
            K_depth = frame.K.copy()
            K_depth[0] *= dw / frame.width
            K_depth[1] *= dh / frame.height
            rr.log("world/camera/depth", rr.Pinhole(image_from_camera=K_depth, resolution=[dw, dh],
                                                     camera_xyz=rr.ViewCoordinates.RDF, image_plane_distance=0.1))
            rr.log("world/camera/depth", rr.DepthImage(depth, meter=1000.0))
        rr.log("tracking", rr.TextLog(frame.tracking))

        self.positions.append(T[:3, 3].copy())
        if len(self.positions) % 5 == 0:
            rr.log("world/trajectory", rr.LineStrips3D([np.array(self.positions)]))


def cmd_view(args):
    logger = RerunLogger()
    meter = RateMeter()
    with open_source(args) as source:
        for frame in source:
            logger.log(frame)
            if (fps := meter.tick()) is not None:
                print(status_line(frame, fps))
    print("Stream ended.")


def cmd_record(args):
    out_dir = args.out_dir or f"recordings/{datetime.now():%Y%m%d_%H%M%S}"
    logger = RerunLogger() if args.view else None
    meter = RateMeter()
    with open_source(args) as source, Recorder(out_dir) as recorder:
        stop = f"{args.duration:g} s" if args.duration else "Ctrl-C"
        print(f"Recording to {out_dir} (stops after {stop})")
        start = time.monotonic()
        try:
            for frame in source:
                recorder.write(frame)
                if logger:
                    logger.log(frame)
                if (fps := meter.tick()) is not None:
                    print(f"[{recorder.count} frames] " + status_line(frame, fps))
                if args.duration and time.monotonic() - start >= args.duration:
                    break
        except KeyboardInterrupt:
            pass
    print(f"Saved {recorder.count} frames to {out_dir}")


def cmd_publish(args):
    meter = RateMeter()
    count = 0
    with open_client(args) as client, Publisher(args.bind) as publisher:
        stop = f"{args.duration:g} s" if args.duration else "Ctrl-C"
        print(f"Publishing on {args.bind} (stops after {stop})")
        start = time.monotonic()
        try:
            for message in client.messages():
                if count == 0 and args.calibration_yaml:
                    write_vslamlab_calibration(args.calibration_yaml, first_frame_header(message), args.fps)
                    print(f"Wrote {args.calibration_yaml}")
                publisher.publish(message)
                count += 1
                if (fps := meter.tick()) is not None:
                    print(f"[{count} frames] {fps:5.1f} fps published")
                if args.duration and time.monotonic() - start >= args.duration:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            publisher.publish_end()
    print(f"Published {count} frames, end of stream sent.")


def main():
    parser = argparse.ArgumentParser(prog="arstream", description=__doc__)
    phone = argparse.ArgumentParser(add_help=False)
    phone.add_argument("--wifi", action="store_true", help="find the phone on Wi-Fi via Bonjour (default: USB)")
    phone.add_argument("--host", help="phone IP for Wi-Fi")
    phone.add_argument("--port", type=int, default=DEFAULT_PORT)
    phone.add_argument("--serial", help="device serial/UDID if several phones are plugged in")
    source = argparse.ArgumentParser(add_help=False, parents=[phone])
    source.add_argument("--sub", nargs="?", const=DEFAULT_URL, metavar="URL",
                        help=f"read from a relay (`arstream publish`) instead of the phone (default URL {DEFAULT_URL})")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("view", parents=[source], help="live 3D view in rerun")
    p.set_defaults(func=cmd_view)

    p = sub.add_parser("record", parents=[source], help="record to disk")
    p.add_argument("out_dir", nargs="?", help="default: recordings/<date_time>")
    p.add_argument("--view", action="store_true", help="also show the rerun viewer")
    p.add_argument("--duration", type=float, help="stop after this many seconds (default: until Ctrl-C)")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("publish", parents=[phone], help="relay the phone's stream to ZeroMQ subscribers")
    p.add_argument("--bind", default=DEFAULT_BIND, help=f"ZeroMQ PUB endpoint (default {DEFAULT_BIND})")
    p.add_argument("--calibration-yaml", metavar="PATH",
                   help="write a VSLAM-LAB calibration.yaml from the first frame before publishing it")
    p.add_argument("--fps", type=float, default=30.0, help="nominal frame rate written to the calibration (default 30)")
    p.add_argument("--duration", type=float, help="stop after this many seconds (default: until Ctrl-C)")
    p.set_defaults(func=cmd_publish)

    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
