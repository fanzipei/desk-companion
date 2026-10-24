# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest

from pc.core import Repository
from pc.display import FONT_SIZES, display_packet


class DisplaySettingsTests(unittest.TestCase):
    def test_defaults_are_compatible_with_existing_state(self):
        self.assertEqual(display_packet({}), {"op":"display_config","theme":"light","show_hints":True,
                                              "font_size":16,"line_spacing":7})

    def test_all_font_sizes_themes_and_spacing_boundaries(self):
        for theme in ("light", "dark"):
            for size in FONT_SIZES:
                for spacing in (0, 7, 16):
                    result=display_packet({"screen_theme":theme,"screen_font_size":size,
                                           "screen_line_spacing":spacing,"screen_show_hints":False})
                    self.assertEqual((result["theme"],result["font_size"],result["line_spacing"]),(theme,size,spacing))
                    self.assertIs(result["show_hints"],False)

    def test_invalid_settings_never_replace_saved_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            repo=Repository(directory)
            repo.configure(screen_theme="dark",screen_font_size=24,screen_line_spacing=4,screen_show_hints=False)
            expected=repo.snapshot()["config"]
            for invalid in ({"screen_theme":"sepia"},{"screen_show_hints":"false"},{"screen_font_size":20},
                            {"screen_font_size":True},{"screen_font_size":16.0},{"screen_line_spacing":-1},
                            {"screen_line_spacing":17},{"screen_line_spacing":True}):
                with self.assertRaises(ValueError):
                    repo.configure(**invalid)
                self.assertEqual(repo.snapshot()["config"],expected)
            self.assertEqual(Repository(directory).snapshot()["config"],expected)


if __name__ == "__main__":
    unittest.main()
