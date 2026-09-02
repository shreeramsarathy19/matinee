"""Endpoint-level tests with a sandboxed library and database.

The TestClient is used WITHOUT a `with` block on purpose: that skips the app's
lifespan (rescan thread, converter, trickplay sweep), which must never run
against the test sandbox — trickplay.sweep_orphans() would otherwise delete
real preview sheets for ids the sandbox database doesn't know.
"""
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from matinee import app as appmod
from matinee import db, library


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="matinee-api-"))
        lib = cls.tmp / "library"
        (lib / "Show/Season 01").mkdir(parents=True)
        (lib / "Movies").mkdir()
        for name in ("Show - S01E01.mp4", "Show - S01E02.mp4"):
            (lib / "Show/Season 01" / name).write_bytes(b"\0" * 4096)
        (lib / "Show/Season 01/Show - S01E01.en.srt").write_text(
            "1\n00:00:10,000 --> 00:00:12,000\nHello\n")
        (lib / "Movies/Heat (1995).mp4").write_bytes(b"\0" * 4096)

        cls._orig_db = db.DB_PATH
        db.DB_PATH = cls.tmp / "test.db"
        db.init("Alice")
        cls._orig_lib = appmod.cfg.library
        appmod.cfg.library = lib
        # the sandbox declares its own profiles — never depend on a developer's config.yaml
        cls._orig_profiles = list(appmod.cfg.profiles)
        appmod.cfg.profiles = ["Alice", "Bob", "Carol"]
        library.scan(appmod.cfg)
        cls.e01 = library.video_id("Show/Season 01/Show - S01E01.mp4")
        cls.e02 = library.video_id("Show/Season 01/Show - S01E02.mp4")
        cls.heat = library.video_id("Movies/Heat (1995).mp4")
        cls.client = TestClient(appmod.app)   # no `with`: lifespan must not run

    @classmethod
    def tearDownClass(cls):
        appmod.cfg.library = cls._orig_lib
        appmod.cfg.profiles = cls._orig_profiles
        db.DB_PATH = cls._orig_db
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---- home -----------------------------------------------------------
    def test_home_lists_the_sandbox(self):
        d = self.client.get("/api/home?p=Alice").json()
        self.assertEqual(d["count"], 3)
        self.assertFalse(d["library_offline"])
        names = [r["name"] for r in d["rows"]]
        self.assertIn("Show", str(names) + str([e.get("folder") for e in d["recent"]]))

    def test_continue_watching_one_card_per_series(self):
        db.save_progress(self.e01, 100, 1000, "Alice")
        db.save_progress(self.e02, 100, 1000, "Alice")
        db.save_progress(self.heat, 100, 1000, "Alice")
        cont = self.client.get("/api/home?p=Alice").json()["continue"]
        shows = [v for v in cont if v["folder"] == "Show"]
        self.assertEqual(len(shows), 1, "episodes of one series must collapse to one card")
        self.assertEqual(len(cont), 2)   # the series card + the movie
        for vid in (self.e01, self.e02, self.heat):
            db.clear_progress(vid, "Alice")

    # ---- video detail ---------------------------------------------------
    def test_video_detail(self):
        d = self.client.get(f"/api/video/{self.e01}?p=Alice").json()
        self.assertTrue(d["series"])
        self.assertTrue(d["subtitles"])
        self.assertEqual(d["next"]["id"], self.e02)
        self.assertEqual(d["sub_offset"], 0)

    def test_movie_has_no_next(self):
        d = self.client.get(f"/api/video/{self.heat}?p=Alice").json()
        self.assertFalse(d["series"])
        self.assertIsNone(d["next"])

    # ---- subtitles ------------------------------------------------------
    def test_suboffset_shifts_the_vtt(self):
        r = self.client.post(f"/api/video/{self.e01}/suboffset", json={"offset": 2})
        self.assertEqual(r.json()["offset"], 2.0)
        vtt = self.client.get(f"/subs/{self.e01}.vtt").text
        self.assertIn("00:00:12.000 --> 00:00:14.000", vtt)
        self.client.post(f"/api/video/{self.e01}/suboffset", json={"offset": 0})
        self.assertIn("00:00:10.000", self.client.get(f"/subs/{self.e01}.vtt").text)

    # ---- progress -------------------------------------------------------
    def test_progress_roundtrip_and_clear(self):
        r = self.client.post("/api/progress",
                             json={"id": self.e02, "position": 42, "duration": 100, "profile": "Bob"})
        self.assertEqual(r.status_code, 200)
        d = self.client.get(f"/api/video/{self.e02}?p=Bob").json()
        self.assertEqual(round(d["position"]), 42)
        self.assertEqual(self.client.get(f"/api/video/{self.e02}?p=Alice").json()["position"], 0,
                         "profiles must not share progress")
        self.client.post(f"/api/progress/{self.e02}/clear?p=Bob")
        self.assertEqual(self.client.get(f"/api/video/{self.e02}?p=Bob").json()["position"], 0)

    # ---- search / stream / misc ----------------------------------------
    def test_search(self):
        hits = self.client.get("/api/search?q=heat&p=Alice").json()["results"]
        self.assertTrue(any(h["type"] == "movie" and h["video"]["id"] == self.heat for h in hits))

    def test_stream_answers_range_requests(self):
        r = self.client.get(f"/stream/{self.e01}", headers={"Range": "bytes=0-99"})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(len(r.content), 100)

    def test_index_is_auto_versioned(self):
        html = self.client.get("/").text
        self.assertRegex(html, r"/static/app\.js\?v=[0-9a-f]{8}")
        self.assertRegex(html, r"/static/style\.css\?v=[0-9a-f]{8}")

    def test_change_library_rejects_missing_folder(self):
        r = self.client.post("/api/library", json={"path": "/Volumes/NoSuchDisk/lib"})
        self.assertEqual(r.status_code, 422)

    def test_movie_endpoint(self):
        from matinee import metadata
        orig = metadata.movie_info
        metadata.movie_info = lambda title, fetch=True: {"year": "1995", "description": "1995 film",
                                                         "extract": "A heist thriller."}
        try:
            d = self.client.get(f"/api/movie/{self.heat}?p=Alice").json()
        finally:
            metadata.movie_info = orig
        self.assertEqual(d["info"]["year"], "1995")
        self.assertIn("configured", d["subsearch"])
        self.assertFalse(d["subtitles"])

    def test_browse_lists_folders(self):
        d = self.client.get(f"/api/browse?path={self.tmp}/library").json()
        names = [x["name"] for x in d["dirs"]]
        self.assertIn("Show", names)
        self.assertIn("Movies", names)
        self.assertFalse(d["denied"])
        d2 = self.client.get(f"/api/browse?path={self.tmp}/library/Movies").json()
        self.assertEqual(d2["media_count"], 1)
        self.assertEqual(self.client.get("/api/browse?path=/no/such/dir").status_code, 404)

    def test_setup_first_run_roundtrip(self):
        from matinee import config as configmod
        orig_path = configmod.CONFIG_PATH
        orig = (appmod.cfg.app_name, list(appmod.cfg.profiles),
                appmod.cfg.convert_for_iphone, appmod.cfg.skip_seconds, appmod.cfg.library)
        configmod.CONFIG_PATH = self.tmp / "config.yaml"
        try:
            d = self.client.get("/api/setup").json()
            self.assertTrue(d["first_run"])
            r = self.client.post("/api/setup", json={
                "app_name": "HomeFlix", "profiles": ["A", "B", "A"],
                "skip_seconds": 15, "convert_for_iphone": False})
            self.assertEqual(r.status_code, 200)
            text = configmod.CONFIG_PATH.read_text()
            self.assertIn("app_name: HomeFlix", text)
            self.assertIn("profiles: [A, B]", text)
            self.assertIn("skip_seconds: 15", text)
            d2 = self.client.get("/api/setup").json()
            self.assertFalse(d2["first_run"])
            self.assertEqual(d2["app_name"], "HomeFlix")
            self.assertEqual(d2["profiles"], ["A", "B"])
        finally:
            configmod.CONFIG_PATH = orig_path
            (appmod.cfg.app_name, appmod.cfg.profiles,
             appmod.cfg.convert_for_iphone, appmod.cfg.skip_seconds, appmod.cfg.library) = orig
            db.init("Alice")

    def test_setup_rejects_bad_library(self):
        r = self.client.post("/api/setup", json={"app_name": "X", "library": "/no/such/place"})
        self.assertEqual(r.status_code, 422)

    def test_unknown_video_404(self):
        self.assertEqual(self.client.get("/api/video/doesnotexist").status_code, 404)


if __name__ == "__main__":
    unittest.main()
