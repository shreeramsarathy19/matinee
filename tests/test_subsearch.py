"""OpenSubtitles integration: hash algorithm, search params, quota guard. No network."""
import json
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from matinee import subsearch
from matinee.subsearch import OpenSubtitles, SubsearchError


class OshashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="matinee-osh-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_zeros_hash_is_the_file_size(self):
        p = self.tmp / "z.mp4"
        p.write_bytes(b"\0" * 131072)
        self.assertEqual(subsearch.oshash(p), "0000000000020000")   # 0x20000 = 131072

    def test_known_word_adds_in(self):
        data = bytearray(b"\0" * 131072)
        data[0:8] = struct.pack("<Q", 5)     # first word contributes 5, in both 64K windows? no — only the head
        p = self.tmp / "w.mp4"
        p.write_bytes(bytes(data))
        self.assertEqual(subsearch.oshash(p), f"{131072 + 5:016x}")

    def test_small_file_has_no_hash(self):
        p = self.tmp / "s.mp4"
        p.write_bytes(b"\0" * 1000)
        self.assertIsNone(subsearch.oshash(p))


class NameParamsTests(unittest.TestCase):
    def test_episode(self):
        p = OpenSubtitles.name_params({"title": "Solo Leveling - S01E07 - Title", "folder": "Solo Leveling"})
        self.assertEqual(p, {"query": "Solo Leveling", "season_number": 1, "episode_number": 7})

    def test_movie_with_year(self):
        p = OpenSubtitles.name_params({"title": "Heat (1995)", "folder": "Movies"})
        self.assertEqual(p, {"query": "Heat", "year": 1995})

    def test_movie_without_year(self):
        self.assertEqual(OpenSubtitles.name_params({"title": "1917", "folder": "Movies"}),
                         {"query": "1917"})


def result(fid, lang="en", hash_match=False, downloads=0, hi=False):
    return {"attributes": {"language": lang, "moviehash_match": hash_match,
                           "download_count": downloads, "hearing_impaired": hi,
                           "release": "grp", "files": [{"file_id": fid}]}}


class QueryMatchTests(unittest.TestCase):
    def item(self, release="", title="", parent=""):
        return {"attributes": {"release": release,
                               "feature_details": {"title": title, "parent_title": parent}}}

    def test_wrong_show_rejected(self):
        wrong = self.item(release="secret.level.s01e14.hdr.2160p.web.h265-successfulcrab",
                          title="Secret Level", parent="Secret Level")
        self.assertFalse(OpenSubtitles.query_matches(wrong, "Solo Leveling"))

    def test_dotted_release_accepted(self):
        right = self.item(release="Solo.Leveling.s02e02.1080p.NF.WEB-DL")
        self.assertTrue(OpenSubtitles.query_matches(right, "Solo Leveling"))

    def test_parent_title_accepted(self):
        right = self.item(release="ep14.720p", parent="Solo Leveling")
        self.assertTrue(OpenSubtitles.query_matches(right, "Solo Leveling"))


