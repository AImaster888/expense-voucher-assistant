"""Google Drive 檔案讀取、下載與安全歸檔移動。"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from googleapiclient.http import MediaIoBaseDownload


@dataclass(frozen=True)
class DriveFileMeta:
    """Google Drive 來源檔案基本中繼資料。"""

    file_id: str
    name: str
    mime_type: str
    md5_checksum: str
    web_view_link: str


class DriveClient:
    """封裝 Google Drive API 操作，確保移動檔案不丟失原始連結且不刪除原檔。"""

    def __init__(self, service: Any) -> None:
        self.service = service

    def list_pending_files(self, folder_id: str) -> list[DriveFileMeta]:
        """列出待處理資料夾內非資料夾且未進垃圾桶的檔案。"""
        files: list[DriveFileMeta] = []
        page_token: str | None = None
        query = (
            f"'{folder_id}' in parents and trashed = false "
            "and mimeType != 'application/vnd.google-apps.folder'"
        )
        while True:
            resp = (
                self.service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="nextPageToken, files(id, name, mimeType, md5Checksum, webViewLink)",
                    orderBy="createdTime asc",
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for item in resp.get("files", []):
                files.append(
                    DriveFileMeta(
                        file_id=item["id"],
                        name=item["name"],
                        mime_type=item.get("mimeType", ""),
                        md5_checksum=item.get("md5Checksum", ""),
                        web_view_link=item.get(
                            "webViewLink",
                            f"https://drive.google.com/file/d/{item['id']}/view",
                        ),
                    )
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return files

    def download_file(self, file_id: str, dest_path: Path) -> Path:
        """下載 Drive 原始檔案到本機暫存路徑。"""
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        with io.FileIO(str(dest_path), "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return dest_path

    def move_file(self, file_id: str, from_folder_id: str, to_folder_id: str) -> None:
        """移動檔案至目標資料夾（fileId 與 webViewLink 維持不變）。"""
        if from_folder_id == to_folder_id:
            return
        self.service.files().update(
            fileId=file_id,
            addParents=to_folder_id,
            removeParents=from_folder_id,
            fields="id, parents",
            supportsAllDrives=True,
        ).execute()
