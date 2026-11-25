"""Behavioral checks. Run: python -m unittest -v test_main

GUI checks are opt-in: BLIP_GUI_TESTS=1 python -m unittest -v test_main
They create a real tray/window, exercise native hit testing on Windows, and exit.
No Gmail account is contacted; IMAP is tested with a scripted read-only mailbox.
"""

import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import queue
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

import main


class MotionTests(unittest.TestCase):
    def test_walking_reflects_at_both_edges(self):
        x, direction = main.walk_step(99, 1, 0.1, 0, 100)
        self.assertLess(x, 100)
        self.assertEqual(direction, -1)
        x, direction = main.walk_step(1, -1, 0.1, 0, 100)
        self.assertGreater(x, 0)
        self.assertEqual(direction, 1)
        for _ in range(2000):
            x, direction = main.walk_step(x, direction, 0.1, 0, 100)
            self.assertTrue(0 <= x <= 100)

    def test_small_workarea_and_suspended_clock(self):
        self.assertEqual(main.walk_step(10, 1, 10, 20, 5), (20.0, 1))
        x, direction = main.walk_step(30, 1, 15, 0, 50)
        self.assertTrue(0 <= x <= 50)
        self.assertIn(direction, (-1, 1))

    def test_jump_has_two_peaks_and_settles(self):
        self.assertAlmostEqual(main.bounce_height(0), 0)
        self.assertAlmostEqual(main.bounce_height(0.31), 58)
        self.assertAlmostEqual(main.bounce_height(0.62), 0)
        self.assertAlmostEqual(main.bounce_height(0.76), 14)
        self.assertEqual(main.bounce_height(1), 0)


class MaskTests(unittest.TestCase):
    def test_shape_rectangles_preserve_holes(self):
        source = Image.new("L", (40, 32))
        draw = ImageDraw.Draw(source)
        draw.ellipse((2, 2, 36, 28), fill=255)
        draw.rectangle((12, 10, 20, 18), fill=0)
        rebuilt = Image.new("L", source.size)
        ink = ImageDraw.Draw(rebuilt)
        for x, y, width, height in main.mask_rectangles(source):
            ink.rectangle((x, y, x + width - 1, y + height - 1), fill=255)
        self.assertEqual(source.tobytes(), rebuilt.tobytes())
        self.assertLess(len(main.mask_rectangles(source)), 100)


class ScriptedStop:
    def __init__(self, polls):
        self.polls, self.waits, self.stopped = polls, 0, False

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.waits += 1
        return self.waits >= self.polls

    def set(self):
        self.stopped = True


class MailTests(unittest.TestCase):
    def run_mail(self, states, error=None):
        events, calls = queue.SimpleQueue(), []
        state_iter = iter(states)

        class Mailbox:
            def __init__(self, *args, **kwargs):
                self.state = next(state_iter)
                calls.append(("connect", args, kwargs))

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def login(self, address, password):
                if error:
                    raise error
                calls.append(("login", address))

            def select(self, mailbox, readonly):
                calls.append(("select", mailbox, readonly))
                return "OK", [b"50"]

            def status(self, mailbox, fields):
                return "OK", [f"INBOX (UIDNEXT {self.state[0]} UIDVALIDITY {self.state[1]})".encode()]

            def uid(self, action, *args):
                calls.append((action, args))
                if action == "SEARCH":
                    return "OK", [self.state[2]]
                return "OK", [(b"header", b"From: Friend <friend@example.com>\r\n"
                               b"Subject: =?utf-8?q?Hello_=E2=98=80?=\r\n\r\n"), b")"]

        watcher = main.GmailWatcher("person@gmail.com", "a" * 16, 7, events)
        watcher.stopped = ScriptedStop(len(states))
        with patch.object(main.imaplib, "IMAP4_SSL", Mailbox):
            watcher.run()
        received = []
        while not events.empty():
            received.append(events.get_nowait())
        return received, calls

    def test_baseline_new_mail_and_read_only_headers(self):
        events, calls = self.run_mail([(101, 1, b""), (103, 1, b"3 101 102")])
        alerts = [event for event in events if event[0] == "mail"]
        self.assertEqual(len(alerts), 1)
        self.assertIn("2 new emails", alerts[0][2])
        self.assertIn("Hello ☀", alerts[0][2])
        self.assertTrue(all(call[2] for call in calls if call[0] == "select"))
        self.assertIn(("FETCH", ("102", "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")), calls)

    def test_reversed_imap_range_never_realerts_old_uid(self):
        events, _ = self.run_mail([(101, 1, b""), (101, 1, b"100")])
        self.assertFalse(any(event[0] == "mail" for event in events))

    def test_mailbox_recreation_resets_baseline(self):
        events, _ = self.run_mail([(101, 1, b""), (501, 2, b"500")])
        self.assertFalse(any(event[0] == "mail" for event in events))

    def test_bad_credentials_disable_alerts_without_leaking_password(self):
        events, _ = self.run_mail([(1, 1, b"")], main.imaplib.IMAP4.error("password rejected"))
        self.assertEqual(events[0][0], "mail_auth")
        self.assertNotIn("a" * 16, events[0][2])

    def test_dropped_connection_retries_without_disabling_alerts(self):
        events, calls = self.run_mail([(1, 1, b""), (1, 1, b"")],
                                      main.imaplib.IMAP4.abort("connection lost"))
        self.assertEqual(sum(call[0] == "connect" for call in calls), 2)
        self.assertEqual([event[0] for event in events], ["mail_status"])


