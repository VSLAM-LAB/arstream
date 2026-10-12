"""Relay: one connection to the phone, fan-out to any number of subscribers over ZeroMQ PUB/SUB.

Each ARS1 message from the phone is published as one single-part ZeroMQ message, bytes untouched
(single-part so subscribers can use ZMQ_CONFLATE and always process the newest frame). An end-of-stream
message ({"event": "end"}, empty payload) is published when the relay stops, so subscribers can finish
cleanly. The optional VSLAM-LAB calibration.yaml is written from the first frame, before it is published,
so a SLAM system started afterwards finds it.
"""

import zmq

from .client import decode_message, end_message

DEFAULT_BIND = "tcp://*:5556"
DEFAULT_URL = "tcp://localhost:5556"


class Publisher:
    def __init__(self, bind: str = DEFAULT_BIND):
        self.context = zmq.Context.instance()
        self.socket = self.context.socket(zmq.PUB)
        # A subscriber that stops reading does not make the relay buffer whole seconds of frames:
        # ZeroMQ drops at the high-water mark per subscriber (CONFLATE on their side keeps the newest).
        self.socket.setsockopt(zmq.SNDHWM, 4)
        self.socket.setsockopt(zmq.LINGER, 500)
        self.socket.bind(bind)
        self.bind = bind

    def publish(self, message: bytes) -> None:
        self.socket.send(message, copy=False)

    def publish_end(self) -> None:
        self.socket.send(end_message())

    def close(self) -> None:
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def write_vslamlab_calibration(path, header: dict, fps: float) -> None:
    """VSLAM-LAB calibration.yaml (one pinhole rgb_0 camera, body frame = camera) from a frame header:
    the format Datasets/DatasetVSLAMLAB_calibration.py writes, so VSLAM-LAB baselines can be started on
    the stream with calibration_yaml:<path>."""
    K = header["K"]
    fx, fy, cx, cy = K[0], K[4], K[2], K[5]
    identity = ", ".join(f"{v:.13f}" for v in (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0))
    lines = [
        "%YAML 1.2",
        "---",
        "cameras:",
        "  - {cam_name: rgb_0,",
        "     cam_type: rgb,",
        "     cam_model: pinhole,",
        f"     focal_length: [{fx}, {fy}],",
        f"     principal_point: [{cx}, {cy}],",
        f"     image_dimension: [{header['width']}, {header['height']}],",
        f"     fps: {float(fps)},",
        f"     T_BS: [{identity}] # Sensor extrinsics wrt. the body-frame.",
        "    }",
        "",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def first_frame_header(message: bytes) -> dict:
    header, _ = decode_message(message)
    return header
