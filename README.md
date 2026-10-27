# FrameKeep 3.0

###### (C) 2025 Tanner Lucier. Licensed under the MIT License, see [LICENSE](LICENSE)

**A low-power, tiny-file, non-invasive screen recorder for all the moments you wish you had saved.**

![FrameKeep dashboard](docs/screenshots/dashboard.png)

| Recordings | Settings |
|---|---|
| ![Recordings browser](docs/screenshots/recordings.png) | ![Settings](docs/screenshots/settings.png) |
| **Benchmark** | **First run** |
| ![Benchmark](docs/screenshots/benchmark.png) | ![Welcome wizard](docs/screenshots/wizard.png) |

> ### Just want to use it? Get the `.exe`
> Download **`FrameKeep.exe`** from the [Releases page](../../releases/latest). It's one portable file with
> everything inside it, FFmpeg included. No Python, no installer, no dependencies.
>
> **If you're using the `.exe`, you can delete everything else in this repo and keep only `FrameKeep.exe`.**
> Put it wherever you like and double-click it.

---

## What's new in 3.0

- **H.266 / VVC recording.** The newest video codec, encoded live. On desktop content it's **about 70% smaller than
  v2's encoding at the same visual quality** ([measurements below](#how-small)).
- **AV1 with screen-content coding** (palette mode and intra block copy, tools built for text and UI), plus
  H.265 and GPU encoders (NVIDIA / Intel / AMD).
- **One pass, straight to the final file.** v2 recorded with OpenH264 and then re-encoded everything with FFmpeg.
  3.0 captures and encodes once, so there's no second encode, no temporary MKV and no conversion step.
- **A still screen costs almost nothing.** Unchanged frames are skipped, so reading, idling or watching a static page
  barely grows the file.
- **Smart auto-pause.** Pauses when you lock the screen, step away, or bring a private app (password manager,
  banking) to the front. Resumes on its own.
- **A real app.** Dashboard, recordings browser with thumbnails, settings with no JSON editing, a built-in benchmark,
  live logs and a first-run wizard. The tray icon still works exactly like before.
- **Multi-monitor.** Record one, some or all displays.
- **Global hotkeys** (`Ctrl+Alt+F9` start/stop, `Ctrl+Alt+F10` pause), **Start with Windows**, and a one-file `.exe`.
- **Your choice of file type.** MP4, MKV or WebM, with any format that fits in it.
- **Crash-safe files.** Recordings are written in 5-second pieces, so even if the PC loses power the file plays up to
  the last few seconds.
- **Correct timing.** v2 played back too fast; a 3.0 hour plays back as an hour.

### FrameKeep never touches your recordings
FrameKeep only ever **creates new files**, a new one every hour by default. It never edits, converts, moves or deletes
an existing recording. Deleting old recordings is entirely up to you, whenever you like, in Explorer. Even the
"Share copy" button writes a *new* file next to the original and leaves the original alone.

---

## How small?

Measured with FrameKeep's own **Benchmark** page on a 30-second desktop sample (code editor, terminal and a web app),
1080p downscaled to 960×540 at 5 FPS, *Balanced* quality, bitrate cap on, i5-12600K:

| Format | Codec | Size per hour of screen activity | CPU (whole PC) | Quality (VMAF) |
|---|---|---|---|---|
| **Ultra** | H.266 / VVC | **21 MB** | 2.7% | 97.0 |
| Smallest | AV1 screen-content (SVT-AV1) | 32 MB | 1.0% | 97.4 |
| Compatible | H.264 (tuned x264) | 53 MB | 0.7% | 96.8 |
| Smaller | H.265 / HEVC | 61 MB | 1.1% | 96.8 |
| Low CPU | AV1 on an NVIDIA GPU | 69 MB | 0.6% | 97.5 |
| *v2 encoding, for reference* | *H.264 (x264 fast, CRF 23)* | *74 MB* | | *97.0* |

VMAF scores quality from 0 to 100. Above ~93, most people can't tell the result from the original.

Some things to keep in mind:
- **"Per hour of activity" is the worst case for that content.** Idle time, a locked screen and auto-paused time cost
  close to nothing, so a real 8-hour day usually lands well under 8× these numbers.
- **Your numbers will differ.** Fast-moving content (games, video) needs far more data than text and UI. The bitrate
  cap stops it from ballooning. Run the Benchmark on your own PC: it measures every format on your screen and
  recommends the smallest one your CPU handles comfortably.
