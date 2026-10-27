FrameKeep - (C) 2025 Tanner Lucier

Licensed under the MIT License - see LICENSE file for details (v2.1.0 and earlier were released under GPLv3)

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