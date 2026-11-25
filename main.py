"""Blip: a small Sun Wukong companion for Windows, macOS, and X11 desktops.

Install:  python -m pip install -r requirements.txt
Run:      python main.py
Optional: python main.py --sprites spritesheet.png --frame-width 128 --frame-height 128

All application logic lives here. Tk owns the main thread; tray, IMAP, and screen
capture workers communicate through a queue and never call Tk. Closing a window
hides it; use Quit in Blip's menu to stop the application.

Gmail: use Gmail settings in the menu, or set BLIP_GMAIL_ADDRESS and
BLIP_GMAIL_APP_PASSWORD. Credentials are kept in memory, never written to disk.
Alerts start disabled and check for new unread messages every 60 seconds. IMAP
opens the inbox read-only and fetches headers with BODY.PEEK (no mail is changed).
App passwords require Google 2-Step Verification and may be unavailable on some
managed accounts: https://support.google.com/accounts/answer/185833

Look at screen captures the entire desktop on demand using Pillow ImageGrab.
Windows captures all monitors; other platforms capture the desktop exposed by
their screen service. A preview lets you save the original PNG locally. There is
no upload, background recording, OCR, or AI service. macOS requires Screen
Recording permission; Wayland may require the desktop's capture permission/tool.

Linux requires an X11/XWayland session, libX11, libXext with Shape support, and a
desktop with a system tray. Native Wayland does not expose portable global window
positioning or pointer tracking to Tk. macOS should use a current python.org Tk.
The only direct third-party dependencies are Pillow and pystray (including the
platform dependencies that pystray installs itself).
"""

from __future__ import annotations

import argparse
import ctypes as C
import ctypes.util
import email
from email import policy
import imaplib
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import platform
import queue
import random
import re
import ssl
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter, ImageGrab, ImageOps, ImageTk


LOG = logging.getLogger("blip")
__version__ = "0.1.0"
WIDTH, HEIGHT = 360, 400
COLOR_KEY = "#ff00ff"
FRAME_MS, POINTER_MS = 33, 100
BUBBLE_SECONDS, NEAR_DISTANCE, SPEED = 4.0, 150, 37.0
ENCOURAGEMENTS = (
    "One small step still moves your mountain.",
    "You've got this. I'll hold the cloud steady.",
    "Progress counts, even when it's quiet.",
    "Take a breath. Your next step can be small.",
    "A little courage goes a very long way.",
    "Rest is part of the journey, too.",
    "You don't have to finish everything today.",
    "Your effort matters. I see you showing up.",
    "Even the Monkey King started with a leap.",
    "Let's do one kind thing for future you.",
)
FUNNY_LINES = (
    "My cloud has unlimited storage. Mostly bananas.",
    "I put the pro in procrastina... ooh, a cloud!",
    "This staff doubles as a very long selfie stick.",
    "I tried a standing desk. The cloud got jealous.",
    "My five-year plan? More bananas. Fewer tabs.",
    "I have 72 transformations. None can fold laundry.",
    "Cloud computing? I've been doing it for centuries.",
    "My staff has excellent reach. My Wi-Fi doesn't.",
    "Today's forecast: a small chance of monkey business.",
    "I came. I saw. I forgot why I opened this tab.",
)
MOODS = (
    ("😊", "Glad you're feeling good! Let's keep that little spark going."),
    ("😐", "An okay day counts, too. A stretch or a sip of water might help."),
    ("😔", "I'm here with you. Take it gently; one small breath at a time."),
)


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self):
        return self.x + self.width

    @property
    def bottom(self):
        return self.y + self.height


def clamp(value, lower, upper):
    return max(lower, min(value, upper))


def bounce_height(elapsed):
    """A main leap followed by a smaller landing bounce, in pixels."""
    if 0 <= elapsed < 0.62:
        return 58 * math.sin(math.pi * elapsed / 0.62)
    if 0.62 <= elapsed < 0.9:
        return 14 * math.sin(math.pi * (elapsed - 0.62) / 0.28)
    return 0.0


def walk_step(x, direction, elapsed, left, right):
    """Reflect at either edge without ever leaving the usable primary screen."""
    if right <= left:
        return float(left), direction
    x += direction * SPEED * elapsed
    while x < left or x > right:
        if x > right:
            x, direction = 2 * right - x, -1
        elif x < left:
            x, direction = 2 * left - x, 1
    return x, direction


def mask_rectangles(mask):
    """Merge identical scanline runs for X Shape's visual and input regions."""
    width, height = mask.size
    pixels = mask.convert("L").tobytes()
    active, finished = {}, []
    for y in range(height):
        runs = {}
        row = pixels[y * width:(y + 1) * width]
        for match in re.finditer(b"[^\x00]+", row):
            key = (match.start(), match.end() - match.start())
            old = active.pop(key, None)
            runs[key] = (key[0], old[1] if old else y, key[1],
                         old[3] + 1 if old else 1)
        finished.extend(active.values())
        active = runs
    finished.extend(active.values())
    return finished


def enable_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        C.windll.user32.SetProcessDpiAwarenessContext(C.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            C.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            C.windll.user32.SetProcessDPIAware()


def configure_logging():
    """Keep bounded diagnostic logs when launched without a console."""
    if sys.platform == "win32":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Blip"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs" / "Blip"
    else:
        base = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "blip"
    handlers = []
    log_path = base / "blip.log"
    try:
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_path, maxBytes=262144, backupCount=2,
                                           encoding="utf-8"))
    except OSError:
        log_path = None
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    if not handlers:
        handlers.append(logging.NullHandler())
    logging.basicConfig(level=logging.WARNING, handlers=handlers, force=True,
                        format="%(asctime)s %(levelname)s Blip: %(message)s")
    LOG.setLevel(logging.INFO)
    LOG.info("Starting Blip %s on %s with Python %s", __version__, sys.platform,
             platform.python_version())
    return log_path


def create_root():
    """Use a normal Tk install, with a fallback for this workspace's lean Python."""
    try:
        return tk.Tk()
    except tk.TclError as error:
        runtime = Path(__file__).resolve().parent / ".tools" / "tk-runtime"
        if (sys.platform != "win32" or "init.tcl" not in str(error) or
                not (runtime / "tcl_library" / "init.tcl").is_file()):
            raise
        os.environ["TCL_LIBRARY"] = str(runtime / "tcl_library")
        os.environ["TK_LIBRARY"] = str(runtime / "tk_library")
        return tk.Tk()


