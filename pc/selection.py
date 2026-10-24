# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
"""Bounded, isolated Win32 selection capture without OLE clipboard objects."""
import ctypes
from ctypes import wintypes as w
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

LOG=logging.getLogger(__name__)
MAX_CLIPBOARD_BYTES=32*1024*1024
MEMORY_FORMATS={1,7,8,13,15,16,17}  # Text, DIB, file list, locale, DIBV5.
REGISTERED_FORMATS={"html format","rich text format","rich text format without objects",
    "png","image/png","text/html","text/plain","text/uri-list","utf8_string",
    "csv","text/csv","uniformresourcelocator","uniformresourcelocatorw",
    "filename","filenamew","preferred dropeffect"}


def capture_selection():
    """Native clipboard providers can fail or hang; never run them in the tray process."""
    python=Path(sys.executable).with_name("python.exe")
    environment={**os.environ,"PYTHONIOENCODING":"utf-8"}
    try:
        result=subprocess.run([str(python),"-m","pc.selection"],cwd=Path(__file__).resolve().parents[1],
            capture_output=True,encoding="utf-8",errors="replace",env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW,timeout=8)
    except subprocess.TimeoutExpired:
        raise ValueError("当前应用的取词响应超时，请重新选中后重试。") from None
    if result.returncode:
        LOG.warning("Selection helper exited: 0x%08X",result.returncode & 0xffffffff)
        raise ValueError("当前应用的取词失败，后台仍在运行，请重试。")
    try:
        response=json.loads(result.stdout)
        if response.get("ok") is True and isinstance(response.get("text"),str) and response["text"].strip():
            return response["text"]
        error=response.get("error")
    except (ValueError,AttributeError):
        error=None
    raise ValueError(error or "没有读取到选中文字，请重新选中后重试。")


