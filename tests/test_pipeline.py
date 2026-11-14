"""端對端管線測試（Mock Drive / Sheets / Vertex AI，搭配真實 PyMuPDF 產生多頁 PDF）。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import fitz

from src.config import Settings
from src.drive_client import DriveFileMeta
from src.models import (
    ITEM_HEADERS,
    LOG_HEADERS,
    MASTER_HEADERS,
    SHEET_ITEMS,
    SHEET_LOGS,
    SHEET_MASTER,
    PageExtractionResult,
    RawVoucherExtraction,
    RawVoucherItem,
)
from src.ocr_provider import OCRProvider
from src.pipeline import run_drive_pipeline
from src.sheets_client import SheetsClient
from tests.test_sheets_idempotency import FakeSheetsService


class FakeDriveClient:
    def __init__(self, pending: list[DriveFileMeta], local_sources: dict[str, Path]):
        self.pending = pending
        self.local_sources = local_sources
        self.moved: list[tuple[str, str, str]] = []

    def list_pending_files(self, folder_id: str) -> list[DriveFileMeta]:
        return list(self.pending)

    def download_file(self, file_id: str, dest_path: Path) -> Path:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(self.local_sources[file_id].read_bytes())
        return dest_path

    def move_file(self, file_id: str, from_folder_id: str, to_folder_id: str) -> None:
        self.moved.append((file_id, from_folder_id, to_folder_id))


class FakeOCRProvider(OCRProvider):
    def extract_page(self, image_bytes: bytes, mime_type: str, page_number: int) -> PageExtractionResult:
        if page_number == 1:
            return PageExtractionResult(
                vouchers=[
                    RawVoucherExtraction(
                        voucher_index_on_page=1,
                        voucher_type="發票",
                        raw_date="115/11/13",
                        invoice_number="XY00001234",
                        buyer_name="甲公司",
                        buyer_tax_id="04123456",
                        seller_name="乙商店",
                        seller_tax_id="01234567",
                        amount_untaxed=Decimal("100"),
                        tax_amount=Decimal("5"),
                        amount_total=Decimal("105"),
                        items=[
                            RawVoucherItem(
                                item_name="原子筆",
                                quantity=Decimal("1"),
                                unit="支",
                                unit_price=Decimal("105"),
                                subtotal=Decimal("105"),
                            )
                        ],
                    )
                ]
            )
        # 第 2 頁接續第 1 頁同一張憑證
        return PageExtractionResult(
            vouchers=[
                RawVoucherExtraction(
                    voucher_index_on_page=1,
                    continues_from_prev_page=True,
                    continuation_uncertain=False,
                    voucher_type="發票",
                    raw_date="115/11/13",
                    invoice_number="XY00001234",
                    buyer_name="甲公司",
                    buyer_tax_id="04123456",
                    seller_name="乙商店",
                    seller_tax_id="01234567",
                    amount_untaxed=Decimal("100"),
                    tax_amount=Decimal("5"),
                    amount_total=Decimal("105"),
                    items=[
                        RawVoucherItem(
                            item_name="筆記本",
                            quantity=Decimal("1"),
                            unit="本",
                            unit_price=Decimal("0"),
                            subtotal=Decimal("0"),
                        )
                    ],
                )
            ]
        )


def test_multipage_cross_page_voucher_and_idempotent_rerun(tmp_path: Path):
    # 建立真實 2 頁測試 PDF
    pdf_path = tmp_path / "sample_2pages.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    doc.save(str(pdf_path))
    doc.close()

    tables = {
        SHEET_MASTER: [list(MASTER_HEADERS)],
        SHEET_ITEMS: [list(ITEM_HEADERS)],
        SHEET_LOGS: [list(LOG_HEADERS)],
    }
    sheets_svc = FakeSheetsService(tables)
    sheets = SheetsClient(sheets_svc, "sheet-123")

    file_meta = DriveFileMeta(
        file_id="drive-file-1",
        name="sample_2pages.pdf",
        mime_type="application/pdf",
        md5_checksum="abc123md5",
        web_view_link="https://drive.google.com/file/d/drive-file-1/view",
    )
    drive = FakeDriveClient([file_meta], {"drive-file-1": pdf_path})

    settings = Settings(
        google_cloud_project="test-proj",
        google_cloud_location="asia-east1",
        vertex_model="gemini-2.5-flash",
        drive_folder_pending="folder-pending",
        drive_folder_done="folder-done",
        drive_folder_review="folder-review",
        drive_folder_failed="folder-failed",
        spreadsheet_id="sheet-123",
        auth_mode="oauth",
        credentials_path=tmp_path / "c.json",
        token_path=tmp_path / "t.json",
        timezone="Asia/Taipei",
        lock_file_path=tmp_path / "run.lock",
        temp_dir=tmp_path / "work",
        max_retries=1,
    )

    summary = run_drive_pipeline(settings, drive, sheets, FakeOCRProvider())
    assert summary.processed_files == 1
    assert summary.succeeded_vouchers == 2
    assert summary.suspected_duplicates == 0
    # 跨頁同一憑證應關聯同一筆主檔（1 列標題 + 1 列主檔）
    assert len(tables[SHEET_MASTER]) == 2
    # 明細應有 2 列，且明細序號為 1 與 2，共用同一個唯一代碼
    assert len(tables[SHEET_ITEMS]) == 3
    assert tables[SHEET_ITEMS][1][0] == tables[SHEET_ITEMS][2][0]
    assert tables[SHEET_ITEMS][1][1] == "1"
    assert tables[SHEET_ITEMS][2][1] == "2"
    # 檔案應移至處理完成資料夾
    assert drive.moved == [("drive-file-1", "folder-pending", "folder-done")]

    # 重跑一次相同檔案應全部跳過，不重複建檔
    summary2 = run_drive_pipeline(settings, drive, sheets, FakeOCRProvider())
    assert summary2.skipped_vouchers == 2
    assert len(tables[SHEET_MASTER]) == 2
    assert len(tables[SHEET_ITEMS]) == 3
