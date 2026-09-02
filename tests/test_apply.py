"""Moves on a real (temporary) library: sidecars, junk sweep, progress carry-over, undo."""
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from matinee import db, library, organize
from matinee.config import Config


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="matinee-test-"))
        self.lib = self.tmp / "library"
        self.lib.mkdir()
        self.cfg = Config(library=self.lib)
        self._orig_db = db.DB_PATH
        db.DB_PATH = self.tmp / "test.db"
        db.init()

    def tearDown(self):
        db.DB_PATH = self._orig_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    def touch(self, rel, size=1000, age=120):
        p = self.lib / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        old = time.time() - age
        os.utime(p, (old, old))
        return p

    def test_auto_organize_release_folder(self):
        self.touch("Breaking.Bad.S01.720p-GRP/Breaking.Bad.S01E01.720p.mkv")
        self.touch("Breaking.Bad.S01.720p-GRP/Breaking.Bad.S01E01.720p.srt")
        self.touch("Breaking.Bad.S01.720p-GRP/info.nfo")
        self.touch("Movies/Heat (1995).mkv")
        self.touch("Some.Movie.2020.1080p.mkv")
        self.touch("Fresh.Show.S01E01.mkv", age=5)          # still copying: must wait

        library.scan(self.cfg)
        old_id = library.video_id("Breaking.Bad.S01.720p-GRP/Breaking.Bad.S01E01.720p.mkv")
        db.save_progress(old_id, 123.0, 1000.0)

        results = organize.auto_organize(self.cfg)
        moved = {r["src"]: r["dst"] for r in results if r["ok"]}
        self.assertEqual(moved, {
            "Breaking.Bad.S01.720p-GRP/Breaking.Bad.S01E01.720p.mkv": "Breaking Bad/Season 01/Breaking Bad - S01E01.mkv",
            "Some.Movie.2020.1080p.mkv": "Movies/Some Movie (2020).mkv",
        })
        self.assertTrue((self.lib / "Breaking Bad/Season 01/Breaking Bad - S01E01.mkv").is_file())
        self.assertTrue((self.lib / "Breaking Bad/Season 01/Breaking Bad - S01E01.srt").is_file(), "sidecar moved")
        self.assertFalse((self.lib / "Breaking.Bad.S01.720p-GRP").exists(), "emptied release folder removed")
        self.assertTrue((self.lib / "Fresh.Show.S01E01.mkv").is_file(), "fresh file left alone")
        self.assertTrue((self.lib / "Movies/Heat (1995).mkv").is_file())

        new_id = library.video_id("Breaking Bad/Season 01/Breaking Bad - S01E01.mkv")
        self.assertEqual(db.get_video(new_id)["position"], 123.0, "watch progress survives the move")
        self.assertEqual(len(db.recent_moves()), 2)

        # a second pass does nothing (already tidy, fresh file still too new)
        self.assertEqual(organize.auto_organize(self.cfg), [])

        # undo puts it back (folder recreated) and restores progress on the old id
        mv = [m for m in db.recent_moves() if m["dst"].startswith("Breaking Bad/")][0]
        res = organize.undo(self.cfg, mv["id"])
        self.assertTrue(res["ok"], res)
        self.assertTrue((self.lib / "Breaking.Bad.S01.720p-GRP/Breaking.Bad.S01E01.720p.mkv").is_file())
        self.assertEqual(db.get_video(old_id)["position"], 123.0)
        self.assertTrue(db.get_move(mv["id"])["undone"])

        # ...and the auto-organizer respects the undo instead of redoing it
        self.assertEqual(organize.auto_organize(self.cfg), [])
        p = [p for p in organize.plan(self.cfg) if p.src.startswith("Breaking.Bad.S01.720p-GRP/")][0]
        self.assertFalse(p.confident)
        self.assertIn("undid", p.note)

    def test_apply_rejects_bad_destinations(self):
        self.touch("a.mkv")
        bad = ["../a.mkv", "/abs/a.mkv", "b.mp4", "", "x/"]
        for dst in bad:
            r = organize.move_one(self.cfg, "a.mkv", dst)
            self.assertFalse(r["ok"], dst)
        self.touch("taken.mkv")
        r = organize.move_one(self.cfg, "a.mkv", "taken.mkv")
        self.assertFalse(r["ok"])
        self.assertTrue((self.lib / "a.mkv").is_file())

    def test_plan_flags_conflicts(self):
        self.touch("Breaking.Bad.S01E01.720p.mkv")
        self.touch("Breaking Bad/Season 01/Breaking Bad - S01E01.mkv")
        plans = {p.src: p for p in organize.plan(self.cfg)}
        p = plans["Breaking.Bad.S01E01.720p.mkv"]
        self.assertTrue(p.changed)
        self.assertFalse(p.confident)
        self.assertIn("already exists", p.note)
        self.assertEqual(organize.auto_organize(self.cfg), [])


if __name__ == "__main__":
    unittest.main()