class Cocoa:
    """Small, typed Objective-C bridge; no extra Python GUI dependency."""

    class Point(C.Structure):
        _fields_ = [("x", C.c_double), ("y", C.c_double)]

    class Size(C.Structure):
        _fields_ = [("width", C.c_double), ("height", C.c_double)]

    def __init__(self):
        class NSRect(C.Structure):
            _fields_ = [("origin", Cocoa.Point), ("size", Cocoa.Size)]
        self.rect_type = NSRect
        self.lib = C.CDLL("/usr/lib/libobjc.A.dylib")
        self.lib.objc_getClass.argtypes = [C.c_char_p]
        self.lib.objc_getClass.restype = C.c_void_p
        self.lib.sel_registerName.argtypes = [C.c_char_p]
        self.lib.sel_registerName.restype = C.c_void_p
        self.methods = {}

    def cls(self, name):
        return self.lib.objc_getClass(name.encode())

    def send(self, receiver, selector, result=C.c_void_p, types=(), values=()):
        key = (selector, result, types)
        if key not in self.methods:
            function = C.CFUNCTYPE(result, C.c_void_p, C.c_void_p, *types)(
                ("objc_msgSend", self.lib))
            self.methods[key] = (function, self.lib.sel_registerName(selector.encode()))
        function, method = self.methods[key]
        return function(receiver, method, *values)

    def rect(self, receiver, selector):
        if platform.machine() == "x86_64":
            result = self.rect_type()
            function = C.CFUNCTYPE(None, C.POINTER(self.rect_type), C.c_void_p,
                                  C.c_void_p)(("objc_msgSend_stret", self.lib))
            function(C.byref(result), receiver,
                     self.lib.sel_registerName(selector.encode()))
            return result
        return self.send(receiver, selector, self.rect_type)