class PickBestTests(unittest.TestCase):
    def test_hash_match_beats_popularity(self):
        best = OpenSubtitles.pick_best([result(1, downloads=9000), result(2, hash_match=True)], ["en"])
        self.assertEqual(best["attributes"]["files"][0]["file_id"], 2)

    def test_language_priority(self):
        best = OpenSubtitles.pick_best([result(1, lang="fr", downloads=9000), result(2, lang="en")], ["en", "fr"])
        self.assertEqual(best["attributes"]["files"][0]["file_id"], 2)

    def test_empty(self):
        self.assertIsNone(OpenSubtitles.pick_best([], ["en"]))


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="matinee-osf-"))
        self._orig_state = subsearch.STATE_PATH
        subsearch.STATE_PATH = self.tmp / "state.json"
        self.video_path = self.tmp / "Show - S01E01.mkv"
        self.video_path.write_bytes(b"\0" * 131072)
        self.calls = []

    def tearDown(self):
        subsearch.STATE_PATH = self._orig_state
        shutil.rmtree(self.tmp, ignore_errors=True)

    def client(self, limit=20, search_result=None):
        def http(method, url, headers, body=None, timeout=20):
            self.calls.append((method, url, body))
            if "/login" in url:
                return {"token": "T"}
            if "/subtitles?" in url:
                return {"data": search_result if search_result is not None else [result(7, hash_match=True)]}
            if "/download" in url:
                return {"link": "https://dl.example/x.srt", "requests": 1, "remaining": 19}
            raise AssertionError(url)
        return OpenSubtitles({"username": "u", "password": "p", "api_key": "k", "daily_limit": limit},
                             http=http, fetch=lambda url, timeout=30: b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")

    def test_downloads_and_saves_sidecar(self):
        out = self.client().find_and_download({"title": "Show - S01E01", "folder": "Show"}, self.video_path)
        self.assertTrue(out["ok"])
        self.assertEqual(out["matched_by"], "hash")
        self.assertEqual(out["used"], 1)
        sidecar = self.tmp / "Show - S01E01.en.srt"
        self.assertTrue(sidecar.is_file())
        self.assertIn("Hi", sidecar.read_text())

    def test_name_fallback_when_hash_finds_nothing(self):
        c = self.client()
        def http(method, url, headers, body=None, timeout=20):
            self.calls.append((method, url, body))
            if "/login" in url: return {"token": "T"}
            if "moviehash" in url: return {"data": []}
            if "/subtitles?" in url: return {"data": [result(7)]}
            if "/download" in url: return {"link": "https://dl.example/x.srt", "remaining": 19}
            raise AssertionError(url)
        c.http = http
        out = c.find_and_download({"title": "Show - S01E01", "folder": "Show"}, self.video_path)
        self.assertEqual(out["matched_by"], "name")
        self.assertTrue(any("season_number=1" in u for _, u, _ in self.calls))

    def test_quota_blocks_download(self):
        subsearch.STATE_PATH.write_text(json.dumps({"date": subsearch._today(), "used": 2}))
        with self.assertRaises(SubsearchError) as cm:
            self.client(limit=2).find_and_download({"title": "X", "folder": "Y"}, self.video_path)
        self.assertIn("limit reached", str(cm.exception))
        self.assertEqual(self.calls, [], "no API calls once the quota is spent")

    def test_server_remaining_caps_quota(self):
        subsearch.STATE_PATH.write_text(json.dumps({"date": subsearch._today(), "used": 0, "server_remaining": 0}))
        with self.assertRaises(SubsearchError):
            self.client(limit=20).find_and_download({"title": "X", "folder": "Y"}, self.video_path)

    def test_not_configured(self):
        c = OpenSubtitles({})
        with self.assertRaises(SubsearchError) as cm:
            c.find_and_download({"title": "X"}, self.video_path)
        self.assertIn("config.yaml", str(cm.exception))

    def test_replace_excludes_previous_choice(self):
        c = self.client(search_result=[result(7, hash_match=True, downloads=100), result(8, downloads=5)])
        out = c.find_and_download({"id": "vid1", "title": "Show - S01E01", "folder": "Show"}, self.video_path)
        self.assertEqual(subsearch.chosen_ids("vid1"), {7})
        out2 = c.find_and_download({"id": "vid1", "title": "Show - S01E01", "folder": "Show"},
                                   self.video_path, exclude=subsearch.chosen_ids("vid1"))
        self.assertEqual(subsearch.chosen_ids("vid1"), {7, 8})
        with self.assertRaises(SubsearchError) as cm:
            c.find_and_download({"id": "vid1", "title": "Show - S01E01", "folder": "Show"},
                                self.video_path, exclude=subsearch.chosen_ids("vid1"))
        self.assertIn("No other subtitles", str(cm.exception))

    def test_absolute_number_fallback(self):
        c = self.client()
        def http(method, url, headers, body=None, timeout=20):
            self.calls.append((method, url, body))
            if "/login" in url: return {"token": "T"}
            if "moviehash" in url or "season_number" in url: return {"data": []}
            if "episode_number=14" in url: return {"data": [result(9)]}
            if "/download" in url: return {"link": "https://dl.example/x.srt", "remaining": 10}
            raise AssertionError(url)
        c.http = http
        out = c.find_and_download({"title": "Show - S02E02", "folder": "Show", "abs_episode": 14}, self.video_path)
        self.assertEqual(out["matched_by"], "absolute number")

    def test_nothing_found(self):
        with self.assertRaises(SubsearchError) as cm:
            self.client(search_result=[]).find_and_download({"title": "X", "folder": "Y"}, self.video_path)
        self.assertIn("No subtitles", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
