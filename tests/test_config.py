import json
from framekeep import config

V2 = {"log_root": "D:/rec", "fps": 5, "downscale_factor": 0.5, "codec": "H264", "chunk_minutes": 60,
      "convert_to_mp4": True, "use_crf": True, "crf_value": 23}

def test_migrate_v2_keeps_user_values_and_maps_crf():
    cfg = config.validate(config.migrate(dict(V2)))
    assert cfg["log_root"] == "D:/rec"
    assert cfg["fps"] == 5 and cfg["downscale_factor"] == 0.5 and cfg["chunk_minutes"] == 60
    assert cfg["quality"] == "balanced"
    assert cfg["schema_version"] == config.SCHEMA_VERSION
    assert not cfg["onboarded"]  # upgraders see the wizard once
    for old in ("codec", "convert_to_mp4", "use_crf", "crf_value"):
        assert old not in cfg

def test_crf_mapping():
    assert [config.crf_to_quality(c) for c in (18, 23, 28, 35)] == ["high", "balanced", "saver", "max_saver"]
    assert config.validate(config.migrate(V2 | {"use_crf": False}))["quality"] == "high"

def test_validate_clamps_and_repairs():
    cfg = config.validate({"fps": 500, "downscale_factor": 0, "chunk_minutes": "abc", "preset": "nope",
                           "monitors": [], "private_apps": [" KeePass.exe ", ""], "draw_mouse": 0})
    assert cfg["fps"] == 30
    assert cfg["downscale_factor"] == 0.1
    assert cfg["chunk_minutes"] == config.DEFAULT_CONFIG["chunk_minutes"]
    assert cfg["preset"] == config.DEFAULT_CONFIG["preset"]
    assert cfg["monitors"] == ["primary"]
    assert cfg["private_apps"] == ["keepass.exe"]
    assert cfg["draw_mouse"] is False

def test_save_and_load_roundtrip():
    cfg = config.validate({"log_root": "C:/x", "preset": "ultra"})
    config.save_config(cfg)
    assert config.load_config() == cfg
    with open(config.CONFIG_FILE) as f:
        assert json.load(f)["preset"] == "ultra"
