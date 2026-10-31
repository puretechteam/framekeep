# FrameKeep 3.1.0 vs 3.0.0: measured

Every number here was measured on the same PC (i5-12600K, 16 threads, Windows 11), with the same bundled
FFmpeg (9.0.2-FrameKeep-fk3, which carries 3.1's H.266 timestamp fix, so 3.0.0 benefits from it too) and the same
120-second desktop sample, using each version's own code. Raw data: [3.0.0.json](3.0.0.json),
[3.1.0.json](3.1.0.json), [ab-live.json](ab-live.json).

**The sample** is a synthetic 1080p desktop rendered by `tools/measure.py sample`: scrolling and typing in a code
editor, a terminal printing test output, a web page with text and an image, 15 s of idle screen, 15 s of video
(a Mandelbrot zoom) and window switching, at 5 FPS. It's deterministic, so anyone can regenerate the same pixels.
Default settings throughout: Balanced quality, 50% downscale (960×540), 5 FPS, bitrate cap on, MP4.

## Summary

| | 3.0.0 | 3.1.0 | Change |
|---|---|---|---|
| App memory in the tray (committed) | 520 MB | 21 MB | **−96%** |
| App memory in the tray (working set, in RAM) | 68 MB | 38 MB | **−45%** |
| App wake-ups in the tray | 90 /s | 3 /s | **−96%** |
| App CPU with the window open | 0.26% | 0.01% | **−96%** |
| FFmpeg memory recording H.264 (Compatible) | 330 MB | 250 MB | **−24%** |
| FFmpeg memory recording AV1 (Smallest) | 550 MB | 426 MB | **−23%** |
| FFmpeg memory recording H.266 (Ultra) | 627 MB | 555 MB | **−11%** |
| File size H.264 / AV1, same quality | 46.1 / 26.0 MB/hr | 44.7 / 25.5 MB/hr | **−3% / −2%** |
| Encoder CPU time, all formats | | | **−3% to −8%** |
| H.266 frames shown at the wrong time (3.0's FFmpeg) | 332 of 398 | 0 of 398 | **fixed** |

New in 3.1: [cameras](#cameras-new-in-31). On a 720p webcam sample at 15 FPS, a camera recorded in AV1 takes 86 MB per
hour, 2.2% of the PC's CPU and 311 MB of memory; three at once kept real time even limited to 2 CPU cores (on that still
scene; one with constant movement fell about 5 s behind per minute on 2 cores). A network
camera's own stream, kept as-is, costs 0.04% CPU. Motion detection found every test movement at the default
sensitivity, with no false events, for 0.05% of one core.

Also new in 3.1: [desktop audio](#desktop-audio-new-in-31), off by default. Turned on, Opus at Balanced added 0.10% of
the PC's CPU and 21.1 MB of memory with the bundled fk4 (30.4 MB when the audio encoder was a full FFmpeg). The video encoder's CPU
didn't change. On a test mix that's a third tones, a third speech-like noise and a third silence, the sound took
19.5 MB per hour.

Also new in 3.1: [the FrameKeep player](#player-new-in-31) replaces ffplay (lower or equal CPU than ffplay on all 8
test files, less memory on 7, faster seeks on 6), and [a native tray](#tray-new-in-31) replaces pystray (in the tray
FrameKeep commits 19.0 MB instead of 22.6 MB and holds 34.8 MB instead of 39.0 MB).

## FrameKeep app process

`python tools/measure.py run --parts app` starts each version with a private settings folder and samples it for
45 s after a 15 s warm-up: in the tray (started with `--minimized`, as Start with Windows does), with the
dashboard open, and recording in the tray. "Committed" is private bytes (memory the process has reserved RAM or
page file for); "working set" is what's actually in RAM (Task Manager's Details tab shows both).

| State | Committed memory (MB) | Working set (MB) | CPU (% of PC) | Wake-ups (context switches/s) |
|---|---|---|---|---|
| In the tray | 520 → 21 (−96%) | 68 → 38 (−45%) | 0.02 → 0.00 | 90 → 3 (−96%) |
| Window open | 521 → 33 (−94%) | 69 → 61 (−11%) | 0.26 → 0.01 (−96%) | 127 → 35 (−72%) |
| Recording, in the tray | 521 → 21 (−96%) | 68 → 38 (−45%) | 0.01 → 0.00 | 84 → 9 (−89%) |

With the window open, wake-ups depend heavily on whether the mouse is moving over it (every movement is an event
Tk handles): across idle runs 3.0.0 measured 127–715/s and 3.1.0 35–73/s. The table shows the latest idle pair.

Where it came from:
- **~500 MB of commit:** Pillow imports NumPy whenever NumPy is installed (only for type hints), and NumPy's math
  library commits ~30 MB per CPU thread on import. FrameKeep never uses NumPy and now blocks that import. The
  `.exe` never bundled NumPy, so for `.exe` users this part was already absent; the working-set and wake-up gains
  apply to everyone.
- **Tray:** the window is only built when opened and is destroyed (its memory returned) when closed. Auto-pause
  listens for Windows lock/unlock and foreground-window events instead of polling three checks 4 times a second.
- **Window open:** widgets only redraw when their value changes (CustomTkinter redraws on every `configure()`),
  the queue pump backs off when idle, and CustomTkinter's 30 ms light/dark-theme poll is off (FrameKeep is
  always dark). Also fixed during 3.1 work: CustomTkinter kept every closed window alive (~2 MB per open/close);
  10 open/close cycles now stay flat at 34 MB committed.

## Encoding (offline)

`python tools/measure.py run --parts offline` runs each version's exact recording command on the sample as fast
as possible (bundled FFmpeg `-benchmark`), both versions back to back. Quality is VMAF of what a viewer sees at
each of the 600 captured moments (both sides at a constant 5 FPS, so skipped or mistimed frames count against it).

| Format | File size (MB/hr) | Quality (VMAF) | CPU time (s) | FFmpeg peak RAM (MB) | Speed (× real time) | Mistimed frames |
|---|---|---|---|---|---|---|
| Compatible (H.264) | 46.1 → 44.7 (−3%) | 97.87 → 97.81 | 15.4 → 14.3 (−7%) | 374 → 284 (−24%) | 43.7 → 45.9 | 0 → 0 of 398 |
| Smaller (H.265) | 57.6 → 58.1 (+1%) | 94.47 → 94.87 (+0.4) | 18.8 → 17.8 (−6%) | 507 → 496 (−2%) | 23.3 → 23.7 | 0 → 0 of 398 |
| Smallest (AV1) | 26.0 → 25.5 (−2%) | 96.12 → 96.18 | 17.4 → 16.2 (−7%) | 617 → 491 (−20%) | 37.8 → 41.0 | 0 → 0 of 398 |
| Ultra (H.266) | 19.6 → 19.6 (0%) | 96.54 → 96.39 | 41.1 → 37.9 (−8%) | 699 → 624 (−11%) | 14.6 → 12.7 | 0 → 0 of 398 |
| Low CPU (NVIDIA AV1) | 38.5 → 39.2 (+2%) | 97.90 → 97.92 | 10.8 → 10.5 (−3%) | 608 → 600 (−1%) | 52.4 → 67.2 | 0 → 0 of 398 |

What changed: x264 look-ahead 50 → 10 frames, SVT-AV1 mini-GOP 32 → 16 frames (`hierarchical-levels=4`), VVenC
3 threads instead of 4 (less CPU and memory, a little less speed, still 12× faster than real time), and frames are
compared after downscaling instead of at full resolution. Each encoder setting was picked from a sweep on this
sample and kept only if files grew ≤3% and VMAF dropped ≤0.3. x265's look-ahead was left alone because shortening
it cost 0.6 VMAF.

## The H.266 timestamp fix

With 3.0's FFmpeg (fk2), Ultra recordings had broken timestamps. FFmpeg's VVenC wrapper assumes one timestamp
tick per frame, but FrameKeep records variable frame rate with millisecond timestamps, so VVenC returned invalid
decode times. In MP4, **332 of 398 frames** were shown at the wrong moment (including a 20-second jump after a
still stretch), and quality measured as **VMAF 24**. The same happened capturing a live screen ("Invalid DTS"
errors).

[tools/patches/vvenc-vfr-timestamps.patch](../../tools/patches/vvenc-vfr-timestamps.patch) hands VVenC frame
numbers and maps each packet back to the real capture time. `tools/build_ffmpeg.sh` applies it, and the bundled
FFmpeg is now 9.0.2-fk3. Result: **0 of 398 frames mistimed, VMAF 96.4**, file size unchanged, and live
captures have strictly increasing timestamps on the capture grid with no "Invalid DTS" errors.

## Live recording

`python tools/measure.py run --parts pipeline` feeds the sample to each version's live command in real time
(`-re`) and samples FFmpeg every second. Memory is stable run to run:

| Format | FFmpeg memory, mean (MB) | FFmpeg memory, peak (MB) | File size (MB/hr) |
|---|---|---|---|
| Compatible (H.264) | 330 → 250 (−24%) | 342 → 253 (−26%) | 46.1 → 44.7 (−3%) |
| Smaller (H.265) | 457 → 446 (−2%) | 473 → 462 (−2%) | 57.6 → 58.4 (+1%) |
| Smallest (AV1) | 550 → 426 (−23%) | 558 → 435 (−22%) | 26.0 → 25.5 (−2%) |
| Ultra (H.266) | 627 → 555 (−11%) | 665 → 589 (−11%) | 19.6 → 19.6 (0%) |
| Low CPU (NVIDIA AV1) | 582 → 571 (−2%) | 583 → 571 (−2%) | 38.5 → 39.2 (+2%) |

**Live CPU** is too small to compare across separate runs: at around 1% of the PC, the clock speed Windows picks
drifts enough that the same 3.0.0 command measured anywhere from 0.67% to 1.06% in different runs. So it was
measured with `tools/measure_ab.py`, alternating 3.0.0 and 3.1.0 back to back (ABBA order, two rounds each):

| Format | 3.0.0 runs (% of PC) | 3.1.0 runs (% of PC) | Median change |
|---|---|---|---|
| Compatible (H.264) | 0.82, 1.05 | 0.89, 1.08 | +5% |
| Smaller (H.265) | 1.43, 1.33 | 1.32, 1.28 | −6% |
| Smallest (AV1) | 0.77, 0.74 | 0.74, 0.72 | −4% |
| Ultra (H.266) | 7.76, 7.76 | 7.63, 7.59 | −2% |
| Low CPU (NVIDIA AV1) | 0.50, 0.48 | 0.50, 0.44 | −4% |

The differences are within the run-to-run spread, so live encoder CPU is about the same; 3.1's live encoder gains
are memory and (for H.264/AV1) file size. The offline runs above, at full speed where clocks are stable, show the
encoders doing 3–8% less work.

## Desktop audio (new in 3.1)

Desktop audio is off by default, so it costs nothing unless you turn it on. These numbers are what turning it on
adds. Raw data: [audio-codecs.json](audio-codecs.json), [audio-live.json](audio-live.json).

### How it records

FrameKeep captures what Windows plays with WASAPI loopback (ctypes, no extra package). A second, small FFmpeg
encodes it, and a third FFmpeg copies the video and the audio into the file. The video FFmpeg is the same as
without audio, except that it writes to a pipe and keeps millisecond timestamps.

It's split this way because FFmpeg lets no input run more than 0.1 s ahead of the slowest output, and the video
encoder's output trails real time by its look-ahead. Measured with raw audio as a second input to the video
FFmpeg (AV1, 5 FPS):
- The audio pipe blocked for 9.25 s of a 12 s recording.
- The finished file held 0.09 s of audio.

With the split, the audio that waits is already compressed (the queue held 18 KB after 8 s), and it all reaches
the file when recording stops: an 8 s recording has 8.0 s of audio, and a 4 s H.266 file keeps all 4 s.

### Codec settings

`python tools/measure_audio.py codecs` encodes a fixed 90 s mix: 30 s of tones and chords, 30 s of noisy
speech-like bursts and 30 s of silence. The table shows each setting's bitrate per part, file size per hour of
that mix (container included), CPU time per hour of audio on this PC, and a fidelity check on the tonal part. The
check is SDR, signal-to-distortion: higher means the decoded waveform is closer to the original. It isn't a
perceptual score, and noise can't be compared sample by sample, so the speech-like part has none.

| Setting | Tones (kbps) | Speech-like (kbps) | Silence (kbps) | File (MB/hr of the mix) | CPU (s per hour of audio) | SDR, tones (dB) |
|---|---|---|---|---|---|---|
| **Opus 24k** | 50.3 | 19.4 | 0.72 | 11.4 | 16.2 | 21.1 |
| **Opus 32k** | 63.0 | 26.2 | 0.81 | 14.34 | 15.6 | 28.3 |
| **Opus 48k** | 84.2 | 39.0 | 0.98 | 19.48 | 16.2 | 31.4 |
| **Opus 96k** | 146.2 | 66.4 | 1.36 | 32.95 | 20.0 | 35.7 |
| Opus 48k without DTX | 84.2 | 39.0 | 2.05 | 19.44 | 15.6 | 31.4 |
| Opus 48k 60 ms frames | 48.0 | 39.1 | 0.57 | 13.44 | 15.6 | 25.8 |
| Opus 48k complexity 5 | 58.9 | 43.3 | 0.91 | 16.32 | 12.5 | 26.2 |
| **AAC 64k** | 65.7 | 64.1 | 2.27 | 20.61 | 35.0 | 11.0 |
| **AAC 96k** | 98.8 | 96.2 | 2.27 | 30.39 | 30.6 | 20.1 |
| **AAC 128k** | 132.7 | 128.2 | 2.27 | 40.28 | 26.2 | 32.6 |
| **AAC 160k** | 164.9 | 160.2 | 2.27 | 49.9 | 29.4 | 40.2 |
| AAC 128k fast coder | 99.8 | 128.2 | 2.27 | 35.34 | 30.6 | 54.0 |

Bold rows are the settings FrameKeep uses (one per Quality level).

What was chosen, and why:
- **Opus, VBR, DTX, 20 ms frames.**
  - DTX halves what silence costs (2.05 → 0.98 kbps), with identical results on sound.
  - 60 ms frames made the mix 31% smaller, but only because the encoder spent less on the tones: SDR dropped
    from 31.4 to 25.8 dB. That isn't a free saving, so FrameKeep keeps the default.
  - Complexity 5 saved 23% CPU (16 → 12.5 s per hour) at 5 dB lower SDR. Opus's whole cost is already under
    0.5% of one core, so FrameKeep keeps full quality.
- **AAC (FFmpeg's encoder), default coder.** Only Compatible (H.264) uses it by default, so those files play
  everywhere. At its Balanced 128 kbps it's twice the size of Opus at 48 kbps, for similar SDR (32.6 vs 31.4 dB).
  The "fast" coder used 17% more CPU. On the tones it spent fewer bits at a higher SDR, but FFmpeg documents
  it as the lower-quality coder overall, and this test can't judge that. So FrameKeep keeps the default.
- **Bitrate follows Quality:** Opus 96/48/32/24 kbps, AAC 160/128/96/64 kbps.

### Live cost

`python tools/measure_audio.py live` records the docs/perf desktop sample in real time with audio off and on,
alternating (ABBA, 2 rounds, 60 s each), while the fixed sound clip loops quietly. It samples every FrameKeep
process once a second.

The video FFmpeg and the muxer are the bundled build. The audio encoder is the full FFmpeg on PATH, because
the bundled build only gets Opus/AAC in fk4. The full build's many libraries add to its memory, so its figure
is an upper bound.

| Format | Process | Audio off: CPU (% of PC) | Audio off: memory (MB) | Audio on: CPU | Audio on: memory |
|---|---|---|---|---|---|
| Compatible (H.264) + AAC 128k | app | - | 17.3 | 0.011 | 18.2 |
| Compatible (H.264) + AAC 128k | video | 1.063 | 256.2 | 1.038 | 262.3 |
| Compatible (H.264) + AAC 128k | audio encoder | - | - | 0.081 | 22.0 |
| Compatible (H.264) + AAC 128k | mux | - | - | 0.003 | 8.9 |
| Smallest (AV1) + Opus 48k | app | - | 16.6 | 0.011 | 18.0 |
| Smallest (AV1) + Opus 48k | video | 1.258 | 431.1 | 1.280 | 431.3 |
| Smallest (AV1) + Opus 48k | audio encoder | - | - | 0.064 | 21.3 |
| Smallest (AV1) + Opus 48k | mux | - | - | 0.003 | 7.5 |

- **Compatible (H.264) + AAC 128k:** audio adds +0.070% CPU and +37.9 MB in total; the audio track averaged 71.5 kbps on the looping clip.
- **Smallest (AV1) + Opus 48k:** audio adds +0.100% CPU and +30.4 MB in total; the audio track averaged 25.8 kbps on the looping clip.

Video encoder CPU is the same within run-to-run noise. Its memory rose 6 MB for H.264 when writing to the pipe, and 0.2 MB for AV1. The audio encoder above was the full FFmpeg. With the bundled fk4 for every process, as in the app (same tool, same rounds, raw data in [audio-live-fk4.json](audio-live-fk4.json)): Smallest + Opus adds +0.104% CPU and +21.1 MB (encoder 8.4, muxer 8.0, the app's capture 1.4, video encoder +3.3), Compatible + AAC +0.073% CPU and +22.1 MB. Limited to 2 cores it still kept up: 60.8 s of video and 60.05 s of audio from 60 s, 0.91% CPU in total. The fk4 encoder alone, encoding the fixed sound: Opus 48k 8.9 MB against 21.7 MB for a full FFmpeg, AAC 128k 9.6 MB against 22.4 MB.

**Limited to 2 CPU cores** (Smallest + Opus, everything pinned to 2 logical CPUs, like an old dual-core): video 60.8 s and audio 60.049 s from a 60 s recording, total CPU 0.91% of this 16-thread PC.

### Sync

- **Audio timeline.** The audio track is stamped on the same clock as the capture. A test played a short beep
  every 2 s and logged when each one started. In the recorded file, every beep sits 18–28 ms after its start
  time, for both AAC and Opus. That gap is the playback delay of the sound itself, which loopback measured
  separately at 20–30 ms. So the audio timeline matches real time to within a few milliseconds. Beeps logged
  2.001–2.003 s apart are exactly 2.000 s apart in the file, with no drift.
- **Video timeline.** The video is stamped on the wall clock: GDI capture stamps it already, and Desktop
  Duplication frames get their first frame's time added. Both count from the moment the audio starts.
  - Without the 1 ms encoder time base, FFmpeg rounded every video timestamp to the frame grid: at 5 FPS the
    first frame always came out at exactly 0.0 or 0.2 s, an error of up to 100 ms.
  - With it, the first frame lands 46–102 ms after the audio starts: the time FFmpeg takes to start.
  - File sizes stayed the same: H.264 −0.00%, H.265 −0.34%, AV1 and H.266 byte-identical.
- **Not measured yet:** a flash-and-beep check of the finished picture against the sound. The PC's display was
  off and then locked during this work, and screen capture needs it on.

### Leaving apps out

The test played a steady 500 Hz tone from one app (ffplay) and a 1 kHz beep every 2 s from another, then
captured 8 s with and without ffplay.exe on the leave-out list (Windows 11 process loopback). The table shows
each tone's level in the captured sound:

| | 500 Hz (left-out app) | 1 kHz beeps (other app) |
|---|---|---|
| Nothing left out | 0.0022 | all 4 beeps, peak 0.025 |
| ffplay.exe left out | 0.0000–0.0003 (noise floor) | all 4 beeps, peak 0.022–0.025 |

Windows gives a process-loopback stream a 10 ms buffer whatever size is asked for. At first FrameKeep read it
10 times a second like the normal stream, which caught only 0.9 s of every 3 s and chopped the beeps. Now it's
read each time Windows signals new sound, about 100 short wake-ups a second, but only while a left-out app is
running. Normal capture keeps its 1 s buffer and 10 wake-ups a second.

### Stopping

During development, stopping a file could deadlock on slow PCs until FrameKeep's 15 s safety timeout killed the video FFmpeg:
- The muxer holds the video back while the audio looks behind.
- The audio encoder only sent its last packets once its input closed, and FrameKeep closed that input after the
  video FFmpeg had exited.

Limited to 2 cores, every 45–60 s recording stopped this way. Now the audio input is closed the moment capture
ends, and the audio encoder passes packets on every 200 ms. Limited to 2 cores, the video FFmpeg now exits
0.96 s after stop (twice in a row), and the files keep 61.0 s of video and 60.05 s of audio from 60 s
recordings. With 16 threads and no limit, stopping takes about 1 s.

### Crash safety

- **Start of a file.** Audio doesn't make the start of a file any less safe. The time until the first
  fragment is written is set by the video encoder's look-ahead plus the 5 s fragment: AV1 14.5 s with audio vs
  14.5 s without, H.264 9.5 vs 9.0 s, H.266 16.5 vs 15.0 s.
- **Hard kill.** Killing every process 13 s into an H.264 recording left a file that plays: 5.0 s of video and
  4.9 s of audio, with no decode errors.

## Cameras (new in 3.1)

Cameras are measured on their own sample: a fixed 2-minute 720p MJPEG clip from a USB webcam (an indoor scene that
barely moves), fed in real time through FrameKeep's real camera command at the defaults: 720p, 15 FPS, Balanced,
light denoise, motion detection on. Raw data: [cameras.json](cameras.json). The clip itself isn't published (it
shows a room); `tools/measure_cameras.py` records a new one from your own webcam.

### Formats

Each format recorded the sample in real time for 60 s, one at a time. CPU is the share of the whole PC (16 threads);
memory is the FFmpeg process's private memory, analysis output included.

| Format | Encoder | MB per hour | CPU of the PC | Memory | Kept real time |
|---|---|---|---|---|---|
| **Smallest (AV1)** | libsvtav1 | **86** | 2.2% | 311 MB | yes |
| Smaller (H.265) | libx265 | 739 | 7.8% | 431 MB | yes |
| Compatible (H.264) | libx264 | 1906 | 11.3% | 154 MB | yes |
| Low CPU (GPU) | av1_nvenc | 1397 | 0.75% | 591 MB | yes |
| Ultra (H.266) | libvvenc | | 18.8% | 690 MB | **no: 34 s recorded in 60 s** |

H.266 can't keep up with camera video (VVenC on a 720p 15 FPS camera needs about 5 CPU cores' worth; its 3 threads
manage 3), so cameras never use it: "Same as desktop" with Ultra records cameras in AV1.

The sizes above use each format's own Balanced level, which was tuned on screen content. On camera video they
don't give the same quality, so the quality test below compares them properly.

### Quality

`measure_cameras.py quality` encodes 60 s of the sample (denoised, as FrameKeep records it) with each codec at each
level and scores it with VMAF against the undenoised original. The denoise itself counts against every format, so
scores top out around 88-95.

| Level | H.264 MB/hr (VMAF) | H.265 MB/hr (VMAF) | AV1 MB/hr (VMAF) |
|---|---|---|---|
| High | 2807.9 (95.3) | 1322.4 (93.4) | 186.8 (87.6) |
| Balanced | 1588 (93.1) | 554.4 (90.2) | 63.1 (86.3) |
| Saver | 618.6 (89.7) | 179.2 (86.5) | 33.4 (86.0) |

At the same quality AV1 is far smaller: Balanced AV1 (VMAF 86.3) is 63.1 MB per hour, where H.265 needs
179.2 (Saver, VMAF 86.5) and H.264 618.6 (Saver, VMAF 89.7). That's why AV1 is
the format to pick for cameras.

**SVT-AV1 tuned for cameras:** FrameKeep uses 3 hierarchical levels for camera video (4 for the screen).

| SVT-AV1 at Balanced | MB per hour | VMAF | Peak memory |
|---|---|---|---|
| 4 levels (the screen's setting) | 77.8 | 86.4 | 467 MB |
| **3 levels (cameras)** | 63.1 | 86.3 | 387 MB |
| 2 levels | 75.1 | 86.5 | 317 MB |
| 3 levels, no look-ahead | 84 | 86.5 | 321 MB |
| 3 levels, low delay | 132.6 | 86.0 | 202 MB |

Live, the change took a camera from 115 to 86 MB per hour (−25%) and from 402 to 311 MB of memory (−23%), with CPU
the same within noise (2.09% → 2.22%). Low delay used the least memory but made files twice the size, so it isn't used.

### What else costs what

| Setting (AV1, live) | MB per hour | CPU of the PC | Change |
|---|---|---|---|
| Defaults (light denoise on) | 86.3 | 2.22% | |
| Denoise off | 92.2 | 2.14% | denoise saves 6% |
| Save space when nothing moves | 76.9 | 1.79% | −11% size, −19% CPU |
| Date and time in the picture | 88.1 | 2.17% | +2% size |

The quiet saver drops near-identical frames. On this still scene it saved 11%; sensor noise keeps most frames
different, so the default keeps every frame and the option is there for scenes where it pays off.

### Network cameras

A network camera's stream was simulated with what a typical IP camera sends (H.264 1080p at 4 Mbit/s, a keyframe
every 2 s), played in real time:

| | MB per hour | CPU of the PC | Memory |
|---|---|---|---|
| Keep the camera's own stream (default) | 1833 | **0.04%** | 85 MB |
| Make smaller (AV1, 720p) | **59** | 2.3% | 450 MB |

Kept as-is, FFmpeg only decodes keyframes for motion detection and the live tile. Making it smaller re-encodes it to
3% of the size for about 2% of the PC's CPU.

### Three cameras at once

Three AV1 cameras recording the sample at the same time used 8.1% of the PC's CPU and 932 MB in total, and each kept
real time (60.9 s recorded in 60 s). Limited to 2 CPU cores, as on an old dual-core PC: 8.8% (of all 16 threads, so
about 70% of the two cores) and every camera still recorded 60.9-61.0 s in 60 s. A scene with constant movement is
harder: see [With FrameKeep's own FFmpeg](#with-framekeeps-own-ffmpeg-fk4).

### Motion detection

FrameKeep looks for motion in the small analysis frames (192×108, 4 a second): a grid of every 4th pixel's green
value, compared with a slowly learned background, where a cell counts as moving only if it also changed since the
last frame (otherwise a departed object's fading "ghost" kept events going about 7 s too long).

The test overlays a person-sized object (7% of the picture) walking across the real sample at known times, 20-26 s
and 60-63 s:

| Sensitivity | Events found | On the untouched sample |
|---|---|---|
| 3 | none | none |
| **5 (default)** | 20.8-25.8 s, 60.2-63.0 s | none |
| 7 | 20.2-26.0 s, 60.2-63.0 s | none |
| 10 | both, plus 70.8-81 s | 70.8-82 s (a small real movement in the clip) |

The webcam's own noise never covered more than 0.7% of the grid; the slow walker covered 4.8%. That set the default
at 2.5%. The first scale had the default at 5% and missed the walker, so it was rescaled. Detection costs 0.13 ms
per frame in Python: about 0.05% of one CPU core per camera.

### Files and sharing the webcam

- **No gap between files.** FFmpeg's segment muxer starts each file on the clock (on the hour by default) while the
  capture keeps running. A 40 s live test with a 1-minute chunk split at exactly 16:56:00 into 10.7 s + 27.2 s of
  video and Opus audio, matching the 38 s captured.
- **Crash safety.** Each file is fragmented MP4 or clustered Matroska, as for the desktop. Killing FFmpeg left every
  finished file playable with no decode errors, and the file being written kept what had been flushed.
- **Webcam and microphone in one FFmpeg.** Unlike desktop audio, a camera's video and its microphone come from one
  input, which FFmpeg doesn't hold back: a 20 s AV1 test kept all 20 s of audio (977 packets) with no buffer warnings.
- **A webcam serves one program at a time.** With FFmpeg recording it, a second program opening it got "device
  already in use". That's why FrameKeep can pause a camera from the tray, a hotkey or while chosen apps run.

### With FrameKeep's own FFmpeg (fk4)

The numbers above were recorded with a full FFmpeg. Once the bundled 9.0.2-fk4 was built, every recording part was
run again through it and, right after, through the full FFmpeg, on the same new sample, same PC, pinned to 12 of 16
threads. The first sample (a still scene) had been deleted for privacy, and the new one has movement throughout (frame
difference median 4.6, where a still scene measures about 1 to 2), so its files are much bigger than the ones above.
Compare the two columns with each other, not with the tables above. Raw data: [cameras-fk4.json](cameras-fk4.json).

| 720p 15 FPS, 60 s live | MB/hr, fk4 / full | CPU of the PC, fk4 / full | Memory, fk4 / full | Recorded of 60 s, fk4 / full |
|---|---|---|---|---|
| Smallest (AV1) | 526.8 / 526.3 | 3.14% / 3.15% | 298 / 310 MB | 60.7 / 60.8 s |
| Compatible (H.264) | 2,091 / 2,092 | 11.2% / 11.4% | 140 / 153 MB | 61.1 / 60.9 s |
| Smaller (H.265) | 1,441 / 1,407 | 10.9% / 9.7% | 422 / 433 MB | **40.8** / 60.9 s |
| Low CPU (NVENC AV1) | 1,909 / 1,909 | 0.75% / 0.50% | 577 / 590 MB | 60.7 / 60.8 s |
| Network camera kept as-is | 1,813 / 1,814 | 0.05% / 0.07% | 62 / 75 MB | 61.3 / 61.3 s |
| Network camera made smaller | 198 / 198 | 2.99% / 3.26% | 413 / 425 MB | 61.1 / 61.1 s |
| Three AV1 cameras | | 11.6% / 11.2% | 894 / 929 MB | 60.7 / 60.9 s each |
| Three AV1 cameras on 2 cores | | 11.9% / 11.8% | 897 / 931 MB | 52.7-55.5 / 54.0-54.7 s |

- **Same files and quality.** Offline, all 13 codec settings gave the same size within 0.4% and the same VMAF within
  0.02 on both builds (for example AV1 Balanced 351.3 / 350.1 MB/hr at 88.69 / 88.70).
- **Less memory.** fk4's smaller libraries save 10 to 15 MB per camera.
- **H.265 is the one difference.** fk4's H.265 fell behind real time in the first run (40.8 s of 60). Repeated, it
  recorded 58.3 s and 60.7 s, against 60.9 s every time for the full FFmpeg. AV1, FrameKeep's camera default, kept
  real time in every run on both.
- **This busy scene is harder:** three AV1 cameras limited to 2 cores fell about 5 s behind per minute on both builds,
  where the still scene kept up. On a 2-core PC with cameras that see constant movement, use fewer cameras or a
  lower frame rate.

## Tray (new in 3.1)

pystray was replaced by [puretech-tray](https://github.com/puretechteam/puretech-tray), Pure Tech's own tray in pure
ctypes. Raw data: [tray.json](tray.json).

**FrameKeep itself**, `tools/measure.py run --parts app`, same PC, the same Python 3.13 environment (no NumPy),
only the tray library swapped:

| | pystray | puretech-tray | Change |
|---|---|---|---|
| In the tray, committed | 22.6 MB | 19.0 MB | **−16%** |
| In the tray, working set | 39.0 MB | 34.8 MB | **−11%** |
| Window open, committed | 34.2 MB | 31.5 MB | **−8%** |
| Window open, working set | 62.1 MB | 58.9 MB | **−5%** |
| CPU | 0.0% | 0.0% | same |

With the player integrated too, the tray state measured 18.9 MB committed and 35.1 MB working set: the player is
only loaded once it's opened.

**The library on its own** (its own `tools/measure.py`, fresh process per sample, 7 runs, medians):

| | pystray 0.19.5 | puretech-tray 0.1.0 | |
|---|---|---|---|
| Import | 53.2 ms | 25.9 ms | 2.1x faster |
| Start to icon visible | 114 ms | 27 ms | 4.2x sooner |
| Private memory added | 5,668 KB | 2,216 KB | 2.6x less |
| Working set added | 9,500 KB | 5,324 KB | 1.8x less |
| Icon update | 2.59 ms | 0.21 ms | 12x faster |
| Installed size | 176.5 KB (with six) | 101.3 KB | 1.7x smaller |

The first version loaded Windows' notification components at start, which made FrameKeep's working set 4.4 MB
*larger* than with pystray. They now load on the first notification and unload again once no notification can be
clicked.

## Player (new in 3.1)

ffplay was replaced by the FrameKeep player, built on [puretech-player](https://github.com/puretechteam/puretech-player)
(FFmpeg decodes, Direct3D 11 draws, WASAPI plays). Raw data: [player.json](player.json).

Both players opened the same generated files (testsrc2 with a time code, sine sound, 70 s, a keyframe every 2 s) in a
1280x720 window, one at a time, against ffplay 9.0.1. CPU is processor cycles over 60 s at 1x as % of one core;
memory is ffplay's private memory against what the player adds to a running Python/Tk app.

| File | First frame, ffplay / ours | Seek, ffplay / ours | CPU, ffplay / ours | Private RAM MB, ffplay / ours | Dropped |
|---|---|---|---|---|---|
| AV1 1080p30 | 349 / 327 ms | 50 / 39 ms | 23.0 / 16.0 | 127 / 112 | 0 / 0 |
| H.265 1080p30 | 353 / 353 ms | 59 / 54 ms | 28.6 / 24.1 | 189 / 133 | 0 / 0 |
| H.266 1080p30 | 482 / 313 ms | **182 / 230 ms** | 107.9 / 52.0 | **713 / 732** | 1 / 0 |
| H.264 1080p30 | 383 / 341 ms | 64 / 52 ms | 21.9 / 16.9 | 166 / 124 | 0 / 0 |
| AV1 720p15 | 345 / 318 ms | 41 / 29 ms | 7.2 / 5.9 | 69 / 65 | 0 / 0 |
| H.265 720p15 | 347 / 316 ms | 53 / 36 ms | 8.2 / 8.3 | 97 / 76 | 0 / 0 |
| H.266 720p15 | 392 / 320 ms | **94 / 112 ms** | 26.3 / 14.6 | 371 / 362 | 0 / 0 |
| H.264 720p15 | 336 / 303 ms | 58 / 33 ms | 6.7 / 6.0 | 90 / 74 | 0 / 0 |

Still behind (bold): H.266 seeks, because FFmpeg's H.266 decoder only hands over the keyframe before the target once it
has decoded the next one (ffplay shows whichever keyframe it lands on, often a second past the target), and H.266
1080p memory by 19 MB next to a decoder that alone needs about 660 MB. The player is 128 KB of Python against a
15.1 MB ffplay.exe, and fk4 no longer builds ffplay or SDL2.

**Inside FrameKeep** (the real app on a synthetic library: desktop chunks in AV1, H.264 and H.265 with a 3-minute
hole, two cameras, motion events):

- A motion notification's Play opened the player from the tray thread, on a hidden main window, 2 s before the event.
- Desktop and two cameras played side by side, crossing a codec change, all three in sync.
- 16x advanced 48 s of footage in 3 s with no dropped frames.
- Playback reaching the hole jumped to the next recording (10:02 to 10:05).
- Frame step back moved exactly one frame (0.2 s at 5 FPS), and play continued from the stepped frame.
- A clip asked for 10:01:05 to 10:01:20 started on the 10:01:00 keyframe, as announced, and ffprobe showed AV1 + AAC
  copied as-is, 20.0 s long.
- Closing the main window kept the player playing, and closing the player then freed the window's memory.

## Tried and dropped

- **Windows efficiency mode (EcoQoS) for FFmpeg.** It schedules the encoder on efficiency cores at lower clocks.
  Measured live, it *raised* CPU time 35–70% (AV1 0.76% → 1.28%) and VVenC's context switches to ~22,000/s.
  Not shipped.
- **Low-delay VVenC** (no frame reordering) fixes Ultra's timestamps and cuts its memory 37%, but files grew 20%.
  **Constant-frame-rate input to VVenC** also fixes timestamps, but files grew 17% and CPU rose 20%. Both failed
  the storage-first rule; the FFmpeg patch fixes the timestamps without either cost.
- **Shorter x265 look-ahead:** −11% memory but −0.6 VMAF.
- **Raw desktop audio as a second input to the video FFmpeg:** FFmpeg held the audio back behind the video
  encoder's look-ahead; a 12 s AV1 recording kept 0.09 s of audio. Replaced by a separate audio encoder and a
  copy-only muxer (see Desktop audio).
- **Dropping near-duplicate frames on cameras by default:** FFmpeg's default thresholds dropped no frames at all
  on webcam footage (sensor noise makes every frame differ); camera-tuned thresholds saved 5-11%. Kept as an option.
- **Low-delay SVT-AV1 for cameras:** least memory, but files twice the size on the quality test (133 vs 63 MB per hour).
- **Matroska between the video FFmpeg and the muxer:** it drops decode timestamps, which gave H.266 13 timestamp
  warnings and 2 decode errors on a test clip. Fragmented MP4 gives 0.

## Reproducing

```
pip install psutil
python tools/fetch_ffmpeg.py                       # the bundled FFmpeg (fk3)
python tools/measure.py sample                     # needs a full FFmpeg on PATH for this one step
git worktree add build/perf/v3.0.0 v3.0.0
python tools/measure.py run --repo build/perf/v3.0.0 --out docs/perf/3.0.0.json
python tools/measure.py run --out docs/perf/3.1.0.json
python tools/measure.py compare docs/perf/3.0.0.json docs/perf/3.1.0.json
python tools/measure_ab.py --a build/perf/v3.0.0 --b . --rounds 2 --out docs/perf/ab-live.json
python tools/measure_audio.py codecs --out docs/perf/audio-codecs.json   # needs a full FFmpeg on PATH
python tools/measure_audio.py live --out docs/perf/audio-live.json
python tools/measure_cameras.py all --out docs/perf/cameras.json     # needs a webcam once, and a full FFmpeg on PATH
```

The app test briefly opens a FrameKeep window and records the screen into `build/perf` (deleted afterwards).
Nothing is written to your real settings or recordings folders.
