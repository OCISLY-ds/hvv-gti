import tempfile
import unittest
from pathlib import Path

from hvv_gti.icons import IconStore, badge_color

BADGE = '<svg><g fill="rgb(255,255,255)"/><rect fill="rgb(0,137,187)"/><path fill="black"/></svg>'


class ColorTests(unittest.TestCase):
    def test_badge_color_skips_white_and_black(self):
        self.assertEqual(badge_color(BADGE), "#0089bb")
        self.assertIsNone(badge_color('<svg><g fill="rgb(255,255,255)"/><path fill="black"/></svg>'))

    def test_store_collects_colors_of_all_local_svgs(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "S5.svg").write_text(BADGE)
            Path(tmp, "RE5.svg").write_text("<svg><g fill=\"rgb(255,255,255)\"/></svg>")
            self.assertEqual(IconStore(tmp).colors(), {"S5": "#0089bb"})


if __name__ == "__main__":
    unittest.main()
