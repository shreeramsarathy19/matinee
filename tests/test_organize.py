"""Run with:  .venv/bin/python -m unittest discover -s tests"""
import unittest

from matinee.organize import propose


class ProposeTests(unittest.TestCase):
    def check(self, src, dst, kind=None, confident=None):
        p = propose(src)
        self.assertEqual(p.dst, dst, f"\n  {src}\n  got:  {p.dst}\n  want: {dst}")
        if kind:
            self.assertEqual(p.kind, kind)
        if confident is not None:
            self.assertEqual(p.confident, confident, f"{src}: confident={p.confident}")

    # ---- episodes, loose files at the root
    def test_scene_episode(self):
        self.check("Breaking.Bad.S01E03.720p.HDTV.x264-KILLERS.mkv",
                   "Breaking Bad/Season 01/Breaking Bad - S01E03.mkv", "episode", True)

    def test_episode_with_title(self):
        self.check("Breaking.Bad.S01E01.Pilot.1080p.BluRay.x265.mkv",
                   "Breaking Bad/Season 01/Breaking Bad - S01E01 - Pilot.mkv")

    def test_spaces_and_dash(self):
        self.check("Breaking Bad - S02E05 - Breakage.mp4",
                   "Breaking Bad/Season 02/Breaking Bad - S02E05 - Breakage.mp4")

    def test_x_format(self):
        self.check("the.office.us.2x04.hdtv.mkv", "The Office Us/Season 02/The Office Us - S02E04.mkv")

    def test_season_episode_words(self):
        self.check("Friends Season 3 Episode 7.mp4", "Friends/Season 03/Friends - S03E07.mp4")

    def test_double_episode(self):
        self.check("Show.Name.S01E01E02.720p.mkv", "Show Name/Season 01/Show Name - S01E01-E02.mkv")
        self.check("Show.Name.S01E01-E02.720p.mkv", "Show Name/Season 01/Show Name - S01E01-E02.mkv")

    def test_show_with_year(self):
        self.check("Doctor.Who.2005.S01E01.Rose.mkv", "Doctor Who (2005)/Season 01/Doctor Who (2005) - S01E01 - Rose.mkv")

    def test_group_tag_not_title(self):
        self.check("Lost.S01E01-LOL.mkv", "Lost/Season 01/Lost - S01E01.mkv")

    def test_anime_style(self):
        self.check("[HorribleSubs] One Punch Man - 03 [1080p].mkv", "One Punch Man/Season 01/One Punch Man - S01E03.mkv")

    def test_episode_only_defaults_to_season_1(self):
        self.check("Planet Earth Episode 2.mp4", "Planet Earth/Season 01/Planet Earth - S01E02.mp4")

    # ---- episodes inside folders
    def test_release_folder(self):
        self.check("Breaking.Bad.S01.1080p.WEB-DL.x264-GRP/breaking.bad.s01e02.1080p.web-dl.mkv",
                   "Breaking Bad/Season 01/Breaking Bad - S01E02.mkv")

    def test_release_folder_supplies_show(self):
        self.check("Breaking.Bad.S02.720p.BluRay-GRP/E05.mkv", "Breaking Bad/Season 02/Breaking Bad - S02E05.mkv")

    def test_user_show_folder_kept(self):
        self.check("Breaking Bad/Breaking.Bad.S01E03.720p.mkv", "Breaking Bad/Season 01/Breaking Bad - S01E03.mkv")

    def test_user_season_folder_normalized(self):
        self.check("Breaking Bad/Season 1/Episode 3.mp4", "Breaking Bad/Season 01/Breaking Bad - S01E03.mp4")

    def test_user_season_folder_trumps_abbreviation(self):
        self.check("Breaking Bad/Season 1/BrBa.S01E03.mkv", "Breaking Bad/Season 01/Breaking Bad - S01E03.mkv")

    def test_category_folder_kept_as_base(self):
        self.check("TV/Breaking.Bad.S01E03.720p.mkv", "TV/Breaking Bad/Season 01/Breaking Bad - S01E03.mkv")

    def test_already_tidy_is_unchanged(self):
        src = "Breaking Bad/Season 01/Breaking Bad - S01E03.mkv"
        self.check(src, src)
        src = "Breaking Bad/Season 01/Breaking Bad - S01E01 - Pilot.mkv"
        self.check(src, src)

    def test_bracketed_episode_markers_survive(self):
        # Episode numbers written inside [brackets] must not be stripped as junk
        self.check("Uzumaki/[AC] [S1-E01] Uzumaki [480p].mkv",
                   "Uzumaki/Season 01/Uzumaki - S01E01.mkv", "episode", True)
        self.check("high school d and d/[S01 E010] High School Dxd [480p] [Dual].mkv",
                   "high school d and d/Season 01/high school d and d - S01E10.mkv")
        self.check("silo/[Silo Season 3] [Episode 2] [1080p] [ @Tv_Series_ETY_ ].mp4",
                   "silo/Season 03/silo - S03E02.mp4")

    # ---- movies
    def test_scene_movie(self):
        self.check("The.Matrix.1999.1080p.BluRay.x265-RARBG.mp4", "Movies/The Matrix (1999).mp4", "movie", True)

    def test_movie_in_release_folder(self):
        self.check("Inception.2010.1080p.BluRay.x264-SPARKS/Inception.2010.1080p.BluRay.x264-SPARKS.mkv",
                   "Movies/Inception (2010).mkv")

    def test_movie_in_user_folder_stays(self):
        self.check("Action/Heat.1995.720p.WEB-DL.mkv", "Action/Heat (1995).mkv")

    def test_year_in_title(self):
        self.check("Blade.Runner.2049.2017.2160p.UHD.mkv", "Movies/Blade Runner 2049 (2017).mkv")
        self.check("2012.2009.1080p.mkv", "Movies/2012 (2009).mkv")
        self.check("1917.mkv", "Movies/1917.mkv")

    def test_clean_user_movie_untouched(self):
        self.check("Kids/Paddington 2.mp4", "Kids/Paddington 2.mp4")
        self.check("Kids/Toy Story 3 (2010).mp4", "Kids/Toy Story 3 (2010).mp4")
        p = propose("Family trip 2019 highlights.mp4")
        self.assertFalse(p.confident)   # has a year in the middle but isn't a download: ask first

    def test_root_movie_goes_to_movies_folder(self):
        self.check("Heat.mp4", "Movies/Heat.mp4", confident=False)

    def test_keeps_extension_case(self):
        self.check("Some.Show.S01E01.MKV", "Some Show/Season 01/Some Show - S01E01.mkv")

    def test_resolution_not_episode(self):
        self.check("Movie.Name.2015.1920x1080.mkv", "Movies/Movie Name (2015).mkv", "movie")

    def test_acronym_kept(self):
        self.check("JFK.1991.1080p.mkv", "Movies/JFK (1991).mkv")


if __name__ == "__main__":
    unittest.main()
