"""核銷憑證辨識與建檔主流程管線。"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from src.config import Settings
from src.drive_client import DriveClient, DriveFileMeta
from src.lock import acquire_single_instance_lock
from src.models import (
    SHEET_MASTER,
    ProcessLogRecord,
    RawVoucherExtraction,
    VoucherItemRecord,
    VoucherMasterRecord,
)
from src.ocr_provider import OCRProvider, build_ocr_provider
from src.pdf_splitter import compute_sha256, extract_pages
from src.sheets_client import PartialWriteError, SheetsClient
from src.validator import (
    build_voucher_records,
    format_iso_taipei,
    generate_unique_code,
    now_taipei,
)


@dataclass
class RunSummary:
    """批次執行結果彙整報告。"""

    processed_files: int = 0
    succeeded_vouchers: int = 0
    suspected_duplicates: int = 0
    needs_review_vouchers: int = 0
    failed_vouchers: int = 0
    skipped_vouchers: int = 0
    warnings: list[str] = field(default_factory=list)
    review_items: list[str] = field(default_factory=list)
    spreadsheet_url: str = ""


def _build_local_preview(
    file_path: Path,
    ocr: OCRProvider,
    tz_name: str,
) -> list[tuple[VoucherMasterRecord, list[VoucherItemRecord]]]:
    """對本機檔案執行辨識與檢核（預覽模式，不寫入 Google Sheets）。"""
    pages = extract_pages(file_path)
    results: list[tuple[VoucherMasterRecord, list[VoucherItemRecord]]] = []
    prev_code: str | None = None
    prev_item_count = 0

    for page in pages:
        page_res = ocr.extract_page(page.image_bytes, page.mime_type, page.page_number)
        if not page_res.vouchers:
            reason = page_res.page_unreadable_reason or "本頁未偵測到憑證內容"
            code = generate_unique_code(now_taipei(tz_name), tz_name)
            master = VoucherMasterRecord(
                unique_code=code,
                created_at=format_iso_taipei(tz_name=tz_name),
                recognition_status="辨識失敗",
                review_status="待覆核",
                voucher_type="其他",
                amount_check_result="資料不足",
                original_filename=file_path.name,
                original_file_link=str(file_path.resolve()),
                page_info=f"第 {page.page_number} 頁",
                notes=reason,
            )
            results.append((master, []))
            continue

        for voucher in page_res.vouchers:
            is_continuation = (
                voucher.continues_from_prev_page
                and not voucher.continuation_uncertain
                and prev_code is not None
            )
            code = prev_code if is_continuation else generate_unique_code(now_taipei(tz_name), tz_name)
            master, items = build_voucher_records(
                extraction=voucher,
                unique_code=code,
                created_at=format_iso_taipei(tz_name=tz_name),
                original_filename=file_path.name,
                original_file_link=str(file_path.resolve()),
                page_info=f"第 {page.page_number} 頁（第 {voucher.voucher_index_on_page} 張）",
                existing_masters=[m.to_row_dict() for m, _ in results],
            )
            if is_continuation:
                # 跨頁延續時將明細序號接續於上一頁之後
                for offset, item in enumerate(items, start=1):
                    item.item_seq = prev_item_count + offset
                prev_item_count += len(items)
            else:
                prev_code = code
                prev_item_count = len(items)
            results.append((master, items))

    return results


def run_dry_run(file_paths: list[Path], settings: Settings, ocr: OCRProvider | None = None) -> None:
    """離線／預覽模式：只執行本機檔案辨識與檢核並印出結構化結果。"""
    provider = ocr or build_ocr_provider(settings)
    for path in file_paths:
        records = _build_local_preview(path, provider, settings.timezone)
        payload = [
            {
                "master": master.to_row_dict(),
                "items": [item.to_row_dict() for item in items],
            }
            for master, items in records
        ]
        print(f"=== 預覽結果（尚未寫入 Google Sheets）：{path.name} ===")
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def _process_drive_file(
    file_meta: DriveFileMeta,
    local_path: Path,
    settings: Settings,
    ocr: OCRProvider,
    sheets: SheetsClient,
    summary: RunSummary,
) -> str:
    """處理單一 Drive 檔案，回傳建議移動的目標資料夾 ID。"""
    file_hash = file_meta.md5_checksum or compute_sha256(local_path)
    pages = extract_pages(local_path)

    any_failed = False
    any_needs_review = False
    prev_code: str | None = None
    prev_item_count = 0

    for page in pages:
        page_res = ocr.extract_page(page.image_bytes, page.mime_type, page.page_number)
        if not page_res.vouchers:
            any_failed = True
            summary.failed_vouchers += 1
            reason = page_res.page_unreadable_reason or "無法從此頁辨識出憑證"
            source_key = f"{file_meta.file_id}:{file_hash}:p{page.page_number}:v0"
            prev_status, prev_existing_code, retry_count = sheets.find_log_state_by_source_key(source_key)
            if prev_status == "成功":
                summary.skipped_vouchers += 1
                continue

            code = prev_existing_code or generate_unique_code(now_taipei(settings.timezone), settings.timezone)
            now_iso = format_iso_taipei(tz_name=settings.timezone)
            master = VoucherMasterRecord(
                unique_code=code,
                created_at=now_iso,
                recognition_status="辨識失敗",
                review_status="待覆核",
                voucher_type="其他",
                amount_check_result="資料不足",
                original_filename=file_meta.name,
                original_file_link=file_meta.web_view_link,
                page_info=f"第 {page.page_number} 頁",
                notes=reason,
            )
            log_rec = ProcessLogRecord(
                processed_at=now_iso,
                source_key=source_key,
                original_filename=file_meta.name,
                page_or_seq=f"p{page.page_number}",
                unique_code=code,
                status="失敗",
                failure_reason=reason,
                retry_count=retry_count + (1 if prev_status else 0),
            )
            sheets.write_voucher_idempotent(master, [], log_rec)
            summary.review_items.append(f"{file_meta.name} 第{page.page_number}頁（{code}）：{reason}")
            continue

        for voucher in page_res.vouchers:
            seq_label = f"p{page.page_number}-v{voucher.voucher_index_on_page}"
            source_key = f"{file_meta.file_id}:{file_hash}:{seq_label}"
            prev_status, prev_existing_code, retry_count = sheets.find_log_state_by_source_key(source_key)
            if prev_status == "成功":
                summary.skipped_vouchers += 1
                prev_code = prev_existing_code
                continue

            is_continuation = (
                voucher.continues_from_prev_page
                and not voucher.continuation_uncertain
                and prev_code is not None
            )
            code = (
                prev_existing_code
                or (prev_code if is_continuation else None)
                or generate_unique_code(now_taipei(settings.timezone), settings.timezone)
            )
            now_iso = format_iso_taipei(tz_name=settings.timezone)
            _, existing_masters = sheets.read_all_rows_as_dicts(SHEET_MASTER)

            master, items = build_voucher_records(
                extraction=voucher,
                unique_code=code,
                created_at=now_iso,
                original_filename=file_meta.name,
                original_file_link=file_meta.web_view_link,
                page_info=f"第 {page.page_number} 頁（第 {voucher.voucher_index_on_page} 張）",
                existing_masters=existing_masters,
            )
            if is_continuation:
                for offset, item in enumerate(items, start=1):
                    item.item_seq = prev_item_count + offset
                prev_item_count += len(items)
            else:
                prev_code = code
                prev_item_count = len(items)

            log_rec = ProcessLogRecord(
                processed_at=now_iso,
                source_key=source_key,
                original_filename=file_meta.name,
                page_or_seq=seq_label,
                unique_code=code,
                status="成功",
                failure_reason="",
                retry_count=retry_count + (1 if prev_status else 0),
            )
            try:
                warnings = sheets.write_voucher_idempotent(master, items, log_rec)
                summary.warnings.extend(warnings)
            except PartialWriteError as exc:
                # 無法確保完全寫入時記錄部分失敗並交由人工處理，不移動原檔
                sheets.append_failure_log(
                    ProcessLogRecord(
                        processed_at=format_iso_taipei(tz_name=settings.timezone),
                        source_key=source_key,
                        original_filename=file_meta.name,
                        page_or_seq=seq_label,
                        unique_code=code,
                        status="部分成功待人工處理",
                        failure_reason=str(exc),
                        retry_count=retry_count + (1 if prev_status else 0),
                    )
                )
                raise

            summary.succeeded_vouchers += 1
            if master.suspected_duplicate.startswith("疑似重複"):
                summary.suspected_duplicates += 1
            if master.recognition_status == "需人工確認":
                any_needs_review = True
                summary.needs_review_vouchers += 1
                summary.review_items.append(
                    f"{file_meta.name} {seq_label}（{code}）：{master.notes}"
                )

    if any_failed and summary.succeeded_vouchers == 0:
        return settings.drive_folder_failed
    if any_failed or any_needs_review:
        return settings.drive_folder_review
    return settings.drive_folder_done


def run_drive_pipeline(
    settings: Settings,
    drive: DriveClient,
    sheets: SheetsClient,
    ocr: OCRProvider,
) -> RunSummary:
    """在單一執行程序鎖保護下執行完整 Google Drive -> Vertex AI -> Google Sheets 管線。"""
    settings.validate_for_drive_run()
    summary = RunSummary(
        spreadsheet_url=f"https://docs.google.com/spreadsheets/d/{settings.spreadsheet_id}/edit"
    )

    with acquire_single_instance_lock(settings.lock_file_path):
        pending_files = drive.list_pending_files(settings.drive_folder_pending)
        for file_meta in pending_files:
            summary.processed_files += 1
            work_dir = settings.temp_dir / file_meta.file_id
            local_path = work_dir / file_meta.name
            try:
                drive.download_file(file_meta.file_id, local_path)
                target_folder = _process_drive_file(
                    file_meta=file_meta,
                    local_path=local_path,
                    settings=settings,
                    ocr=ocr,
                    sheets=sheets,
                    summary=summary,
                )
                try:
                    drive.move_file(
                        file_id=file_meta.file_id,
                        from_folder_id=settings.drive_folder_pending,
                        to_folder_id=target_folder,
                    )
                except Exception as move_exc:
                    summary.warnings.append(
                        f"檔案「{file_meta.name}」已寫入試算表但移動資料夾失敗（保留原位，未刪除）：{move_exc}"
                    )
            except Exception as exc:
                summary.failed_vouchers += 1
                summary.review_items.append(f"檔案「{file_meta.name}」處理失敗：{exc}")
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)

    return summary
