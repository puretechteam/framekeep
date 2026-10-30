import pytest
from framekeep import benchmark, stats
from framekeep.config import DEFAULT_CONFIG, validate
from framekeep.hotkeys import parse_hotkey
from framekeep.monitors import Monitor, select_monitors
from framekeep.watchers import pause_reason

A = Monitor(1, "A", (0, 0, 10, 10), (0, 0, 10, 10), True, 0)
B = Monitor(2, "B", (10, 0, 10, 10), (10, 0, 10, 10), False, 1)

def test_select_monitors():
    assert select_monitors(["primary"], [A, B]) == [A]
    assert select_monitors(["all"], [A, B]) == [A, B]
    assert select_monitors(["B"], [A, B]) == [B]
    assert select_monitors(["gone"], [A, B]) == [A]  # unplugged display falls back to primary
    assert select_monitors(["primary"], []) == []

def test_pause_reason_priority():
    cfg = validate(dict(DEFAULT_CONFIG) | {"idle_pause_minutes": 5, "private_apps": ["keepass.exe"]})
    assert pause_reason(cfg, False, "code.exe", 10) is None
    assert pause_reason(cfg, False, "code.exe", 301) == "idle"
    assert pause_reason(cfg, False, "keepass.exe", 301) == "private"
    assert pause_reason(cfg, True, "keepass.exe", 301) == "locked"
    assert pause_reason(cfg | {"pause_on_lock": False, "idle_pause_minutes": 0}, True, "x.exe", 9999) is None

def test_parse_hotkey():
    assert parse_hotkey("ctrl+alt+f9") == (0x2 | 0x1, 0x78)
    assert parse_hotkey("Shift + R") == (0x4, ord("R"))
    assert parse_hotkey("f8") == (0, 0x77)
    for bad in ("", "ctrl+", "hyper+x", "q"):
        with pytest.raises(ValueError):
            parse_hotkey(bad)

def r(kbps, cpu, rt=10, vmaf=96.0):
    return {"kbps": kbps, "mb_per_hour": kbps * 0.45, "cpu_percent": cpu, "realtime": rt, "vmaf": vmaf}

def test_recommend_picks_smallest_light_enough_preset():
    results = {"compatible": r(150, 2, vmaf=97), "smallest": r(80, 3), "ultra": r(50, 5)}
    assert benchmark.recommend(results) == "ultra"
    results["ultra"] = r(50, 40)  # too heavy
    assert benchmark.recommend(results) == "smallest"
    results["smallest"] = r(80, 3, vmaf=90)  # below the quality floor
    assert benchmark.recommend(results) == "compatible"

def test_recommend_uses_absolute_quality_floor():
    """A real high-motion run: AV1 at 94.2 must win even though H.264 scored 96.4."""
    results = {"compatible": r(362.8, 1.4, vmaf=96.4), "smaller": r(318.3, 1.3, vmaf=92.4),
               "smallest": r(154.7, 1.0, vmaf=94.2), "ultra": r(135.6, 4.8, vmaf=87.6), "lowcpu": r(277.4, 0.4, vmaf=93.6)}
    assert benchmark.recommend(results) == "smallest"
    why = benchmark.reasons(results, "smallest")
    assert why["ultra"][0] == "Lower quality" and why["compatible"][0] == "Bigger" and why["smallest"][0] == "Recommended"
    text = benchmark.explain({"results": results, "recommended": "smallest"})
    assert "Ultra is smaller, but quality 87.6" in text and "—" not in text

def test_library_summary(tmp_path):
    day = tmp_path / "2026-10-25"
    day.mkdir()
    (day / "10-00-00.mp4").write_bytes(b"x" * 1000)
    (day / "notes.txt").write_text("ignored")
    (tmp_path / "not-a-day").mkdir()
    s = stats.library_summary(str(tmp_path))
    assert s == {"total": 1000, "files": 1, "days": 1, "today": 0, "today_files": 0}
    assert stats.human_bytes(1536) == "2 KB" and stats.human_bytes(5 * 1024 ** 2) == "5.0 MB"
    assert stats.human_duration(3725) == "1h 02m"

