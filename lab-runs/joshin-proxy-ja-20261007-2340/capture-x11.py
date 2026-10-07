import ctypes
import ctypes.util
import os
import struct
import sys
import zlib
from pathlib import Path


class XImage(ctypes.Structure):
    _fields_ = [('width', ctypes.c_int), ('height', ctypes.c_int), ('xoffset', ctypes.c_int),
                ('format', ctypes.c_int), ('data', ctypes.c_void_p), ('byte_order', ctypes.c_int),
                ('bitmap_unit', ctypes.c_int), ('bitmap_bit_order', ctypes.c_int),
                ('bitmap_pad', ctypes.c_int), ('depth', ctypes.c_int), ('bytes_per_line', ctypes.c_int),
                ('bits_per_pixel', ctypes.c_int), ('red_mask', ctypes.c_ulong),
                ('green_mask', ctypes.c_ulong), ('blue_mask', ctypes.c_ulong)]


output = Path(sys.argv[1])
assert os.environ['DISPLAY'] != ':0'
library = ctypes.CDLL(ctypes.util.find_library('X11'))
library.XOpenDisplay.argtypes = [ctypes.c_char_p]
library.XOpenDisplay.restype = ctypes.c_void_p
display = library.XOpenDisplay(None)
assert display
library.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
library.XDefaultRootWindow.restype = ctypes.c_ulong
library.XDefaultScreen.argtypes = [ctypes.c_void_p]
library.XDefaultScreen.restype = ctypes.c_int
library.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
library.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
screen = library.XDefaultScreen(display)
width, height = library.XDisplayWidth(display, screen), library.XDisplayHeight(display, screen)
library.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                              ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
library.XGetImage.restype = ctypes.POINTER(XImage)
image = library.XGetImage(display, library.XDefaultRootWindow(display), 0, 0, width, height,
                          ctypes.c_ulong(-1).value, 2)
assert image
captured = image.contents
assert captured.bits_per_pixel == 32 and captured.byte_order == 0
assert (captured.red_mask, captured.green_mask, captured.blue_mask) == (0xff0000, 0xff00, 0xff)
raw = ctypes.string_at(captured.data, captured.bytes_per_line * height)
rows = bytearray()
for y in range(height):
    line = raw[y * captured.bytes_per_line:y * captured.bytes_per_line + width * 4]
    rgb = bytearray(width * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = line[2::4], line[1::4], line[0::4]
    rows.extend(b'\0' + rgb)


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)


png = b'\x89PNG\r\n\x1a\n'
png += chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
png += chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')
output.write_bytes(png)
library.XDestroyImage.argtypes = [ctypes.POINTER(XImage)]
library.XDestroyImage(image)
library.XCloseDisplay.argtypes = [ctypes.c_void_p]
library.XCloseDisplay(display)