@unittest.skipUnless(os.getenv("BLIP_GUI_TESTS") == "1", "Set BLIP_GUI_TESTS=1 in a desktop session")
class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.enable_dpi_awareness()
        cls.root = main.create_root()
        cls.app = main.Blip(cls.root, main.parse_args([]))
        cls.callback_errors = []
        cls.root.report_callback_exception = lambda *error: cls.callback_errors.append(error)
        cls.pump(0.8)

    @classmethod
    def pump(cls, duration=0.1):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            cls.root.update()
            time.sleep(0.01)

    @classmethod
    def tearDownClass(cls):
        cls.app.quit()

    def test_01_real_tray_walk_and_primary_workarea(self):
        app = self.app
        self.assertTrue(app.tray_ready)
        self.assertTrue(app.tray.visible)
        before = app.x
        self.pump(0.25)
        self.assertGreater(app.x, before)
        self.assertGreaterEqual(app.window_x, app.work.x)
        self.assertLessEqual(app.window_x + main.WIDTH, app.work.right)
        self.assertLessEqual(app.window_y + main.HEIGHT, app.work.bottom)

    @unittest.skipUnless(sys.platform == "win32", "Windows native hit-test check")
    def test_02_transparent_pixels_pass_through_and_monkey_is_clickable(self):
        app = self.app
        app.bubble = ""
        self.pump()
        user32 = C.windll.user32
        user32.WindowFromPoint.argtypes = [W.POINT]
        user32.WindowFromPoint.restype = W.HWND
        user32.GetAncestor.argtypes = [W.HWND, W.UINT]
        user32.GetAncestor.restype = W.HWND
        pet = user32.GetAncestor(self.root.winfo_id(), 2)
        empty = user32.WindowFromPoint(W.POINT(app.window_x + 4, app.window_y + 4))
        face = user32.WindowFromPoint(W.POINT(app.window_x + main.WIDTH // 2,
                                             app.window_y + round(app.head_y)))
        self.assertNotEqual(user32.GetAncestor(empty, 2), pet)
        self.assertEqual(user32.GetAncestor(face, 2), pet)

    def test_03_click_jump_and_replaced_bubble_timeout(self):
        app = self.app
        app.canvas.event_generate("<Button-1>", x=main.WIDTH // 2, y=round(app.head_y))
        app.canvas.event_generate("<ButtonRelease-1>", x=main.WIDTH // 2, y=round(app.head_y))
        self.pump(0.1)
        self.assertIn(app.bubble, main.ENCOURAGEMENTS)
        self.assertGreater(main.bounce_height(time.monotonic() - app.jump_start), 0)
        app.funny()
        self.assertIn(app.bubble, main.FUNNY_LINES)
        self.pump(4.1)
        self.assertEqual(app.bubble, "")

    def test_04_all_three_mood_replies(self):
        app = self.app
        for index in range(3):
            app.check_in()
            self.pump(0.08)
            self.assertEqual(len(app.mood_buttons), 3)
            (left, top, right, bottom), _ = app.mood_buttons[index]
            app.canvas.event_generate("<Button-1>", x=round((left + right) / 2), y=round((top + bottom) / 2))
            self.pump(0.08)
            self.assertEqual(app.bubble, main.MOODS[index][1])
            self.assertFalse(app.checkin)

    def test_05_hide_and_restore_through_actual_tray_callback(self):
        self.app.hide()
        self.assertEqual(self.root.state(), "withdrawn")
        self.assertTrue(self.app.running)
        self.assertTrue(self.app.tray.visible)
        item = next(item for item in self.app.tray.menu.items if item.text == "Show Blip")
        item(self.app.tray)
        self.pump()
        self.assertTrue(self.app.visible)
        self.assertEqual(self.root.state(), "normal")

    def test_06_gmail_dialog_reopens_after_close(self):
        app = self.app
        app.gmail_settings()
        self.pump()
        window = app.settings_window
        self.root.tk.call(window.protocol("WM_DELETE_WINDOW"))
        self.assertEqual(window.state(), "withdrawn")
        app.gmail_settings()
        self.pump()
        self.assertEqual(window.state(), "normal")
        window.destroy()
        app.settings_window = None

    def test_07_screen_capture_worker_preview_and_lossless_save(self):
        app = self.app
        snapshot = Image.new("RGB", (1600, 900), "#244666")
        with patch.object(main.ImageGrab, "grab", return_value=snapshot) as grab:
            app.look_at_screen()
            self.pump(0.8)
        self.assertFalse(app.capture_busy)
        self.assertTrue(app.preview_window.winfo_exists())
        if sys.platform == "win32":
            grab.assert_called_once_with(all_screens=True)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "screen.png"
            with patch.object(main.filedialog, "asksaveasfilename", return_value=str(path)):
                panel = next(child for child in app.preview_window.winfo_children()
                             if isinstance(child, main.ttk.Frame))
                button = next(child for child in panel.winfo_children() if isinstance(child, main.ttk.Button))
                button.invoke()
            with Image.open(path) as saved:
                self.assertEqual(saved.size, snapshot.size)
                self.assertEqual(saved.tobytes(), snapshot.tobytes())
        app.preview_window.destroy()
        app.preview_window = None

    def test_08_screen_capture_available_in_this_session(self):
        snapshot = main.ImageGrab.grab(all_screens=True) if sys.platform == "win32" else main.ImageGrab.grab()
        self.assertGreaterEqual(snapshot.width, self.app.work.width)
        self.assertGreaterEqual(snapshot.height, self.app.work.height)

    def test_09_optional_spritesheet_grid_and_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sheet.png"
            image = Image.new("RGBA", (64, 32))
            ImageDraw.Draw(image).rectangle((0, 0, 15, 15), fill="gold")
            image.save(path)
            sprites = main.SpriteSheet(path, 32, 32)
            self.assertEqual(len(sprites.photos[1]), 2)
            self.assertEqual(sprites.images[1][0].getpixel((0, 0)), (255, 215, 0, 255))
            self.assertEqual(sprites.images[-1][0].getpixel((31, 0)), (255, 215, 0, 255))
            with self.assertRaises(ValueError):
                main.SpriteSheet(path, 30, 32)

    def test_10_no_tk_callback_errors(self):
        self.assertEqual(self.callback_errors, [])

    def test_11_long_notification_fits_inside_bubble(self):
        text = "New email: Friend <friend@example.com>\nA slightly longer subject that wraps onto another line"
        self.app.show_bubble(text)
        self.pump(0.1)
        text_item = next(item for item in self.app.canvas.find_withtag("frame")
                         if self.app.canvas.type(item) == "text" and
                         self.app.canvas.itemcget(item, "text") == text)
        box = self.app.canvas.bbox(text_item)
        self.assertGreaterEqual(box[1], 0)
        self.assertLess(box[3], self.app.head_y - 64)

    def test_12_quit_through_tray_callback(self):
        item = next(item for item in self.app.tray.menu.items if item.text == "Quit")
        item(self.app.tray)
        self.pump(0.15)
        self.assertFalse(self.app.running)
        self.assertFalse(self.app.tray.visible)
        self.assertEqual(self.callback_errors, [])


if __name__ == "__main__":
    unittest.main()
