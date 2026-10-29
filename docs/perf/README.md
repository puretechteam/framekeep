# FrameKeep 3.1.0 vs 3.0.0: measured

Every number here was measured on the same PC (i5-12600K, 16 threads, Windows 11), with the same bundled
FFmpeg (9.0.2-FrameKeep-fk2) and the same 120-second desktop sample, using each version's own code. Raw data:
[3.0.0.json](3.0.0.json), [3.1.0.json](3.1.0.json), [ab-live.json](ab-live.json).

**The sample** is a synthetic 1080p desktop rendered by `tools/measure.py sample`: scrolling and typing in a code
editor, a terminal printing test output, a web page with text and an image, 15 s of idle screen, 15 s of video
(a Mandelbrot zoom) and window switching, at 5 FPS. It's deterministic, so anyone can regenerate the same pixels.
Default settings throughout: Balanced quality, 50% downscale (960×540), 5 FPS, bitrate cap on, MP4.

## Summary

| | 3.0.0 | 3.1.0 | Change |
|---|---|---|---|
| App memory in the tray (committed) | 521 MB | 21 MB | **−96%** |
| App memory in the tray (working set, in RAM) | 68 MB | 37 MB | **−45%** |
| App wake-ups in the tray | 100 /s | 5 /s | **−95%** |
| App wake-ups with the window open | 620 /s | 73 /s | **−88%** |
| App CPU with the window open | 0.51% | 0.01% | **−98%** |
| FFmpeg memory recording H.264 (Compatible) | 330 MB | 249 MB | **−25%** |
| FFmpeg memory recording AV1 (Smallest) | 550 MB | 426 MB | **−22%** |
| FFmpeg memory recording H.266 (Ultra) | 628 MB | 559 MB | **−11%** |
| File size H.264 / AV1, same quality | 46.1 / 26.0 MB/hr | 44.7 / 25.5 MB/hr | **−3% / −2%** |
| H.266 encoding CPU time | 55.4 s | 41.6 s | **−25%** |
| Encoder CPU while recording live | | | no measurable change (see below) |

## FrameKeep app process

`python tools/measure.py run --parts app` starts each version with a private settings folder and samples it for
45 s after a 15 s warm-up: in the tray (started with `--minimized`, as Start with Windows does), with the
dashboard open, and recording in the tray. "Committed" is private bytes (memory the process has reserved RAM or
page file for); "working set" is what's actually in RAM (Task Manager's Details tab shows both).

| State | Committed memory (MB) | Working set (MB) | CPU (% of PC) | Wake-ups (context switches/s) |
|---|---|---|---|---|
| In the tray | 521 → 21 (−96%) | 68 → 37 (−45%) | 0.02 → 0.00 | 100 → 5 (−95%) |
| Window open | 521 → 33 (−94%) | 69 → 61 (−11%) | 0.51 → 0.01 (−98%) | 620 → 73 (−88%) |
| Recording, in the tray | 521 → 21 (−96%) | 68 → 37 (−45%) | 0.03 → 0.00 | 120 → 6 (−95%) |

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
as possible (bundled FFmpeg `-benchmark`). Quality is VMAF of what a viewer sees at each of the 600 captured
moments (both sides at a constant 5 FPS, so skipped or mistimed frames count against it).

| Format | File size (MB/hr) | Quality (VMAF) | CPU time (s) | FFmpeg peak RAM (MB) | Speed (× real time) | Mistimed frames |
|---|---|---|---|---|---|---|
| Compatible (H.264) | 46.1 → 44.7 (−3%) | 97.87 → 97.81 | 14.7 → 15.0 (+2%) | 375 → 286 (−24%) | 43.5 → 43.9 | 0 → 0 of 398 |
| Smaller (H.265) | 57.4 → 57.5 (0%) | 94.49 → 94.89 (+0.4) | 22.1 → 24.6 (+11%) | 508 → 495 (−3%) | 13.6 → 12.9 | 0 → 0 of 398 |
| Smallest (AV1) | 26.0 → 25.5 (−2%) | 96.12 → 96.18 | 18.0 → 18.6 (+3%) | 616 → 490 (−21%) | 32.0 → 33.1 | 0 → 0 of 398 |
| Ultra (H.266) | 19.6 → 19.7 (+1%) | 23.85 → 23.82 ¹ | 55.4 → 41.6 (−25%) | 699 → 624 (−11%) | 7.2 → 11.6 (+61%) | 332 → 332 of 398 ¹ |
| Low CPU (NVIDIA AV1) | 38.5 → 39.2 (+2%) | 97.90 → 97.92 | 11.6 → 11.1 (−4%) | 613 → 604 (−1%) | 47.9 → 62.5 | 0 → 0 of 398 |

