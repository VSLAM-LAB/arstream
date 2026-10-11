"""Writes a stream to disk in a TUM-RGB-D-like layout.

out_dir/
  rgb/<timestamp>.jpg     original JPEGs from the phone (not re-encoded)
  rgb.txt                 "timestamp rgb/<timestamp>.jpg"
  arkit_poses.txt         TUM trajectory: "timestamp tx ty tz qx qy qz qw" (camera-to-world, OpenCV camera)
  frames.jsonl            full per-frame header: intrinsics, ARKit pose, tracking state, ...
"""

import json
from pathlib import Path

import numpy as np

from .client import Frame


def rotation_to_quaternion(R: np.ndarray) -> np.ndarray:
    """3x3 rotation matrix -> (qx, qy, qz, qw)."""
    m = R
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = 2.0 * np.sqrt(trace + 1.0)
        q = [(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = 2.0 * np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2])
        q = [0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s]
    elif m[1, 1] > m[2, 2]:
        s = 2.0 * np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2])
        q = [(m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s]
    else:
        s = 2.0 * np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1])
        q = [(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s]
    q = np.array(q)
    return q / np.linalg.norm(q)


class Recorder:
    def __init__(self, out_dir: str | Path):
        self.out_dir = Path(out_dir)
        (self.out_dir / "rgb").mkdir(parents=True, exist_ok=False)
        self._rgb = open(self.out_dir / "rgb.txt", "w")
        self._poses = open(self.out_dir / "arkit_poses.txt", "w")
        self._frames = open(self.out_dir / "frames.jsonl", "w")
        self._rgb.write("# timestamp filename\n")
        self._poses.write("# timestamp tx ty tz qx qy qz qw (camera-to-world, OpenCV camera axes)\n")
        self.count = 0

    def write(self, frame: Frame):
        name = f"rgb/{frame.timestamp:.6f}.jpg"
        (self.out_dir / name).write_bytes(frame.jpeg)
        self._rgb.write(f"{frame.timestamp:.6f} {name}\n")

        t = frame.T_world_camera[:3, 3]
        q = rotation_to_quaternion(frame.T_world_camera[:3, :3])
        self._poses.write(f"{frame.timestamp:.6f} " + " ".join(f"{v:.7f}" for v in (*t, *q)) + "\n")

        record = {**frame.header, "file": name, "T_world_camera_opencv": frame.T_world_camera.ravel().tolist()}
        self._frames.write(json.dumps(record) + "\n")
        self.count += 1

    def close(self):
        for f in (self._rgb, self._poses, self._frames):
            f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
