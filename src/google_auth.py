"""Google Drive 與 Google Sheets 共用認證工具。"""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def load_google_credentials(
    auth_mode: str,
    credentials_path: Path,
    token_path: Path,
):
    """載入 OAuth 2.0 或 Service Account 憑證。"""
    if auth_mode == "service_account":
        if not credentials_path.exists():
            raise FileNotFoundError(f"找不到 Service Account 金鑰檔：{credentials_path}")
        return service_account.Credentials.from_service_account_file(
            str(credentials_path),
            scopes=SCOPES,
        )

    if auth_mode != "oauth":
        raise ValueError(f"不支援的 AUTH_MODE：{auth_mode}（請設為 oauth 或 service_account）")

    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise FileNotFoundError(
                    f"找不到 OAuth 用戶端金鑰檔：{credentials_path}。"
                    "請從 Google Cloud Console 下載桌面應用程式 OAuth JSON。"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def build_drive_and_sheets_services(
    auth_mode: str,
    credentials_path: Path,
    token_path: Path,
):
    """建立 Google Drive v3 與 Google Sheets v4 API client。"""
    creds = load_google_credentials(auth_mode, credentials_path, token_path)
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
    sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return drive_service, sheets_service