- H.265 isn't consistently smaller than tuned H.264 on screen content, which is why Ultra and Smallest are the
  recommended formats.

### Playing H.266 files
H.266 is new, so Windows' built-in player and browsers can't open it yet. FrameKeep handles this for you:
- **Play** in the Recordings page opens H.265/H.266 files in FrameKeep's **built-in player** (FFmpeg's `ffplay`).
  Controls: `Space` pause, `←/→` seek 10 s, `↑/↓` seek 1 min, right-click to jump, `F` fullscreen, `Esc` close.
- **Share copy** makes a new H.264 file that plays anywhere, for sending to someone.
- Other players built on FFmpeg 7.1 or newer (recent mpv builds, for example) also play H.266.

If you need files that open everywhere with no fuss, pick **Compatible** (H.264).

---

## Requirements

- Windows 10 or 11 (64-bit)
- Any quad-core CPU from roughly the last 10 years. Dual-cores over 2 GHz work with the lighter formats.
- 4 GB RAM (8 GB recommended for Ultra). While recording, FrameKeep itself uses about 80 MB and the FFmpeg encoder
  roughly 260 MB (H.264), 470 MB (AV1) or 570 MB (H.266), measured as private memory on a 1080p display.
- Any HDD or SSD
- A GPU isn't required. If you have one, FrameKeep uses it to capture the screen (Desktop Duplication) and offers
  its hardware encoder as the **Low CPU** format.

The Benchmark tells you which formats your PC can record live. On slower CPUs it will recommend AV1 or H.264
instead of H.266.

## Install

### Option 1: the `.exe` (recommended)
1. Download `FrameKeep.exe` from [Releases](../../releases/latest).
2. Double-click it. The welcome wizard asks where to save recordings, runs the 30-second benchmark and lets you pick a format.
3. That's it. FrameKeep lives in the system tray (bottom-right, near the clock). Drag its icon out of the overflow
   arrow so it's always visible.

Windows SmartScreen may warn about an unrecognized app the first time. Click **More info → Run anyway**.

