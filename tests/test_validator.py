"""確定性檢核與換算邏輯測試。"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from src.models import RawVoucherExtraction, RawVoucherItem
from src.validator import (
    build_voucher_records,
    detect_business_duplicate,
    format_iso_taipei,
    normalize_id,
    generate_unique_code,
    parse_chinese_uppercase_amount,
    parse_roc_or_ad_date,
)


def test_format_iso_taipei_unified():
    utc_moment = dt.datetime(2026, 11, 14, 10, 16, 8, tzinfo=dt.timezone.utc)
    assert format_iso_taipei(utc_moment, "Asia/Taipei") == "2026-11-14 18:16:08"


def test_generate_unique_code_format():
    moment = dt.datetime(2026, 11, 13, 23, 20, 0)
    code1 = generate_unique_code(moment)
    code2 = generate_unique_code(moment)
    assert code1 != code2
    assert re.match(
        r"^EXP-20261113-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        code1,
    )


def test_parse_roc_and_ad_dates():
    iso, note = parse_roc_or_ad_date("115/11/13")
    assert iso == "2026-11-13"
    assert "民國 115 年" in note

    iso2, _ = parse_roc_or_ad_date("中華民國115年02月28日")
    assert iso2 == "2026-02-28"

    iso3, note3 = parse_roc_or_ad_date("2026-11-13")
    assert iso3 == "2026-11-13"
    assert note3 == ""

    # 非法日期不可猜測
    bad_iso, bad_note = parse_roc_or_ad_date("115/02/30")
    assert bad_iso == ""
    assert "不合法" in bad_note


def test_parse_chinese_uppercase_amount():
    assert parse_chinese_uppercase_amount("新臺幣壹萬貳仟參佰肆拾伍元整") == Decimal("12345")
    assert parse_chinese_uppercase_amount("參拾萬零伍佰元正") == Decimal("300500")
    assert parse_chinese_uppercase_amount("拾伍元") == Decimal("15")
    assert parse_chinese_uppercase_amount("壹仟零伍元參角貳分") == Decimal("1005.32")
    assert parse_chinese_uppercase_amount("零元整") == Decimal("0")
    assert parse_chinese_uppercase_amount("") is None
    assert parse_chinese_uppercase_amount("金額模糊不清") is None


def test_build_voucher_records_consistent_and_keeps_leading_zeros():
    extraction = RawVoucherExtraction(
        voucher_type="發票",
        raw_date="115/11/13",
        invoice_number="AB00123456",
        buyer_name="測試公司",
        buyer_tax_id="04123456",
        seller_name="範例商店",
        seller_tax_id="01234567",
        amount_untaxed=Decimal("1000"),
        tax_amount=Decimal("50"),
        amount_total=Decimal("1050"),
        amount_chinese="壹仟零伍拾元整",
        items=[
            RawVoucherItem(
                item_name="A4影印紙",
                quantity=Decimal("2"),
                unit="箱",
                unit_price=Decimal("525"),
                subtotal=Decimal("1050"),
            )
        ],
    )
    master, items = build_voucher_records(
        extraction=extraction,
        unique_code="EXP-20261113-test-uuid",
        created_at="2026-11-13T23:20:00+08:00",
        original_filename="inv.pdf",
        original_file_link="https://drive.google.com/file/d/123/view",
        page_info="第 1 頁",
        existing_masters=[],
    )
    assert master.recognition_status == "辨識完成"
    assert master.review_status == "待覆核"
    assert master.buyer_tax_id == "04123456"
    assert master.seller_tax_id == "01234567"
    assert master.voucher_date == "2026-11-13"
    assert master.raw_voucher_date == "115/11/13"
    assert master.chinese_converted_amount == "1050"
    assert master.amount_check_result == "一致"
    assert len(items) == 1
    assert items[0].item_seq == 1


def test_build_voucher_records_flags_mismatch_and_unclear():
    extraction = RawVoucherExtraction(
        voucher_type="收據",
        raw_date="115/11/13",
        invoice_number=None,
        seller_name="小吃店",
        seller_tax_id="01234567",
        amount_untaxed=Decimal("100"),
        tax_amount=Decimal("5"),
        amount_total=Decimal("200"),  # 不一致
        amount_chinese="壹佰零伍元整",
        unclear_fields=["買受人抬頭被遮擋"],
    )
    master, _ = build_voucher_records(
        extraction=extraction,
        unique_code="EXP-20261113-test-2",
        created_at="2026-11-13T23:20:00+08:00",
        original_filename="receipt.jpg",
        original_file_link="https://drive.google.com/file/d/456/view",
        page_info="第 1 頁",
        existing_masters=[],
    )
    assert master.recognition_status == "需人工確認"
    assert master.review_status == "待覆核"
    assert master.amount_check_result == "不一致"
    assert master.buyer_name == ""
    assert "買受人抬頭被遮擋" in master.notes


def test_detect_business_duplicate():
    existing = [
        {
            "唯一代碼": "EXP-20261113-existing-1",
            "發票／收據號碼": "AB12345678",
            "商家統編": "12345678",
            "商家名稱": "文具行",
            "憑證日期": "2026-11-13",
            "含稅金額": "500",
        }
    ]
    status, reason = detect_business_duplicate(
        invoice_number="ab12345678",
        seller_tax_id="12345678",
        seller_name="文具行",
        voucher_date="2026-11-13",
        amount_total="500",
        existing_masters=existing,
    )
    assert status == "疑似重複"
    assert "EXP-20261113-existing-1" in reason

    # 缺號碼時的輔助比對
    status2, reason2 = detect_business_duplicate(
        invoice_number="",
        seller_tax_id="",
        seller_name="文具行",
        voucher_date="2026-11-13",
        amount_total="500",
        existing_masters=existing,
    )
    assert status2 == "疑似重複"
    assert "缺發票號碼" in reason2


def test_normalize_id_removes_spaces_and_fullwidth():
    assert normalize_id("AB 12345678") == "AB12345678"
    assert normalize_id(" ａｂ　１２３４５６７８ ") == "AB12345678"
    assert normalize_id("8765 4321") == "87654321"
    assert normalize_id("AB-12345678") == "AB12345678"
    assert normalize_id("ab－１２３４５６７８") == "AB12345678"
    assert normalize_id(None) == ""


def test_detect_duplicate_ignores_spaces_between_engines():
    # Gemini 寫入的舊列沒有空格，OpenAI 這次回傳有空格，仍應判定疑似重複
    existing = [
        {
            "唯一代碼": "EXP-20261114-gemini-1",
            "發票／收據號碼": "AB12345678",
            "商家統編": "87654321",
        }
    ]
    status, _ = detect_business_duplicate(
        invoice_number="AB 12345678",
        seller_tax_id="87654321",
        seller_name="",
        voucher_date="2026-09-19",
        amount_total="200",
        existing_masters=existing,
    )
    assert status == "疑似重複"

    # 反過來：舊列含空格（歷史資料），新值沒有空格
    existing[0]["發票／收據號碼"] = "AB 12345678"
    status2, _ = detect_business_duplicate(
        invoice_number="AB12345678",
        seller_tax_id="87654321 ",
        seller_name="",
        voucher_date="2026-09-19",
        amount_total="200",
        existing_masters=existing,
    )
    assert status2 == "疑似重複"


def test_duplicate_matches_across_hyphen_and_space_formats():
    existing = [{"唯一代碼": "EXP-OLD", "發票／收據號碼": "AB-12345678", "商家統編": "12345678"}]
    status, note = detect_business_duplicate("AB 12345678", "12345678", "", "", "", existing)
    assert status == "疑似重複"
