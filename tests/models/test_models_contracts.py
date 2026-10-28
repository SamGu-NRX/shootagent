"""Coordinate conversion, prompt/request formats, mask cleanup and the judge's verdict rule."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from shootagent.models import inpaint, segment, vision

# ---- segment ------------------------------------------------------------------------------------------------


def test_normalize_prompt_produces_grounding_dino_format():
    assert segment.normalize_prompt("Car, Trash  Bin;lamp.") == "car. trash bin. lamp."
    with pytest.raises(ValueError):
        segment.normalize_prompt(" . , ")


def test_expand_phrases_reports_the_users_phrase_for_synonyms():
    pairs = segment.expand_phrases("trash bin. sofa")
    assert ("trash bin", "trash bin") in pairs and ("trash can", "trash bin") in pairs
    assert ("sofa", "sofa") in pairs
    assert len(pairs) == len({q for q, _ in pairs})


def test_norm_to_px_converts_clips_and_rejects_inverted_boxes():
    assert segment.norm_to_px([0.25, 0.5, 0.75, 1.0], 400, 200) == (100, 100, 300, 200)
    assert segment.norm_to_px([-0.1, -0.1, 1.2, 1.2], 400, 200) == (0, 0, 400, 200)
    x0, y0, x1, y1 = segment.norm_to_px([0.5, 0.5, 0.5001, 0.5001], 400, 200)
    assert x1 > x0 and y1 > y0  # sub-pixel boxes still cover a pixel
    with pytest.raises(ValueError, match="inverted"):
        segment.norm_to_px([0.6, 0.1, 0.4, 0.2], 400, 200)


def test_clean_mask_fills_enclosed_holes_drops_specks_keeps_open_background():
    m = np.zeros((100, 100), bool)
    m[20:60, 20:60] = True
    m[35:45, 35:45] = False  # enclosed hole: filled
    m[90:92, 90:92] = True  # speck far away: dropped
    m[0:10, 70:100] = True  # second real object (large enough): kept
    out = segment.clean_mask(m)
    assert out[40, 40] and not out[91, 91] and out[5, 80]
    assert not out[70, 10]  # open background stays background


def test_nms_keeps_the_best_of_overlapping_boxes():
    a = segment.Detection((0.1, 0.1, 0.5, 0.5), 0.6, "bin")
    b = segment.Detection((0.12, 0.1, 0.5, 0.52), 0.8, "trash can")
    c = segment.Detection((0.6, 0.6, 0.9, 0.9), 0.4, "bin")
    assert segment.nms_detections([a, b, c]) == [b, c]


def test_refine_crop_covers_small_boxes_inside_the_frame_and_skips_big_ones():
    w, h = 7000, 4600
    crop = segment.refine_crop((6900, 4500, 6990, 4590), w, h)
    x0, y0, x1, y1 = crop
    assert 0 <= x0 <= 6900 and x1 == 7000 and 0 <= y0 <= 4500 and y1 == 4600
    assert x1 - x0 >= segment.REFINE_MIN_SIDE
    assert segment.refine_crop((0, 0, 4000, 3000), w, h) is None


# ---- inpaint ------------------------------------------------------------------------------------------------


def test_hole_thickness_caps_working_scale():
    hole = np.zeros((1000, 1000), bool)
    hole[300:700, 100:900] = True  # 400 px thick
    assert inpaint.hole_thickness(hole) == pytest.approx(400, abs=3)
    assert inpaint.work_scale((1000, 1000), hole) == pytest.approx(inpaint.MAX_HOLE_THICKNESS / 400, rel=0.02)
    thin = np.zeros((1000, 1000), bool)
    thin[500:510, :] = True  # a cord: full resolution
    assert inpaint.work_scale((1000, 1000), thin) == pytest.approx(1.0)


def test_ensure_lama_refuses_a_tampered_file_at_a_pinned_url(monkeypatch, tmp_path):
    from shootagent.config import Settings

    settings = Settings(data_dir=tmp_path, lama_url=next(iter(inpaint.PINNED_SHA256)))
    monkeypatch.setattr(inpaint, "get_settings", lambda: settings)
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "big-lama.pt").write_bytes(b"not the weights")
    with pytest.raises(RuntimeError, match="SHA-256"):
        inpaint.ensure_lama()


# ---- vision -------------------------------------------------------------------------------------------------


def test_encode_image_downscales_to_claude_limit_keeping_aspect():
    _data, (w, h) = vision.encode_image(np.zeros((3000, 6000, 3), np.uint8))
    assert (w, h) == (1568, 784)
    _data, (w, h) = vision.encode_image(np.zeros((300, 400, 3), np.uint8))
    assert (w, h) == (400, 300)


@pytest.mark.parametrize(("model", "choice"), [("claude-sonnet-5-5", "auto"), ("claude-opus-5-5", "auto"), ("claude-haiku-5-5", "tool")])
def test_build_request_does_not_force_tools_on_models_that_reject_it(model, choice):
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
    req = vision.build_request([("a", np.zeros((10, 10, 3), np.uint8))] * 2, "q", schema, model, 1000, "low", None)
    assert req["tool_choice"]["type"] == choice
    assert req["tools"][0]["input_schema"] is schema and req["tools"][0]["strict"] is True
    content = req["messages"][0]["content"]
    assert [b["type"] for b in content] == ["text", "image", "text", "image", "text"]
    assert content[2]["text"].startswith("Image 2: a")


def fake_response(stop_reason="tool_use", tool_input=None, text=None, cache_read=0):
    content = []
    if text:
        content.append(SimpleNamespace(type="text", text=text))
    if tool_input is not None:
        content.append(SimpleNamespace(type="tool_use", name=vision.TOOL_NAME, input=tool_input))
    usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=cache_read, cache_creation_input_tokens=0)
    return SimpleNamespace(stop_reason=stop_reason, content=content, usage=usage, stop_details=None)


def test_parse_response_returns_tool_input_and_prices_cache_reads():
    answer, usage = vision.parse_response(fake_response(tool_input={"ok": True}, cache_read=5000), "claude-sonnet-5-5")
    assert answer == {"ok": True}
    assert usage.input_tokens == 6000 and usage.output_tokens == 200
    assert usage.usd == pytest.approx((1000 * 2 + 5000 * 0.2 + 200 * 10) / 1e6)


@pytest.mark.parametrize(
    ("resp", "match"),
    [
        (fake_response(stop_reason="end_turn", text="It looks fine."), "without calling"),
        (fake_response(stop_reason="refusal"), "refused"),
        (fake_response(stop_reason="max_tokens"), "max_tokens"),
    ],
)
def test_parse_response_raises_instead_of_guessing(resp, match):
    with pytest.raises(RuntimeError, match=match):
        vision.parse_response(resp, "claude-sonnet-5-5")


def answer(verdict="pass", intent=True, photoreal=True, outside=True, artifacts=()):
    return {"intent_achieved": intent, "photoreal": photoreal, "outside_unchanged": outside,
            "artifacts": [{"description": d, "severity": s} for d, s in artifacts], "verdict": verdict, "reason": "r"}


def test_verdict_fails_on_a_major_artifact_even_when_the_model_says_pass():
    verdict, major, minor = vision.combine_verdict(answer(artifacts=[("dark smear on floor", "major"), ("soft edge", "minor")]))
    assert verdict == "fail" and major == ["dark smear on floor"] and minor == ["soft edge"]


def test_verdict_passes_with_only_minor_artifacts_and_fails_on_any_false_check():
    assert vision.combine_verdict(answer(artifacts=[("soft edge", "minor")]))[0] == "pass"
    assert vision.combine_verdict(answer(intent=False))[0] == "fail"
    assert vision.combine_verdict(answer(outside=False))[0] == "fail"
    assert vision.combine_verdict(answer(verdict="fail"))[0] == "fail"


def test_judge_crop_box_contains_the_mask_inside_the_frame():
    m = np.zeros((2000, 3000), bool)
    m[1900:2000, 0:50] = True
    x0, y0, x1, y1 = vision.judge_crop_box(m)
    assert x0 == 0 and y1 == 2000 and y0 <= 1900 and x1 >= 50
    assert x1 - x0 >= vision.MIN_CROP and y1 - y0 >= vision.MIN_CROP