class NativeWindow:
    """Color-keyed Windows, transparent Cocoa, or shaped X11 canvas window."""

    def __init__(self, root):
        self.root, self.display, self.cocoa, self.nswindow = root, None, None, None
        self.last_ignore = None
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=COLOR_KEY)
        self.background = COLOR_KEY
        if sys.platform == "win32":
            root.attributes("-transparentcolor", COLOR_KEY)
            root.attributes("-toolwindow", True)
        elif sys.platform == "darwin":
            self.background = "systemTransparent"
            root.configure(bg=self.background)
            root.attributes("-transparent", True)
            self.cocoa = Cocoa()
        else:
            if root.tk.call("tk", "windowingsystem") != "x11":
                raise RuntimeError("Blip needs X11/XWayland for a shaped desktop window.")
            self._open_x11()
            try:
                root.attributes("-type", "utility")
            except tk.TclError:
                pass

    def _open_x11(self):
        self.x11 = C.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        self.shape = C.CDLL(ctypes.util.find_library("Xext") or "libXext.so.6")
        self.x11.XOpenDisplay.argtypes = [C.c_char_p]
        self.x11.XOpenDisplay.restype = C.c_void_p
        self.display = self.x11.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("Cannot connect to an X11 display. Start Blip in a desktop session.")
        self.x11.XDefaultRootWindow.argtypes = [C.c_void_p]
        self.x11.XDefaultRootWindow.restype = C.c_ulong
        self.xroot = self.x11.XDefaultRootWindow(self.display)
        self.x11.XFlush.argtypes = [C.c_void_p]
        self.x11.XCloseDisplay.argtypes = [C.c_void_p]
        self.shape.XShapeQueryVersion.argtypes = [C.c_void_p, C.POINTER(C.c_int),
                                                 C.POINTER(C.c_int)]
        major, minor = C.c_int(), C.c_int()
        if not self.shape.XShapeQueryVersion(self.display, C.byref(major), C.byref(minor)):
            self.close()
            raise RuntimeError("The X11 server does not support shaped windows.")
        self.input_shape = (major.value, minor.value) >= (1, 1)

        class XRectangle(C.Structure):
            _fields_ = [("x", C.c_short), ("y", C.c_short),
                        ("width", C.c_ushort), ("height", C.c_ushort)]
        self.xrect = XRectangle
        self.shape.XShapeCombineRectangles.argtypes = [
            C.c_void_p, C.c_ulong, C.c_int, C.c_int, C.c_int,
            C.POINTER(XRectangle), C.c_int, C.c_int, C.c_int]
        self.x11.XInternAtom.argtypes = [C.c_void_p, C.c_char_p, C.c_int]
        self.x11.XInternAtom.restype = C.c_ulong
        self.x11.XGetWindowProperty.argtypes = [
            C.c_void_p, C.c_ulong, C.c_ulong, C.c_long, C.c_long, C.c_int,
            C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_int),
            C.POINTER(C.c_ulong), C.POINTER(C.c_ulong), C.POINTER(C.c_void_p)]
        self.x11.XFree.argtypes = [C.c_void_p]

    def mapped(self):
        """Apply native flags only after Tk has constructed the real window."""
        if sys.platform == "win32":
            user32 = C.windll.user32
            user32.GetAncestor.argtypes = [C.c_void_p, C.c_uint]
            user32.GetAncestor.restype = C.c_void_p
            hwnd = user32.GetAncestor(self.root.winfo_id(), 2)
            get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            get_long.argtypes, get_long.restype = [C.c_void_p, C.c_int], C.c_ssize_t
            set_long.argtypes = [C.c_void_p, C.c_int, C.c_ssize_t]
            set_long.restype = C.c_ssize_t
            # NOACTIVATE keeps pet clicks from stealing the foreground app's focus.
            # Do not set WS_EX_TRANSPARENT: the visible monkey must stay clickable.
            set_long(hwnd, -20, (get_long(hwnd, -20) | 0x08000000 | 0x80) & ~0x40000)
        elif self.cocoa:
            cocoa = self.cocoa
            app = cocoa.send(cocoa.cls("NSApplication"), "sharedApplication")
            # Accessory apps live in the menu bar without adding a Dock icon.
            cocoa.send(app, "setActivationPolicy:", C.c_bool, (C.c_long,), (1,))
            windows = cocoa.send(app, "windows")
            count = cocoa.send(windows, "count", C.c_ulong)
            for index in range(count):
                window = cocoa.send(windows, "objectAtIndex:", types=(C.c_ulong,), values=(index,))
                title = cocoa.send(window, "title")
                if cocoa.send(title, "UTF8String", C.c_char_p) == b"Blip":
                    self.nswindow = window
                    cocoa.send(window, "setHasShadow:", None, (C.c_bool,), (False,))
                    cocoa.send(window, "setCollectionBehavior:", None,
                               (C.c_ulong,), ((1 << 0) | (1 << 8),))
                    break

    def _xproperty(self, name):
        atom = self.x11.XInternAtom(self.display, name.encode(), 1)
        if not atom:
            return []
        actual, fmt, count, remaining = C.c_ulong(), C.c_int(), C.c_ulong(), C.c_ulong()
        data = C.c_void_p()
        status = self.x11.XGetWindowProperty(
            self.display, self.xroot, atom, 0, 1024, 0, 0, C.byref(actual),
            C.byref(fmt), C.byref(count), C.byref(remaining), C.byref(data))
        try:
            if status == 0 and data.value and fmt.value == 32:
                return list(C.cast(data, C.POINTER(C.c_ulong))[:count.value])
            return []
        finally:
            if data.value:
                self.x11.XFree(data)

    def workarea(self):
        fallback = Rect(0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        if sys.platform == "win32":
            from ctypes import wintypes as W

            class MonitorInfo(C.Structure):
                _fields_ = [("size", W.DWORD), ("monitor", W.RECT),
                            ("work", W.RECT), ("flags", W.DWORD)]
            user32 = C.windll.user32
            user32.MonitorFromPoint.argtypes = [W.POINT, W.DWORD]
            user32.MonitorFromPoint.restype = W.HANDLE
            user32.GetMonitorInfoW.argtypes = [W.HANDLE, C.POINTER(MonitorInfo)]
            info = MonitorInfo()
            info.size = C.sizeof(info)
            monitor = user32.MonitorFromPoint(W.POINT(0, 0), 1)
            if user32.GetMonitorInfoW(monitor, C.byref(info)):
                rect = info.work
                return Rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        elif self.cocoa:
            screens = self.cocoa.send(self.cocoa.cls("NSScreen"), "screens")
            screen = self.cocoa.send(screens, "objectAtIndex:", types=(C.c_ulong,), values=(0,))
            full, visible = self.cocoa.rect(screen, "frame"), self.cocoa.rect(screen, "visibleFrame")
            return Rect(round(visible.origin.x),
                        round(full.size.height - visible.origin.y - visible.size.height),
                        round(visible.size.width), round(visible.size.height))
        elif self.display:
            primary = fallback
            try:
                randr = C.CDLL(ctypes.util.find_library("Xrandr") or "libXrandr.so.2")

                class Monitor(C.Structure):
                    _fields_ = [("name", C.c_ulong), ("primary", C.c_int),
                                ("automatic", C.c_int), ("noutput", C.c_int),
                                ("x", C.c_int), ("y", C.c_int),
                                ("width", C.c_int), ("height", C.c_int),
                                ("mwidth", C.c_int), ("mheight", C.c_int),
                                ("outputs", C.POINTER(C.c_ulong))]
                randr.XRRGetMonitors.argtypes = [C.c_void_p, C.c_ulong, C.c_int,
                                                C.POINTER(C.c_int)]
                randr.XRRGetMonitors.restype = C.POINTER(Monitor)
                randr.XRRFreeMonitors.argtypes = [C.POINTER(Monitor)]
                count = C.c_int()
                monitors = randr.XRRGetMonitors(self.display, self.xroot, 1, C.byref(count))
                try:
                    if monitors and count.value:
                        chosen = next((i for i in range(count.value) if monitors[i].primary), 0)
                        item = monitors[chosen]
                        primary = Rect(item.x, item.y, item.width, item.height)
                finally:
                    if monitors:
                        randr.XRRFreeMonitors(monitors)
            except (OSError, AttributeError):
                pass
            work = self._xproperty("_NET_WORKAREA")
            desktop = self._xproperty("_NET_CURRENT_DESKTOP") or [0]
            offset = desktop[0] * 4
            if len(work) >= offset + 4:
                x, y, width, height = work[offset:offset + 4]
                left, top = max(primary.x, x), max(primary.y, y)
                right, bottom = min(primary.right, x + width), min(primary.bottom, y + height)
                if right > left and bottom > top:
                    return Rect(left, top, right - left, bottom - top)
            return primary
        return fallback

    def shape_canvas(self, mask):
        if not self.display:
            return
        # A one-pixel inset avoids showing the color key at differing raster edges.
        runs = mask_rectangles(mask.filter(ImageFilter.MinFilter(3)))
        rectangles = (self.xrect * len(runs))(*(self.xrect(*rect) for rect in runs))
        frame = self.root.tk.call("wm", "frame", self.root._w)
        frame_id = int(str(frame), 0)
        for window in {frame_id, self.root.winfo_id()}:
            self.shape.XShapeCombineRectangles(self.display, window, 0, 0, 0,
                                               rectangles, len(runs), 0, 0)
            if self.input_shape:
                self.shape.XShapeCombineRectangles(self.display, window, 2, 0, 0,
                                                   rectangles, len(runs), 0, 0)
        self.x11.XFlush(self.display)

    def pointer_hit(self, hit):
        if self.nswindow and self.last_ignore != (not hit):
            self.cocoa.send(self.nswindow, "setIgnoresMouseEvents:", None,
                            (C.c_bool,), (not hit,))
            self.last_ignore = not hit

    def close(self):
        if self.display:
            self.x11.XCloseDisplay(self.display)
            self.display = None


class Painter:
    """Draw Canvas shapes and the matching native hit/shape mask together."""

    def __init__(self, canvas):
        self.canvas = canvas
        self.mask = Image.new("L", (WIDTH, HEIGHT))
        self.ink = ImageDraw.Draw(self.mask)

    def oval(self, box, color):
        box = tuple(round(v) for v in box)
        self.canvas.create_oval(*box, fill=color, outline="", tags="frame")
        self.ink.ellipse(box, fill=255)

    def polygon(self, points, color):
        points = [tuple(round(v) for v in point) for point in points]
        self.canvas.create_polygon(*[v for point in points for v in point],
                                   fill=color, outline="", tags="frame")
        self.ink.polygon(points, fill=255)

    def line(self, points, color, width=2):
        points = [tuple(round(v) for v in point) for point in points]
        self.canvas.create_line(*[v for point in points for v in point], fill=color,
                                width=width, capstyle=tk.ROUND, joinstyle=tk.ROUND, tags="frame")
        self.ink.line(points, fill=255, width=round(width))
        radius = width / 2
        for x, y in points:
            self.ink.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)

    def rounded(self, box, color, radius=15):
        x1, y1, x2, y2 = box
        self.canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2,
                                     fill=color, outline="", tags="frame")
        self.canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius,
                                     fill=color, outline="", tags="frame")
        for x, y in ((x1, y1), (x2 - 2 * radius, y1),
                     (x1, y2 - 2 * radius), (x2 - 2 * radius, y2 - 2 * radius)):
            self.canvas.create_oval(x, y, x + 2 * radius, y + 2 * radius,
                                    fill=color, outline="", tags="frame")
        self.ink.rounded_rectangle(box, radius, fill=255)


