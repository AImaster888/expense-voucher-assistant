"""Google Sheets 動態欄名對應、RAW 純文字寫入與冪等回查驗證。"""

from __future__ import annotations

from typing import Any

from src.models import (
    SHEET_ITEMS,
    SHEET_LOGS,
    SHEET_MASTER,
    ProcessLogRecord,
    VoucherItemRecord,
    VoucherMasterRecord,
)


class PartialWriteError(RuntimeError):
    """主檔、明細或紀錄寫入未完全成功時拋出，禁止後續移動原始檔。"""


class SheetsClient:
    """封裝 Google Sheets 讀寫，依實際第 1 列欄名映射並以 RAW 純文字寫入。"""

    def __init__(self, service: Any, spreadsheet_id: str) -> None:
        self.service = service
        self.spreadsheet_id = spreadsheet_id

    def get_headers(self, sheet_name: str) -> list[str]:
        """讀取工作表第 1 列既有欄名。"""
        resp = (
            self.service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{sheet_name}'!1:1",
            )
            .execute()
        )
        values = resp.get("values", [])
        if not values or not values[0]:
            raise ValueError(f"工作表「{sheet_name}」第 1 列找不到欄名，請先初始化標題列。")
        return [str(col).strip() for col in values[0]]

    def read_all_rows_as_dicts(self, sheet_name: str) -> tuple[list[str], list[dict[str, str]]]:
        """讀取工作表全部資料並依欄名轉成字典清單。"""
        resp = (
            self.service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{sheet_name}'!A:ZZ",
                valueRenderOption="UNFORMATTED_VALUE",
            )
            .execute()
        )
        values = resp.get("values", [])
        if not values:
            raise ValueError(f"工作表「{sheet_name}」為空，缺少標題列。")

        headers = [str(col).strip() for col in values[0]]
        records: list[dict[str, str]] = []
        for row in values[1:]:
            record: dict[str, str] = {}
            for idx, header in enumerate(headers):
                if header:
                    record[header] = str(row[idx]) if idx < len(row) else ""
            records.append(record)
        return headers, records

    @staticmethod
    def map_dict_to_row(headers: list[str], row_dict: dict[str, str]) -> tuple[list[str], list[str]]:
        """依工作表實際欄名順序組裝寫入列，回傳 (寫入列, 無法對應的來源欄位)。"""
        row = [str(row_dict.get(header, "")) for header in headers]
        unmapped = [key for key in row_dict.keys() if key not in headers]
        return row, unmapped

    def append_rows_raw(self, sheet_name: str, rows: list[list[str]]) -> None:
        """以 RAW 模式附加資料列（保留前導 0 與純文字格式，不讓 Sheets 自動轉型）。"""
        if not rows:
            return
        (
            self.service.spreadsheets()
            .values()
            .append(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{sheet_name}'!A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            )
            .execute()
        )

    def find_log_state_by_source_key(self, source_key: str) -> tuple[str | None, str | None, int]:
        """回查《處理紀錄》中該來源處理鍵的最後狀態：(處理狀態, 既有唯一代碼, 已重試次數)。"""
        _, logs = self.read_all_rows_as_dicts(SHEET_LOGS)
        matched = [row for row in logs if row.get("來源識別碼／雜湊", "").strip() == source_key]
        if not matched:
            return None, None, 0
        latest = matched[-1]
        retry_raw = latest.get("重試次數", "0").strip()
        retry_count = int(retry_raw) if retry_raw.isdigit() else 0
        return (
            latest.get("處理狀態", "").strip() or None,
            latest.get("唯一代碼", "").strip() or None,
            retry_count,
        )

    def write_voucher_idempotent(
        self,
        master: VoucherMasterRecord,
        items: list[VoucherItemRecord],
        log_record: ProcessLogRecord,
    ) -> list[str]:
        """依序寫入主檔 → 明細 → 處理紀錄，並回查驗證結果。

        回傳未對應的欄位警告清單；若寫入後回查失敗則拋出 PartialWriteError。
        """
        warnings: list[str] = []

        # 1. 寫入並回查《憑證主檔》
        master_headers, existing_masters = self.read_all_rows_as_dicts(SHEET_MASTER)
        master_exists = any(
            row.get("唯一代碼", "").strip() == master.unique_code for row in existing_masters
        )
        if not master_exists:
            master_row, unmapped = self.map_dict_to_row(master_headers, master.to_row_dict())
            if unmapped:
                warnings.append(f"{SHEET_MASTER} 無對應欄位：{', '.join(unmapped)}")
            self.append_rows_raw(SHEET_MASTER, [master_row])

            _, verify_masters = self.read_all_rows_as_dicts(SHEET_MASTER)
            if not any(r.get("唯一代碼", "").strip() == master.unique_code for r in verify_masters):
                raise PartialWriteError(f"主檔寫入後回查失敗：唯一代碼 {master.unique_code}")

        # 2. 寫入並回查《商品明細》（僅補寫尚不存在的明細序號）
        if items:
            item_headers, existing_items = self.read_all_rows_as_dicts(SHEET_ITEMS)
            existing_seqs = {
                row.get("明細序號", "").strip()
                for row in existing_items
                if row.get("唯一代碼", "").strip() == master.unique_code
            }
            rows_to_append: list[list[str]] = []
            expected_seqs: set[str] = set()
            for item in items:
                seq_str = str(item.item_seq)
                expected_seqs.add(seq_str)
                if seq_str not in existing_seqs:
                    row, unmapped = self.map_dict_to_row(item_headers, item.to_row_dict())
                    if unmapped:
                        warnings.append(f"{SHEET_ITEMS} 無對應欄位：{', '.join(unmapped)}")
                    rows_to_append.append(row)

            if rows_to_append:
                self.append_rows_raw(SHEET_ITEMS, rows_to_append)

            _, verify_items = self.read_all_rows_as_dicts(SHEET_ITEMS)
            persisted_seqs = {
                row.get("明細序號", "").strip()
                for row in verify_items
                if row.get("唯一代碼", "").strip() == master.unique_code
            }
            missing_seqs = expected_seqs - persisted_seqs
            if missing_seqs:
                raise PartialWriteError(
                    f"商品明細寫入不完整（唯一代碼 {master.unique_code}，缺少序號：{sorted(missing_seqs)}）"
                )

        # 3. 寫入《處理紀錄》
        log_headers = self.get_headers(SHEET_LOGS)
        log_row, unmapped = self.map_dict_to_row(log_headers, log_record.to_row_dict())
        if unmapped:
            warnings.append(f"{SHEET_LOGS} 無對應欄位：{', '.join(unmapped)}")
        self.append_rows_raw(SHEET_LOGS, [log_row])

        return warnings

    def append_failure_log(self, log_record: ProcessLogRecord) -> None:
        """寫入失敗或待人工處置紀錄到《處理紀錄》。"""
        log_headers = self.get_headers(SHEET_LOGS)
        log_row, _ = self.map_dict_to_row(log_headers, log_record.to_row_dict())
        self.append_rows_raw(SHEET_LOGS, [log_row])
