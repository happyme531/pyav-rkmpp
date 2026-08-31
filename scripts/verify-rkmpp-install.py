import importlib.metadata

import av
from av.codec.codec import Codec
from av.codec.hwaccel import hwdevices_available


assert importlib.metadata.version("pyav-rkmpp") == av.__version__
assert Codec("h264_rkmpp", "r").name == "h264_rkmpp"
assert "rkmpp" in hwdevices_available()
print(av.__version__, av.ffmpeg_version_info, hwdevices_available())