class SpriteSheet:
    """Optional uniform grid, ordered left-to-right then top-to-bottom."""

    def __init__(self, path, frame_width=0, frame_height=0):
        with Image.open(path) as source:
            sheet = source.convert("RGBA")
        frame_height = frame_height or frame_width or sheet.height
        frame_width = frame_width or frame_height
        if (sheet.width % frame_width or sheet.height % frame_height or
                frame_width > sheet.width or frame_height > sheet.height):
            raise ValueError("Spritesheet dimensions must be exact multiples of the frame size.")
        count = (sheet.width // frame_width) * (sheet.height // frame_height)
        if count > 256:
            raise ValueError("Use a spritesheet with at most 256 frames.")
        self.images, self.photos = {1: [], -1: []}, {1: [], -1: []}
        for y in range(0, sheet.height, frame_height):
            for x in range(0, sheet.width, frame_width):
                tile = sheet.crop((x, y, x + frame_width, y + frame_height))
                tile.thumbnail((150, 175), Image.Resampling.LANCZOS)
                for direction, image in ((1, tile), (-1, ImageOps.mirror(tile))):
                    self.images[direction].append(image)
                    self.photos[direction].append(ImageTk.PhotoImage(image))


class GmailWatcher:
    """Cancelable, read-only IMAP polling; constructed afresh on every enable."""

    def __init__(self, address, password, generation, events):
        self.address, self.password = address, password
        self.generation, self.events = generation, events
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name="Blip Gmail", daemon=True)

    def emit(self, kind, text):
        if not self.stopped.is_set():
            self.events.put((kind, self.generation, text))

    def run(self):
        last_uid, validity, failures = None, None, 0
        while not self.stopped.is_set():
            try:
                with imaplib.IMAP4_SSL("imap.gmail.com", 993,
                                      ssl_context=ssl.create_default_context(), timeout=15) as client:
                    client.login(self.address, self.password)
                    if client.select("INBOX", readonly=True)[0] != "OK":
                        raise imaplib.IMAP4.error("Inbox is unavailable")
                    status, data = client.status("INBOX", "(UIDNEXT UIDVALIDITY)")
                    if status != "OK" or not data or not data[0]:
                        raise imaplib.IMAP4.error("Inbox status is unavailable")
                    next_match = re.search(rb"UIDNEXT\s+(\d+)", data[0])
                    epoch_match = re.search(rb"UIDVALIDITY\s+(\d+)", data[0])
                    if not next_match or not epoch_match:
                        raise imaplib.IMAP4.error("Inbox has no stable message identifiers")
                    uid_next, epoch = int(next_match[1]), int(epoch_match[1])
                    if last_uid is None or epoch != validity:
                        # Establish a baseline, avoiding a flood of old unread mail.
                        last_uid, validity = uid_next - 1, epoch
                        self.emit("mail_status", "Email alerts are on. I'll watch for new mail.")
                    else:
                        status, rows = client.uid("SEARCH", None, "UNSEEN", "UID", f"{last_uid + 1}:*")
                        if status != "OK":
                            raise imaplib.IMAP4.error("Inbox search failed")
                        uids = sorted(int(uid) for uid in (rows[0] or b"").split()
                                      if int(uid) > last_uid)
                        if uids:
                            status, payload = client.uid(
                                "FETCH", str(uids[-1]), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
                            header = next((part[1] for part in (payload or [])
                                           if isinstance(part, tuple)), b"")
                            if status != "OK":
                                raise imaplib.IMAP4.error("Message header fetch failed")
                            message = email.message_from_bytes(header, policy=policy.default)
                            sender = " ".join(str(message.get("From", "Someone")).split())
                            subject = " ".join(str(message.get("Subject", "(no subject)")).split())
                            prefix = f"{len(uids)} new emails" if len(uids) > 1 else "New email"
                            self.emit("mail", f"{prefix}: {sender[:38]}\n{subject[:68]}")
                        last_uid = max([last_uid, uid_next - 1] + uids)
                    failures = 0
            except imaplib.IMAP4.abort:
                failures += 1
                if failures == 1:
                    self.emit("mail_status", "Gmail disconnected. I'll retry in a minute.")
            except (OSError, TimeoutError):
                failures += 1
                if failures == 1:
                    self.emit("mail_status", "Gmail is unreachable. I'll retry in a minute.")
            except imaplib.IMAP4.error:
                self.emit("mail_auth", "Gmail rejected the connection. Check your address, app password, and IMAP access.")
                return
            if self.stopped.wait(min(300, 60 * max(1, failures))):
                return

    def stop(self):
        self.stopped.set()


def tray_image():
    image = Image.new("RGBA", (64, 64))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 62, 62), radius=18, fill="#192a36")
    draw.ellipse((9, 19, 27, 39), fill="#b86734")
    draw.ellipse((37, 19, 55, 39), fill="#b86734")
    draw.ellipse((16, 12, 48, 47), fill="#b86734")
    draw.ellipse((20, 20, 44, 45), fill="#ffe0a3")
    draw.rounded_rectangle((16, 16, 48, 22), 3, fill="#f6c65a")
    draw.ellipse((25, 28, 29, 33), fill="#283342")
    draw.ellipse((35, 28, 39, 33), fill="#283342")
    draw.arc((27, 31, 37, 39), 0, 180, fill="#813f36", width=2)
    draw.line((51, 14, 51, 53), fill="#f6c65a", width=4)
    for box in ((8, 45, 37, 57), (25, 42, 49, 57), (37, 47, 57, 57)):
        draw.ellipse(box, fill="#edf8ff")
    return image


