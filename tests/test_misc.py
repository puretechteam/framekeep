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
