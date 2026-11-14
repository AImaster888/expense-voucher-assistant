"""資料模型與 Google Sheets 標準欄位定義。"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, Field


SHEET_MASTER = "憑證主檔"
SHEET_ITEMS = "商品明細"
SHEET_LOGS = "處理紀錄"

MASTER_HEADERS: list[str] = [
    "唯一代碼",
    "建檔日期時間",
    "辨識狀態",
    "覆核狀態",
    "憑證類型",
    "憑證日期",
    "憑證日期原文",
    "發票／收據號碼",
    "買受人抬頭",
    "買受人統編",
    "商家名稱",
    "商家統編",
    "未稅金額",
    "稅額",
    "含稅金額",
    "金額（國字）",
    "國字換算金額",
    "金額核對結果",
    "原始檔名",
    "原始檔連結",
    "頁碼",
    "疑似重複",
    "覆核人員",
    "覆核日期時間",
    "備註",
]

ITEM_HEADERS: list[str] = [
    "唯一代碼",
    "明細序號",
    "商品名稱",
    "數量",
    "單位",
    "單價",
    "小計",
    "備註",
]

LOG_HEADERS: list[str] = [
    "處理時間",
    "來源識別碼／雜湊",
    "原始檔名",
    "頁碼或憑證序號",
    "唯一代碼",
    "處理狀態",
    "失敗原因",
    "重試次數",
]


class RawVoucherItem(BaseModel):
    """AI/OCR 辨識出的單筆商品明細原文（禁止自行推算補值）。"""

    item_name: str | None = Field(default=None, description="商品名稱原文，看不清請回傳 null")
    quantity: Decimal | None = Field(default=None, description="數量，未列出請回傳 null")
    unit: str | None = Field(default=None, description="單位，未列出請回傳 null")
    unit_price: Decimal | None = Field(default=None, description="單價，未列出請回傳 null")
    subtotal: Decimal | None = Field(default=None, description="小計，未列出請回傳 null")
    tax_included_uncertain: bool = Field(
        default=False, description="若無法判定單價是否含稅則標記為 true"
    )
    note: str | None = Field(default=None, description="模糊或特殊標註原因")


class RawVoucherExtraction(BaseModel):
    """AI/OCR 從影像辨識出的單張憑證原文資料。"""

    voucher_index_on_page: int = Field(default=1, ge=1, description="本頁第幾張憑證（從 1 起算）")
    continues_from_prev_page: bool = Field(
        default=False, description="是否明確為上一頁同一張憑證之延續"
    )
    continuation_uncertain: bool = Field(
        default=False, description="跨頁歸屬是否無法確定（需人工確認）"
    )
    voucher_type: Literal["發票", "收據", "其他"] = Field(
        default="其他", description="憑證類型"
    )
    raw_date: str | None = Field(default=None, description="憑證上的原始日期文字，例如 115/11/13")
    invoice_number: str | None = Field(default=None, description="發票或收據號碼原文，缺少則 null")
    buyer_name: str | None = Field(default=None, description="買受人抬頭原文，缺少或遮擋則 null")
    buyer_tax_id: str | None = Field(default=None, description="買受人統編原文（保留前導0），缺少則 null")
    seller_name: str | None = Field(default=None, description="商家名稱原文，缺少或看不清則 null")
    seller_tax_id: str | None = Field(default=None, description="商家統編原文（保留前導0），缺少則 null")
    amount_untaxed: Decimal | None = Field(default=None, description="憑證實際列出的未稅金額")
    tax_amount: Decimal | None = Field(default=None, description="憑證實際列出的稅額")
    amount_total: Decimal | None = Field(default=None, description="憑證實際列出的含稅/總金額")
    amount_chinese: str | None = Field(
        default=None, description="僅記錄憑證原文出現的中文大寫金額，未出現則 null"
    )
    items: list[RawVoucherItem] = Field(default_factory=list, description="辨識出的商品明細列")
    unclear_fields: list[str] = Field(
        default_factory=list, description="被遮擋、模糊或無法辨識的欄位名稱與原因"
    )


class PageExtractionResult(BaseModel):
    """單一影像頁面的辨識結果（可能包含 0 到多張憑證）。"""

    vouchers: list[RawVoucherExtraction] = Field(default_factory=list)
    page_unreadable_reason: str | None = Field(
        default=None, description="整頁無法辨識時說明原因（例如過於模糊、非憑證）"
    )


class VoucherMasterRecord(BaseModel):
    """寫入《憑證主檔》的結構化紀錄。"""

    unique_code: str
    created_at: str
    recognition_status: Literal["辨識完成", "需人工確認", "辨識失敗"]
    review_status: Literal["待覆核"] = "待覆核"  # 程式永遠鎖定為待覆核
    voucher_type: Literal["發票", "收據", "其他"]
    voucher_date: str = ""
    raw_voucher_date: str = ""
    invoice_number: str = ""
    buyer_name: str = ""
    buyer_tax_id: str = ""
    seller_name: str = ""
    seller_tax_id: str = ""
    amount_untaxed: str = ""
    tax_amount: str = ""
    amount_total: str = ""
    amount_chinese: str = ""
    chinese_converted_amount: str = ""
    amount_check_result: str = "資料不足"
    original_filename: str = ""
    original_file_link: str = ""
    page_info: str = ""
    suspected_duplicate: str = "否"
    reviewer: str = ""
    reviewed_at: str = ""
    notes: str = ""

    def to_row_dict(self) -> dict[str, str]:
        """轉換為以欄名為鍵的純文字字典。"""
        return {
            "唯一代碼": self.unique_code,
            "建檔日期時間": self.created_at,
            "辨識狀態": self.recognition_status,
            "覆核狀態": self.review_status,
            "憑證類型": self.voucher_type,
            "憑證日期": self.voucher_date,
            "憑證日期原文": self.raw_voucher_date,
            "發票／收據號碼": self.invoice_number,
            "買受人抬頭": self.buyer_name,
            "買受人統編": self.buyer_tax_id,
            "商家名稱": self.seller_name,
            "商家統編": self.seller_tax_id,
            "未稅金額": self.amount_untaxed,
            "稅額": self.tax_amount,
            "含稅金額": self.amount_total,
            "金額（國字）": self.amount_chinese,
            "國字換算金額": self.chinese_converted_amount,
            "金額核對結果": self.amount_check_result,
            "原始檔名": self.original_filename,
            "原始檔連結": self.original_file_link,
            "頁碼": self.page_info,
            "疑似重複": self.suspected_duplicate,
            "覆核人員": self.reviewer,
            "覆核日期時間": self.reviewed_at,
            "備註": self.notes,
        }


class VoucherItemRecord(BaseModel):
    """寫入《商品明細》的結構化紀錄。"""

    unique_code: str
    item_seq: int
    item_name: str = ""
    quantity: str = ""
    unit: str = ""
    unit_price: str = ""
    subtotal: str = ""
    notes: str = ""

    def to_row_dict(self) -> dict[str, str]:
        """轉換為以欄名為鍵的純文字字典。"""
        return {
            "唯一代碼": self.unique_code,
            "明細序號": str(self.item_seq),
            "商品名稱": self.item_name,
            "數量": self.quantity,
            "單位": self.unit,
            "單價": self.unit_price,
            "小計": self.subtotal,
            "備註": self.notes,
        }


class ProcessLogRecord(BaseModel):
    """寫入《處理紀錄》的結構化紀錄。"""

    processed_at: str
    source_key: str
    original_filename: str
    page_or_seq: str
    unique_code: str
    status: str
    failure_reason: str = ""
    retry_count: int = 0

    def to_row_dict(self) -> dict[str, str]:
        """轉換為以欄名為鍵的純文字字典。"""
        return {
            "處理時間": self.processed_at,
            "來源識別碼／雜湊": self.source_key,
            "原始檔名": self.original_filename,
            "頁碼或憑證序號": self.page_or_seq,
            "唯一代碼": self.unique_code,
            "處理狀態": self.status,
            "失敗原因": self.failure_reason,
            "重試次數": str(self.retry_count),
        }
