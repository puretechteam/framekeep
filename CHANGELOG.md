FrameKeep - (C) 2025 Pure Tech

Licensed under the MIT License - see LICENSE file for details (v2.1.0 and earlier were released under GPLv3)

v3.1.0 Changelog
#
Efficiency (every number measured against 3.0.0 on the same PC, FFmpeg and sample; tables, raw data and the commands to reproduce them are in docs/perf)
- FrameKeep itself commits 96% less memory (520 MB to 21 MB in the tray) and holds 45% less in RAM (68 MB to 38 MB). Pillow imported NumPy whenever it was installed, which reserved about 0.5 GB for nothing. The .exe never bundled NumPy, but the other savings below apply to it too.
- The window only exists while it's open. Starting minimized (Start with Windows) never loads the interface at all, and closing the window to the tray releases its memory. Reopening takes about a second.
- 96% fewer wake-ups in the tray (90 to 3 per second). Auto-pause now listens for Windows' lock/unlock and foreground-window events instead of checking 4 times a second, so a private app is caught the moment it comes to the front.
- With the window open, 96% less CPU and 72% fewer wake-ups. Widgets only redraw when something changed, and CustomTkinter's 30 ms light/dark-theme check is off (FrameKeep is always dark).
- Less encoder memory while recording: H.264 -24% (330 to 250 MB), AV1 -23% (550 to 426 MB), H.266 -11% (627 to 555 MB). Shorter look-ahead for x264 and SVT-AV1 and 3 VVenC threads instead of 4.
- Encoders do 3-8% less work (CPU time) for every format.
- Slightly smaller files at the same quality: H.264 -3%, AV1 -2%.
- Unchanged frames are detected after downscaling instead of at full resolution, which removes a full-size color conversion of every captured frame.
- The dashboard's library totals no longer rescan every day folder every 30 seconds. Finished days are cached until their folder changes.
- The disk-space check reuses that cache for camera folders: on 12,960 test files it took 37 ms instead of 257 ms the first time and 5 ms instead of 257 ms after that.
- The Recordings page frees its thumbnails when you leave it and decodes them at the size they're shown.
- FrameKeep's thumbnail cache in AppData is capped at 50 MB (least recently viewed removed first), and the probe cache is written once per batch instead of once per recording.

