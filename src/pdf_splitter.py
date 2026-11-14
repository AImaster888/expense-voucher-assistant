"""PDF 逐頁轉圖與檔案 SHA-256 雜湊工具。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF


SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass(frozen=True)
class PageImage:
    """單一待辨識頁面影像。"""

    page_number: int
    image_bytes: bytes
    mime_type: str


def compute_sha256(file_path: Path) -> str:
    """計算本機檔案的 SHA-256 雜湊值。"""
    hasher = hashlib.sha256()
    with file_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def extract_pages(file_path: Path, dpi: int = 200) -> list[PageImage]:
    """讀取圖片或 PDF，若為 PDF 則逐頁渲染為 PNG 影像。"""
    ext = file_path.suffix.lower()
    if ext == ".pdf":
        pages: list[PageImage] = []
        with fitz.open(str(file_path)) as doc:
            for idx, page in enumerate(doc, start=1):
                pix = page.get_pixmap(dpi=dpi)
                pages.append(
                    PageImage(
                        page_number=idx,
                        image_bytes=pix.tobytes("png"),
                        mime_type="image/png",
                    )
                )
        return pages

    if ext in SUPPORTED_IMAGE_EXTS:
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".tif": "image/tiff",
            ".tiff": "image/tiff",
        }
        return [
            PageImage(
                page_number=1,
                image_bytes=file_path.read_bytes(),
                mime_type=mime_map[ext],
            )
        ]

    raise ValueError(f"不支援的檔案格式：{file_path.name}（僅支援 PDF 與常見影像格式）")