### Option 2: run from source
1. Install [Python](https://www.python.org/downloads/) 3.10 or newer.
2. Download this repo (**Code → Download ZIP**) and extract it.
3. Double-click **`dependencies.bat`**. It installs the three Python packages and downloads FFmpeg (~21 MB, checksum-verified)
   into the `ffmpeg` folder. Run it again after updating.
4. Double-click **`FrameKeep.pyw`** to start without a console window, or run it from a terminal:
   ```
   python -m framekeep
   ```

If the `ffmpeg` folder is missing (or its build lacks H.266) and an FFmpeg with `libvvenc` is on your PATH
(for example `winget install Gyan.FFmpeg`), FrameKeep finds and uses that one automatically.

---

## Using FrameKeep

| To… | Do this |
|---|---|
| Start / stop recording | **Start recording** on the dashboard, the tray menu, or `Ctrl+Alt+F9` |
| Pause / resume | **Pause** on the dashboard, the tray menu, or `Ctrl+Alt+F10` |
| Open the window | Left-click the tray icon. Closing the window just hides it to the tray. |
| Quit | Right-click the tray icon → **Quit**. The current file is finished cleanly first. |
| Find a moment | **Recordings** page: pick a day, see thumbnails, hit **Play** |
| Free up space | Delete old day folders in Explorer. FrameKeep never deletes anything itself. |

**Tray icon colors:** red = recording, yellow = paused, purple = auto-paused, gray = ready.

**Files:** recordings are saved as `<your folder>\YYYY-MM-DD\HH-MM-SS.mp4`, one file per hour by default.
Pausing finishes the current file, and resuming starts a new one named after its start time. With several displays
selected, each display gets its own file (`HH-MM-SS_mon2.mp4`).

**File type** (Settings → Storage) picks the container, separately from the codec:

| File type | Works with | Notes |
|---|---|---|
| **MP4** (default) | H.264, H.265, AV1, H.266 | Opens almost everywhere |
| **MKV** | H.264, H.265, AV1, H.266 | Works with everything; a favorite for archiving |
| **WebM** | AV1 (Smallest, or Low CPU on an AV1 GPU) | Open web format. With any other codec, FrameKeep saves MKV instead. |

All three are written so a file still plays if FrameKeep or the PC dies mid-recording. The file type doesn't change
the file size noticeably; the codec does that.

### Auto-pause
| Trigger | Default | Setting |
|---|---|---|
| Screen locked (Win+L, sleep, UAC prompts) | On | Pause when the screen is locked |
| No keyboard/mouse input | 5 minutes | Pause when you're away (0 = never) |
| A private app is the active window | KeePass, KeePassXC, 1Password, Bitwarden | Private apps, one program name per line |

Auto-pause works within about a quarter of a second, and recording resumes automatically when the reason goes away.

---

## Settings

Everything is in the **Settings** page and applies from the next recording file. You can still edit
`%APPDATA%\FrameKeepOS\config.json` by hand if you prefer, but restart FrameKeep afterwards.
Your v2 config is upgraded automatically: folder, FPS, downscale and chunk length are kept, and your CRF becomes a quality level.

| Key | Default | What it does |
|---|---|---|
| `log_root` | *(chosen in the wizard)* | Recordings folder. A sub-folder is made per day. |
| `preset` | `smallest` | `ultra` (H.266), `smallest` (AV1), `smaller` (H.265), `compatible` (H.264), `lowcpu` (GPU). Falls back automatically if your PC can't run it. |
| `quality` | `balanced` | `high`, `balanced`, `saver`, `max_saver` |
| `container` | `mp4` | File type: `mp4`, `mkv` or `webm`. WebM holds AV1 only; other formats are saved as MKV. |
| `bitrate_cap` | `true` | Caps bitrate so fullscreen video/games can't balloon a file |
| `fps` | `5` | Maximum frames per second (1–30). Unchanged frames are skipped anyway. |
| `downscale_factor` | `0.5` | 0.5 turns 1080p into 540p, 1440p into 720p, 4K into 1080p |
| `chunk_minutes` | `60` | Minutes per file |
| `monitors` | `["primary"]` | `"primary"`, `"all"`, or display names like `"\\\\.\\DISPLAY2"` |
| `capture_backend` | `auto` | `auto`, `ddagrab` (GPU Desktop Duplication), `gdigrab` (GDI) |
| `drop_duplicates` | `true` | Skip frames where nothing changed |
| `draw_mouse` | `true` | Show the cursor |
| `crop_taskbar` | `false` | Record the work area only |
| `grayscale` | `false` | Drop color for the smallest text-only archive |
| `pause_on_lock` | `true` | Auto-pause on lock |
| `idle_pause_minutes` | `5` | Auto-pause after this many idle minutes (0 = never) |
| `private_apps` | password managers | Auto-pause while one of these `.exe`s is in front |
| `hotkey_toggle` / `hotkey_pause` | `ctrl+alt+f9` / `ctrl+alt+f10` | Global hotkeys |
| `record_on_launch` | `false` | Start recording as soon as FrameKeep opens |
| `start_with_windows` | `false` | Launch hidden in the tray when you sign in |
| `player` | `auto` | `auto` (built-in player for H.265/H.266), `builtin`, `system` |
| `ffmpeg_path` | *(automatic)* | Use a specific `ffmpeg.exe` |

### Quality levels
Each level maps to the right value for each codec (CRF for x264/x265/AV1, QP for H.266). *Balanced* is tuned
to look the same as v2's default CRF 23. *High* keeps more detail in small text, while *Saver* and *Max saver*
trade sharpness for space.

---

## Command line

```
python -m framekeep                 open FrameKeep (same as FrameKeep.pyw / FrameKeep.exe)
python -m framekeep --minimized     start hidden in the tray
python -m framekeep --no-gui        tray + console only, like v2
python -m framekeep --benchmark 30  measure every format on your screen and print a table
python -m framekeep --list-monitors show detected displays
```

## Building the `.exe` yourself

```
python -m pip install -r requirements.txt pyinstaller
python tools/fetch_ffmpeg.py
pyinstaller framekeep.spec
```
The result is `dist\FrameKeep.exe`. Pushing a `v*` tag runs the same build on GitHub Actions and attaches the
`.exe` to a release ([.github/workflows/release.yml](.github/workflows/release.yml)).

The bundled FFmpeg is a minimal build containing only what FrameKeep uses, made by
[tools/build_ffmpeg.sh](tools/build_ffmpeg.sh) in an [MSYS2](https://www.msys2.org/) UCRT64 shell
(`bash tools/build_ffmpeg.sh --deps`) or by the manually run [FFmpeg workflow](.github/workflows/ffmpeg.yml).
Install a zip you built yourself with `python tools/fetch_ffmpeg.py --zip <path>`.

Run the tests with `python -m pytest`.

---

## How it works

```
 screen ──► ddagrab (GPU)   ──► skip unchanged ──► downscale ──► H.266 / AV1 / H.264 ──► MP4 / MKV / WebM
            or gdigrab (GDI)     frames (mpdecimate)                 (one encode)            (written once)
```

- **One FFmpeg process per display per file.** Python is just the conductor: it starts FFmpeg, rolls to a new file
  every `chunk_minutes`, and stops it cleanly (it sends `q`, so FFmpeg finishes the file properly).
- **Variable frame rate.** Frames are only produced when the screen changes, timestamped as they're captured, so
  playback timing is exact. At least one frame is kept every 10 seconds so durations stay accurate.
- **Long keyframe interval** (once per minute of activity). Screens change slowly, so this saves a lot of space.
- **Crash-safe containers.** MP4 is written as fragmented MP4 with a fragment every 5 seconds; MKV and WebM write a
  cluster every 5 seconds. If FrameKeep or the PC dies, the file still plays. Only the last few seconds the encoder
  was still working on are lost (measured: a hard kill 30 s in keeps about 15 to 20 s in every container).
- **Fallbacks everywhere.** If GPU capture fails it switches to GDI. If a format's encoder isn't available it falls back
  to the next smallest. If FFmpeg crashes, a new file starts a moment later. The old file is never reopened.
- **Never overwrites.** FFmpeg runs with `-n` and every file name is checked first, so an existing file can't be clobbered.

## FAQ

**My recordings won't open in Windows' Movies & TV / Media Player.**
They're probably H.266 (Ultra). Use **Play** in FrameKeep (built-in player), a recent mpv build, or **Share copy**
for an H.264 version. H.265 files need Microsoft's HEVC extension or the built-in player.

**Windows Security flagged ffmpeg.exe or FrameKeep.exe.**
They're unsigned, so Windows may be cautious the first time. Allow them in Windows Security. FrameKeep never uploads
anything: no network access, no telemetry.

**Does it record audio?** No, video only.

**Why 5 FPS?** It's plenty to see what happened, and the file stays tiny. Raise it in Settings if you want smoother playback.

**Where are the logs and settings?** `%APPDATA%\FrameKeepOS\`, which also holds thumbnails and the benchmark result.
None of it is stored next to your recordings.

**Something went wrong.** Check the **Logs** page, then [open an issue](../../issues) with what it says.

---

## Dependencies explained

- **Python** (source version only) is the language FrameKeep is written in. The `.exe` has Python built in.
- **CustomTkinter** draws the modern dark interface on top of Python's built-in tkinter. It's light: the app itself
  uses about 80 MB of RAM.
- **pystray** puts the icon in the system tray.
- **Pillow** draws the tray icon, the interface icons and the thumbnails.
- **FFmpeg** does the capturing and encoding: GPU/GDI screen capture, H.266 (VVenC), AV1 (SVT-AV1, libaom),
  H.264 (x264), H.265 (x265) and the GPU encoders. It also makes thumbnails and plays recordings (`ffplay`).
  It runs only on your PC and never connects to anything.

Gone since v2: OpenCV, NumPy, mss, pywin32, pythonnet and OpenH264. FFmpeg now handles everything they did, and
does it with fewer resources.

## Third-party software

FrameKeep uses **FFmpeg** 9.0.2 ([source](https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz)), built under GPLv3
by [tools/build_ffmpeg.sh](tools/build_ffmpeg.sh) with x264, x265, SVT-AV1, libaom, dav1d, VVenC, libvmaf, SDL2
and Intel VPL, each under its own license. FFmpeg is not part of this project's code and isn't covered by
FrameKeep's MIT License: FrameKeep runs it as a separate program, and `FrameKeep.exe` carries a copy of it under
FFmpeg's own GPLv3 terms. See `ffmpeg/LICENSE` and `ffmpeg/BUILDINFO.txt` (exact configure line and library versions)
after running `tools/fetch_ffmpeg.py`.

Python packages: CustomTkinter (MIT), Pillow (MIT-CMU), pystray (LGPLv3).

Icons are from Windows' built-in Segoe Fluent Icons / Segoe MDL2 Assets fonts.
