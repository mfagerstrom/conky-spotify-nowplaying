#!/usr/bin/env python3
"""Save the running widget's window as a PNG.

usage: scripts/capture-widget.py <out.png>

The widget is an X window (Conky runs under XWayland), so its pixels can be read
straight from the X server through libX11 without a screenshot portal or any tool
outside the widget's own dependencies. Only the widget window is captured, never
the rest of the screen.

The window is translucent and holds premultiplied ARGB, in which Conky's image draw
leaves near-white channels above their pixel's alpha. The compositor adds those, so
they show as white; each channel is clamped to 255 as it is un-premultiplied, where
GDK's own capture wraps it round to a dark value and shows false colour.
"""
import ctypes
import re
import subprocess
import sys

import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, GLib  # noqa: E402

WM_CLASS = 'ConkySpotifyNowPlaying'   # own_window_class in src/conky.conf


def find_window():
    run = subprocess.run(['xwininfo', '-root', '-tree'], capture_output=True, text=True)
    if run.returncode != 0:
        sys.exit(f'xwininfo failed: {run.stderr.strip()}')
    tree = run.stdout
    m = re.search(rf'^\s*(0x[0-9a-f]+) .*"{WM_CLASS}"', tree, re.MULTILINE)
    return int(m.group(1), 16) if m else None


class XImage(ctypes.Structure):
    _fields_ = [('width', ctypes.c_int), ('height', ctypes.c_int), ('xoffset', ctypes.c_int),
                ('format', ctypes.c_int), ('data', ctypes.c_void_p), ('byte_order', ctypes.c_int),
                ('bitmap_unit', ctypes.c_int), ('bitmap_bit_order', ctypes.c_int),
                ('bitmap_pad', ctypes.c_int), ('depth', ctypes.c_int),
                ('bytes_per_line', ctypes.c_int), ('bits_per_pixel', ctypes.c_int)]   # and more, unused


def read_window(xid):
    """The window's width, height and pixels as 8-bit RGBA, un-premultiplied."""
    x11 = ctypes.CDLL('libX11.so.6')
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    uint = ctypes.c_uint
    x11.XGetGeometry.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                 ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                                 *[ctypes.POINTER(uint)] * 4]
    x11.XGetImage.restype = ctypes.POINTER(XImage)
    x11.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                              uint, uint, ctypes.c_ulong, ctypes.c_int]
    dpy = x11.XOpenDisplay(None)
    if not dpy:
        sys.exit('no X display; run this from the desktop session')
    root, x, y = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_int()
    width, height, border, depth = uint(), uint(), uint(), uint()
    if not x11.XGetGeometry(dpy, xid, root, x, y, width, height, border, depth):
        sys.exit(f'could not read window {xid:#x}')
    image = x11.XGetImage(dpy, xid, 0, 0, width, height, 0xffffffff, 2)   # AllPlanes, ZPixmap
    if not image:
        sys.exit(f'could not read window {xid:#x}; is it on screen?')
    img = image.contents
    if img.bits_per_pixel != 32 or img.byte_order != 0:
        sys.exit(f'unexpected pixel format: {img.bits_per_pixel} bpp, byte order {img.byte_order}')
    w, h, stride, opaque = img.width, img.height, img.bytes_per_line, img.depth < 32
    raw = ctypes.string_at(img.data, stride * h)
    out = bytearray(w * h * 4)
    for row in range(h):
        for col in range(w):
            i = row * stride + col * 4
            b, g, r, a = raw[i:i + 4]               # LSBFirst: B, G, R, A in memory
            a = 255 if opaque else a
            o = (row * w + col) * 4
            if a == 255:
                out[o:o + 4] = bytes((r, g, b, 255))
            elif a:
                out[o:o + 4] = bytes(min(255, (v * 255 + a // 2) // a) for v in (r, g, b)) + bytes((a,))
    x11.XCloseDisplay(dpy)
    return w, h, bytes(out)


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__.strip().splitlines()[2])
    xid = find_window()
    if xid is None:
        sys.exit(f'no {WM_CLASS} window; is the widget running?')
    width, height, pixels = read_window(xid)
    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(pixels), GdkPixbuf.Colorspace.RGB,
                                             True, 8, width, height, width * 4)
    pixbuf.savev(argv[1], 'png', [], [])
    print(f'{argv[1]}: {width}x{height}')


if __name__ == '__main__':
    main(sys.argv)
