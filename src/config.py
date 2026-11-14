"""環境設定載入與驗證。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """應用程式組態設定。"""

    google_cloud_project: str
    google_cloud_location: str
    vertex_model: str
    drive_folder_pending: str
    drive_folder_done: str
    drive_folder_review: str
    drive_folder_failed: str
    spreadsheet_id: str
    auth_mode: str
    credentials_path: Path
    token_path: Path
    timezone: str
    lock_file_path: Path
    temp_dir: Path
    max_retries: int
    ocr_provider: str = "vertex"  # vertex 或 openai
    openai_model: str = "gpt-5-mini"
    vertex_thinking_level: str = "low"  # Gemini 3 思考等級；空字串＝用模型預設

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> "Settings":
        """從 .env 與環境變數載入設定。"""
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv()

        return cls(
            google_cloud_project=os.getenv("GOOGLE_CLOUD_PROJECT", "").strip(),
            google_cloud_location=os.getenv("GOOGLE_CLOUD_LOCATION", "global").strip(),
            vertex_model=os.getenv("VERTEX_MODEL", "gemini-3-flash-preview").strip(),
            drive_folder_pending=os.getenv("DRIVE_FOLDER_PENDING", "").strip(),
            drive_folder_done=os.getenv("DRIVE_FOLDER_DONE", "").strip(),
            drive_folder_review=os.getenv("DRIVE_FOLDER_REVIEW", "").strip(),
            drive_folder_failed=os.getenv("DRIVE_FOLDER_FAILED", "").strip(),
            spreadsheet_id=os.getenv("SPREADSHEET_ID", "").strip(),
            auth_mode=os.getenv("AUTH_MODE", "oauth").strip().lower(),
            credentials_path=Path(os.getenv("CREDENTIALS_PATH", "credentials/oauth_client.json")),
            token_path=Path(os.getenv("TOKEN_PATH", "credentials/token.json")),
            timezone=os.getenv("TIMEZONE", "Asia/Taipei").strip(),
            lock_file_path=Path(os.getenv("LOCK_FILE_PATH", "tmp/voucher_worker.lock")),
            temp_dir=Path(os.getenv("TEMP_DIR", "tmp/work")),
            max_retries=max(0, int(os.getenv("MAX_RETRIES", "2"))),
            ocr_provider=os.getenv("OCR_PROVIDER", "vertex").strip().lower(),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5-mini").strip(),
            vertex_thinking_level=os.getenv("VERTEX_THINKING_LEVEL", "low").strip().lower(),
        )

    def validate_for_drive_run(self) -> None:
        """檢查執行完整 Drive -> AI 辨識 -> Sheets 管線所需的必要設定。"""
        missing = []
        if self.ocr_provider == "vertex" and not self.google_cloud_project:
            missing.append("GOOGLE_CLOUD_PROJECT")
        for field_name in (
            "drive_folder_pending",
            "drive_folder_done",
            "drive_folder_review",
            "drive_folder_failed",
            "spreadsheet_id",
        ):
            if not getattr(self, field_name):
                missing.append(field_name.upper())
        if missing:
            raise ValueError(f"缺少必要環境變數：{', '.join(missing)}")
