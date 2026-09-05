# Matinee

[![tests](https://github.com/shreeramsarathy19/matinee/actions/workflows/tests.yml/badge.svg)](https://github.com/shreeramsarathy19/matinee/actions/workflows/tests.yml)

**Home cinema for your own video files.**

Point Matinee at a folder of videos and get a Netflix-style site for them: a
home page with posters and "Continue Watching", series pages with season tabs
and episode synopses, a proper player that remembers where everyone stopped —
on your computer, your phone, and everything else on your Wi-Fi. One small
Python server, no build step, no accounts, no cloud.

- 🎬 **Netflix-style home** — hero banner, Continue Watching, Recently Added, genre rows, posters
- 📺 **Series & movie pages** — season tabs, episode names, synopses and stills (from TVmaze), movie plot info (from Wikipedia)
- ▶️ **A real player** — resume, skip buttons, speed, subtitles, fullscreen, a lock button, scrubber preview thumbnails, in-player episode list, Netflix-style "Up Next"
- 👨‍👩‍👧 **Profiles** — separate watch history per person
- 📱 **Phone as remote** — play on the laptop, control it from the couch: transport, scrubber, volume, episodes, subtitles, Up Next
- 🗂 **Auto-organize** — drop messy downloads in, get `Show/Season 01/Show - S01E03.mkv` out (cautious, undoable)
- 📲 **Plays on phones** — MKV/AVI converted to MP4 in the background (usually a seconds-long re-wrap)
- 💬 **Subtitles** — embedded tracks extracted automatically; missing ones fetched from OpenSubtitles with an in-player sync nudge
- 💾 **External-disk friendly** — unplug-safe, switch library folders from the UI, self-backing-up history

## Quick start

You need [Python 3.9 or newer](https://www.python.org/downloads/). `ffmpeg` is
optional but recommended (phone conversion, subtitle extraction, preview
thumbnails):

| | install ffmpeg |
|---|---|
| macOS | `brew install ffmpeg` |
| Linux | `sudo apt install ffmpeg` |
| Windows | `winget install ffmpeg` |

**macOS / Linux**

```
git clone https://github.com/shreeramsarathy19/matinee
cd matinee
./run.sh
```

**Windows** — same, but run `run.bat` (double-clicking it in Explorer works too).

The first run creates a private Python environment and installs two
dependencies. The terminal then prints the addresses:

```
  Matinee  —  0 video(s) in …/matinee/library
  On this computer: http://localhost:8765
  On your phone:    http://192.168.1.23:8765
```

Open the first one. A short **setup screen** greets you: name your cinema,
list who'll be watching, pick how far the skip buttons jump, choose whether to
convert files for phones, and select your video folder (a folder browser is
built in). That's it — start dropping videos into the library folder and they
appear within 30 seconds.

Nothing else is needed from the repo. The first run creates everything local
to you — `.venv/` (Python packages), `config.yaml` (your settings, written by
the setup screen), `library/` (your videos, unless you pick another folder) and
`data/` (watch history, posters, episode info). None of these are part of the
repository, so `git pull` later never touches your settings or history.

**On your phone**, open the second address in the browser. Phone and computer
must be on the same Wi-Fi or hotspot. If the OS asks whether Python may accept
incoming connections, allow it. Then use *Share → Add to Home Screen* for a
full-screen app icon.

## How it works

- The server **streams your files exactly as they are** — the browser's own
  video player does the decoding, so nothing is re-encoded while you watch and
  seeking is instant.
- Phones are picky: iPhone Safari only opens MP4/MOV with H.264/HEVC video and
  AAC audio. So a background worker **converts** other files once, ahead of
  time. Most MKVs just get re-wrapped (seconds, no quality loss); only genuinely
  unsupported codecs get re-encoded. Until a file is converted it plays on the
  computer and shows a small `queued` badge for phones.
- Everything Matinee learns — watch positions, profiles, posters, episode
  info — lives in a small database next to the code, not in your video folder.

## Your library

The library is just a folder. Every top-level folder becomes a row on the
home page; file names become titles:

```
library/
  Breaking Bad/
    Season 01/
      Breaking Bad - S01E01.mp4
  Movies/
    Heat (1995).mp4
  Kids/
    Paddington.mp4
  Some Movie.mp4            ← loose files go into a row called "Movies"
```

**You don't have to name things yourself.** Drop a download in — a loose
file or a whole release folder — and about a minute after it finishes copying,
Matinee files it:

```
Breaking.Bad.S01E03.720p.HDTV.x264-KILLERS.mkv   → Breaking Bad/Season 01/Breaking Bad - S01E03.mkv
Friends Season 3 Episode 7.mp4                   → Friends/Season 03/Friends - S03E07.mp4
The.Matrix.1999.1080p.BluRay.x265-RARBG.mp4      → Movies/The Matrix (1999).mp4
[HorribleSubs] One Punch Man - 03 [1080p].mkv    → One Punch Man/Season 01/One Punch Man - S01E03.mkv
```

- Folders you created are respected; subtitles move along; emptied release
  folders are cleaned up; watch progress survives renames.
- Anything Matinee isn't sure about is **never moved silently** — it appears as
  a suggestion on the **Organize** page (folder icon, top-right; a yellow badge
  tells you when something is waiting there). Every move can be undone.
- After a conversion the original is parked in `library/.originals/`, hidden
  from the app; the Organize page shows how much space that is and can send
  it to the Bin.

**Subtitles**: a `.srt` next to a video is picked up automatically, embedded
text tracks are extracted during conversion, and the player's **CC** button can
fetch missing subtitles from OpenSubtitles (see Configuration).

## Watching

- **Home**: the banner resumes what you played last (or offers the next
  episode). **Continue Watching** shows one card per series — the episode you
  touched last, or the next one once you finished it. **Recently Added** flags
  new series and new episodes. Tapping a series or movie opens its page;
  tapping a Continue Watching card plays immediately.
- **Series pages** have season tabs, episode names, synopses and progress;
  **movie pages** show the plot, a Resume/Start-over button and subtitle
  options.
- **The player** works the same on desktop and phone: skip buttons (length is
  your choice), speed, volume, fullscreen, a **lock** button that ignores every
  touch until unlocked (great when kids hold the phone), thumbnails while
  scrubbing, a **☰ Episodes** list over the video for series, and a **CC**
  menu with on/off, a ½-second sync nudge (remembered per episode), and "try a
  different subtitle". On phones, swipe up on the control bar for big buttons.
  When an episode ends, a Netflix-style **Up Next** card counts down to the
  next one.
- **Phone as remote**: tap the cast icon on the phone, pick the computer, then
  tap any title — it plays there. The phone gets the full control bar including
  a scrubber with thumbnails, volume, the episode list, subtitle sync and the
  Up Next card. Only one device drives a screen at a time; taking over asks
  first.
- **Profiles**: every device asks "Who's watching?" once. Each profile has its
  own Continue Watching, resume points and ✓ marks.
- Desktop keys: `space` play/pause, `←`/`→` ±10 s, `↑`/`↓` volume, `f`
  fullscreen, `m` mute, `Esc` closes the panel or the player.

## Configuration

The setup screen writes `config.yaml`; edit it anytime (see
`config.example.yaml` for every option with comments).

| key | what it does | default |
|---|---|---|
| `library` | folder with your videos (can be an external disk) | `./library` |
| `app_name` | the name in the logo and browser tab | `Matinee` |
| `profiles` | who's watching — each gets their own history | `[You, Family]` |
| `skip_seconds` | how far the skip buttons jump | `30` |
| `convert_for_iphone` | convert MKV/AVI in the background | `true` |
| `keep_originals` | park originals in `.originals/` instead of deleting | `true` |
| `auto_organize` | tidy messy names automatically | `true` |
| `port` | web port | `8765` |
| `extensions` | file types to list | mp4, m4v, mov, webm, mkv, avi |
| `opensubtitles` | username / password / api_key / languages / daily_limit | off |

**Subtitles from OpenSubtitles** are optional: create a free account at
opensubtitles.com and an API key at opensubtitles.com/consumers, then fill in
the `opensubtitles:` block (use your site *username*, not your email). The free
tier allows 20 downloads a day; `daily_limit` makes sure Matinee never exceeds
it. Lookups match by exact file hash first, then by name, with a title check so
a lookalike show is never accepted.

## Library on an external disk

1. Copy your library folder to the disk (format it **exFAT** if it must also
   work with Windows or TVs; always eject before unplugging).
2. Point Matinee at it — the folder browser in the app, or `library:` in
   config.yaml. All history, profiles and posters survive: they're keyed by
   the paths *inside* the library, not by where it sits.

When the disk isn't connected the app shows **Library offline** with Retry and
"Use a different folder…" — and never touches your catalogue. While the
library lives on another disk, a daily copy of the history database is written
to `.matinee-backup/` on the disk, so the collection carries its own metadata.

## Troubleshooting

- **The phone can't reach the site** — both devices must be on the same
  Wi-Fi/hotspot; allow Python through the firewall when asked; use the numeric
  address if the `.local` one doesn't resolve.
- **"Library offline"** — the folder moved or the disk is unplugged. Use the
  folder browser to point at the new place.
- **macOS asks about Downloads/Desktop access** — click Allow. Matinee reads
  only the folder you selected as the library.
- **A file won't play on the phone** — it's still in the conversion queue
  (badge on the card), or `ffmpeg` isn't installed. The Organize page lists
  conversion failures with a Retry button.
- **Windows/Linux "delete originals"** parks the files next to the library
  rather than using a Bin.

## Development

- `.venv/bin/python -m matinee --reload` — dev server with auto-reload.
- `.venv/bin/python -m unittest discover -s tests` — 99 tests (name parsing,
  organizing, conversion, profiles, subtitles, offline guard, API). They run on
  CI for Linux, macOS and Windows.
- The front end is plain JavaScript in `static/js/` — numbered parts served as
  one file, versioned automatically. Edit, reload, done.
- Server code lives in `matinee/`: `app.py` (API + streaming), `library.py`
  (scan, offline guard), `organize.py` (name parser + mover), `convert.py`
  (ffmpeg worker), `catalog.py` (series grouping, Continue Watching rules),
  `metadata.py` / `artwork.py` (TVmaze, Wikipedia), `subsearch.py`
  (OpenSubtitles), `subs.py` (subtitle conversion), `trickplay.py` (preview
  sheets), `remote.py` (phone-as-remote).

## License

MIT — see [LICENSE](LICENSE).
