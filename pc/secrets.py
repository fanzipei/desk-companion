# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
"""API credentials protected by Windows DPAPI for the current user."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _transform(value, protect):
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_string_buffer(value)
    incoming = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    function = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    function.argtypes = (ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob))
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise OSError("无法访问本机加密的 API 密钥。")
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(outgoing.data)


class ApiKeyStore:
    def __init__(self, directory):
        self.path = Path(directory) / "news-api-key.dpapi"

    def exists(self):
        return self.path.is_file()

    def load(self):
        return _transform(self.path.read_bytes(), False).decode("utf-8") if self.exists() else ""

    def save(self, value):
        encrypted = _transform(value.encode("utf-8"), True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        os.replace(temporary, self.path)
