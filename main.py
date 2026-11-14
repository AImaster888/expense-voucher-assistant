"""核銷憑證辨識與建檔助手 CLI 入口。"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.config import Settings
from src.drive_client import DriveClient
from src.google_auth import build_drive_and_sheets_services
from src.ocr_provider import build_ocr_provider
from src.pipeline import run_drive_pipeline, run_dry_run
from src.sheets_client import SheetsClient


def main() -> int:
    parser = argparse.ArgumentParser(description="核銷憑證辨識與建檔助手")
    parser.add_argument(
        "--env",
        type=Path,
        default=None,
        help="指定 .env 設定檔路徑（預設讀取當前目錄 .env）",
    )
    parser.add_argument(
        "--dry-run",
        nargs="+",
        type=Path,
        default=None,
        help="離線預覽模式：指定一個或多個本機檔案進行辨識與檢核，不寫入 Google Sheets",
    )
    args = parser.parse_args()

    settings = Settings.from_env(args.env)

    if args.dry_run:
        run_dry_run(args.dry_run, settings)
        return 0

    settings.validate_for_drive_run()
    drive_svc, sheets_svc = build_drive_and_sheets_services(
        auth_mode=settings.auth_mode,
        credentials_path=settings.credentials_path,
        token_path=settings.token_path,
    )
    ocr = build_ocr_provider(settings)
    summary = run_drive_pipeline(
        settings=settings,
        drive=DriveClient(drive_svc),
        sheets=SheetsClient(sheets_svc, settings.spreadsheet_id),
        ocr=ocr,
    )

    print("=== 核銷憑證處理完成報告 ===")
    print(f"處理檔案數：{summary.processed_files}")
    print(f"寫入憑證數：{summary.succeeded_vouchers}")
    print(f"疑似重複數：{summary.suspected_duplicates}")
    print(f"待人工確認：{summary.needs_review_vouchers}")
    print(f"跳過已處理：{summary.skipped_vouchers}")
    print(f"失敗數：{summary.failed_vouchers}")
    print(f"試算表連結：{summary.spreadsheet_url}")
    if summary.warnings:
        print("\n[警告]")
        for w in summary.warnings:
            print(f"- {w}")
    if summary.review_items:
        print("\n[須人工核對項目]")
        for item in summary.review_items:
            print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
