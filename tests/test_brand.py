"""The integration's own brand images (Home Assistant 2026.3 and later read custom_components/<domain>/brand/)."""
import pathlib
import struct

BRAND = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "powerengine" / "brand"


def _size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    return struct.unpack(">II", data[16:24])


def test_the_icons_are_square_and_the_2x_is_double():
    for prefix in ("icon", "dark_icon"):
        w, h = _size(BRAND / f"{prefix}.png")
        assert w == h == 256
        assert _size(BRAND / f"{prefix}@2x.png") == (512, 512)


def test_the_logos_are_wide_and_the_2x_is_double():
    for prefix in ("logo", "dark_logo"):
        w, h = _size(BRAND / f"{prefix}.png")
        assert h == 256 and w > h
        assert _size(BRAND / f"{prefix}@2x.png") == (w * 2, h * 2)
