"""OpenAI 辨識引擎與引擎切換的單元測試（不連網，用假 client）。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import Settings
from src.ocr_provider import OpenAIProvider, _openai_strict_schema, build_ocr_provider


def _settings(**overrides) -> Settings:
    base = Settings(
        google_cloud_project="test-proj",
        google_cloud_location="global",
        vertex_model="gemini-2.5-flash",
        drive_folder_pending="p",
        drive_folder_done="d",
        drive_folder_review="r",
        drive_folder_failed="f",
        spreadsheet_id="s",
        auth_mode="oauth",
        credentials_path=Path("c.json"),
        token_path=Path("t.json"),
        timezone="Asia/Taipei",
        lock_file_path=Path("run.lock"),
        temp_dir=Path("work"),
        max_retries=1,
    )
    return replace(base, **overrides)


class FakeResponses:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.output_text)


def _walk_keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_keys(v)


def test_strict_schema_removes_unsupported_keywords():
    schema = _openai_strict_schema()
    keys = set(_walk_keys(schema))
    assert not keys & {"default", "pattern", "minimum"}
    voucher = schema["$defs"]["RawVoucherExtraction"]
    assert voucher["additionalProperties"] is False
    assert set(voucher["required"]) == set(voucher["properties"])


def test_openai_provider_parses_structured_output():
    payload = {
        "vouchers": [
            {
                "voucher_index_on_page": 1,
                "continues_from_prev_page": False,
                "continuation_uncertain": False,
                "voucher_type": "發票",
                "raw_date": "115/09/19",
                "invoice_number": "AB12345678",
                "buyer_name": None,
                "buyer_tax_id": None,
                "seller_name": "測試飲料店",
                "seller_tax_id": "04123456",
                "amount_untaxed": None,
                "tax_amount": None,
                "amount_total": 200,
                "amount_chinese": None,
                "items": [],
                "unclear_fields": ["品名第1項字跡淡化"],
            }
        ],
        "page_unreadable_reason": None,
    }
    responses = FakeResponses(json.dumps(payload, ensure_ascii=False))
    provider = OpenAIProvider(model="test-model", client=SimpleNamespace(responses=responses))

    result = provider.extract_page(b"\x89PNG", "image/png", 1)

    voucher = result.vouchers[0]
    assert voucher.seller_tax_id == "04123456"
    assert str(voucher.amount_total) == "200"
    assert voucher.unclear_fields == ["品名第1項字跡淡化"]

    call = responses.calls[0]
    assert call["model"] == "test-model"
    assert call["text"]["format"]["strict"] is True
    image_part = call["input"][0]["content"][0]
    assert image_part["type"] == "input_image"
    assert image_part["image_url"].startswith("data:image/png;base64,")


def test_openai_provider_empty_output_marks_page_unreadable():
    provider = OpenAIProvider(client=SimpleNamespace(responses=FakeResponses("")))
    result = provider.extract_page(b"x", "image/jpeg", 2)
    assert result.vouchers == []
    assert "OpenAI" in (result.page_unreadable_reason or "")


def test_build_provider_openai_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        build_ocr_provider(_settings(ocr_provider="openai"))


def test_build_provider_openai_with_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    provider = build_ocr_provider(_settings(ocr_provider="openai", openai_model="m1"))
    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "m1"


def test_build_provider_vertex_requires_project():
    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
        build_ocr_provider(_settings(google_cloud_project=""))


def test_build_provider_rejects_unknown_name():
    with pytest.raises(ValueError, match="vertex 或 openai"):
        build_ocr_provider(_settings(ocr_provider="claude"))


def test_drive_run_without_gcp_project_ok_for_openai():
    _settings(ocr_provider="openai", google_cloud_project="").validate_for_drive_run()
    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
        _settings(google_cloud_project="").validate_for_drive_run()


def _bare_vertex(model, level):
    """不建立 genai.Client，只測思考設定邏輯。"""
    from src.ocr_provider import VertexAIGeminiProvider
    p = VertexAIGeminiProvider.__new__(VertexAIGeminiProvider)
    p.model, p.thinking_level = model, level
    return p


def test_vertex_thinking_level_low_for_gemini3():
    cfg = _bare_vertex("gemini-3-flash-preview", "low")._thinking_config()
    assert cfg is not None
    assert str(cfg.thinking_level).lower().endswith("low")


def test_vertex_thinking_level_skipped_for_gemini2_or_empty():
    assert _bare_vertex("gemini-2.5-flash", "low")._thinking_config() is None
    assert _bare_vertex("gemini-3-flash-preview", "")._thinking_config() is None


def test_settings_default_thinking_level_is_low(monkeypatch):
    from src.config import Settings
    monkeypatch.delenv("VERTEX_THINKING_LEVEL", raising=False)
    assert Settings.from_env("__no_such_env__").vertex_thinking_level == "low"
