"""纯函数测试（不需要 GPU / 模型 / 界面事件循环）。

跑法：
    python -m pytest tests -q
    python -m pytest tests -q -k split      # 只跑某一类

这些断言的作用是「改功能时别把底层逻辑改坏」，不追求覆盖率。
GPU 相关的回归在 tools/ 下（e2e_test / api_test / mcp_test / cancel_test）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voxcpm_studio.api import ApiError, _materialize_audio, _params_from  # noqa: E402
from voxcpm_studio.config import AppConfig, MODES  # noqa: E402
from voxcpm_studio.engine import (GenerationParams, TTSEngine,  # noqa: E402
                                  split_text)
from voxcpm_studio.presets import (CLONE_TAGS, DESIGN_TAGS, QUICK_COMBOS,  # noqa: E402
                                   compose)
from voxcpm_studio.widgets import WaveformWidget, elide_middle  # noqa: E402


# ------------------------------------------------------------------ #
# 文本切分
# ------------------------------------------------------------------ #
def test_split_short_text_is_untouched():
    assert split_text("你好。", 120) == ["你好。"]


def test_split_respects_max_chars():
    text = "第一句。" * 30
    parts = split_text(text, 20)
    assert all(len(p) <= 20 for p in parts), [len(p) for p in parts]
    assert "".join(parts) == text


def test_split_keeps_sentence_boundaries():
    parts = split_text("这是第一句话。这是第二句话。这是第三句话。", 12)
    assert len(parts) > 1
    for p in parts[:-1]:
        assert p.endswith("。"), p


def test_split_no_empty_pieces():
    parts = split_text("。。。\n\n第二段。  ", 10)
    assert all(p.strip() for p in parts)


def test_split_zero_means_disabled():
    text = "长" * 500
    assert split_text(text, 0) == [text]


def test_split_single_huge_sentence_gets_cut():
    text = "字" * 100  # 没有任何标点
    parts = split_text(text, 30)
    assert "".join(parts) == text
    assert all(len(p) <= 30 for p in parts)


# ------------------------------------------------------------------ #
# 描述组合
# ------------------------------------------------------------------ #
def test_compose_follows_dimension_order_not_click_order():
    tags = {"口音": "台湾腔", "性别年龄": "年轻女性", "音色": "温柔甜美"}
    assert compose(tags, list(DESIGN_TAGS)) == "年轻女性，温柔甜美，台湾腔"


def test_compose_skips_empty_dimensions():
    tags = {"性别年龄": "成熟男性", "音色": "", "情绪": "平静"}
    assert compose(tags, list(DESIGN_TAGS)) == "成熟男性，平静"


def test_compose_empty_is_empty_string():
    assert compose({}, list(DESIGN_TAGS)) == ""


def test_presets_are_non_empty_and_unique():
    for groups in (DESIGN_TAGS, CLONE_TAGS):
        for dim, tags in groups.items():
            assert tags, f"{dim} 维度为空"
            assert len(tags) == len(set(tags)), f"{dim} 有重复标签"
    assert set(QUICK_COMBOS) == {"设计", "克隆"}


def test_clone_presets_have_no_timbre_dimension():
    # 克隆模式下音色由参考音频决定，给音色标签只会干扰模型
    assert "音色" not in CLONE_TAGS
    assert "性别年龄" not in CLONE_TAGS


# ------------------------------------------------------------------ #
# 描述 → 提示
# ------------------------------------------------------------------ #
@pytest.mark.parametrize(
    "text,control,expected",
    [
        ("你好", None, "你好"),
        ("你好", "", "你好"),
        ("你好", "  温柔  ", "(温柔)你好"),
        ("Hello", "young woman", "(young woman)Hello"),
    ],
)
def test_build_text(text, control, expected):
    assert TTSEngine.build_text(text, control) == expected


# ------------------------------------------------------------------ #
# 默认参数
# ------------------------------------------------------------------ #
def test_quality_first_defaults():
    p = GenerationParams()
    assert p.cfg_value == 3.0
    assert p.inference_timesteps == 30
    cfg = AppConfig()
    assert (cfg.cfg_value, cfg.inference_timesteps) == (3.0, 30)
    assert cfg.retry_badcase is True      # 勾上重试 = 走一次性生成，重试才生效
    assert cfg.theme in ("light", "dark")


def test_modes_cover_three_paths():
    assert set(MODES) == {"design", "clone", "ultimate"}


# ------------------------------------------------------------------ #
# 配置往返
# ------------------------------------------------------------------ #
def test_config_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    cfg = AppConfig()
    cfg.cfg_value = 2.4
    cfg.api_enabled = True
    cfg.api_port = 8811
    cfg.save(path)
    loaded = AppConfig.load(path)
    assert loaded.cfg_value == 2.4
    assert loaded.api_enabled is True
    assert loaded.api_port == 8811


def test_config_survives_corrupt_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ not json", encoding="utf-8")
    assert AppConfig.load(path).cfg_value == 3.0   # 坏文件 → 回默认值


# ------------------------------------------------------------------ #
# API 参数校验
# ------------------------------------------------------------------ #
def test_api_params_accept_recommended_range():
    p = _params_from({"params": {"cfg_value": 3.0, "inference_timesteps": 30}}, None)
    assert (p.cfg_value, p.inference_timesteps) == (3.0, 30)


@pytest.mark.parametrize("bad", [{"cfg_value": 9.0}, {"inference_timesteps": 2}, {"max_len": 99999}])
def test_api_params_reject_out_of_range(bad):
    with pytest.raises(ApiError) as exc:
        _params_from({"params": bad}, None)
    assert exc.value.status == 400
    assert exc.value.payload["error"] == "bad_params"


def test_api_stream_flag_disables_retry():
    p = _params_from({"stream": True}, None)
    assert p.retry_badcase is False


def test_api_reference_must_exist(tmp_path):
    with pytest.raises(ApiError) as exc:
        _materialize_audio(str(tmp_path / "nope.wav"), tmp_path)
    assert exc.value.status == 400


def test_api_reference_accepts_base64(tmp_path):
    import base64

    payload = base64.b64encode(b"RIFFfake").decode()
    path = _materialize_audio(f"data:audio/wav;base64,{payload}", tmp_path)
    assert Path(path).read_bytes() == b"RIFFfake"


# ------------------------------------------------------------------ #
# 其它
# ------------------------------------------------------------------ #
def test_optimize_probe_is_honest():
    # 不勾 → 关闭；CPU → 关闭；本机无 triton → 关闭且给出原因
    assert TTSEngine.resolve_optimize(False, "cuda")[0] is False
    assert TTSEngine.resolve_optimize(True, "cpu")[0] is False
    enabled, reason = TTSEngine.resolve_optimize(True, "cuda")
    if not enabled:
        assert "triton" in reason or "CUDA" in reason


def test_compute_peaks_normalizes_and_matches_columns():
    wav = np.sin(np.linspace(0, 100, 4800)).astype(np.float32)
    mins, maxs = WaveformWidget._compute_peaks(wav, 120)
    assert len(mins) == len(maxs) > 0
    assert np.abs(mins).max() <= 1.0 + 1e-6
    assert np.abs(maxs).max() <= 1.0 + 1e-6
    assert np.abs(maxs).max() > 0.9          # 峰值被归一化到 1 附近


def test_compute_peaks_handles_empty():
    mins, maxs = WaveformWidget._compute_peaks(np.zeros(0, dtype=np.float32), 50)
    assert len(mins) == 50 and not mins.any() and not maxs.any()


@pytest.mark.parametrize(
    "text,limit,expected",
    [
        ("short", 10, "short"),
        ("0123456789abcdef", 9, "012…def"),
        ("0123456789abcdef", 12, "0123…cdef"),
        ("0123456789abcdef", 10, "012…def"),   # 保留长度取整后可能比 limit 短 1
    ],
)
def test_elide_middle(text, limit, expected):
    assert elide_middle(text, limit) == expected