class Clipboard:
    """Snapshot ordinary memory-backed formats; never retain COM pointers or GDI handles."""
    def __init__(self):
        self.user=ctypes.WinDLL("user32",use_last_error=True)
        self.kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        for name,args,result in (
            ("OpenClipboard",(w.HWND,),w.BOOL),("CloseClipboard",(),w.BOOL),
            ("EmptyClipboard",(),w.BOOL),("EnumClipboardFormats",(w.UINT,),w.UINT),
            ("GetClipboardData",(w.UINT,),w.HANDLE),("SetClipboardData",(w.UINT,w.HANDLE),w.HANDLE),
            ("GetClipboardSequenceNumber",(),w.DWORD),
            ("GetClipboardFormatNameW",(w.UINT,w.LPWSTR,ctypes.c_int),ctypes.c_int),
            ("CreateWindowExW",(w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,ctypes.c_int,ctypes.c_int,
                ctypes.c_int,ctypes.c_int,w.HWND,w.HMENU,w.HINSTANCE,ctypes.c_void_p),w.HWND),
            ("DestroyWindow",(w.HWND,),w.BOOL)):
            function=getattr(self.user,name); function.argtypes=args; function.restype=result
        for name,args,result in (
            ("GlobalSize",(w.HGLOBAL,),ctypes.c_size_t),("GlobalLock",(w.HGLOBAL,),ctypes.c_void_p),
            ("GlobalUnlock",(w.HGLOBAL,),w.BOOL),("GlobalAlloc",(w.UINT,ctypes.c_size_t),w.HGLOBAL),
            ("GlobalFree",(w.HGLOBAL,),w.HGLOBAL)):
            function=getattr(self.kernel,name); function.argtypes=args; function.restype=result

    @contextmanager
    def opened(self,owner=None):
        for _ in range(15):
            if self.user.OpenClipboard(owner):
                break
            time.sleep(.02)
        else:
            raise OSError("剪贴板暂时被占用，请重试。")
        try:
            yield
        finally:
            self.user.CloseClipboard()

    def read(self,format):
        handle=self.user.GetClipboardData(format)
        size=self.kernel.GlobalSize(handle) if handle else 0
        if not size:
            return None
        if size>MAX_CLIPBOARD_BYTES:
            raise ValueError("剪贴板内容过大，请缩小选区后重试。")
        pointer=self.kernel.GlobalLock(handle)
        if not pointer:
            raise OSError("无法读取剪贴板，请重试。")
        try:
            return ctypes.string_at(pointer,size)
        finally:
            self.kernel.GlobalUnlock(handle)

    def snapshot(self):
        result={}; total=0
        with self.opened():
            format=0
            while True:
                format=self.user.EnumClipboardFormats(format)
                if not format:
                    break
                if format not in MEMORY_FORMATS:
                    name=ctypes.create_unicode_buffer(256)
                    self.user.GetClipboardFormatNameW(format,name,len(name))
                    if name.value.lower() not in REGISTERED_FORMATS:
                        continue
                data=self.read(format)
                if data is not None:
                    total+=len(data)
                    if total>MAX_CLIPBOARD_BYTES:
                        raise ValueError("剪贴板内容过大，请稍后重试。")
                    result[format]=data
        return result

    def text(self):
        with self.opened():
            sequence=self.user.GetClipboardSequenceNumber()
            data=self.read(13)
            text=data.decode("utf-16-le").split("\0",1)[0] if data else ""
            return text,sequence

    def restore(self,snapshot,sequence):
        # Use a real owner window: OpenClipboard(NULL) + EmptyClipboard cannot set data.
        if not snapshot:
            return
        window=self.user.CreateWindowExW(0,"STATIC","DeskCompanion clipboard",0,0,0,0,0,
            ctypes.c_void_p(-3),None,None,None)  # HWND_MESSAGE; never visible or focused.
        if not window:
            raise OSError("无法恢复剪贴板。")
        handles={}
        try:
            # Allocate the complete replacement before touching the current clipboard.
            for format,data in snapshot.items():
                handle=self.kernel.GlobalAlloc(2,len(data))  # GMEM_MOVEABLE
                if not handle:
                    raise MemoryError("无法恢复剪贴板。")
                handles[format]=handle
                pointer=self.kernel.GlobalLock(handle)
                if not pointer:
                    raise OSError("无法恢复剪贴板。")
                try:
                    ctypes.memmove(pointer,data,len(data))
                finally:
                    self.kernel.GlobalUnlock(handle)
            with self.opened(window):
                if self.user.GetClipboardSequenceNumber()!=sequence:
                    return  # Preserve newer clipboard content copied by the user/app.
                if not self.user.EmptyClipboard():
                    raise OSError("无法恢复剪贴板。")
                for format,handle in handles.items():
                    if not self.user.SetClipboardData(format,handle):
                        raise OSError("无法恢复剪贴板。")
                    handles[format]=None  # Ownership passed to Windows; never free it here.
        finally:
            for handle in handles.values():
                if handle:
                    self.kernel.GlobalFree(handle)
            self.user.DestroyWindow(window)


def native_capture():
    from .windows import user32,send_chord
    user32.GetForegroundWindow.restype=w.HWND
    foreground=user32.GetForegroundWindow()
    end=time.monotonic()+1.5
    while any(user32.GetAsyncKeyState(key)&0x8000 for key in (0x11,0x12,0x10,0x5B,0x5C)):
        if time.monotonic()>end:
            raise ValueError("请松开快捷键后重试。")
        time.sleep(.015)
    clipboard=Clipboard()
    previous=clipboard.snapshot()
    sequence=clipboard.user.GetClipboardSequenceNumber()
    copied_sequence=None
    try:
        if user32.GetForegroundWindow()!=foreground:
            raise ValueError("当前窗口已切换，请重新选中文字。")
        send_chord(0x11,ord("C"))
        end=time.monotonic()+1.2
        while time.monotonic()<end:
            if clipboard.user.GetClipboardSequenceNumber()!=sequence:
                text,copied_sequence=clipboard.text()
                if not text.strip():
                    raise ValueError("选中内容不是文字，请使用截图识别。")
                return text
            time.sleep(.03)
        raise ValueError("没有读取到选中文字，请重新选中后重试。")
    finally:
        if copied_sequence is not None:
            try:
                clipboard.restore(previous,copied_sequence)
            except Exception:
                # Restoration failure must not discard a successfully captured selection.
                LOG.warning("Could not restore clipboard formats",exc_info=True)


def main():
    # Suppress OS crash dialogs in the disposable worker; the tray reports failures silently.
    ctypes.WinDLL("kernel32").SetErrorMode(0x0001|0x0002)
    try:
        result={"ok":True,"text":native_capture()}
    except Exception as exc:
        result={"ok":False,"error":str(exc)}
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
