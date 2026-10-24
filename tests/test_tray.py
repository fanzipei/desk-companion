from pathlib import Path
import queue
import json
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from pc.app import App
from pc.core import Repository
from pc.placement import popup_position
from pc.windows import capture_selection
from pc.selection import native_capture,Clipboard


class ClipboardTests(unittest.TestCase):
    def test_restore_failure_does_not_discard_captured_text(self):
        with patch("pc.windows.user32") as user, patch("pc.selection.Clipboard") as clipboard, \
                patch("pc.windows.send_chord"):
            user.GetAsyncKeyState.return_value=0
            user.GetForegroundWindow.return_value=123
            instance=clipboard.return_value
            instance.snapshot.return_value={13:b"old"}
            instance.user.GetClipboardSequenceNumber.side_effect=[11,12]
            instance.text.return_value=("selected",12)
            instance.restore.side_effect=OSError("Clipboard unavailable")
            with self.assertLogs("pc.selection",level="WARNING"):
                self.assertEqual(native_capture(),"selected")
            instance.restore.assert_called_once_with({13:b"old"},12)

    def test_native_crash_or_timeout_becomes_recoverable_error(self):
        with patch("pc.selection.subprocess.run") as run:
            run.return_value.returncode=0xC000041D
            with self.assertLogs("pc.selection",level="WARNING"), self.assertRaises(ValueError):
                capture_selection()
            run.side_effect=subprocess.TimeoutExpired("helper",8)
            with self.assertRaisesRegex(ValueError,"超时"):
                capture_selection()
            run.side_effect=None
            run.return_value.returncode=0
            run.return_value.stdout=json.dumps({"ok":True,"text":"第一行\n  - 第二项"})
            self.assertEqual(capture_selection(),"第一行\n  - 第二项")

    def test_newer_clipboard_content_is_never_overwritten(self):
        clipboard=object.__new__(Clipboard)
        clipboard.user=Mock(); clipboard.kernel=Mock()
        clipboard.user.GetClipboardSequenceNumber.return_value=99
        clipboard.user.OpenClipboard.return_value=True
        clipboard.kernel.GlobalLock.return_value=1
        with patch("pc.selection.ctypes.memmove"):
            clipboard.restore({13:b"old\x00"},12)
        clipboard.user.EmptyClipboard.assert_not_called()
        clipboard.user.SetClipboardData.assert_not_called()
        clipboard.kernel.GlobalFree.assert_called_once()

    def test_focus_change_does_not_send_copy_to_the_wrong_window(self):
        with patch("pc.windows.user32") as user, patch("pc.selection.Clipboard"), \
                patch("pc.windows.send_chord") as send:
            user.GetAsyncKeyState.return_value=0
            user.GetForegroundWindow.side_effect=[123,456]
            with self.assertRaisesRegex(ValueError,"窗口已切换"):
                native_capture()
            send.assert_not_called()


class TrayInteractionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = App.__new__(App)
        self.app.repository = Repository(directory.name)
        self.app.events = queue.Queue()
        self.app.bridge = Mock()
        self.app.tray = Mock()
        self.app.root = Mock()
        self.app.connected = True
        self.app.closed = False
        self.app.error = ""
        self.app.pending_settings = None
        self.app.last_command = 10**30
        self.app.show_input = Mock()
        self.app.show_settings = Mock()
        self.app.input_window = Mock()
        self.app.input_window.winfo_viewable.return_value = False

    def assert_no_interface(self):
        self.app.show_input.assert_not_called()
        self.app.show_settings.assert_not_called()
        self.app.tray.notify.assert_not_called()

    def test_selected_text_is_queued_without_a_window_or_toast(self):
        self.app.events.put(("capture", "划选内容静默发送"))
        self.app.poll()
        self.assertEqual(self.app.repository.next_operation()["text"], "划选内容静默发送")
        self.app.bridge.wake.assert_called_once()
        self.assert_no_interface()

    def test_selection_errors_and_oversize_do_not_open_any_window(self):
        self.app.events.put(("capture_error", "没有选中文字"))
        self.app.events.put(("capture", "字"*1025))
        self.app.poll()
        self.assertIsNone(self.app.repository.next_operation())
        self.assert_no_interface()

    def test_delivery_and_news_updates_remain_silent(self):
        self.app.events.put(("delivered", {"op": "add"}))
        self.app.events.put(("news_updated", "HN 已刷新"))
        self.app.poll()
        self.assert_no_interface()

    def test_only_compose_event_opens_the_input(self):
        self.app.events.put(("compose", None))
        self.app.poll()
        self.app.show_input.assert_called_once()
        self.app.show_settings.assert_not_called()

    def test_enter_queues_and_closes_the_input(self):
        self.app.text = Mock()
        self.app.text.get.return_value = "回车发送"
        with patch("pc.app.ime_composing", return_value=False):
            self.assertEqual(self.app.send_input(), "break")
        self.assertEqual(self.app.repository.next_operation()["text"], "回车发送")
        self.app.text.delete.assert_called_once_with("1.0", "end")
        self.app.input_window.withdraw.assert_called_once()

    def test_enter_during_chinese_ime_does_not_send(self):
        self.app.text = Mock()
        with patch("pc.app.ime_composing", return_value=True):
            self.assertIsNone(self.app.send_input())
        self.assertIsNone(self.app.repository.next_operation())
        self.app.input_window.withdraw.assert_not_called()

    def test_custom_limit_is_persistent_and_limits_the_offline_queue(self):
        self.app.repository.configure(note_limit=2)
        self.app.repository.enqueue_note("one")
        self.app.repository.enqueue_note("two")
        with self.assertRaises(ValueError):
            self.app.repository.enqueue_note("three")
        with self.assertRaises(ValueError):
            self.app.repository.configure(note_limit=1)
        self.assertEqual(self.app.repository.snapshot()["config"]["note_limit"], 2)

    def test_lowering_capacity_does_not_remove_confirmed_notes(self):
        notes = [{"id": str(i), "text": "keep", "pinned": False} for i in range(3)]
        self.app.repository.apply_state({"notes": notes})
        with self.assertRaises(ValueError):
            self.app.repository.configure(note_limit=2)
        self.assertEqual(self.app.repository.snapshot()["notes"], notes)


class PlacementTests(unittest.TestCase):
    def test_bottom_taskbar_and_overflow_anchor(self):
        self.assertEqual(popup_position((0, 0, 1920, 1040), (1780, 1040, 1810, 1080), 360, 112), (1450, 918))
        x, y = popup_position((0, 0, 1920, 1040), (1800, 800, 1830, 830), 360, 112)
        self.assertEqual((x, y), (1470, 678))

    def test_fallback_and_negative_monitor_coordinates(self):
        self.assertEqual(popup_position((0, 0, 1920, 1040), None, 360, 112), (1550, 918))
        x, y = popup_position((-1920, 0, 0, 1040), (-120, 1040, -90, 1080), 360, 112)
        self.assertTrue(-1920 <= x <= -360)
        self.assertTrue(0 <= y <= 928)

    def test_top_and_side_taskbars_stay_inside_work_area(self):
        for work, icon in [((0, 48, 1920, 1080), (1750, 0, 1780, 48)),
                           ((48, 0, 1920, 1080), (0, 900, 48, 930)),
                           ((0, 0, 1872, 1080), (1872, 900, 1920, 930))]:
            x, y = popup_position(work, icon, 360, 112)
            self.assertGreaterEqual(x, work[0])
            self.assertGreaterEqual(y, work[1])
            self.assertLessEqual(x+360, work[2])
            self.assertLessEqual(y+112, work[3])


if __name__ == "__main__":
    unittest.main()
