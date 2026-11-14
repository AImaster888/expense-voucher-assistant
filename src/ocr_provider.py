"""可抽換的 OCR / 視覺辨識引擎介面，含 Google Cloud Vertex AI 與 OpenAI 兩種實作。"""

from __future__ import annotations

import base64
import os
from abc import ABC, abstractmethod

from google import genai
from google.genai import types

from src.models import PageExtractionResult


SYSTEM_PROMPT = """你是一個嚴謹的「核銷憑證原文擷取助理」，只能據實記錄圖片中實際看得到的發票或收據資訊。

必須嚴格遵守以下規則：
1. 絕對不可猜測、推算或補全任何模糊、被遮擋、裁切或原文未出現的欄位（包含抬頭、統編、日期、發票號碼、金額、數量、單價、商品名稱）。看不清或沒有的欄位請一律輸出 null，並把原因寫進 unclear_fields。
2. 統一編號與發票／收據號碼必須完整保留前導 0（例如 "04123456"）。
3. 金額（國字）只能填入圖片上實際印出或手寫的中文大寫金額，若沒有中文大寫金額請輸出 null，不可自行把阿拉伯數字轉成國字。
4. 若單一頁面內有多張獨立發票或收據，請依照從上到下、從左到右的順序分別拆成多個 vouchers 物件，並依序標記 voucher_index_on_page (1, 2, ...)。
5. 若本頁明顯是上一頁長條發票或同一張憑證的接續頁面，請設定 continues_from_prev_page=true；若無法判斷是否屬於上一張憑證，設定 continuation_uncertain=true。
6. 若整頁完全無法辨識（非憑證或嚴重失焦），請讓 vouchers 為空陣列並填寫 page_unreadable_reason。
7. 台灣收銀機統一發票的版面慣例（只用來判斷欄位位置，仍不可猜字）：
   - 賣方統編通常印在店名、地址、電話附近，標示為「No:」「統編:」或「營業人統編」，後面接 8 位數字，請填入 seller_tax_id。
   - 發票中段標示「統一編號:」的欄位是「買受人」統編；該處空白代表買方沒打統編，buyer_tax_id 請填 null。
   - 店名可能分成兩處印出（例如抬頭的公司名稱與「店:」後的門市名），請照抬頭原文填入 seller_name。
"""


class OCRProvider(ABC):
    """憑證影像辨識抽象介面，便於未來切換企業 API 或地端模型。"""

    @abstractmethod
    def extract_page(self, image_bytes: bytes, mime_type: str, page_number: int) -> PageExtractionResult:
        """辨識單一頁面影像，回傳結構化原文結果。"""


class VertexAIGeminiProvider(OCRProvider):
    """使用 Google Cloud Vertex AI Gemini 模型進行多模態結構化擷取。"""

    def __init__(
        self,
        project_id: str,
        location: str = "global",
        model: str = "gemini-3-flash-preview",
        thinking_level: str = "low",
    ) -> None:
        self.client = genai.Client(
            vertexai=True,
            project=project_id,
            location=location,
        )
        self.model = model
        self.thinking_level = thinking_level

    def _thinking_config(self) -> types.ThinkingConfig | None:
        """Gemini 3 起用 thinking_level 控制思考量（思考 token 照輸出價計費）。

        實測同一張發票：預設思考 62,913 tokens／253 秒，low 0 tokens／4 秒。
        Gemini 1.x／2.x 不支援 thinking_level，一律不送；空字串代表用模型預設。
        """
        if not self.thinking_level or self.model.startswith(("gemini-1", "gemini-2")):
            return None
        return types.ThinkingConfig(thinking_level=self.thinking_level)

    def extract_page(self, image_bytes: bytes, mime_type: str, page_number: int) -> PageExtractionResult:
        """呼叫 Vertex AI 並強制輸出符合 PageExtractionResult 的 JSON。"""
        response = self.client.models.generate_content(
            model=self.model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                f"請辨識這份第 {page_number} 頁的憑證影像，嚴格遵守系統指令擷取原文欄位。",
            ],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=PageExtractionResult,
                thinking_config=self._thinking_config(),
            ),
        )
        if not response.text:
            return PageExtractionResult(
                vouchers=[],
                page_unreadable_reason="Vertex AI 回傳空內容，無法辨識此頁",
            )
        return PageExtractionResult.model_validate_json(response.text)


def _openai_strict_schema() -> dict:
    """把 PageExtractionResult 轉成 OpenAI strict JSON Schema。

    移除 default／pattern／minimum 等 OpenAI strict 模式可能不收的關鍵字；
    數值格式與範圍仍由 pydantic 在 model_validate_json 時把關。
    """
    from openai.lib._pydantic import to_strict_json_schema

    def strip(node):
        if isinstance(node, dict):
            return {
                k: strip(v)
                for k, v in node.items()
                if k not in ("default", "pattern", "minimum", "title")
            }
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return strip(to_strict_json_schema(PageExtractionResult))


class OpenAIProvider(OCRProvider):
    """使用 OpenAI API（Responses API＋Structured Outputs）進行多模態結構化擷取。

    API Key 由 openai 套件自動讀取環境變數 OPENAI_API_KEY（.env 載入後即生效）。
    """

    def __init__(self, model: str = "gpt-5-mini", client=None) -> None:
        if client is None:
            from openai import OpenAI

            client = OpenAI()
        self.client = client
        self.model = model
        self.schema = _openai_strict_schema()

    def extract_page(self, image_bytes: bytes, mime_type: str, page_number: int) -> PageExtractionResult:
        """呼叫 OpenAI 並強制輸出符合 PageExtractionResult 的 JSON。"""
        data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        response = self.client.responses.create(
            model=self.model,
            instructions=SYSTEM_PROMPT,
            input=[
                {
                    "role": "user",
                    "content": [
                        # detail=high：避免自動縮圖，熱感應紙細字（統編、品名）才認得出來
                        {"type": "input_image", "image_url": data_url, "detail": "high"},
                        {
                            "type": "input_text",
                            "text": f"請辨識這份第 {page_number} 頁的憑證影像，嚴格遵守系統指令擷取原文欄位。",
                        },
                    ],
                }
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "page_extraction_result",
                    "schema": self.schema,
                    "strict": True,
                }
            },
        )
        if not response.output_text:
            return PageExtractionResult(
                vouchers=[],
                page_unreadable_reason="OpenAI 回傳空內容（可能被拒答），無法辨識此頁",
            )
        return PageExtractionResult.model_validate_json(response.output_text)


def build_ocr_provider(settings) -> OCRProvider:
    """依 .env 的 OCR_PROVIDER（vertex／openai）建立辨識引擎。"""
    if settings.ocr_provider == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OCR_PROVIDER=openai 時，請於 .env 設定 OPENAI_API_KEY。")
        return OpenAIProvider(model=settings.openai_model)
    if settings.ocr_provider == "vertex":
        if not settings.google_cloud_project:
            raise ValueError("請於 .env 設定 GOOGLE_CLOUD_PROJECT 以呼叫 Vertex AI。")
        return VertexAIGeminiProvider(
            project_id=settings.google_cloud_project,
            location=settings.google_cloud_location,
            model=settings.vertex_model,
            thinking_level=settings.vertex_thinking_level,
        )
    raise ValueError(f"OCR_PROVIDER 只能是 vertex 或 openai，目前是：{settings.ocr_provider}")
