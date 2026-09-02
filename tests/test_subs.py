"""Subtitle conversion hygiene."""
import tempfile
import unittest
from pathlib import Path

from matinee import subs


class SubsTests(unittest.TestCase):
    def test_ass_tags_and_font_wrappers_stripped(self):
        srt = ("1\n00:00:01,000 --> 00:00:02,000\n{\\an5}{\\k12}Hello\n\n"
               "2\n00:00:03,000 --> 00:00:04,000\n<font face=\"X\">World</font>\n")
        p = Path(tempfile.mkstemp(suffix=".srt")[1])
        p.write_text(srt)
        v = subs.to_vtt(p)
        p.unlink()
        self.assertIn("Hello", v)
        self.assertIn("World", v)
        self.assertNotIn("an5", v)
        self.assertNotIn("<font", v)


if __name__ == "__main__":
    unittest.main()


class ShiftTests(unittest.TestCase):
    V = "WEBVTT\n\n00:00:12.387 --> 00:00:15.140\nHi\n"

    def test_later(self):
        self.assertIn("00:00:14.887 --> 00:00:17.640", subs.shift(self.V, 2.5))

    def test_earlier_and_clamped_at_zero(self):
        self.assertIn("00:00:09.887", subs.shift(self.V, -2.5))
        self.assertIn("00:00:00.000 --> 00:00:00.000", subs.shift(self.V, -20))

    def test_zero_is_identity(self):
        self.assertEqual(subs.shift(self.V, 0), self.V)

    def test_only_timing_lines_touched(self):
        v = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nAt 00:00:01.000 sharp\n"
        out = subs.shift(v, 1)
        self.assertIn("00:00:02.000 --> 00:00:03.000", out)
        self.assertIn("At 00:00:01.000 sharp", out)
