import os
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

import numpy as np
import pytest

import av
from av import VideoFrame
from av.filter import Graph


def require_rkmpp_filter_device() -> av.codec.hwaccel.HWDevice:
    if os.environ.get("PYAV_RKMPP_TESTS") != "1":
        pytest.skip("Set PYAV_RKMPP_TESTS=1 to run RKMPP filter tests")
    assert "rkmpp" in av.codec.hwaccel.hwdevices_available()
    assert "hwupload" in av.filter.filters_available
    assert "scale_rkrga" in av.filter.filters_available
    return av.codec.hwaccel.HWDevice("rkmpp")


def test_rkmpp_upload_scale_and_download() -> None:
    device = require_rkmpp_filter_device()
    width, height = 320, 240
    frame = VideoFrame.from_ndarray(
        np.zeros((height * 3 // 2, width), dtype=np.uint8), format="nv12"
    )
    frame.pts = 7
    frame.time_base = Fraction(1, 30)
    frame.color_range = 1  # AVCOL_RANGE_MPEG
    frame.colorspace = 1  # AVCOL_SPC_BT709
    frame.color_primaries = 1  # AVCOL_PRI_BT709
    frame.color_trc = 1  # AVCOL_TRC_BT709

    graph = Graph(hw_device=device)
    graph.link_nodes(
        graph.add_buffer(template=frame),
        graph.add("hwupload"),
        graph.add(
            "scale_rkrga",
            w=str(width),
            h=str(height),
            format="bgr24",
            async_depth="0",
        ),
        graph.add("hwdownload"),
        graph.add("format", pix_fmts="bgr24"),
        graph.add("buffersink"),
    ).configure()

    graph.push(frame)
    output = graph.pull()

    assert isinstance(output, VideoFrame)
    assert graph.hw_device is device
    assert output.width == width
    assert output.height == height
    assert output.format.name == "bgr24"
    assert output.pts == frame.pts
    assert output.time_base == frame.time_base
    assert output.color_range == 2  # AVCOL_RANGE_JPEG
    assert output.colorspace == 0  # AVCOL_SPC_RGB
    assert output.color_primaries == frame.color_primaries
    assert output.color_trc == frame.color_trc


@pytest.mark.parametrize("encoder_name", ["mjpeg_rkmpp", "h264_rkmpp"])
def test_rkrga_output_encodes_without_download(encoder_name: str) -> None:
    device = require_rkmpp_filter_device()
    width, height = 320, 240
    frame = VideoFrame.from_ndarray(
        np.zeros((height, width, 3), dtype=np.uint8), format="bgr24"
    )
    frame.pts = 0
    frame.time_base = Fraction(1, 30)

    graph = Graph(hw_device=device)
    graph.link_nodes(
        graph.add_buffer(template=frame),
        graph.add("hwupload"),
        graph.add(
            "scale_rkrga",
            w=str(width),
            h=str(height),
            format="nv12",
            async_depth="0",
        ),
        graph.add("buffersink"),
    ).configure()
    graph.push(frame)
    output = graph.pull()

    assert isinstance(output, VideoFrame)
    assert output.format.name == "drm_prime"
    assert output.sw_format is not None
    assert output.sw_format.name == "nv12"

    encoder = av.CodecContext.create(encoder_name, "w")
    assert isinstance(encoder, av.VideoCodecContext)
    encoder.width = width
    encoder.height = height
    encoder.time_base = Fraction(1, 30)
    encoder.framerate = Fraction(30, 1)
    encoder.pix_fmt = "drm_prime"
    packets = encoder.encode(output)
    packets.extend(encoder.encode(None))

    assert any(packet.size for packet in packets)


def test_rkmpp_filter_graphs_in_parallel_threads() -> None:
    device = require_rkmpp_filter_device()
    width, height = 320, 240

    def process_frames() -> int:
        frame = VideoFrame.from_ndarray(
            np.zeros((height * 3 // 2, width), dtype=np.uint8), format="nv12"
        )
        frame.time_base = Fraction(1, 30)
        graph = Graph(hw_device=device)
        graph.link_nodes(
            graph.add_buffer(template=frame),
            graph.add("hwupload"),
            graph.add(
                "scale_rkrga",
                w=str(width),
                h=str(height),
                format="bgr24",
                async_depth="0",
            ),
            graph.add("hwdownload"),
            graph.add("format", pix_fmts="bgr24"),
            graph.add("buffersink"),
        ).configure()

        count = 0
        for index in range(16):
            frame.pts = index
            graph.push(frame)
            output = graph.pull()
            assert isinstance(output, VideoFrame)
            assert output.pts == index
            count += 1
        return count

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(lambda _: process_frames(), range(2))) == [16, 16]