What changed: x264 look-ahead 50 → 10 frames, SVT-AV1 mini-GOP 32 → 16 frames (`hierarchical-levels=4`), VVenC
3 threads instead of 4, and frames are compared after downscaling instead of at full resolution. Each encoder
setting was picked from a sweep on this sample and kept only if files grew ≤3% and VMAF dropped ≤0.3. x265's
look-ahead was left alone because shortening it cost 0.6 VMAF.

¹ **Ultra's timestamps are broken in both versions.** FFmpeg's VVenC wrapper assumes one timestamp tick per
frame, but FrameKeep records variable frame rate with millisecond timestamps, so VVenC produces invalid decode
times. In MP4, 332 of 398 frames end up shown at the wrong moment (a 20-second jump after a still stretch). That
is why its VMAF is 24. 3.1 adds a fix for FFmpeg ([tools/patches/vvenc-vfr-timestamps.patch](../../tools/patches/vvenc-vfr-timestamps.patch));
it takes effect once the bundled FFmpeg is rebuilt.

## Live recording

`python tools/measure.py run --parts pipeline` feeds the sample to each version's live command in real time
(`-re`) and samples FFmpeg every second. Memory is stable run to run:

| Format | FFmpeg memory, mean (MB) | FFmpeg memory, peak (MB) | File size (MB/hr) |
|---|---|---|---|
| Compatible (H.264) | 330 → 249 (−25%) | 342 → 253 (−26%) | 46.1 → 44.7 (−3%) |
| Smaller (H.265) | 457 → 446 (−3%) | 473 → 463 (−2%) | 57.4 → 58.6 (+2%) |
| Smallest (AV1) | 550 → 426 (−22%) | 558 → 430 (−23%) | 26.0 → 25.5 (−2%) |
| Ultra (H.266) | 628 → 559 (−11%) | 668 → 595 (−11%) | 19.6 → 19.7 (+1%) |
| Low CPU (NVIDIA AV1) | 582 → 571 (−2%) | 583 → 571 (−2%) | 38.5 → 39.2 (+2%) |

**Live CPU** is too small to compare across separate runs: at around 1% of the PC, the clock speed Windows picks
drifts enough that the same 3.0.0 command measured 0.68% in one run and 0.90–1.06% twenty minutes later. So it was
measured with `tools/measure_ab.py`, alternating 3.0.0 and 3.1.0 back to back (ABBA order, two rounds each):

| Format | 3.0.0 runs (% of PC) | 3.1.0 runs (% of PC) | Median change |
|---|---|---|---|
| Compatible (H.264) | 1.06, 0.90 | 0.94, 0.69 | −17% |
| Smaller (H.265) | 1.03, 1.40 | 1.17, 1.39 | +6% |
| Smallest (AV1) | 1.06, 1.27 | 1.26, 1.24 | +7% |
| Ultra (H.266) | 7.58, 7.02 | 7.20, 7.01 | −3% |
| Low CPU (NVIDIA AV1) | 0.63, 0.55 | 0.52, 0.73 | +5% |

The ranges overlap for every format, so 3.1 doesn't measurably change live encoder CPU. Its encoder gains are
memory and (for H.264/AV1) file size.

## Tried and dropped

- **Windows efficiency mode (EcoQoS) for FFmpeg.** It schedules the encoder on efficiency cores at lower clocks.
  Measured live, it *raised* CPU time 35–70% (AV1 0.76% → 1.28%) and VVenC's context switches to ~22,000/s.
  Not shipped.
- **Low-delay VVenC** (no frame reordering) fixes Ultra's timestamps and cuts its memory 37%, but files grew 20%.
  **Constant-frame-rate input to VVenC** also fixes timestamps, but files grew 17% and CPU rose 20%. Both failed
  the storage-first rule; the FFmpeg patch fixes the timestamps without either cost.
- **Shorter x265 look-ahead:** −11% memory but −0.6 VMAF.

## Reproducing

```
pip install psutil
python tools/measure.py sample                     # needs a full FFmpeg on PATH for this one step
git worktree add build/perf/v3.0.0 v3.0.0          # or the 3.0.0 commit
python tools/measure.py run --repo build/perf/v3.0.0 --out docs/perf/3.0.0.json
python tools/measure.py run --out docs/perf/3.1.0.json
python tools/measure.py compare docs/perf/3.0.0.json docs/perf/3.1.0.json
python tools/measure_ab.py --a build/perf/v3.0.0 --b . --rounds 2 --out docs/perf/ab-live.json
```

The app test briefly opens a FrameKeep window and records the screen into `build/perf` (deleted afterwards).
Nothing is written to your real settings or recordings folders.