class Blip:
    def __init__(self, root, args):
        self.root, self.args = root, args
        self.root.title("Blip")
        self.root.withdraw()
        self.events = queue.SimpleQueue()
        self.native = NativeWindow(root)
        self.canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, highlightthickness=0,
                                borderwidth=0, bg=self.native.background, takefocus=False)
        self.canvas.pack()
        family = "Segoe UI" if sys.platform == "win32" else "Helvetica"
        self.body_font = tkfont.Font(family=family, size=-14)
        self.label_font = tkfont.Font(family=family, size=-10, weight="bold")
        self.emoji_font = tkfont.Font(family="Segoe UI Emoji" if sys.platform == "win32"
                                    else "Apple Color Emoji" if sys.platform == "darwin"
                                    else "Noto Color Emoji", size=-21)
        self.visible, self.running, self.tray_ready = True, True, False
        self.email_enabled, self.mail_generation, self.mail_watcher = False, 0, None
        self.gmail_address = os.getenv("BLIP_GMAIL_ADDRESS", "").strip()
        self.gmail_password = os.getenv("BLIP_GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
        self.settings_window, self.preview_window = None, None
        self.capture_busy, self.menu_open = False, False
        self.bubble, self.bubble_until, self.checkin = "", 0.0, False
        self.direction, self.gaze = 1, (0.0, 0.0)
        self.jump_start, self.last_tick = -10.0, time.monotonic()
        self.hit_mask, self.mood_buttons = Image.new("L", (WIDTH, HEIGHT)), []
        self.head_y = HEIGHT - 133
        self.work = self.native.workarea()
        self.x = self.work.x + (self.work.width - WIDTH) * 0.65
        self.window_x, self.window_y = 0, 0
        self.sprites = SpriteSheet(args.sprites, args.frame_width, args.frame_height) if args.sprites else None
        self._make_context_menu()
        self.canvas.bind("<Button-1>", self._left_click)
        self.canvas.bind("<Button-3>", self._popup_event)
        if sys.platform == "darwin":
            self.canvas.bind("<Button-2>", self._popup_event)
            self.canvas.bind("<Control-Button-1>", self._popup_event)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.bind("<Escape>", lambda event: self.hide())
        self.root.bind("<Alt-F4>", lambda event: self._hide_key())
        self.root.bind("<Map>", self._on_map)
        self._position()
        self._draw(time.monotonic())
        self.native.shape_canvas(self.hit_mask)
        self._start_tray()
        self.root.deiconify()
        self.root.update_idletasks()
        self.native.mapped()
        self.root.after(FRAME_MS, self._animate)
        self.root.after(POINTER_MS, self._track_cursor)
        self.root.after(50, self._drain_events)
        self.root.after(3000, self._refresh_workarea)

    def _on_map(self, event):
        if event.widget is self.root:
            self.native.mapped()
            self.native.shape_canvas(self.hit_mask)

    def _hide_key(self):
        self.hide()
        return "break"

    def _make_context_menu(self):
        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label="Encourage me", command=self.encourage)
        self.menu.add_command(label="Say something funny", command=self.funny)
        self.menu.add_command(label="Check in on me", command=self.check_in)
        self.email_var = tk.BooleanVar(value=False)
        self.menu.add_checkbutton(label="Toggle email alerts", variable=self.email_var,
                                  command=self.toggle_email)
        self.menu.add_command(label="Gmail settings…", command=self.gmail_settings)
        self.menu.add_separator()
        self.menu.add_command(label="Look at screen…", command=self.look_at_screen)
        self.show_var = tk.BooleanVar(value=True)
        self.menu.add_checkbutton(label="Show Blip", variable=self.show_var, command=self.toggle_visible)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.quit)
        self.menu.bind("<Unmap>", lambda event: setattr(self, "menu_open", False))

    def _start_tray(self):
        # Import after Tk initialization; some Linux backends need a display at import.
        import pystray

        def post(action):
            def callback(icon, item):
                self.events.put((action,))
            return callback

        icon_type = pystray.Icon
        if not icon_type.HAS_MENU:
            # pystray's Xorg backend has no native menu. Intercept its button event
            # in this one isolated adapter and open a Tk menu on the main thread.
            event_queue = self.events

            class XorgMenuIcon(icon_type):
                def _on_button_press(self, event):
                    if event.detail == 3:
                        event_queue.put(("menu", event.root_x, event.root_y))
                    elif event.detail == 1:
                        event_queue.put(("show",))
            icon_type = XorgMenuIcon
        menu = pystray.Menu(
            pystray.MenuItem("Encourage me", post("encourage")),
            pystray.MenuItem("Say something funny", post("funny")),
            pystray.MenuItem("Check in on me", post("check_in")),
            pystray.MenuItem("Toggle email alerts", post("toggle_email"),
                             checked=lambda item: self.email_enabled),
            pystray.MenuItem("Gmail settings…", post("gmail_settings")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Look at screen…", post("look_at_screen")),
            pystray.MenuItem("Show Blip", post("toggle_visible"), default=True,
                             checked=lambda item: self.visible),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", post("quit")),
        )
        self.tray = icon_type("blip", tray_image(), "Blip · your little cloud companion", menu)

        def setup(icon):
            icon.visible = True
            self.events.put(("tray_ready",))

        def run():
            try:
                self.tray.run(setup=setup)
            except Exception:
                LOG.exception("Tray backend failed")
                self.events.put(("tray_error",))

        if sys.platform == "darwin":
            self.tray.run_detached(setup=setup)
        else:
            threading.Thread(target=run, name="Blip tray", daemon=True).start()

    def _update_menu(self):
        self.email_var.set(self.email_enabled)
        self.show_var.set(self.visible)
        if self.tray_ready:
            try:
                self.tray.update_menu()
            except Exception:
                LOG.debug("Tray menu update failed", exc_info=True)

    def _position(self):
        left, right = self.work.x, max(self.work.x, self.work.right - WIDTH)
        self.x = clamp(self.x, left, right)
        self.window_x = round(self.x)
        self.window_y = max(self.work.y, self.work.bottom - HEIGHT - 8)
        self.root.geometry(f"{WIDTH}x{HEIGHT}{self.window_x:+d}{self.window_y:+d}")

    def _refresh_workarea(self):
        if not self.running:
            return
        self.work = self.native.workarea()
        self._position()
        self.root.attributes("-topmost", True)
        self.root.after(3000, self._refresh_workarea)

    def _animate(self):
        if not self.running:
            return
        now = time.monotonic()
        elapsed, self.last_tick = min(now - self.last_tick, 0.1), now
        if self.bubble and now >= self.bubble_until:
            self.bubble, self.checkin = "", False
        if self.visible and not self.capture_busy:
            if not self.bubble and not self.menu_open and now - self.jump_start >= 0.9:
                self.x, self.direction = walk_step(
                    self.x, self.direction, elapsed, self.work.x, self.work.right - WIDTH)
            self._position()
            self._draw(now)
            self.native.shape_canvas(self.hit_mask)
            if self.native.cocoa:
                px, py = self.root.winfo_pointerxy()
                self.native.pointer_hit(self._hit(px - self.window_x, py - self.window_y))
        self.root.after(FRAME_MS, self._animate)

    def _hit(self, x, y):
        return 0 <= x < WIDTH and 0 <= y < HEIGHT and bool(self.hit_mask.getpixel((int(x), int(y))))

    def _track_cursor(self):
        if not self.running:
            return
        px, py = self.root.winfo_pointerxy()
        dx, dy = px - (self.window_x + WIDTH / 2), py - (self.window_y + self.head_y)
        distance = math.hypot(dx, dy)
        self.gaze = (dx / max(distance, 1) * 3, dy / max(distance, 1) * 3) if distance <= NEAR_DISTANCE else (0, 0)
        self.root.after(POINTER_MS, self._track_cursor)

    def _draw(self, now):
        # Keep each complete frame until the replacement is built; Tk paints on idle.
        old_items = self.canvas.find_withtag("frame")
        painter = Painter(self.canvas)
        self.head_y = HEIGHT - 133 - bounce_height(now - self.jump_start) + 2 * math.sin(now * 2.8)
        if self.sprites:
            index = int(now * self.args.sprite_fps) % len(self.sprites.photos[self.direction])
            image = self.sprites.images[self.direction][index]
            x, y = round(WIDTH / 2 - image.width / 2), round(self.head_y - 40)
            self.canvas.create_image(x, y, image=self.sprites.photos[self.direction][index],
                                     anchor="nw", tags="frame")
            painter.mask.paste(image.getchannel("A"), (x, y))
        else:
            self._draw_monkey(painter, now)
        self.mood_buttons = []
        if self.bubble:
            self._draw_bubble(painter)
        for item in old_items:
            self.canvas.delete(item)
        self.hit_mask = painter.mask

    def _draw_monkey(self, p, now):
        cx, cy, direction = WIDTH / 2, self.head_y, self.direction
        gold, dark, fur, skin = "#f6c65a", "#75442d", "#ad6336", "#ffdda0"

        def oval(x1, y1, x2, y2, color):
            p.oval((cx + x1, cy + y1, cx + x2, cy + y2), color)

        def points(coords):
            return [(cx + x * direction, cy + y) for x, y in coords]

        # Curled tail, red scarf, and swinging feet behind the body.
        p.line(points([(-22, 67), (-44, 60), (-52, 42), (-44, 31), (-33, 36), (-37, 45)]), dark, 10)
        p.line(points([(-22, 67), (-44, 60), (-52, 42), (-44, 31), (-33, 36), (-37, 45)]), fur, 6)
        flutter = math.sin(now * 6) * 5
        p.polygon(points([(-14, 29), (-53, 31 + flutter), (-43, 43 + flutter), (-18, 41)]), "#d94d4e")
        p.polygon(points([(-16, 36), (-47, 50 + flutter), (-38, 55 + flutter), (-11, 44)]), "#a93640")
        step = math.sin(now * 6) * 3
        oval(-27, 71 + step, -5, 94 + step, dark)
        oval(6, 73 - step, 29, 94 - step, dark)
        oval(-26, 86 + step, -3, 97 + step, "#dc6b48")
        oval(8, 86 - step, 34, 97 - step, "#dc6b48")
        # Golden tunic, belt, arms, and a small central jade clasp.
        oval(-28, 28, 28, 83, dark)
        oval(-25, 29, 25, 80, gold)
        oval(-16, 36, 16, 73, "#ffe09a")
        p.line(points([(-25, 68), (25, 68)]), "#c74542", 8)
        oval(-5, 63, 5, 73, "#439e8d")
        p.line(points([(-20, 40), (-32, 58), (-20, 61)]), fur, 12)
        p.line(points([(20, 40), (39, 49), (48, 38)]), fur, 12)
        # The staff rotates around the gripping hand during the jump.
        t = now - self.jump_start
        angle = (-0.12 + (min(t / 0.9, 1) * math.tau * 2 if 0 <= t < 0.9 else 0)) * direction
        pivot = (cx + 49 * direction, cy + 37)
        vx, vy = math.sin(angle), math.cos(angle)
        endpoints = [(pivot[0] + vx * length, pivot[1] + vy * length) for length in (-70, 70)]
        p.line(endpoints, "#9a611f", 7)
        p.line(endpoints, gold, 4)
        for sign in (-1, 1):
            p.line([(pivot[0] + vx * length * sign, pivot[1] + vy * length * sign)
                    for length in (52, 69)], "#ffe59b", 8)
        oval(41 * direction - 7, 30, 41 * direction + 7, 44, skin)
        # Ears, fur cap, heart-shaped face, and the Monkey King's golden circlet.
        oval(-39, -14, -19, 10, dark)
        oval(19, -14, 39, 10, dark)
        oval(-35, -10, -22, 6, "#e8a56d")
        oval(22, -10, 35, 6, "#e8a56d")
        oval(-30, -35, 30, 30, dark)
        oval(-28, -33, 28, 28, fur)
        p.polygon(points([(-20, -27), (-15, -43), (-3, -34), (6, -44), (12, -29)]), fur)
        oval(-23, -19, 3, 13, skin)
        oval(-3, -19, 23, 13, skin)
        oval(-18, -4, 18, 25, skin)
        p.line([(cx - 26, cy - 23), (cx + 26, cy - 23)], gold, 7)
        p.line([(cx - 7, cy - 23), (cx - 6, cy - 29), (cx, cy - 31),
                (cx + 6, cy - 29), (cx + 7, cy - 23)], "#fff0b2", 3)
        # White eyes remain distinct; pupils follow the nearby global cursor.
        gx, gy = self.gaze
        blink = int(now * 10) % 57 == 0
        for eye_x in (-10, 10):
            if blink:
                p.line([(cx + eye_x - 5, cy - 3), (cx + eye_x + 5, cy - 3)], dark, 2)
            else:
                oval(eye_x - 6, -11, eye_x + 6, 3, "#fffdf7")
                oval(eye_x - 2.8 + gx, -7 + gy, eye_x + 2.8 + gx, -0.5 + gy, "#273343")
                oval(eye_x - 1.5 + gx, -6 + gy, eye_x + gx, -4.5 + gy, "#ffffff")
        oval(-23, 5, -14, 10, "#f4aa7c")
        oval(14, 5, 23, 10, "#f4aa7c")
        oval(-3, 3, 3, 8, "#bd754a")
        p.line([(cx - 8, cy + 13), (cx, cy + 17), (cx + 8, cy + 13)], dark, 2)
        # The cloud rides along with Blip, with a cool rim and warm highlights.
        oval(-67, 108, 69, 128, "#a4c6db")
        for box in ((-69, 98, -19, 123), (-44, 86, 12, 124),
                    (-4, 94, 50, 125), (29, 103, 71, 124)):
            oval(*box, "#eaf7ff")
        p.line([(cx - 50, cy + 112), (cx - 31, cy + 116),
                (cx - 14, cy + 111)], "#c0dce9", 2)
        p.line([(cx + 15, cy + 114), (cx + 29, cy + 119),
                (cx + 47, cy + 115)], "#c0dce9", 2)

    def _draw_bubble(self, p):
        left, right, bottom = 14, WIDTH - 14, self.head_y - 64
        body = self.canvas.create_text(0, 0, text=self.bubble, anchor="nw",
                                       font=self.body_font, fill="#293c49", width=right - left - 36,
                                       justify="left", tags="frame")
        bounds = self.canvas.bbox(body)
        text_height = bounds[3] - bounds[1]
        height = max(128 if self.checkin else 96, text_height + (98 if self.checkin else 50))
        top = bottom - height
        p.rounded((left + 2, top + 3, right + 2, bottom + 3), "#b4c8ce", 16)
        p.rounded((left, top, right, bottom), "#fffaf0", 16)
        p.polygon([(WIDTH / 2 - 12, bottom - 1), (WIDTH / 2 + 3, bottom + 14),
                   (WIDTH / 2 + 15, bottom - 1)], "#fffaf0")
        self.canvas.create_text(left + 18, top + 17, text="B L I P", anchor="w",
                                font=self.label_font, fill="#a66b1e", tags="frame")
        self.canvas.coords(body, left + 18, top + 34)
        self.canvas.tag_raise(body)
        if self.checkin:
            for index, (emoji, _) in enumerate(MOODS):
                x, y = left + 18 + index * 100, bottom - 48
                box = (x, y, x + 90, y + 36)
                p.rounded(box, ("#e5f1dd", "#f6ecd4", "#e4edf8")[index], 10)
                self.canvas.create_text(x + 45, y + 18, text=emoji,
                                        font=self.emoji_font, fill="#293c49", tags="frame")
                self.mood_buttons.append((box, index))

    def _left_click(self, event):
        for (left, top, right, bottom), index in self.mood_buttons:
            if left <= event.x <= right and top <= event.y <= bottom:
                self.show_bubble(MOODS[index][1])
                return "break"
        if self._hit(event.x, event.y):
            self.jump_start = time.monotonic()
            self.encourage()
        return "break"

    def _popup_event(self, event):
        self.popup(event.x_root, event.y_root)
        return "break"

    def popup(self, x, y):
        self.menu_open = True
        self._update_menu()
        try:
            self.menu.tk_popup(x, y)
        finally:
            self.menu.grab_release()

    def show(self):
        self.visible = True
        self._position()
        # Repeated bubbles should not remap a window during a mouse callback.
        # Capture owns the temporary withdrawal until its worker has finished.
        if self.root.state() == "withdrawn" and not self.capture_busy:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
        self._update_menu()

    def hide(self):
        self.visible = False
        self.root.withdraw()
        self._update_menu()

    def toggle_visible(self):
        self.hide() if self.visible else self.show()

    def show_bubble(self, text, checkin=False):
        self.bubble = text
        self.bubble_until = time.monotonic() + BUBBLE_SECONDS
        self.checkin = checkin
        self.show()

    def encourage(self):
        self.show_bubble(random.choice(ENCOURAGEMENTS))

    def funny(self):
        self.show_bubble(random.choice(FUNNY_LINES))

    def check_in(self):
        self.show_bubble("How are you doing?", checkin=True)

    def toggle_email(self):
        if self.email_enabled:
            self._stop_email()
            self.show_bubble("Email alerts are off. Enjoy a little quiet.")
        elif not self.gmail_address or not self.gmail_password:
            self.email_var.set(False)
            self.gmail_settings()
        else:
            self._start_email()

    def _start_email(self):
        self._stop_email()
        self.email_enabled = True
        self.mail_watcher = GmailWatcher(self.gmail_address, self.gmail_password,
                                         self.mail_generation, self.events)
        self.mail_watcher.thread.start()
        self._update_menu()
        self.show_bubble("Connecting to Gmail…")

    def _stop_email(self):
        self.mail_generation += 1
        self.email_enabled = False
        if self.mail_watcher:
            self.mail_watcher.stop()
            self.mail_watcher = None
        self._update_menu()

    def gmail_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.deiconify()
            self.settings_window.lift()
            return
        window = self.settings_window = tk.Toplevel(self.root)
        window.title("Blip · Gmail settings")
        window.resizable(False, False)
        window.attributes("-topmost", True)
        frame = ttk.Frame(window, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="A little nudge when new mail arrives", font=("TkDefaultFont", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
        ttk.Label(frame, text="Gmail address").grid(row=1, column=0, sticky="w", pady=6)
        address = ttk.Entry(frame, width=34)
        address.insert(0, self.gmail_address)
        address.grid(row=1, column=1, padx=(12, 0))
        ttk.Label(frame, text="App password").grid(row=2, column=0, sticky="w", pady=6)
        password = ttk.Entry(frame, width=34, show="•")
        password.insert(0, self.gmail_password)
        password.grid(row=2, column=1, padx=(12, 0))
        ttk.Label(frame, text="Use a 16-character Google app password with 2-Step Verification.\n"
                             "Credentials stay in memory for this session.\n"
                             "Only new unread mail is checked; existing mail stays unchanged.",
                  wraplength=410, justify="left").grid(row=3, column=0, columnspan=2,
                                                      sticky="w", pady=14)
        error = tk.StringVar()
        ttk.Label(frame, textvariable=error, foreground="#ac3535", wraplength=400).grid(
            row=4, column=0, columnspan=2, sticky="w")

        def save():
            user, secret = address.get().strip(), password.get().replace(" ", "").strip()
            if "@" not in user or len(secret) != 16:
                error.set("Enter your email address and a 16-character app password.")
                return
            self.gmail_address, self.gmail_password = user, secret
            password.delete(0, "end")
            window.destroy()
            self.settings_window = None
            self._start_email()

        controls = ttk.Frame(frame)
        controls.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(controls, text="Cancel", command=window.destroy).pack(side="left", padx=6)
        ttk.Button(controls, text="Enable alerts", command=save).pack(side="left")
        window.bind("<Return>", lambda event: save())
        window.protocol("WM_DELETE_WINDOW", window.withdraw)
        window.update_idletasks()
        window.geometry(f"+{self.work.x + max(0, (self.work.width - window.winfo_width()) // 2)}"
                        f"+{self.work.y + max(0, (self.work.height - window.winfo_height()) // 2)}")
        address.focus_set()

    def look_at_screen(self):
        if self.capture_busy:
            return
        self.capture_busy = True
        self.root.withdraw()
        if self.preview_window and self.preview_window.winfo_exists():
            self.preview_window.destroy()
        # Let the compositor remove Blip/menu before capturing.
        def capture():
            try:
                image = ImageGrab.grab(all_screens=True) if sys.platform == "win32" else ImageGrab.grab()
                self.events.put(("screen", image))
            except Exception as error:
                self.events.put(("screen_error", str(error)))

        self.root.after(180, lambda: threading.Thread(target=capture, name="Blip screen", daemon=True).start())

    def _screen_preview(self, image):
        window = self.preview_window = tk.Toplevel(self.root)
        window.title("Blip · Screen snapshot")
        width, height = min(980, self.work.width - 80), min(680, self.work.height - 80)
        window.geometry(f"{max(320, width)}x{max(260, height)}")
        window.configure(bg="#182a35")
        window.protocol("WM_DELETE_WINDOW", window.withdraw)
        panel = ttk.Frame(window, padding=12)
        panel.pack(fill="x", side="bottom")
        ttk.Label(panel, text=f"{image.width} × {image.height} pixels · local snapshot").pack(side="left")

        def save():
            path = filedialog.asksaveasfilename(parent=window, title="Save screen snapshot",
                                               initialfile=time.strftime("blip-screen-%Y%m%d-%H%M%S.png"),
                                               defaultextension=".png", filetypes=[("PNG image", "*.png")])
            if path:
                try:
                    image.save(path, format="PNG")
                except OSError as error:
                    messagebox.showerror("Blip", f"Could not save the snapshot: {error}", parent=window)

        ttk.Button(panel, text="Save PNG…", command=save).pack(side="right")
        picture = tk.Label(window, bg="#182a35", borderwidth=0)
        picture.pack(fill="both", expand=True, padx=16, pady=16)
        pending = [None]

        def redraw():
            pending[0] = None
            if not picture.winfo_exists():
                return
            thumbnail = image.copy()
            thumbnail.thumbnail((max(1, picture.winfo_width()), max(1, picture.winfo_height())),
                                Image.Resampling.LANCZOS)
            picture.photo = ImageTk.PhotoImage(thumbnail)
            picture.configure(image=picture.photo)

        def resize(event):
            if pending[0]:
                window.after_cancel(pending[0])
            pending[0] = window.after(100, redraw)

        picture.bind("<Configure>", resize)
        window.lift()

    def _drain_events(self):
        if not self.running:
            return
        actions = {"encourage": self.encourage, "funny": self.funny,
                   "check_in": self.check_in, "toggle_email": self.toggle_email,
                   "gmail_settings": self.gmail_settings, "look_at_screen": self.look_at_screen,
                   "toggle_visible": self.toggle_visible, "show": self.show, "quit": self.quit}
        for _ in range(100):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind in actions:
                actions[kind]()
            elif kind == "tray_ready":
                self.tray_ready = True
                self._update_menu()
            elif kind == "tray_error":
                self.tray_ready = False
                self.show_bubble("The system tray is unavailable. Right-click me for my menu.")
            elif kind == "menu":
                self.popup(event[1], event[2])
            elif kind.startswith("mail") and event[1] == self.mail_generation and self.email_enabled:
                if kind == "mail_auth":
                    self._stop_email()
                self.show_bubble(event[2])
            elif kind in ("screen", "screen_error"):
                self.capture_busy = False
                if self.visible:
                    self.root.deiconify()
                if kind == "screen":
                    self._screen_preview(event[1])
                else:
                    messagebox.showerror("Blip · Screen capture", "Could not capture the desktop.\n\n"
                                         "On macOS, allow Screen Recording for Python/Terminal. "
                                         "On Linux, check your desktop's screenshot permissions.\n\n" + event[1],
                                         parent=self.root)
            if not self.running:
                return
        self.root.after(50, self._drain_events)

    def quit(self):
        """The sole normal shutdown path, reached through the tray/context menu."""
        if not self.running:
            return
        self.running = False
        if self.mail_watcher:
            self.mail_watcher.stop()
        # Cancel callbacks before destroying their Tcl commands (including previews).
        for job in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        try:
            self.tray.visible = False
        except Exception:
            LOG.debug("Tray hide failed", exc_info=True)
        try:
            self.tray.stop()
        except Exception:
            LOG.debug("Tray stop failed", exc_info=True)
        self.root.destroy()
        self.native.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Blip, your Sun Wukong desktop companion.")
    parser.add_argument("--version", action="version", version=f"Blip {__version__}")
    parser.add_argument("--sprites", type=Path, help="optional PNG spritesheet with uniform frames")
    parser.add_argument("--frame-width", type=int, default=0, help="frame width; default: sheet height")
    parser.add_argument("--frame-height", type=int, default=0, help="frame height; default: square frames")
    parser.add_argument("--sprite-fps", type=float, default=8, help="spritesheet animation rate (default: 8)")
    args = parser.parse_args(argv)
    if args.frame_width < 0 or args.frame_height < 0 or not 0 < args.sprite_fps <= 60:
        parser.error("Frame sizes must be nonnegative and sprite FPS must be between 0 and 60.")
    if args.sprites and not args.sprites.is_file():
        parser.error(f"Spritesheet does not exist: {args.sprites}")
    return args


def main():
    args = parse_args()
    log_path = configure_logging()
    enable_dpi_awareness()
    try:
        root = create_root()
    except tk.TclError as error:
        LOG.error("Tk could not start: %s. Install Python with Tcl/Tk and run in a desktop session.", error)
        return 1

    def callback_error(exc_type, value, traceback):
        LOG.error("An interface callback failed", exc_info=(exc_type, value, traceback))

    root.report_callback_exception = callback_error
    try:
        app = Blip(root, args)
    except Exception as error:
        LOG.exception("Cannot start Blip")
        details = f"\n\nDetails: {log_path}" if log_path else ""
        messagebox.showerror("Blip could not start", f"{error}\n\nCheck your Python/Tk installation "
                             f"and desktop system tray support.{details}", parent=root)
        root.destroy()
        return 1
    try:
        root.mainloop()
    except KeyboardInterrupt:
        app.hide()
        # A terminal interrupt hides Blip; Quit in its menu is the normal exit.
        root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
