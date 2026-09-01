from __future__ import annotations

import argparse
import errno
import os
import resource
import time
from fractions import Fraction

import numpy as np

import av
from av.codec.hwaccel import HWDevice


def build_download_graph(
    device: HWDevice,
    template: av.VideoFrame,
    width: int,
    height: int,
    async_depth: int,
) -> av.filter.Graph:
    graph = av.filter.Graph(hw_device=device)
    graph.link_nodes(
        graph.add_buffer(template=template),
        graph.add("hwupload"),
        graph.add(
            "scale_rkrga",
            w=str(width),
            h=str(height),
            format="bgr24",
            async_depth=str(async_depth),
        ),
        graph.add("hwdownload"),
        graph.add("format", pix_fmts="bgr24"),
        graph.add("buffersink"),
    ).configure()
    return graph


def build_upload_graph(
    device: HWDevice,
    template: av.VideoFrame,
    width: int,
    height: int,
    async_depth: int,
) -> av.filter.Graph:
    graph = av.filter.Graph(hw_device=device)
    graph.link_nodes(
        graph.add_buffer(template=template),
        graph.add("hwupload"),
        graph.add(
            "scale_rkrga",
            w=str(width),
            h=str(height),
            format="nv12",
            async_depth=str(async_depth),
        ),
        graph.add("buffersink"),
    ).configure()
    return graph


def pull_available(graph: av.filter.Graph) -> list[av.VideoFrame]:
    frames: list[av.VideoFrame] = []
    while True:
        try:
            frames.append(graph.pull())
        except av.EOFError:
            return frames
        except av.FFmpegError as exc:
            if exc.errno != errno.EAGAIN:
                raise
            return frames


def current_rss_kib() -> int:
    with open("/proc/self/status", encoding="utf-8") as status:
        for line in status:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    raise RuntimeError("VmRSS is missing from /proc/self/status")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Exercise NV12 -> RGA BGR24 -> RGA NV12 -> RKMPP encode without libswscale"
        )
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rate", type=int, default=30)
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--async-depth", type=int, default=2, choices=range(5))
    parser.add_argument(
        "--realtime", action="store_true", help="pace submissions at --rate FPS"
    )
    parser.add_argument(
        "--encoder", choices=("mjpeg_rkmpp", "h264_rkmpp"), default="mjpeg_rkmpp"
    )
    args = parser.parse_args()

    time_base = Fraction(1, args.rate)
    nv12 = np.zeros((args.height * 3 // 2, args.width), dtype=np.uint8)
    input_frame = av.VideoFrame.from_ndarray(nv12, format="nv12")
    input_frame.time_base = time_base
    bgr_template = av.VideoFrame(args.width, args.height, "bgr24")
    bgr_template.time_base = time_base

    device = HWDevice("rkmpp")
    download_graph = build_download_graph(
        device, input_frame, args.width, args.height, args.async_depth
    )
    upload_graph = build_upload_graph(
        device, bgr_template, args.width, args.height, args.async_depth
    )

    encoder = av.CodecContext.create(args.encoder, "w")
    encoder.width = args.width
    encoder.height = args.height
    encoder.time_base = time_base
    encoder.framerate = Fraction(args.rate, 1)
    encoder.pix_fmt = "drm_prime"

    encoded_frames = 0
    packet_count = 0

    def encode_bgr_frames(frames: list[av.VideoFrame]) -> None:
        nonlocal encoded_frames, packet_count
        for bgr_frame in frames:
            upload_graph.push(bgr_frame)
            hardware_frames = pull_available(upload_graph)
            for hardware_frame in hardware_frames:
                if hardware_frame.format.name != "drm_prime":
                    raise RuntimeError(
                        f"Expected drm_prime, got {hardware_frame.format.name}"
                    )
                if hardware_frame.sw_format is None:
                    raise RuntimeError("RKMPP frame has no software pixel format")
                if hardware_frame.sw_format.name != "nv12":
                    raise RuntimeError(
                        f"Expected NV12 backing, got {hardware_frame.sw_format.name}"
                    )
                packet_count += len(encoder.encode(hardware_frame))
                encoded_frames += 1

    fd_start = len(os.listdir("/proc/self/fd"))
    rss_start = current_rss_kib()
    fd_warm: int | None = None
    rss_warm: int | None = None
    warmup_frames = min(args.rate * 10, args.frames)
    started = time.monotonic()

    for index in range(args.frames):
        input_frame.pts = index
        download_graph.push(input_frame)
        bgr_frames = pull_available(download_graph)
        encode_bgr_frames(bgr_frames)

        if args.realtime:
            delay = started + (index + 1) / args.rate - time.monotonic()
            if delay > 0:
                time.sleep(delay)

        if (index + 1) % max(args.rate * 10, 1) == 0:
            elapsed = time.monotonic() - started
            current_fds = len(os.listdir("/proc/self/fd"))
            current_rss = current_rss_kib()
            if fd_warm is None:
                fd_warm = current_fds
                rss_warm = current_rss
            print(
                f"submitted={index + 1} encoded={encoded_frames} "
                f"effective_fps={encoded_frames / elapsed:.2f} "
                f"fds={current_fds} rss_kib={current_rss}"
            )

    download_graph.push(None)
    bgr_frames = pull_available(download_graph)
    encode_bgr_frames(bgr_frames)
    upload_graph.push(None)
    hardware_frames = pull_available(upload_graph)
    for hardware_frame in hardware_frames:
        packet_count += len(encoder.encode(hardware_frame))
        encoded_frames += 1
    packet_count += len(encoder.encode(None))

    elapsed = time.monotonic() - started
    fd_end = len(os.listdir("/proc/self/fd"))
    rss_end = current_rss_kib()
    max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if encoded_frames != args.frames:
        raise RuntimeError(f"Expected {args.frames} frames, encoded {encoded_frames}")
    if not packet_count:
        raise RuntimeError("RKMPP encoder produced no packets")
    if fd_warm is None or rss_warm is None:
        fd_warm = fd_end
        rss_warm = rss_end

    print(
        f"frames={encoded_frames} packets={packet_count} elapsed_s={elapsed:.3f} "
        f"fps={encoded_frames / elapsed:.2f} warmup_frames={warmup_frames} "
        f"fd_delta_total={fd_end - fd_start} "
        f"fd_delta_after_warmup={fd_end - fd_warm} "
        f"rss_delta_total_kib={rss_end - rss_start} "
        f"rss_delta_after_warmup_kib={rss_end - rss_warm} "
        f"maxrss_kib={max_rss}"
    )


if __name__ == "__main__":
    main()
