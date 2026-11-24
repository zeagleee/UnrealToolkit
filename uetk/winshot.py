"""Capture a window to PNG with PrintWindow (works while occluded; not while minimized). Stdlib only."""
import ctypes
import struct
import zlib

from . import procs  # noqa: F401  (sets DPI awareness before any capture)

PW_RENDERFULLCONTENT = 2
HALFTONE = 4
SRCCOPY = 0x00CC0020

if procs.IS_WINDOWS:
    from ctypes import wintypes

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _g32 = ctypes.WinDLL("gdi32", use_last_error=True)

    for _fn, _args in {
        "GetWindowDC": [wintypes.HWND], "ReleaseDC": [wintypes.HWND, wintypes.HDC],
        "PrintWindow": [wintypes.HWND, wintypes.HDC, wintypes.UINT],
        "GetWindowRect": [wintypes.HWND, ctypes.POINTER(wintypes.RECT)],
    }.items():
        getattr(_u32, _fn).argtypes = _args
    _u32.GetWindowDC.restype = wintypes.HDC
    for _fn, _args in {
        "CreateCompatibleDC": [wintypes.HDC], "CreateCompatibleBitmap": [wintypes.HDC, ctypes.c_int, ctypes.c_int],
        "SelectObject": [wintypes.HDC, wintypes.HGDIOBJ], "DeleteObject": [wintypes.HGDIOBJ], "DeleteDC": [wintypes.HDC],
        "SetStretchBltMode": [wintypes.HDC, ctypes.c_int],
        "SetBrushOrgEx": [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p],
        "StretchBlt": [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                       wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD],
        "GetDIBits": [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                      wintypes.UINT],
    }.items():
        getattr(_g32, _fn).argtypes = _args
    _g32.CreateCompatibleDC.restype = wintypes.HDC
    _g32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    _g32.SelectObject.restype = wintypes.HGDIOBJ

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]


def _write_png(path, width, height, rgb):
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def capture(hwnd, out_path, max_width=1600):
    """Save the window as PNG, downscaled to max_width (0 = native). Returns (width, height)."""
    procs._windows_only("Window screenshots")
    rect = wintypes.RECT()
    _u32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError("window has no area (minimized?)")
    scale = min(1.0, max_width / width) if max_width else 1.0
    out_w, out_h = max(1, int(width * scale)), max(1, int(height * scale))

    window_dc = _u32.GetWindowDC(hwnd)
    full_dc = _g32.CreateCompatibleDC(window_dc)
    full_bmp = _g32.CreateCompatibleBitmap(window_dc, width, height)
    old_full = _g32.SelectObject(full_dc, full_bmp)
    out_dc, out_bmp, old_out = full_dc, full_bmp, old_full
    try:
        if not _u32.PrintWindow(hwnd, full_dc, PW_RENDERFULLCONTENT):
            raise RuntimeError("PrintWindow failed")
        if (out_w, out_h) != (width, height):
            out_dc = _g32.CreateCompatibleDC(window_dc)
            out_bmp = _g32.CreateCompatibleBitmap(window_dc, out_w, out_h)
            old_out = _g32.SelectObject(out_dc, out_bmp)
            _g32.SetStretchBltMode(out_dc, HALFTONE)
            _g32.SetBrushOrgEx(out_dc, 0, 0, None)
            _g32.StretchBlt(out_dc, 0, 0, out_w, out_h, full_dc, 0, 0, width, height, SRCCOPY)
        header = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), out_w, -out_h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(out_w * out_h * 4)
        _g32.SelectObject(out_dc, old_out)  # GetDIBits wants the bitmap deselected
        if not _g32.GetDIBits(out_dc, out_bmp, 0, out_h, buf, ctypes.byref(header), 0):
            raise RuntimeError("GetDIBits failed")
    finally:
        if out_dc != full_dc:
            _g32.DeleteObject(out_bmp)
            _g32.DeleteDC(out_dc)
        _g32.SelectObject(full_dc, old_full)
        _g32.DeleteObject(full_bmp)
        _g32.DeleteDC(full_dc)
        _u32.ReleaseDC(hwnd, window_dc)

    bgra = buf.raw
    rgb = bytearray(out_w * out_h * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    _write_png(out_path, out_w, out_h, bytes(rgb))
    return out_w, out_h
