"""Check actual icon pixels: dimensions alone missed the empty small ICO frames."""
import base64
import binascii
import importlib.util
import io
from pathlib import Path
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('favicon_fix', ROOT / 'deploy/ubuntu/fix-favicon.py')
fix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix)
LEGACY_SMALL_FRAME = 'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAQUlEQVR4nOzTsQ0AIAhE0dMwCI3TOK/T2DCKsWAAvUJDeA3dDyFBcEm1jT0rSO8DBaQAN8jADwHxr3Jms+MAvcECAAD//0N/CzkAAAAGSURBVAMAV38FPgMDmvYAAAAASUVORK5CYII='


def pixels(png):
    if png[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Expected PNG')
    offset, compressed, dimensions = 8, bytearray(), None
    while offset < len(png):
        length = struct.unpack_from('>I', png, offset)[0]
        kind = png[offset + 4:offset + 8]
        data = png[offset + 8:offset + 8 + length]
        crc = struct.unpack_from('>I', png, offset + 8 + length)[0]
        if binascii.crc32(kind + data) & 0xffffffff != crc:
            raise ValueError('Invalid PNG checksum')
        if kind == b'IHDR':
            dimensions = struct.unpack('>IIBBBBB', data)
        elif kind == b'IDAT':
            compressed.extend(data)
        offset += 12 + length
        if kind == b'IEND':
            break
    width, height, depth, color, compression, filtering, interlace = dimensions
    if (depth, color, compression, filtering, interlace) != (8, 6, 0, 0, 0):
        raise ValueError('Expected non-interlaced RGBA8 asset')
    raw = zlib.decompress(compressed)
    stride = width * 4
    if len(raw) != height * (stride + 1):
        raise ValueError('Unexpected PNG scanline size')
    previous = bytearray(stride)
    output = []
    for y in range(height):
        start = y * (stride + 1)
        mode = raw[start]
        row = bytearray(raw[start + 1:start + 1 + stride])
        for x in range(stride):
            left = row[x - 4] if x >= 4 else 0
            above = previous[x]
            corner = previous[x - 4] if x >= 4 else 0
            if mode == 1:
                predictor = left
            elif mode == 2:
                predictor = above
            elif mode == 3:
                predictor = (left + above) // 2
            elif mode == 4:
                p = left + above - corner
                distances = [abs(p - value) for value in (left, above, corner)]
                predictor = (left, above, corner)[distances.index(min(distances))]
            elif mode == 0:
                predictor = 0
            else:
                raise ValueError('Invalid PNG filter')
            row[x] = (row[x] + predictor) & 255
        output.extend(tuple(row[x:x + 4]) for x in range(0, stride, 4))
        previous = row
    return width, height, output


def has_visible_badge(png):
    width, height, rgba = pixels(png)
    green = sum(a >= 128 and g > 40 and g > r * 1.25 and g > b * 1.03 for r, g, b, a in rgba)
    white = sum(a >= 128 and min(r, g, b) >= 225 for r, g, b, a in rgba)
    return green > width * height * .08 and white > width * height * .005


class FaviconTests(unittest.TestCase):
    def test_every_shipped_ico_frame_contains_the_green_badge_and_white_letters(self):
        data = (ROOT / fix.ICON).read_bytes()
        reserved, kind, count = struct.unpack_from('<HHH', data)
        self.assertEqual((reserved, kind), (0, 1))
        self.assertGreaterEqual(count, 1)
        for i in range(count):
            width, height, _, _, planes, bits, length, offset = struct.unpack_from('<BBBBHHII', data, 6 + 16 * i)
            frame = data[offset:offset + length]
            self.assertEqual((width or 256, height or 256), pixels(frame)[:2])
            self.assertEqual((planes, bits), (1, 32))
            self.assertTrue(has_visible_badge(frame), 'Empty/cropped ICO frame')

    def test_the_previously_shipped_16px_frame_is_detected_as_defective(self):
        old = base64.b64decode(LEGACY_SMALL_FRAME)
        self.assertEqual(pixels(old)[:2], (16, 16))
        self.assertFalse(has_visible_badge(old))

    def test_browser_png_and_fallback_ico_use_the_same_intact_approved_asset(self):
        png = (ROOT / fix.SOURCE).read_bytes()
        self.assertEqual(fix.checksum(png), fix.SOURCE_SHA256)
        self.assertTrue(has_visible_badge(png))
        self.assertEqual((ROOT / fix.ICON).read_bytes(), fix.icon_bytes(png))
        self.assertEqual((ROOT / fix.TEMPLATE).read_bytes(), fix.NEW_TEMPLATE)
        self.assertNotIn(b'shortcut icon', fix.NEW_TEMPLATE)
        for invalid in (b'not a PNG', png[:16] + struct.pack('>II', 32, 32) + png[24:]):
            with self.assertRaises(ValueError):
                fix.icon_bytes(invalid)


class FaviconUpdaterTests(unittest.TestCase):
    def fixture(self, directory):
        app = Path(directory).resolve() / 'app'
        for name, data in [(fix.SOURCE, (ROOT / fix.SOURCE).read_bytes()),
                           (fix.ICON, b'old icon'), (fix.TEMPLATE, b'old links')]:
            target = app / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return app

    def test_failed_post_restart_check_restores_both_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.fixture(directory)
            before = {fix.ICON: fix.checksum(b'old icon'), fix.TEMPLATE: fix.checksum(b'old links')}
            with patch.multiple(fix, APP=app, STATE=app.parent / 'receipts', BEFORE=before), \
                    patch.object(fix.os, 'geteuid', return_value=0), \
                    patch.object(fix, 'request', return_value=(200, b'ok')), \
                    patch.object(fix, 'restart') as restart, \
                    patch.object(fix, 'verify', side_effect=RuntimeError('failed check')), \
                    redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'previous files restored'):
                    fix.main()
            self.assertEqual((app / fix.ICON).read_bytes(), b'old icon')
            self.assertEqual((app / fix.TEMPLATE).read_bytes(), b'old links')
            self.assertEqual(restart.call_count, 2)

    def test_unknown_server_edit_stops_before_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.fixture(directory)
            with patch.multiple(fix, APP=app, STATE=app.parent / 'receipts'), \
                    patch.object(fix.os, 'geteuid', return_value=0), \
                    patch.object(fix, 'restart') as restart:
                with self.assertRaisesRegex(RuntimeError, 'Unreviewed server change'):
                    fix.main()
                restart.assert_not_called()
            self.assertEqual((app / fix.ICON).read_bytes(), b'old icon')
            self.assertEqual((app / fix.TEMPLATE).read_bytes(), b'old links')


if __name__ == '__main__':
    unittest.main()
