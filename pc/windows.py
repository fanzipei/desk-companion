"""Small Windows adapters: global hotkeys, safe selection capture and voice typing."""
import ctypes
from ctypes import wintypes as w
import logging
import queue
import threading
from .placement import popup_position
from .selection import capture_selection

LOG = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD), ("dwFlags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD), ("time", w.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", w.DWORD), ("data", INPUTUNION)]


user32.SendInput.argtypes = (w.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = w.UINT
kernel32.GetCurrentThreadId.restype = w.DWORD
user32.PostThreadMessageW.argtypes = (w.DWORD, w.UINT, w.WPARAM, w.LPARAM)
user32.GetMessageW.argtypes = (ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
user32.SetForegroundWindow.argtypes = (w.HWND,)


class GUID(ctypes.Structure):
    _fields_ = [("data1", w.DWORD), ("data2", w.WORD), ("data3", w.WORD), ("data4", ctypes.c_ubyte*8)]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT), ("guidItem", GUID)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT), ("rcWork", w.RECT), ("flags", w.DWORD)]


user32.MonitorFromRect.argtypes = (ctypes.POINTER(w.RECT), w.DWORD)
user32.MonitorFromRect.restype = w.HANDLE
user32.GetMonitorInfoW.argtypes = (w.HANDLE, ctypes.POINTER(MONITORINFO))
user32.GetAncestor.argtypes = (w.HWND, w.UINT)
user32.GetAncestor.restype = w.HWND
user32.SetWindowPos.argtypes = (w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT)
user32.GetFocus.restype = w.HWND


def place_by_tray(window, tray, width, height, gap=10):
    """Anchor to the real icon, falling back to the primary monitor's work area."""
    shell = ctypes.WinDLL("shell32")
    shell.Shell_NotifyIconGetRect.argtypes = (ctypes.POINTER(NOTIFYICONIDENTIFIER), ctypes.POINTER(w.RECT))
    icon = None
    rect = w.RECT()
    identifier = NOTIFYICONIDENTIFIER(cbSize=ctypes.sizeof(NOTIFYICONIDENTIFIER),
                                      hWnd=getattr(tray, "_hwnd", None), uID=id(tray) & 0xffffffff)
    if identifier.hWnd and shell.Shell_NotifyIconGetRect(ctypes.byref(identifier), ctypes.byref(rect)) == 0:
        icon = (rect.left, rect.top, rect.right, rect.bottom)
    monitor = user32.MonitorFromRect(ctypes.byref(rect), 2)  # Nearest; primary for fallback rect.
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        work = (0, 0, window.winfo_screenwidth(), window.winfo_screenheight()-48)
    else:
        work = (info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom)
    width, height = min(width, work[2]-work[0]-2*gap), min(height, work[3]-work[1]-2*gap)
    x, y = popup_position(work, icon, width, height, gap)
    window.geometry(f"{width}x{height}")
    window.update_idletasks()
    window.deiconify()
    window.update_idletasks()
    hwnd = user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id()
    user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), x, y, width, height, 0x0040)
    user32.SetForegroundWindow(hwnd)


def ime_composing():
    imm = ctypes.WinDLL("imm32")
    imm.ImmGetContext.argtypes = (w.HWND,)
    imm.ImmGetContext.restype = w.HANDLE
    imm.ImmGetCompositionStringW.argtypes = (w.HANDLE, w.DWORD, ctypes.c_void_p, w.DWORD)
    imm.ImmGetCompositionStringW.restype = w.LONG
    imm.ImmReleaseContext.argtypes = (w.HWND, w.HANDLE)
    hwnd = user32.GetFocus()
    context = imm.ImmGetContext(hwnd) if hwnd else None
    if not context:
        return False
    try:
        return imm.ImmGetCompositionStringW(context, 8, None, 0) > 0
    finally:
        imm.ImmReleaseContext(hwnd, context)


def send_chord(*keys):
    events = []
    for key in keys:
        events.append(INPUT(type=1, ki=KEYBDINPUT(wVk=key)))
    for key in reversed(keys):
        events.append(INPUT(type=1, ki=KEYBDINPUT(wVk=key, dwFlags=2)))
    array = (INPUT * len(events))(*events)
    if user32.SendInput(len(events), array, ctypes.sizeof(INPUT)) != len(events):
        raise OSError("无法向当前窗口发送按键，请使用普通权限窗口后重试。")


def voice_typing():
    send_chord(0x5B, ord("H"))


def parse_hotkey(value):
    pieces = [piece.strip().upper() for piece in value.split("+")]
    if len(pieces) < 2 or len(set(pieces)) != len(pieces):
        raise ValueError("快捷键请使用 Ctrl+Alt+J 这样的格式。")
    mods = {"ALT": 1, "CTRL": 2, "CONTROL": 2, "SHIFT": 4, "WIN": 8}
    mask = 0
    for piece in pieces[:-1]:
        if piece not in mods:
            raise ValueError("修饰键支持 Ctrl、Alt、Shift、Win。")
        mask |= mods[piece]
    last = pieces[-1]
    if len(last) == 1 and last.isascii() and last.isalnum():
        key = ord(last)
    elif last == "SPACE":
        key = 0x20
    elif last.startswith("F") and last[1:].isdigit() and 1 <= int(last[1:]) <= 24:
        key = 0x6F + int(last[1:])
    else:
        raise ValueError("主键支持字母、数字、Space 或 F1–F24。")
    return mask | 0x4000, key  # MOD_NOREPEAT


class Hotkeys:
    def __init__(self, config, events):
        self.config = config
        self.events = events
        self.thread_id = None
        self.commands = queue.Queue()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        # Create the thread message queue before accepting reload requests.
        message = w.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        current = self.config.copy()

        def register(config):
            for identifier in (1, 2, 3, 4):
                user32.UnregisterHotKey(None, identifier)
            for identifier, field in ((1, "capture_hotkey"), (2, "compose_hotkey"),(3,"screenshot_hotkey"),(4,"reminder_hotkey")):
                if identifier==3 and not config.get("screenshot_enabled",False):
                    continue
                mask, key = parse_hotkey(config[field])
                if not user32.RegisterHotKey(None, identifier, mask, key):
                    raise OSError(f"快捷键 {config[field]} 已被占用，请在设置中修改。")

        try:
            register(current)
            self.events.put(("hotkeys_ready", current))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == 0x0312:
                    if message.wParam == 1:
                        try:
                            self.events.put(("capture", capture_selection()))
                        except Exception as exc:
                            LOG.exception("Selection capture failed")
                            self.events.put(("capture_error", str(exc)))
                    elif message.wParam==2:
                        self.events.put(("compose", None))
                    elif message.wParam==3:
                        self.events.put(("screenshot",None))
                    elif message.wParam==4:
                        self.events.put(("reminder",None))
                elif message.message == 0x8001:
                    candidate = self.commands.get()
                    try:
                        register(candidate)
                        current = candidate
                        self.events.put(("hotkeys_ready", current))
                    except Exception as exc:
                        try:
                            register(current)
                        except Exception:
                            pass
                        self.events.put(("hotkeys_error", str(exc)))
        finally:
            for identifier in (1, 2, 3, 4):
                user32.UnregisterHotKey(None, identifier)

    def reload(self, config):
        self.commands.put(config)
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x8001, 0, 0)

    def stop(self):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
