"""The catalogue must survive an unplugged external disk (missing or empty library)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from matinee import db, library
from matinee.config import Config


class OfflineGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="matinee-guard-"))
        self.lib = self.tmp / "library"
        self.lib.mkdir()
        self._orig_db = db.DB_PATH
        db.DB_PATH = self.tmp / "test.db"
        db.init()
        (self.lib / "Show").mkdir()
        (self.lib / "Show/Show - S01E01.mp4").write_bytes(b"x" * 100)
        (self.lib / "Show/Show - S01E02.mp4").write_bytes(b"x" * 100)
        self.cfg = Config(library=self.lib)
        library.scan(self.cfg)

    def tearDown(self):
        db.DB_PATH = self._orig_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_library_keeps_catalogue(self):
        gone = Config(library=self.tmp / "nope")
        self.assertFalse(library.available(gone))
        self.assertEqual(library.scan(gone), 0)
        self.assertEqual(db.count_videos(), 2, "unplugged disk must not wipe the catalogue")

    def test_empty_library_keeps_catalogue(self):
        # a mount point that exists but lost its disk looks like an empty folder
        empty = self.tmp / "empty"
        empty.mkdir()
        library.scan(Config(library=empty))
        self.assertEqual(db.count_videos(), 2)

    def test_normal_deletion_still_pruned(self):
        (self.lib / "Show/Show - S01E02.mp4").unlink()
        library.scan(self.cfg)
        self.assertEqual(db.count_videos(), 1, "real deletions must still be noticed")


if __name__ == "__main__":
    unittest.main()