New
- Cameras: a new Cameras page turns webcams, capture cards, phones (Iriun, DroidCam, or by network address) and network cameras (RTSP or HTTP MJPEG) into a recording station inside FrameKeep.
  - Each camera records continuously into <recordings>\Cameras\<name>\YYYY-MM-DD\, or another folder. One FFmpeg per camera starts each new file on the clock without stopping, so there's no gap between files.
  - Motion is marked on a timeline per day: click a mark or an event's Play to watch from just before it. Sensitivity per camera, plus an optional Windows notification (at most once a minute).
  - Live tiles, per-camera resolution (720p by default), frame rate (15 FPS), format, sound (the webcam's own microphone, or a network camera's audio), date and time burned into the picture, and "save space when nothing moves", all optional per camera.
  - Cameras run separately from desktop recording and keep going when the PC is locked or idle. A webcam serves one program at a time, so a camera can be paused from its tile, the tray's new Cameras menu or a camera hotkey, or pause by itself while apps you list (for example zoom.exe) are open.
  - Network cameras keep their own stream by default (0.04% CPU) or can be re-encoded smaller. Their passwords are stored encrypted for your Windows account and hidden in logs.
  - Unplugged, offline or busy cameras say so and reconnect by themselves, and one camera failing never affects the others or desktop recording.
  - Recordings can browse each camera's footage, with a motion count per file. The dashboard lists every camera's state and today's motion, and "Using now" includes camera processes.
  - Measured (docs/perf) on a 720p webcam sample at 15 FPS: AV1 at Balanced takes 86 MB per hour, 2.2% of the PC's CPU and 311 MB of memory per camera. At the same quality (VMAF) AV1 was 2.8x smaller than H.265 and 10x smaller than H.264. Three cameras at once used 8.1% CPU and 932 MB and kept real time, also when limited to 2 CPU cores. Motion detection found every test movement at the default sensitivity with no false events, for 0.05% of one CPU core.
  - Camera-tuned encoding: light denoise (files 6% smaller), natural-video encoder settings, a keyframe every 10 s so motion plays from close to where it starts, and 3 SVT-AV1 hierarchy levels (25% smaller files and 23% less memory than the screen's 4).
  - H.266 can't keep up with camera video (34 s recorded in 60 s), so cameras record AV1 when desktop recordings use Ultra.
- Disk space (new card in Settings): a warning on the dashboard and in the tray once recordings pass a size you choose (100 GB by default), and optional cleanup of old camera footage, off by default. It deletes only camera files FrameKeep named, older than the days you set (7, or 30 for files with motion), never a file being recorded and never desktop recordings.
- Desktop audio, off by default (Settings > Audio, or the setup wizard). FrameKeep records what your PC plays into the same file as the video:
  - Opus with every format except Compatible (H.264), which gets AAC so the file plays everywhere. You can pick either. WebM always uses Opus.
  - The bitrate follows the Quality level: Opus 96/48/32/24 kbps, AAC 160/128/96/64 kbps. Opus is variable bitrate and silence costs about 1 kbps (DTX).
  - Switching headphones or speakers is followed in the same file. Sound from apps you list (for example discord.exe) is left out: filtered on Windows 11, silenced while they play on Windows 10.
  - The dashboard shows the audio format, the device and a live level. Recordings shows the audio codec next to the video codec. The Benchmark adds each format's audio to its size, CPU and memory. Share copies keep the sound (AAC).
  - Measured (docs/perf): Opus at Balanced is 19.5 MB per hour of a test mix that's a third tones, a third speech-like noise and a third silence (AAC 128 kbps: 40.3 MB), adds 0.10% of the PC's CPU (AAC 0.07%) and 30 MB of memory in total (measured with a full FFmpeg for the audio encoder; the bundled build is smaller), leaves the video encoder's CPU unchanged, and keeps up when limited to 2 CPU cores. Test beeps land in the file within a few milliseconds of when they actually played.
  - Needs the bundled FFmpeg 9.0.2-fk4 (adds Opus, AAC and raw audio input). With an older FFmpeg, FrameKeep records video only and says so.
- The bundled FFmpeg 9.0.2-fk4 also adds what cameras need: DirectShow input, RTSP/HTTP network input (TLS through Windows' own schannel), the segment muxer, denoise and drawtext (with FreeType and HarfBuzz).
- The FrameKeep player replaces ffplay. Play anywhere in FrameKeep (or Open player on the Recordings page or in the tray) opens it:
  - A whole day plays as one timeline, crossing from file to file without a pause, in every format FrameKeep records (H.266 included).
  - Up to four sources side by side (the desktop and any cameras), in sync on the wall clock, with the sound from the one you pick. Layouts can be saved.
  - A time bar with a lane per source showing when it recorded, motion marks, click or drag to jump, and stretches that weren't recorded skipped.
  - 0.25x to 16x (sound up to 2x), frame-by-frame stepping, previous/next motion, and keyboard shortcuts.
  - Lossless clip export: set In and Out, see exactly where the clip will start and end (copies start on a keyframe), and save a full-quality copy in a moment. Recordings are never changed.
  - Motion notifications have a Play button that opens the player just before the motion.
  - Built on puretech-player, Pure Tech's own engine (FFmpeg decodes, Direct3D 11 draws, Windows' audio API plays). Measured against ffplay 9.0.1 on 8 files (H.264, H.265, AV1 and H.266 at 1080p30 and 720p15): lower or equal CPU on all 8 (H.264 1080p30 16.9% of a core against 21.9%, H.266 52.0% against 107.9%), less memory on 7, faster seeks on 6, a faster or equal first frame on all 8 and no dropped frames. H.266 seeks (230 against 182 ms) and H.266 1080p30 memory (732 against 713 MB) are still behind.
- A native tray icon (puretech-tray, Pure Tech's own) replaces pystray:
  - The icon shows a blue dot while a camera sees motion and an orange badge when a camera can't record or recordings are past the disk-space warning. The tooltip sums it up.
  - Notifications have buttons: Play and Pause camera on motion, Open settings on the disk-space warning.
  - The menu follows Windows' dark or light mode, and the tray, notifications and Start menu say FrameKeep (not FrameKeepOS).
  - Lighter: in the tray FrameKeep commits 19.0 MB instead of 22.6 MB and holds 34.8 MB instead of 39.0 MB (same PC and Python, only the tray changed). The library installs 1.7x smaller than pystray and six, imports 2.1x faster and updates the icon 12x faster.
  - FrameKeep adds a Start menu shortcut for your account the first time it shows a notification: Windows only shows notifications with buttons for apps that have one.
- "Using now" tile on the dashboard: FrameKeep's and FFmpeg's memory and CPU, live.
- The Benchmark measures each format's peak memory (new RAM column) and won't recommend one that needs more than a quarter of your RAM.
- tools/measure.py and tools/measure_ab.py: reproducible before/after measurements on a synthetic desktop sample. tools/measure_audio.py measures what desktop audio costs, tools/measure_cameras.py what cameras cost.

Changed
- Settings > Player is now "FrameKeep player" or "Windows default app". Auto and Built-in become the FrameKeep player.
- The bundled FFmpeg no longer includes ffplay or SDL2, and adds what the player needs (concat, showinfo, atempo, raw float audio out).
- python -m framekeep --help says "screen and camera recorder".

Fixes
- H.266 (Ultra) recordings had broken timestamps after a still stretch: in MP4, 332 of 398 frames on the test sample were shown at the wrong moment, including a 20-second jump. It's a bug in FFmpeg's VVenC wrapper with variable frame rate. The bundled FFmpeg (now 9.0.2-fk3) includes FrameKeep's fix (tools/patches/vvenc-vfr-timestamps.patch): 0 frames mistimed, and measured quality went from VMAF 24 to 96 at the same file size.
- A benchmark started before closing the window can't be started a second time after reopening it.

Tried and dropped
- Windows efficiency mode (EcoQoS) for FFmpeg raised its CPU time 35-70% in testing, so it isn't used.
#

v3.0.0 Changelog
#
Storage
- New recording engine: FFmpeg now captures and encodes in a single pass, straight to the final file. The OpenH264 recording + FFmpeg re-encode pipeline is gone, so there's no second encode, no temporary .mkv and no conversion step.
- H.266 / VVC recording (VVenC), the "Ultra" format. On a desktop sample it measured 21 MB per active hour vs 74 MB for v2's encoding at the same visual quality (VMAF ~97), about 70% smaller.
- AV1 with screen-content coding (SVT-AV1 scm / libaom palette + intra block copy), the "Smallest" format.
- H.265 and GPU encoders (NVIDIA NVENC, Intel QSV, AMD AMF; AV1/HEVC/H.264), the "Smaller" and "Low CPU" formats. Every format falls back automatically if your PC can't run it.
- 10-bit encoding for H.265/AV1/H.266, screen-tuned encoder settings and a one-minute keyframe interval.
- Unchanged frames are skipped (GPU Desktop Duplication + mpdecimate), so a still screen costs almost nothing.
- Bitrate cap so fullscreen video and games can't balloon a file.
- Choice of file type for every format: MP4, MKV or WebM (WebM is AV1-only; other codecs fall back to MKV). All three survive crashes.
- Quality levels (High / Balanced / Saver / Max saver) replace raw CRF values, mapped per codec. Your v2 CRF is converted automatically.
- Built-in Benchmark measures every format on your own screen (size, CPU, VMAF quality) and recommends the smallest one your PC handles comfortably.
- Smart auto-pause: on screen lock, when you're away, or while a private app is in focus.

App
- Brand-new GUI: dashboard, recordings browser with thumbnails, settings page, benchmark, live logs and a first-run wizard. The tray icon still works like before (left-click now opens the window).
- One-file portable FrameKeep.exe with FFmpeg inside.
- Multi-monitor recording (one, some or all displays, one file per display).
- Configurable global hotkeys (Ctrl+Alt+F9 start/stop, Ctrl+Alt+F10 pause). These replace Ctrl+R, which used to fire even when other apps had focus. Settings now apply live from the Settings page.
- Start with Windows (opens hidden in the tray).
- H.266/H.265 recordings play in a built-in player (ffplay), and "Share copy" makes a new H.264 file for sharing.
- FrameKeep never edits, moves, converts or deletes existing recordings. It only creates new files.
- Command line: --benchmark, --no-gui (v2-style tray + console), --minimized, --list-monitors.

Fixes
- Recordings played back too fast because frames were timed by sleeping after each capture. Timestamps now come from capture time, so an hour plays as an hour.
- Quitting while recording no longer leaves a half-written file. FFmpeg is asked to finish cleanly.
- Recordings survive crashes and power loss in every file type (only the last few seconds are lost).
- Display or resolution changes and GPU capture failures recover automatically (falling back to GDI capture).

Under the hood
- The single script became the `framekeep` package (`python -m framekeep`, or double-click FrameKeep.pyw). The version is no longer in the file name.
- Dependencies cut to customtkinter, pystray and pillow. OpenCV, NumPy, mss, pywin32, pythonnet and OpenH264 were removed.
- FrameKeep ships its own minimal FFmpeg 9.0.2 build (tools/build_ffmpeg.sh, built by GitHub Actions) with only what it uses: VVenC, SVT-AV1, libaom, x264, x265, the GPU encoders, screen capture and ffplay. FrameKeep.exe went from 207 MB to 39 MB and starts in about half the time. tools/fetch_ffmpeg.py downloads it with a pinned SHA-256 instead of storing it in git.
- 88 automated tests, including a guard that fails if the recorder ever removes, renames or overwrites anything in the recordings folder.
- GitHub Actions builds and publishes FrameKeep.exe for every version tag.
#

v2.1.0 Changelog
#
- New program icon to match tray icon!
- Removed ~47% of lines of the script. Removed code was script bloat, or optimization choices.
- Clarified README.md customization documentation
- Improved recording status messages to better distinguish between user-initiated stops and automatic hour-limit completions
- Hot-swap configuration feature: Press Ctrl+R in the console to refresh configuration settings without restarting the application
- Added Extra ADUS results
#
Future Plans
- FrameKeep only records the primary monitor, I hope to add configuration settings that let you choose all, or certain monitors.
- H.265 and AV1 are in the plans, and will NOT be replacing the defaults, they will be optional bonuses to users that can computationally afford it.
#

v2.0.0 Changelog
- Fixed FrameKeep not being able to sometimes find openh264-1.8.0-win64.dll on startup
- Added ffmpeg.exe to handle automatic but safe .mkv → .mp4 conversions (you can download your own ffmpeg build, using a different build could cause script instability), this does add ~100MB to the final build. You can delete the .exe and/or turn this off in the configuration file if you wish.
- Added CRF (Constant Rate Factor) to save ~34% more storage space on top of running H.264 and converting to MP4. You can turn CRF off in the configuration .json file, you can also edit the CRF value in the config file, the default is 23 to maintain quality while getting smaller files. CRF is automatically ignored if .mkv → .mp4 conversions are turned off.
- Added the "chunk_minutes" option to the configuration file, this allows you to change how long you want each file to record for. The default has been left at 60 minutes
- Important actions added to the console. Also added color coding, and timestamps.
- Added folders for third-party software to clean the repo up.
- Fixed build version to match Semantic Versioning standards (hopefully), also started saving releases for older versions. FrameKeepOS, FFmpeg, and Cisco's Open H.264 licensing has been added to the repo.
- Adjusted H.264 ADUS to new averages done with personal testing (users are more than welcome to post their own results, they will be taken into account with ADUS numbers).

#

v1.1.0 Changelog
- Switched MJPG from being the default recording codec, to H.264. MJPG is a fallback if for some reason H.264 doesn't work. This cuts recording file sizes by ~85%, with no loss in recording quality, while also using ~20% less RAM. You can also now customize which codec you want to use, in the FrameKeep config.json file.
- Updated MJPG Average Disk Usage Statistics (ADUS) after more testing. Also added H.264 ADUS