def test_ram_heavy_encoders_are_not_recommended():
    results = {"compatible": r(150, 2, vmaf=97), "ultra": r(50, 5) | {"ram_mb": 900}}
    assert benchmark.problem(results["ultra"], ram_limit=1000) is None
    assert benchmark.problem(results["ultra"], ram_limit=800) == "heavy"
    assert benchmark.problem(r(50, 5), ram_limit=1) is None   # reports from before 3.1 have no RAM figure

def test_library_summary_caches_finished_days(tmp_path, monkeypatch):
    day = tmp_path / "2020-01-01"
    day.mkdir()
    (day / "10-00-00.mp4").write_bytes(b"x" * 100)
    assert stats.library_summary(str(tmp_path))["total"] == 100
    scans = []
    real = stats.list_chunks
    monkeypatch.setattr(stats, "list_chunks", lambda p: scans.append(p) or real(p))
    assert stats.library_summary(str(tmp_path))["total"] == 100
    assert scans == []                          # unchanged past day: served from the cache
    (day / "11-00-00.mp4").write_bytes(b"x" * 50)
    os_stat_bump(day)
    assert stats.library_summary(str(tmp_path))["total"] == 150 and scans  # new file: rescanned
    live = str(day / "11-00-00.mp4")
    assert stats.library_summary(str(tmp_path), exclude=[live])["total"] == 100  # a file still being written

def os_stat_bump(path):
    """Some file systems keep coarse folder mtimes; make sure the change is visible."""
    import os
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

def test_prune_thumbs_keeps_recent_and_stays_in_its_folder(tmp_path):
    import os
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    for i in range(5):
        f = thumbs / f"{i}.jpg"
        f.write_bytes(b"x" * 400_000)
        os.utime(f, (1000 + i, 1000 + i))       # 4.jpg is the most recently used
    (thumbs / "keep.txt").write_bytes(b"x" * 900_000)    # not a thumbnail
    (tmp_path / "outside.jpg").write_bytes(b"x" * 900_000)
    assert stats.prune_thumbs(max_mb=1, folder=str(thumbs)) == 3
    assert sorted(p.name for p in thumbs.iterdir()) == ["3.jpg", "4.jpg", "keep.txt"]
    assert (tmp_path / "outside.jpg").exists()

def test_probe_cache_batches_writes(tmp_path):
    path = tmp_path / "probe.json"
    cache = stats.ProbeCache(str(path))
    chunk = {"path": "a.mp4", "size": 1, "mtime": 2}
    for i in range(20):
        cache.put(dict(chunk, size=i), {"codec": "vvc"})
    assert not path.exists()                    # nothing written yet: one write later, not twenty now
    cache.flush()
    assert stats.ProbeCache(str(path)).get(dict(chunk, size=7)) == {"codec": "vvc"}

def test_watcher_sleeps_until_something_can_change(monkeypatch):
    from framekeep import watchers
    class Rec:
        wanted = False
    cfg = validate(dict(DEFAULT_CONFIG) | {"idle_pause_minutes": 5})
    w = watchers.Watcher(Rec(), lambda: cfg)
    assert w._timeout_ms() == watchers.INFINITE          # not recording: only lock/foreground events wake it
    Rec.wanted = True
    monkeypatch.setattr(watchers, "idle_seconds", lambda: 299)
    assert w._timeout_ms() == 1000                        # idle limit reached in 1 s
    monkeypatch.setattr(watchers, "idle_seconds", lambda: 10)
    assert w._timeout_ms() == watchers.Watcher.SAFETY * 1000
    monkeypatch.setattr(watchers, "idle_seconds", lambda: 400)
    assert w._timeout_ms() == watchers.Watcher.AWAY_POLL * 1000   # away: notice the return quickly
