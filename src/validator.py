"""確定性規則檢核與計算引擎（不讓 AI 推算數字、不覆蓋原文、不自動確認覆核狀態）。"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
import uuid
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from src.models import (
    RawVoucherExtraction,
    VoucherItemRecord,
    VoucherMasterRecord,
)

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

_CHINESE_DIGITS: dict[str, int] = {
    "零": 0,
    "〇": 0,
    "壹": 1,
    "一": 1,
    "貳": 2,
    "贰": 2,
    "兩": 2,
    "二": 2,
    "參": 3,
    "叁": 3,
    "参": 3,
    "三": 3,
    "肆": 4,
    "四": 4,
    "伍": 5,
    "五": 5,
    "陸": 6,
    "陆": 6,
    "六": 6,
    "柒": 7,
    "七": 7,
    "捌": 8,
    "八": 8,
    "玖": 9,
    "九": 9,
}

_SMALL_UNITS: dict[str, int] = {
    "拾": 10,
    "十": 10,
    "佰": 100,
    "百": 100,
    "仟": 1000,
    "千": 1000,
}

_BIG_UNITS: dict[str, int] = {
    "萬": 10_000,
    "万": 10_000,
    "億": 100_000_000,
    "亿": 100_000_000,
    "兆": 1_000_000_000_000,
}


def now_taipei(tz_name: str = "Asia/Taipei") -> dt.datetime:
    """取得含明確時區的當前時間。"""
    return dt.datetime.now(ZoneInfo(tz_name))


def format_iso_taipei(moment: dt.datetime | None = None, tz_name: str = "Asia/Taipei") -> str:
    """格式化為試算表與資料庫通用的 YYYY-MM-DD HH:MM:SS（依設定時區轉換，預設 Asia/Taipei）。"""
    tz = ZoneInfo(tz_name)
    if moment is None:
        target = now_taipei(tz_name)
    elif moment.tzinfo is None:
        target = moment.replace(tzinfo=tz)
    else:
        target = moment.astimezone(tz)
    return f"{target:%Y-%m-%d %H:%M:%S}"


def generate_unique_code(moment: dt.datetime | None = None, tz_name: str = "Asia/Taipei") -> str:
    """依 EXP-YYYYMMDD-UUIDv4 格式產生不可重複的唯一代碼。"""
    target = moment or now_taipei(tz_name)
    return f"EXP-{target:%Y%m%d}-{uuid.uuid4()}"


def _format_decimal(value: Decimal | None) -> str:
    """將 Decimal 格式化為乾淨的純文字數字，無值則回傳空字串。"""
    if value is None:
        return ""
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def parse_roc_or_ad_date(raw_text: str | None) -> tuple[str, str]:
    """解析民國或西元日期為 YYYY-MM-DD，保留原文不猜測。

    回傳 (西元日期字串, 檢查備註)。失敗時西元日期字串為空字串。
    """
    if not raw_text or not raw_text.strip():
        return "", ""

    text = raw_text.strip()
    cleaned = re.sub(r"^(中華民國|民國)", "", text).strip()

    # 匹配 115/11/13, 115-11-13, 115.11.13, 115年11月13日, 2026-11-13 等
    match = re.search(
        r"(\d{2,4})\s*[年/\-\.]\s*(\d{1,2})\s*[月/\-\.]\s*(\d{1,2})\s*日?",
        cleaned,
    )
    if not match:
        return "", f"日期格式無法解析：{text}"

    year_num = int(match.group(1))
    month_num = int(match.group(2))
    day_num = int(match.group(3))

    if year_num < 1911:
        ad_year = year_num + 1911
        note = f"民國 {year_num} 年換算為西元 {ad_year} 年"
    else:
        ad_year = year_num
        note = ""

    try:
        parsed = dt.date(ad_year, month_num, day_num)
    except ValueError:
        return "", f"日期不合法（{ad_year}-{month_num:02d}-{day_num:02d}），原文：{text}"

    return parsed.isoformat(), note


def parse_chinese_uppercase_amount(raw_text: str | None) -> Decimal | None:
    """將中文大寫金額（例如：新臺幣壹萬貳仟參佰肆拾伍元整）換算為 Decimal。

    無法安全解析時回傳 None，絕不猜測。
    """
    if not raw_text or not raw_text.strip():
        return None

    text = raw_text.strip()
    # 去除貨幣前綴與結尾整字
    text = re.sub(r"^(新台幣|新臺幣|台幣|臺幣|NT\$|NTD)", "", text, flags=re.IGNORECASE)
    text = text.replace(" ", "").replace("　", "")
    text = re.sub(r"[圓元整正]$", "", text)
    text = re.sub(r"[圓元](?=[整正]?$)", "", text)
    text = re.sub(r"[整正]$", "", text)

    # 去除「元」「圓」如果後面還有角分先做整數與角分拆分
    yuan_match = re.match(r"^(.*?)[圓元](.*)$", text)
    integer_part_str = text
    fraction_part_str = ""
    if yuan_match:
        integer_part_str = yuan_match.group(1)
        fraction_part_str = yuan_match.group(2)

    if not integer_part_str and not fraction_part_str:
        return None

    total = Decimal(0)
    if integer_part_str:
        section_val = 0
        current_num = 0
        has_token = False

        # 處理「拾元」開頭省略「壹」的情況（如 拾伍元 -> 15）
        for ch in integer_part_str:
            if ch in _CHINESE_DIGITS:
                current_num = _CHINESE_DIGITS[ch]
                has_token = True
            elif ch in _SMALL_UNITS:
                unit = _SMALL_UNITS[ch]
                if current_num == 0 and unit == 10 and section_val == 0 and not has_token:
                    current_num = 1
                section_val += current_num * unit
                current_num = 0
                has_token = True
            elif ch in _BIG_UNITS:
                big_unit = _BIG_UNITS[ch]
                section_val += current_num
                if section_val == 0:
                    return None
                total += Decimal(section_val) * Decimal(big_unit)
                section_val = 0
                current_num = 0
                has_token = True
            else:
                return None

        if not has_token:
            return None
        total += Decimal(section_val + current_num)

    if fraction_part_str:
        # 解析角與分
        jiao_match = re.match(r"^(?:([零〇壹一貳贰兩二參叁参三肆四伍五陸陆六柒七捌八玖九])角)?(?:([零〇壹一貳贰兩二參叁参三肆四伍五陸陆六柒七捌八玖九])分)?$", fraction_part_str)
        if not jiao_match:
            return None
        if jiao_match.group(1):
            total += Decimal(_CHINESE_DIGITS[jiao_match.group(1)]) * Decimal("0.1")
        if jiao_match.group(2):
            total += Decimal(_CHINESE_DIGITS[jiao_match.group(2)]) * Decimal("0.01")

    return total


def reconcile_amounts(
    extraction: RawVoucherExtraction,
    converted_chinese: Decimal | None,
) -> tuple[str, list[str], bool]:
    """核對未稅+稅額、國字換算金額與商品明細加總。

    回傳：(金額核對結果, 核對說明列表, 是否需人工確認)。
    """
    notes: list[str] = []
    has_mismatch = False
    has_insufficient = False
    has_any_check = False

    untaxed = extraction.amount_untaxed
    tax = extraction.tax_amount
    total = extraction.amount_total

    # 1. 檢查未稅 + 稅額 == 含稅
    if untaxed is not None and tax is not None and total is not None:
        has_any_check = True
        calc_total = untaxed + tax
        if calc_total != total:
            has_mismatch = True
            notes.append(
                f"未稅({_format_decimal(untaxed)})+稅額({_format_decimal(tax)})="
                f"{_format_decimal(calc_total)}，與含稅金額({_format_decimal(total)})不符"
            )
        else:
            notes.append("未稅+稅額與含稅金額一致")
    elif any(x is not None for x in (untaxed, tax, total)):
        has_insufficient = True
        notes.append("未稅/稅額/含稅欄位不齊全，無法執行三欄加減核對")
    else:
        has_insufficient = True
        notes.append("缺乏數字金額欄位")

    # 2. 檢查原文國字金額
    if extraction.amount_chinese:
        if converted_chinese is None:
            has_mismatch = True
            notes.append(f"國字金額「{extraction.amount_chinese}」無法自動換算，請人工確認")
        elif total is not None:
            has_any_check = True
            if converted_chinese != total:
                has_mismatch = True
                notes.append(
                    f"國字換算({_format_decimal(converted_chinese)})與含稅金額({_format_decimal(total)})不符"
                )
            else:
                notes.append("國字換算金額與含稅金額一致")
        else:
            has_insufficient = True
            notes.append("有國字金額但缺乏含稅數字金額可供比對")

    # 3. 檢查商品明細 數量×單價 與 小計加總
    if extraction.items:
        item_subtotals: list[Decimal] = []
        for idx, item in enumerate(extraction.items, start=1):
            if item.quantity is not None and item.unit_price is not None and item.subtotal is not None:
                has_any_check = True
                expected_sub = item.quantity * item.unit_price
                diff = abs(expected_sub - item.subtotal)
                if diff > Decimal("1"):
                    has_mismatch = True
                    notes.append(
                        f"明細第{idx}項：數量×單價({_format_decimal(expected_sub)})與小計({_format_decimal(item.subtotal)})差異過大"
                    )
                elif diff > Decimal("0"):
                    notes.append(
                        f"明細第{idx}項：數量×單價({_format_decimal(expected_sub)})與小計({_format_decimal(item.subtotal)})有四捨五入尾差"
                    )
            if item.subtotal is not None:
                item_subtotals.append(item.subtotal)

        if (
            not extraction.continues_from_prev_page
            and item_subtotals
            and len(item_subtotals) == len(extraction.items)
            and total is not None
        ):
            has_any_check = True
            sum_sub = sum(item_subtotals, Decimal(0))
            if sum_sub != total and (untaxed is None or sum_sub != untaxed):
                has_mismatch = True
                notes.append(
                    f"明細小計加總({_format_decimal(sum_sub)})與含稅({_format_decimal(total)})"
                    + (f"/未稅({_format_decimal(untaxed)})" if untaxed is not None else "")
                    + "不一致（可能有折扣、運費或稅額未列）"
                )

    if has_mismatch:
        return "不一致", notes, True
    if has_insufficient or not has_any_check:
        return "資料不足", notes, True
    return "一致", notes, False


def normalize_id(text: str | None) -> str:
    """正規化發票號碼／統編：全形轉半形、去掉所有空白與減號、英文轉大寫。

    紙本電子發票證明聯印成 "AB-12345678"，收銀機發票印成 "AB 12345678"，
    但發票號碼本身是 10 碼 "AB12345678"（財政部平台、兌獎 App 也不打減號），
    統一格式後寫入與去重比對才會一致（只改格式，不改字元內容）。
    """
    if not text:
        return ""
    return re.sub(r"[\s\-‐‑‒–—]+", "", unicodedata.normalize("NFKC", text)).upper()


def detect_business_duplicate(
    invoice_number: str,
    seller_tax_id: str,
    seller_name: str,
    voucher_date: str,
    amount_total: str,
    existing_masters: list[dict[str, str]],
    current_unique_code: str = "",
) -> tuple[str, str]:
    """比對既有《憑證主檔》偵測疑似重複憑證（排除同一唯一代碼本身，僅標記、絕不自動刪除）。"""
    inv = normalize_id(invoice_number)
    tax_id = normalize_id(seller_tax_id)

    if inv and tax_id:
        for row in existing_masters:
            code = row.get("唯一代碼", "").strip()
            if current_unique_code and code == current_unique_code:
                continue
            row_inv = normalize_id(row.get("發票／收據號碼", ""))
            row_tax = normalize_id(row.get("商家統編", ""))
            if row_inv == inv and row_tax == tax_id:
                return "疑似重複", f"發票號碼({inv})與商家統編({tax_id})與既有代碼 {code} 相同"

    # 若缺發票號碼，改以商家 + 日期 + 含稅金額輔助比對
    if not inv and (seller_name.strip() or tax_id) and voucher_date and amount_total:
        for row in existing_masters:
            code = row.get("唯一代碼", "").strip()
            if current_unique_code and code == current_unique_code:
                continue
            same_seller = (
                (tax_id and normalize_id(row.get("商家統編", "")) == tax_id)
                or (seller_name.strip() and row.get("商家名稱", "").strip() == seller_name.strip())
            )
            if (
                same_seller
                and row.get("憑證日期", "").strip() == voucher_date
                and row.get("含稅金額", "").strip() == amount_total
            ):
                return "疑似重複", f"缺發票號碼，但商家/日期/含稅金額與既有代碼 {code} 相同"

    return "否", ""


def build_voucher_records(
    extraction: RawVoucherExtraction,
    unique_code: str,
    created_at: str,
    original_filename: str,
    original_file_link: str,
    page_info: str,
    existing_masters: list[dict[str, str]],
) -> tuple[VoucherMasterRecord, list[VoucherItemRecord]]:
    """將原始辨識結果轉換為主檔與明細結構，並完成所有確定性檢核。"""
    notes: list[str] = []
    needs_manual_review = False

    if extraction.continues_from_prev_page:
        notes.append("標記為跨頁憑證延續")
    if extraction.continuation_uncertain:
        needs_manual_review = True
        notes.append("跨頁憑證歸屬不確定，需人工確認")

    if extraction.unclear_fields:
        needs_manual_review = True
        notes.append("模糊或遮擋欄位：" + "；".join(extraction.unclear_fields))

    # 日期換算（保留原文）
    voucher_date, date_note = parse_roc_or_ad_date(extraction.raw_date)
    if date_note:
        notes.append(date_note)
    if extraction.raw_date and not voucher_date:
        needs_manual_review = True
    if not extraction.raw_date:
        needs_manual_review = True
        notes.append("缺少憑證日期")

    # 國字金額換算
    converted_chinese = parse_chinese_uppercase_amount(extraction.amount_chinese)

    # 金額核對
    check_result, check_notes, check_needs_review = reconcile_amounts(extraction, converted_chinese)
    notes.extend(check_notes)
    if check_needs_review:
        needs_manual_review = True

    # 明細轉換與稅別不確定提示
    item_records: list[VoucherItemRecord] = []
    for idx, raw_item in enumerate(extraction.items, start=1):
        item_notes: list[str] = []
        if raw_item.tax_included_uncertain:
            item_notes.append("單價是否含稅待確認")
            needs_manual_review = True
        if raw_item.note:
            item_notes.append(raw_item.note)
        if not raw_item.item_name:
            item_notes.append("商品名稱無法辨識")
            needs_manual_review = True

        item_records.append(
            VoucherItemRecord(
                unique_code=unique_code,
                item_seq=idx,
                item_name=(raw_item.item_name or "").strip(),
                quantity=_format_decimal(raw_item.quantity),
                unit=(raw_item.unit or "").strip(),
                unit_price=_format_decimal(raw_item.unit_price),
                subtotal=_format_decimal(raw_item.subtotal),
                notes="；".join(item_notes),
            )
        )

    invoice_num = normalize_id(extraction.invoice_number)
    seller_name = (extraction.seller_name or "").strip()
    seller_tax_id = normalize_id(extraction.seller_tax_id)
    total_str = _format_decimal(extraction.amount_total)

    dup_status, dup_note = detect_business_duplicate(
        invoice_number=invoice_num,
        seller_tax_id=seller_tax_id,
        seller_name=seller_name,
        voucher_date=voucher_date,
        amount_total=total_str,
        existing_masters=existing_masters,
        current_unique_code=unique_code,
    )
    if dup_status == "疑似重複":
        needs_manual_review = True
        notes.append(dup_note)

    recognition_status = "需人工確認" if needs_manual_review else "辨識完成"

    master = VoucherMasterRecord(
        unique_code=unique_code,
        created_at=created_at,
        recognition_status=recognition_status,
        review_status="待覆核",
        voucher_type=extraction.voucher_type,
        voucher_date=voucher_date,
        raw_voucher_date=(extraction.raw_date or "").strip(),
        invoice_number=invoice_num,
        buyer_name=(extraction.buyer_name or "").strip(),
        buyer_tax_id=normalize_id(extraction.buyer_tax_id),
        seller_name=seller_name,
        seller_tax_id=seller_tax_id,
        amount_untaxed=_format_decimal(extraction.amount_untaxed),
        tax_amount=_format_decimal(extraction.tax_amount),
        amount_total=total_str,
        amount_chinese=(extraction.amount_chinese or "").strip(),
        chinese_converted_amount=_format_decimal(converted_chinese),
        amount_check_result=check_result,
        original_filename=original_filename,
        original_file_link=original_file_link,
        page_info=page_info,
        suspected_duplicate=f"{dup_status}（{dup_note}）" if dup_status == "疑似重複" else "否",
        notes=" | ".join(n for n in notes if n),
    )
    return master, item_records
