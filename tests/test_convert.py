"""Conversion: codec decisions (pure) and a real ffmpeg round-trip on tiny generated files."""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from matinee import convert, db, library
from matinee.config import Config


def fake_probe(container_ext, vcodec="h264", acodec="aac", pix_fmt="yuv420p", subs=None, alang=None):
    streams = [{"codec_type": "video", "codec_name": vcodec, "pix_fmt": pix_fmt, "height": 1080, "disposition": {}}]
    if acodec:
        a = {"codec_type": "audio", "codec_name": acodec, "tags": {}}
        if alang:
            a["tags"]["language"] = alang
        streams.append(a)
    for s in subs or []:
        streams.append({"codec_type": "subtitle", "codec_name": s, "tags": {}})
    return {"format": {"duration": "100.0"}, "streams": streams}


class AnalyseTests(unittest.TestCase):
    def test_mkv_h264_aac_is_remux_only(self):
        a = convert.analyse(fake_probe("mkv"), "mkv")
        self.assertFalse(a["ios_ok"])          # container is wrong...
        self.assertTrue(a["copy_video"] and a["copy_audio"])   # ...but streams can be copied

    def test_mp4_h264_aac_is_fine(self):
        self.assertTrue(convert.analyse(fake_probe("mp4"), "mp4")["ios_ok"])

    def test_ac3_needs_audio_transcode(self):
        a = convert.analyse(fake_probe("mkv", acodec="ac3"), "mkv")
        self.assertTrue(a["copy_video"])
        self.assertFalse(a["copy_audio"])

    def test_10bit_h264_needs_video_transcode(self):
        a = convert.analyse(fake_probe("mkv", pix_fmt="yuv420p10le"), "mkv")
        self.assertFalse(a["copy_video"])

    def test_hevc_10bit_is_ok_in_mp4(self):
        self.assertTrue(convert.analyse(fake_probe("mp4", vcodec="hevc", pix_fmt="yuv420p10le"), "mp4")["ios_ok"])

    def test_vp9_webm_needs_full_transcode(self):
        a = convert.analyse(fake_probe("webm", vcodec="vp9", acodec="opus"), "webm")
        self.assertFalse(a["copy_video"] or a["copy_audio"])

    def test_signs_and_songs_track_avoided(self):
        streams = [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "height": 720, "disposition": {}},
                   {"codec_type": "audio", "codec_name": "aac", "tags": {}},
                   {"codec_type": "subtitle", "codec_name": "ass", "tags": {"title": "Signs & Songs", "language": "eng"}, "disposition": {}},
                   {"codec_type": "subtitle", "codec_name": "ass", "tags": {"title": "Full Subtitles", "language": "eng"}, "disposition": {}}]
        a = convert.analyse({"format": {"duration": "100"}, "streams": streams}, "mkv")
        self.assertEqual(a["sub_index"], 3)

    def test_text_subtitle_picked(self):
        a = convert.analyse(fake_probe("mkv", subs=["hdmv_pgs_subtitle", "subrip"]), "mkv")
        self.assertEqual(a["sub_index"], 3)    # the subrip one, not the bitmap one


@unittest.skipUnless(convert.available(), "ffmpeg not installed")
class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="matinee-conv-"))
        self.lib = self.tmp / "library"
        self.lib.mkdir()
        self.cfg = Config(library=self.lib)
        self._orig_db = db.DB_PATH
        db.DB_PATH = self.tmp / "test.db"
        db.init()
        convert._instance = None

    def tearDown(self):
        db.DB_PATH = self._orig_db
        convert._instance = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, rel, acodec="aac", seconds=2):
        out = self.lib / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([convert.ffmpeg_path(), "-y", "-loglevel", "error",
                        "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=10:duration={seconds}",
                        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", acodec, "-shortest", str(out)], check=True)
        old = time.time() - 120
        os.utime(out, (old, old))
        return out

    def test_mkv_becomes_mp4_with_progress_kept(self):
        self.make("Show/Season 01/Show - S01E01.mkv")
        self.make("Show/Season 01/Show - S01E02.mkv", acodec="ac3")
        library.scan(self.cfg)
        old_id = library.video_id("Show/Season 01/Show - S01E01.mkv")
        db.save_progress(old_id, 6.0, 20.0)   # above the resume threshold (tiny saves are ignored)

        conv = convert.get_converter(self.cfg)
        # first pass probes; following passes convert one file each
        for _ in range(4):
            conv._pass()

        self.assertTrue((self.lib / "Show/Season 01/Show - S01E01.mp4").is_file())
        self.assertTrue((self.lib / "Show/Season 01/Show - S01E02.mp4").is_file())
        self.assertFalse((self.lib / "Show/Season 01/Show - S01E01.mkv").exists())
        self.assertTrue((self.lib / ".originals/Show/Season 01/Show - S01E01.mkv").is_file(), "original parked")

        new_id = library.video_id("Show/Season 01/Show - S01E01.mp4")
        v = db.get_video(new_id)
        self.assertEqual(v["position"], 6.0, "progress carried over")
        self.assertEqual(v["ios_ok"], 1)

        out = convert.analyse(convert.probe(self.lib / "Show/Season 01/Show - S01E02.mp4"), "mp4")
        self.assertTrue(out["ios_ok"])
        self.assertEqual(out["acodec"], "aac", "ac3 got transcoded to aac")

        kinds = [m["kind"] for m in db.recent_moves()]
        self.assertEqual(kinds.count("converted"), 2)
        self.assertEqual([c["status"] for c in db.conversions()], ["done", "done"])

        # nothing left to do
        library.scan(self.cfg)
        self.assertFalse(conv._pass())

    def test_delete_originals_option(self):
        self.cfg.keep_originals = False
        self.make("Clip.mkv")
        library.scan(self.cfg)
        conv = convert.get_converter(self.cfg)
        for _ in range(3):
            conv._pass()
        self.assertTrue((self.lib / "Clip.mp4").is_file())
        self.assertFalse((self.lib / ".originals").exists())


if __name__ == "__main__":
    unittest.main()
