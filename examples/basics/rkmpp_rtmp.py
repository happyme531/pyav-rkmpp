from __future__ import annotations

import argparse
import time
from fractions import Fraction

import av
from av.codec.hwaccel import HWAccel

parser = argparse.ArgumentParser(
    description="Decode and encode with RKMPP, then publish video over RTMP"
)
parser.add_argument("input")
parser.add_argument("output", help="rtmp(s):// destination or local FLV path")
parser.add_argument("--decoder", default="h264_rkmpp")
parser.add_argument("--encoder", default="h264_rkmpp")
parser.add_argument("--bitrate", type=int, default=4_000_000)
parser.add_argument("--rate", type=int, default=30)
parser.add_argument("--realtime", action="store_true")
parser.add_argument("--copy-back", action="store_true")
parser.add_argument("--frames", type=int, default=0, help="0 means until EOF")
args = parser.parse_args()

hwaccel = HWAccel(
    "rkmpp",
    allow_software_fallback=False,
    is_hw_owned=not args.copy_back,
)
source = av.open(args.input, video_decoder=args.decoder, hwaccel=hwaccel)
destination = av.open(args.output, "w", format="flv")

input_stream = source.streams.video[0]
output_stream = destination.add_stream(args.encoder, rate=args.rate)
output_stream.width = input_stream.codec_context.width
output_stream.height = input_stream.codec_context.height
output_stream.pix_fmt = "nv12" if args.copy_back else "drm_prime"
output_stream.bit_rate = args.bitrate
encoder_time_base = Fraction(1, args.rate)
output_stream.time_base = encoder_time_base

started = time.monotonic()
frame_index = 0
try:
    for frame in source.decode(input_stream):
        frame.pts = frame_index
        frame.time_base = encoder_time_base
        for packet in output_stream.encode(frame):
            destination.mux(packet)
        frame_index += 1
        if args.frames and frame_index >= args.frames:
            break
        if args.realtime:
            delay = started + frame_index / args.rate - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    for packet in output_stream.encode():
        destination.mux(packet)
finally:
    destination.close()
    source.close()

elapsed = time.monotonic() - started
if not frame_index:
    raise RuntimeError("decoder produced no frames")
print(
    f"pushed_frames={frame_index} elapsed_s={elapsed:.3f} "
    f"zero_copy={not args.copy_back}"
)
