from __future__ import annotations

import argparse
import time

import av
from av.codec.hwaccel import HWAccel

parser = argparse.ArgumentParser(description="Decode video with Rockchip MPP")
parser.add_argument("input", help="input file or network URL")
parser.add_argument("--decoder", default="h264_rkmpp")
parser.add_argument("--frames", type=int, default=120)
parser.add_argument("--download", action="store_true")
args = parser.parse_args()

hwaccel = HWAccel(
    "rkmpp",
    allow_software_fallback=False,
    is_hw_owned=not args.download,
)

count = 0
started = time.monotonic()
with av.open(
    args.input,
    video_decoder=args.decoder,
    hwaccel=hwaccel,
) as container:
    stream = container.streams.video[0]
    if stream.codec_context.codec.name != args.decoder:
        raise RuntimeError(
            f"requested {args.decoder}, got {stream.codec_context.codec.name}"
        )
    for frame in container.decode(stream):
        if args.download:
            if frame.format.name == "drm_prime":
                raise RuntimeError("frame was not downloaded to system memory")
            if not frame.planes or not frame.planes[0].buffer_size:
                raise RuntimeError("downloaded an empty frame")
        count += 1
        if count >= args.frames:
            break

elapsed = time.monotonic() - started
if not count:
    raise RuntimeError("decoder produced no frames")
print(
    f"decoder={args.decoder} frames={count} elapsed_s={elapsed:.3f} "
    f"fps={count / elapsed:.2f} download={args.download}"
)
