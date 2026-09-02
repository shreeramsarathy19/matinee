"""Next-episode lookup, per-profile progress isolation, and the profile DB migration."""
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from matinee import catalog, db


def sv(title, folder="Show", subfolder="", vid=None, **kw):
    """Minimal serialized-video dict for catalog functions."""
    d = {"id": vid or title, "title": title, "folder": folder, "subfolder": subfolder,
         "position": 0, "finished": False, "played_at": None, "added_at": 0, "duration": 100,
         "percent": 0, "ios_ok": True}
    d.update(kw)
    return d


class ContinueWatchingRowTests(unittest.TestCase):
    """The exact flows the user cares about: last-touched episode wins, and
    finishing a later episode must never resurface an older half-watched one."""

    def eps(self):
        return [sv(f"Show - S01E{e:02d}") for e in range(1, 9)]

    def row(self, vids):
        return catalog.continue_row(catalog.build(vids))

    def test_switching_episodes_shows_the_latest_one(self):
        eps = self.eps()
        eps[1].update(played_at=100, position=300, percent=30)      # E02 half-watched at 10:00
        eps[6].update(played_at=200, position=200, percent=20)      # then E07 started at 10:03
        row = self.row(eps)
        self.assertEqual([v["title"] for v in row], ["Show - S01E07"])

    def test_finishing_later_episode_advances_never_goes_back(self):
        eps = self.eps()
        eps[1].update(played_at=100, position=300, percent=30)              # E02 half-watched
        eps[6].update(played_at=200, position=100, finished=True)           # E07 FINISHED after it
        row = self.row(eps)
        self.assertEqual([v["title"] for v in row], ["Show - S01E08"],
                         "after finishing E07 the card must be E08, not the old E02")

    def test_fully_watched_series_leaves_the_row(self):
        eps = self.eps()
        for i, e in enumerate(eps):
            e.update(played_at=100 + i, finished=True)
        self.assertEqual(self.row(eps), [])

    def test_movies_listed_individually_finished_ones_dropped(self):
        movies = [sv("Heat", folder="Movies", played_at=50, position=100),
                  sv("Ronin", folder="Movies", played_at=90, position=100),
                  sv("Old", folder="Movies", played_at=99, position=100, finished=True)]
        row = self.row(movies)
        self.assertEqual([v["title"] for v in row], ["Ronin", "Heat"])

    def test_ordered_by_most_recently_touched(self):
        eps = self.eps()
        eps[0].update(played_at=500, position=100, percent=10)
        movie = sv("Heat", folder="Movies", played_at=900, position=100)
        row = self.row(eps + [movie])
        self.assertEqual([v["title"] for v in row], ["Heat", "Show - S01E01"])


class NextUpTests(unittest.TestCase):
    def test_search_returns_no_episodes(self):
        eps = [sv("Show - S01E01 - Warriors"), sv("Show - S01E02")]
        hits = catalog.search(eps, "warriors")
        self.assertEqual(hits, [])
        hits = catalog.search(eps, "show")
        self.assertEqual([h["type"] for h in hits], ["series"])


class SubThresholdSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="matinee-thr-"))
        self._orig = db.DB_PATH
        db.DB_PATH = self.tmp / "t.db"
        db.init("Alice")
        with db.connect() as conn:
            conn.execute("INSERT INTO videos (id, rel_path, title, folder, ext, size, mtime, added_at) "
                         "VALUES ('vid', 'Show/Show - S01E01.mp4', 'Show - S01E01', 'Show', 'mp4', 1, 0, 0)")

    def tearDown(self):
        db.DB_PATH = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tiny_save_never_creates_a_row(self):
        r = db.save_progress("vid", 0.0, 1000.0, "Alice")
        self.assertTrue(r["ignored"])
        self.assertIsNone(db.get_video("vid", "Alice")["played_at"])

    def test_tiny_save_never_overwrites_real_progress(self):
        db.save_progress("vid", 900.0, 1000.0, "Alice")       # finished
        db.save_progress("vid", 0.0, 1000.0, "Alice")         # the stray-tab beacon
        v = db.get_video("vid", "Alice")
        self.assertEqual(v["position"], 900.0)
        self.assertTrue(v["finished"])

    def test_real_saves_still_work(self):
        db.save_progress("vid", 42.0, 1000.0, "Alice")
        self.assertEqual(db.get_video("vid", "Alice")["position"], 42.0)


