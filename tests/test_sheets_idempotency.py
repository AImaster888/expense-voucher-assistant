"""Google Sheets 動態欄名對應、RAW 寫入與部分寫入冪等回查測試。"""

from __future__ import annotations

from src.models import (
    ITEM_HEADERS,
    LOG_HEADERS,
    MASTER_HEADERS,
    SHEET_ITEMS,
    SHEET_LOGS,
    SHEET_MASTER,
    ProcessLogRecord,
    VoucherItemRecord,
    VoucherMasterRecord,
)
from src.sheets_client import SheetsClient


class _FakeExecute:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeValuesAPI:
    def __init__(self, tables: dict[str, list[list[str]]]):
        self.tables = tables
        self.last_value_input_options: list[str] = []

    def get(self, spreadsheetId: str, range: str, valueRenderOption: str = "FORMATTED_VALUE"):
        sheet_name = range.split("!")[0].strip("'")
        rows = self.tables[sheet_name]
        if range.endswith("!1:1"):
            return _FakeExecute({"values": [rows[0]] if rows else []})
        return _FakeExecute({"values": [list(r) for r in rows]})

    def append(
        self,
        spreadsheetId: str,
        range: str,
        valueInputOption: str,
        insertDataOption: str,
        body: dict,
    ):
        self.last_value_input_options.append(valueInputOption)
        sheet_name = range.split("!")[0].strip("'")
        self.tables[sheet_name].extend(body["values"])
        return _FakeExecute({"updates": {"updatedRows": len(body["values"])}})


class _FakeSpreadsheetsAPI:
    def __init__(self, tables: dict[str, list[list[str]]]):
        self._values = _FakeValuesAPI(tables)

    def values(self):
        return self._values


class FakeSheetsService:
    def __init__(self, tables: dict[str, list[list[str]]]):
        self._spreadsheets = _FakeSpreadsheetsAPI(tables)

    def spreadsheets(self):
        return self._spreadsheets


def test_dynamic_header_mapping_and_partial_item_recovery():
    # 故意將《憑證主檔》欄位順序對調，測試依欄名映射而非位置寫入
    reordered_master_headers = list(reversed(MASTER_HEADERS))
    tables = {
        SHEET_MASTER: [reordered_master_headers],
        SHEET_ITEMS: [list(ITEM_HEADERS)],
        SHEET_LOGS: [list(LOG_HEADERS)],
    }
    service = FakeSheetsService(tables)
    client = SheetsClient(service, "fake-sheet-id")

    # 模擬前一次失敗：主檔與第 1 項明細已寫入，第 2 項明細漏寫
    code = "EXP-20261113-partial-uuid"
    master = VoucherMasterRecord(
        unique_code=code,
        created_at="2026-11-13T23:25:00+08:00",
        recognition_status="辨識完成",
        voucher_type="發票",
        buyer_tax_id="04123456",
        seller_tax_id="00112233",
    )
    items = [
        VoucherItemRecord(unique_code=code, item_seq=1, item_name="品項A", subtotal="100"),
        VoucherItemRecord(unique_code=code, item_seq=2, item_name="品項B", subtotal="200"),
    ]
    # 預先塞入主檔與第 1 項明細
    row_dict = master.to_row_dict()
    tables[SHEET_MASTER].append([row_dict.get(h, "") for h in reordered_master_headers])
    tables[SHEET_ITEMS].append([code, "1", "品項A", "", "", "", "100", ""])

    log_rec = ProcessLogRecord(
        processed_at="2026-11-13T23:26:00+08:00",
        source_key="file1:hash1:p1-v1",
        original_filename="inv.pdf",
        page_or_seq="p1-v1",
        unique_code=code,
        status="成功",
        retry_count=1,
    )

    warnings = client.write_voucher_idempotent(master, items, log_rec)
    assert warnings == []
    # 主檔不應重複新增
    assert len(tables[SHEET_MASTER]) == 2
    # 明細應只補寫缺少的第 2 項
    assert len(tables[SHEET_ITEMS]) == 3
    assert tables[SHEET_ITEMS][2][1] == "2"
    assert tables[SHEET_ITEMS][2][2] == "品項B"
    # 必須使用 RAW 模式寫入以保留前導 0
    assert all(opt == "RAW" for opt in service.spreadsheets().values().last_value_input_options)
    # 處理紀錄應新增一筆
    assert len(tables[SHEET_LOGS]) == 2
