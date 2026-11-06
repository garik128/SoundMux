"""Generate the app's small code-native microphone icon without imaging dependencies."""
import struct
from pathlib import Path

size = 32
pixels = bytearray()
for y in reversed(range(size)):
    for x in range(size):
        mic = (12 <= x <= 19 and 5 <= y <= 20) or (8 <= x <= 9 and 15 <= y <= 23) or (22 <= x <= 23 and 15 <= y <= 23) or (9 <= x <= 22 and 22 <= y <= 24) or (15 <= x <= 16 and 24 <= y <= 27) or (11 <= x <= 20 and y == 28)
        r, g, b = (43, 26, 10) if mic else (229, 158, 36)
        pixels.extend([b, g, r, 255])
mask = bytes(size * size // 8)
bitmap = struct.pack('<IiiHHIIiiII', 40, size, size * 2, 1, 32, 0, len(pixels), 0, 0, 0, 0) + pixels + mask
image = struct.pack('<HHH', 0, 1, 1) + struct.pack('<BBBBHHII', size, size, 0, 0, 1, 32, len(bitmap), 22) + bitmap
target = Path(__file__).resolve().parents[1] / 'src-tauri/icons/icon.ico'
target.parent.mkdir(exist_ok=True)
target.write_bytes(image)