class NextAfterTests(unittest.TestCase):
    def eps(self):
        return [sv(f"Show - S0{s}E{e:02d}") for s in (1, 2) for e in (1, 2, 3)]

    def test_middle_and_season_boundary(self):
        eps = self.eps()
        self.assertEqual(catalog.next_after(eps, "Show - S01E01")["title"], "Show - S01E02")
        self.assertEqual(catalog.next_after(eps, "Show - S01E03")["title"], "Show - S02E01")

    def test_last_episode_has_no_next(self):
        self.assertIsNone(catalog.next_after(self.eps(), "Show - S02E03"))

    def test_movie_folder_has_no_next(self):
        movies = [sv("Heat", folder="Movies"), sv("Ronin", folder="Movies")]
        self.assertIsNone(catalog.next_after(movies, "Heat"))

    def test_unknown_id(self):
        self.assertIsNone(catalog.next_after(self.eps(), "nope"))


class ProfileDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="matinee-prof-"))
        self._orig = db.DB_PATH
        db.DB_PATH = self.tmp / "t.db"

    def tearDown(self):
        db.DB_PATH = self._orig
        db.DEFAULT_PROFILE = "Alice"
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seed_video(self):
        db.upsert_videos([{"id": "vid1", "rel_path": "Show/e1.mp4", "title": "e1",
                           "folder": "Show", "ext": "mp4", "size": 1, "mtime": 0}])

    def test_profiles_are_isolated(self):
        db.init("Alice")
        self.seed_video()
        db.save_progress("vid1", 50, 100, "Bob")
        db.save_progress("vid1", 10, 100, "Carol")

        self.assertEqual(db.get_video("vid1", "Bob")["position"], 50)
        self.assertEqual(db.get_video("vid1", "Carol")["position"], 10)
        self.assertIsNone(db.get_video("vid1", "Alice")["position"])

        self.assertEqual(len(db.continue_watching(profile="Bob")), 1)
        self.assertEqual(db.continue_watching(profile="Alice"), [])

        db.clear_progress("vid1", "Bob")
        self.assertIsNone(db.get_video("vid1", "Bob")["position"])
        self.assertEqual(db.get_video("vid1", "Carol")["position"], 10, "other profile untouched")

    def test_default_profile_used_when_omitted(self):
        db.init("Alice")
        self.seed_video()
        db.save_progress("vid1", 42, 100)                       # no profile arg
        self.assertEqual(db.get_video("vid1")["position"], 42)  # same default
        self.assertIsNone(db.get_video("vid1", "Bob")["position"])

    def test_migration_moves_old_rows_to_default_profile(self):
        # build a pre-profile database by hand
        conn = sqlite3.connect(db.DB_PATH)
        conn.executescript("""
            CREATE TABLE progress (
                video_id   TEXT PRIMARY KEY,
                position   REAL NOT NULL,
                duration   REAL,
                updated_at REAL NOT NULL,
                finished   INTEGER NOT NULL DEFAULT 0
            );""")
        conn.execute("INSERT INTO progress VALUES ('vid1', 77, 100, ?, 0)", (time.time(),))
        conn.commit()
        conn.close()

        db.init("Alice")   # runs the migration
        self.seed_video()
        self.assertEqual(db.get_video("vid1", "Alice")["position"], 77)
        self.assertIsNone(db.get_video("vid1", "Bob")["position"])
        db.init("Alice")   # idempotent
        self.assertEqual(db.get_video("vid1", "Alice")["position"], 77)


if __name__ == "__main__":
    unittest.main()
