"""Programmfenster mit eingebettetem WebView2 – nur über ctypes und Microsofts signierte ``WebView2Loader.dll``.

Bewusst ohne pywebview/pythonnet: deren kompilierte Bausteine sind unsigniert und würden von Smart App Control
blockiert. Die Oberfläche ist dieselbe wie im Browser; das Fenster lädt die lokale Adresse des eingebauten Servers.

Alles läuft auf dem Haupt-Thread (COM-STA + Windows-Nachrichtenschleife). Andere Threads sprechen das Fenster nur über
``post_open()``/``post_close()`` an (PostMessage), nie direkt.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32")
gdi32 = ctypes.WinDLL("gdi32")

HRESULT = ctypes.c_long
S_OK, E_NOINTERFACE, E_FAIL = 0, -2147467262, -2147467259
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WS_OVERLAPPEDWINDOW, WS_POPUP, WS_VISIBLE = 0x00CF0000, 0x80000000, 0x10000000
GWL_STYLE, SW_SHOWMAXIMIZED, SW_RESTORE = -16, 3, 9
WM_DESTROY, WM_SIZE, WM_MOVE, WM_CLOSE, WM_SETFOCUS, WM_SETICON, WM_GETMINMAXINFO = 2, 5, 3, 0x10, 7, 0x80, 0x24
WM_APP_OPEN, WM_APP_QUIT = 0x8001, 0x8002
SWP_NOZORDER, SWP_FRAMECHANGED, SWP_NOOWNERZORDER = 0x4, 0x20, 0x200
MONITOR_DEFAULTTONEAREST = 2
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
CW_USEDEFAULT = 0x80000000 - 0x100000000


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def make(cls, d1, d2, d3, d4) -> "GUID":
        return cls(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4))

    def key(self) -> tuple:
        return (self.Data1, self.Data2, self.Data3, bytes(self.Data4))


IID_IUnknown = GUID.make(0, 0, 0, (0xC0, 0, 0, 0, 0, 0, 0, 0x46))
IID_ENV_DONE = GUID.make(0x4E8A3389, 0xC9D8, 0x4BD2, (0xB6, 0xB5, 0x12, 0x4F, 0xEE, 0x6C, 0xC1, 0x4D))
IID_CTRL_DONE = GUID.make(0x6C4819F3, 0xC9B7, 0x4260, (0x81, 0x27, 0xC9, 0xF5, 0xBD, 0xE7, 0xF6, 0x8C))
IID_TITLE = GUID.make(0xF5F2B923, 0x953E, 0x4042, (0x9F, 0x95, 0xF3, 0xA1, 0x18, 0xE1, 0xAF, 0xD4))
IID_FULLSCREEN = GUID.make(0xE45D98B1, 0xAFEF, 0x45BE, (0x8B, 0xAF, 0x6C, 0x77, 0x28, 0x86, 0x7F, 0x73))
IID_NEWWINDOW = GUID.make(0xD4C185FE, 0xC81C, 0x4989, (0x97, 0xAF, 0x2D, 0x3F, 0xA7, 0xAB, 0x56, 0x51))


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT), ("rcNormalPosition", RECT)]


class MINMAXINFO(ctypes.Structure):
    _fields_ = [("ptReserved", wintypes.POINT), ("ptMaxSize", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("ptMinTrackSize", wintypes.POINT), ("ptMaxTrackSize", wintypes.POINT)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class EventToken(ctypes.Structure):
    _fields_ = [("value", ctypes.c_int64)]


for name, res, args in [
    ("RegisterClassExW", wintypes.ATOM, [ctypes.POINTER(WNDCLASSEXW)]),
    ("CreateWindowExW", wintypes.HWND, [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                        wintypes.HINSTANCE, wintypes.LPVOID]),
    ("DefWindowProcW", LRESULT, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]),
    ("GetMessageW", wintypes.BOOL, [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]),
    ("TranslateMessage", wintypes.BOOL, [ctypes.POINTER(wintypes.MSG)]),
    ("DispatchMessageW", LRESULT, [ctypes.POINTER(wintypes.MSG)]),
    ("PostMessageW", wintypes.BOOL, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]),
    ("PostQuitMessage", None, [ctypes.c_int]),
    ("DestroyWindow", wintypes.BOOL, [wintypes.HWND]),
    ("ShowWindow", wintypes.BOOL, [wintypes.HWND, ctypes.c_int]),
    ("SetForegroundWindow", wintypes.BOOL, [wintypes.HWND]),
    ("IsIconic", wintypes.BOOL, [wintypes.HWND]),
    ("GetClientRect", wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(RECT)]),
    ("SetWindowTextW", wintypes.BOOL, [wintypes.HWND, wintypes.LPCWSTR]),
    ("SendMessageW", LRESULT, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]),
    ("LoadImageW", wintypes.HANDLE, [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]),
    ("LoadCursorW", wintypes.HANDLE, [wintypes.HINSTANCE, wintypes.LPVOID]),
    ("GetWindowLongPtrW", ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int]),
    ("SetWindowLongPtrW", ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]),
    ("GetWindowPlacement", wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]),
    ("SetWindowPlacement", wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]),
    ("MonitorFromWindow", wintypes.HANDLE, [wintypes.HWND, wintypes.DWORD]),
    ("GetMonitorInfoW", wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]),
    ("SetWindowPos", wintypes.BOOL, [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]),
    ("GetDpiForWindow", wintypes.UINT, [wintypes.HWND]),
]:
    fn = getattr(user32, name)
    fn.restype, fn.argtypes = res, args
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]


# ---------------------------------------------------------------- COM-Hilfen

def _vcall(ptr, index: int, *args, argtypes=(), restype=HRESULT):
    """Methode Nr. ``index`` der vtable des COM-Objekts ``ptr`` aufrufen."""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])(ptr, *args)


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} fehlgeschlagen (HRESULT 0x{hr & 0xFFFFFFFF:08X})")


_QI = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
_REF = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)


class _Handler:
    """Minimales COM-Objekt (IUnknown + Invoke) für WebView2-Rückrufe. Hält sich selbst am Leben."""

    _alive: list["_Handler"] = []

    def __init__(self, iid: GUID, invoke_proto, invoke: Callable):
        class Vtbl(ctypes.Structure):
            _fields_ = [("QueryInterface", _QI), ("AddRef", _REF), ("Release", _REF), ("Invoke", invoke_proto)]

        class Obj(ctypes.Structure):
            _fields_ = [("lpVtbl", ctypes.POINTER(Vtbl))]

        self._iid = iid.key()
        self._refs = 1

        def qi(this, riid, ppv):
            if riid.contents.key() in (self._iid, IID_IUnknown.key()):
                ppv[0] = this
                self._refs += 1
                return S_OK
            ppv[0] = None
            return E_NOINTERFACE

        def add_ref(_this):
            self._refs += 1
            return self._refs

        def release(_this):
            self._refs -= 1
            return max(self._refs, 1)  # Python verwaltet den Speicher (_alive), nie freigeben

        def call(*args):
            try:
                return invoke(*args[1:]) or S_OK
            except Exception:  # noqa: BLE001 – Ausnahmen dürfen nicht in WebView2 zurückfließen
                log.exception("Fehler in einem WebView2-Rückruf")
                return S_OK

        self._fns = (_QI(qi), _REF(add_ref), _REF(release), invoke_proto(call))
        self._vtbl = Vtbl(*self._fns)
        self._obj = Obj(ctypes.pointer(self._vtbl))
        _Handler._alive.append(self)

    @property
    def ptr(self) -> ctypes.c_void_p:
        return ctypes.c_void_p(ctypes.addressof(self._obj))


_DONE_PROTO = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, HRESULT, ctypes.c_void_p)
_EVENT_PROTO = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


def webview2_available() -> bool:
    """Ist die WebView2-Laufzeit installiert (Windows 11 immer, Windows 10 meist über Edge)?"""
    try:
        import winreg
    except ImportError:
        return False
    client = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for root, path in ((winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + client),
                       (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + client),
                       (winreg.HKEY_CURRENT_USER, "Software\\" + client)):
        try:
            with winreg.OpenKey(root, path) as k:
                version, _ = winreg.QueryValueEx(k, "pv")
                if version and version != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------- Fenster

class AppWindow:
    def __init__(self, *, title: str, icon_path: Path, loader_path: Path, user_data: Path, url: str,
                 own_origin: str, on_close_request: Callable[[], bool], on_closed: Callable[[], None],
                 dev_tools: bool = False):
        self.title = title
        self.icon_path, self.loader_path, self.user_data = icon_path, loader_path, user_data
        self.start_url, self.own_origin = url, own_origin
        self.on_close_request, self.on_closed = on_close_request, on_closed
        self.dev_tools = dev_tools
        self.hwnd = None
        self.controller = None
        self.webview = None
        self._pending_url: str | None = None
        self._pending_lock = threading.Lock()
        self._fullscreen_saved: tuple | None = None
        self._force_close = False
        self.ready = threading.Event()
        self.failed: str | None = None

    # -- Aufrufe aus anderen Threads -------------------------------------------------------------------------------
    def post_open(self, url: str) -> None:
        """Fenster nach vorn holen und ``url`` laden (thread-sicher)."""
        with self._pending_lock:
            self._pending_url = url
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_OPEN, 0, 0)

    def post_close(self) -> None:
        """Fenster ohne Rückfrage schließen (z. B. „Beenden“ in den Einstellungen)."""
        self._force_close = True
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_QUIT, 0, 0)

    # -- Haupt-Thread ----------------------------------------------------------------------------------------------
    def run(self) -> None:
        """Erzeugt Fenster und WebView2, blockiert bis zum Schließen."""
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # Per-Monitor v2
        except (AttributeError, OSError):
            pass
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Flip7Live.App")
        except (AttributeError, OSError):
            pass
        ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        hinst = kernel32.GetModuleHandleW(None)
        icon_big = user32.LoadImageW(None, str(self.icon_path), IMAGE_ICON, 256, 256, LR_LOADFROMFILE)
        icon_small = user32.LoadImageW(None, str(self.icon_path), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        self._wndproc = WNDPROC(self._proc)  # Referenz halten
        wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), style=0x3, lpfnWndProc=self._wndproc, hInstance=hinst,
                         hIcon=icon_big, hCursor=user32.LoadCursorW(None, ctypes.c_void_p(32512)),
                         hbrBackground=gdi32.CreateSolidBrush(0x00161110), lpszClassName="Flip7LiveWindow", hIconSm=icon_small)
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            raise OSError("Fensterklasse konnte nicht registriert werden")
        self.hwnd = user32.CreateWindowExW(0, "Flip7LiveWindow", self.title, WS_OVERLAPPEDWINDOW, CW_USEDEFAULT,
                                           CW_USEDEFAULT, 1280, 800, None, None, hinst, None)
        if not self.hwnd:
            raise OSError("Fenster konnte nicht erzeugt werden")
        try:  # dunkle Titelleiste passend zum Design (Windows 10 20H1+ / 11)
            dark = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(self.hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
        except (AttributeError, OSError):
            pass
        user32.SendMessageW(self.hwnd, WM_SETICON, 1, icon_big or 0)
        user32.SendMessageW(self.hwnd, WM_SETICON, 0, icon_small or 0)
        user32.ShowWindow(self.hwnd, SW_SHOWMAXIMIZED)
        self._create_webview()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _create_webview(self) -> None:
        loader = ctypes.WinDLL(str(self.loader_path))
        create = loader.CreateCoreWebView2EnvironmentWithOptions
        create.restype = HRESULT
        create.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]
        self.user_data.mkdir(parents=True, exist_ok=True)

        def env_done(hr, env):
            if hr < 0 or not env:
                return self._fail(f"WebView2 konnte nicht gestartet werden (0x{hr & 0xFFFFFFFF:08X})")
            self._ctrl_handler = _Handler(IID_CTRL_DONE, _DONE_PROTO, ctrl_done)
            _check(_vcall(env, 3, self.hwnd, self._ctrl_handler.ptr, argtypes=(wintypes.HWND, ctypes.c_void_p)),
                   "CreateCoreWebView2Controller")

        def ctrl_done(hr, controller):
            if hr < 0 or not controller:
                return self._fail(f"WebView2-Fenster konnte nicht erzeugt werden (0x{hr & 0xFFFFFFFF:08X})")
            _vcall(controller, 1, restype=wintypes.ULONG)  # AddRef – wir behalten den Controller
            self.controller = ctypes.c_void_p(controller)
            core = ctypes.c_void_p()
            _check(_vcall(self.controller, 25, ctypes.byref(core), argtypes=(ctypes.c_void_p,)), "get_CoreWebView2")
            self.webview = core
            self._configure()
            self._resize()
            _vcall(self.controller, 4, True, argtypes=(wintypes.BOOL,))  # put_IsVisible
            self._navigate(self._take_pending() or self.start_url)
            _vcall(self.controller, 12, 0, argtypes=(ctypes.c_int,))  # MoveFocus(PROGRAMMATIC)
            self.ready.set()

        self._env_handler = _Handler(IID_ENV_DONE, _DONE_PROTO, env_done)
        hr = create(None, str(self.user_data), None, self._env_handler.ptr)
        if hr < 0:
            self._fail(f"WebView2-Laufzeit nicht gefunden (0x{hr & 0xFFFFFFFF:08X})")

    def _configure(self) -> None:
        settings = ctypes.c_void_p()
        _check(_vcall(self.webview, 3, ctypes.byref(settings), argtypes=(ctypes.c_void_p,)), "get_Settings")
        _vcall(settings, 10, False, argtypes=(wintypes.BOOL,))  # put_IsStatusBarEnabled
        _vcall(settings, 12, bool(self.dev_tools), argtypes=(wintypes.BOOL,))  # put_AreDevToolsEnabled
        _vcall(settings, 14, bool(self.dev_tools), argtypes=(wintypes.BOOL,))  # put_AreDefaultContextMenusEnabled
        token = EventToken()
        self._title_handler = _Handler(IID_TITLE, _EVENT_PROTO, lambda *_: self._update_title())
        _vcall(self.webview, 46, self._title_handler.ptr, ctypes.byref(token), argtypes=(ctypes.c_void_p, ctypes.c_void_p))
        self._fs_handler = _Handler(IID_FULLSCREEN, _EVENT_PROTO, lambda *_: self._sync_fullscreen())
        _vcall(self.webview, 52, self._fs_handler.ptr, ctypes.byref(token), argtypes=(ctypes.c_void_p, ctypes.c_void_p))
        self._nw_handler = _Handler(IID_NEWWINDOW, _EVENT_PROTO, self._on_new_window)
        _vcall(self.webview, 44, self._nw_handler.ptr, ctypes.byref(token), argtypes=(ctypes.c_void_p, ctypes.c_void_p))

    def _on_new_window(self, _sender, args):
        """Neue Fenster/Tabs: eigene Seiten im selben Fenster, fremde Adressen im Standardbrowser."""
        uri = wintypes.LPWSTR()
        _vcall(args, 3, ctypes.byref(uri), argtypes=(ctypes.c_void_p,))
        target = uri.value or ""
        ole32.CoTaskMemFree(uri)
        _vcall(args, 6, True, argtypes=(wintypes.BOOL,))  # put_Handled → kein neues Fenster
        if target.startswith(self.own_origin):
            self._navigate(target)
        elif target.startswith(("http://", "https://", "ms-settings:")):
            try:
                os.startfile(target)  # type: ignore[attr-defined]
            except OSError:
                log.warning("Adresse konnte nicht geöffnet werden: %s", target)

    def _update_title(self) -> None:
        title = wintypes.LPWSTR()
        if _vcall(self.webview, 48, ctypes.byref(title), argtypes=(ctypes.c_void_p,)) >= 0 and title.value:
            text = title.value
            ole32.CoTaskMemFree(title)
            user32.SetWindowTextW(self.hwnd, text if self.title in text else f"{text} – {self.title}")

    def _sync_fullscreen(self) -> None:
        flag = wintypes.BOOL()
        _vcall(self.webview, 54, ctypes.byref(flag), argtypes=(ctypes.c_void_p,))
        self._set_fullscreen(bool(flag.value))

    def _set_fullscreen(self, on: bool) -> None:
        if on and self._fullscreen_saved is None:
            placement = WINDOWPLACEMENT(length=ctypes.sizeof(WINDOWPLACEMENT))
            user32.GetWindowPlacement(self.hwnd, ctypes.byref(placement))
            style = user32.GetWindowLongPtrW(self.hwnd, GWL_STYLE)
            self._fullscreen_saved = (style, placement)
            mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
            user32.GetMonitorInfoW(user32.MonitorFromWindow(self.hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(mi))
            user32.SetWindowLongPtrW(self.hwnd, GWL_STYLE, (style & ~WS_OVERLAPPEDWINDOW) | WS_POPUP | WS_VISIBLE)
            r = mi.rcMonitor
            user32.SetWindowPos(self.hwnd, None, r.left, r.top, r.right - r.left, r.bottom - r.top,
                                SWP_NOZORDER | SWP_FRAMECHANGED | SWP_NOOWNERZORDER)
        elif not on and self._fullscreen_saved is not None:
            style, placement = self._fullscreen_saved
            self._fullscreen_saved = None
            user32.SetWindowLongPtrW(self.hwnd, GWL_STYLE, style)
            user32.SetWindowPlacement(self.hwnd, ctypes.byref(placement))
            user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, SWP_NOZORDER | SWP_FRAMECHANGED | SWP_NOOWNERZORDER | 0x3)

    def _navigate(self, url: str) -> None:
        if self.webview:
            _vcall(self.webview, 5, url, argtypes=(wintypes.LPCWSTR,))

    def _take_pending(self) -> str | None:
        with self._pending_lock:
            url, self._pending_url = self._pending_url, None
        return url

    def _resize(self) -> None:
        if self.controller:
            rect = RECT()
            user32.GetClientRect(self.hwnd, ctypes.byref(rect))
            _vcall(self.controller, 6, rect, argtypes=(RECT,))  # put_Bounds

    def _fail(self, text: str) -> int:
        log.error(text)
        self.failed = text
        self.ready.set()
        self._force_close = True
        user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        return S_OK

    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_SIZE:
                self._resize()
                return 0
            if msg == WM_MOVE:
                if self.controller:
                    _vcall(self.controller, 23)  # NotifyParentWindowPositionChanged
                return 0
            if msg == WM_SETFOCUS and self.controller:
                _vcall(self.controller, 12, 0, argtypes=(ctypes.c_int,))
                return 0
            if msg == WM_GETMINMAXINFO:
                info = ctypes.cast(lparam, ctypes.POINTER(MINMAXINFO)).contents
                info.ptMinTrackSize.x, info.ptMinTrackSize.y = 480, 360
                return 0
            if msg == WM_APP_OPEN:
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
                url = self._take_pending()
                if url:
                    self._navigate(url)
                return 0
            if msg == WM_APP_QUIT:
                self._force_close = True
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_CLOSE:
                if self._force_close or self.on_close_request():
                    user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                if self.controller:
                    _vcall(self.controller, 24)  # Close
                    self.controller = None
                self.on_closed()
                user32.PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001
            log.exception("Fehler in der Fensterverarbeitung")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
