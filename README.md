# 核銷憑證辨識與建檔助手

Windows 本機 Python + Google Cloud Vertex AI（或 OpenAI API）+ Google Drive / Google Sheets 自動化管線。

> 版本：v0.2.4（2026-11-14）

> 📖 完整日常操作手冊、Windows 排程教學與 Vertex AI API 計費說明，請見 [USER_GUIDE.md](USER_GUIDE.md)。

## 核心原則

- **不猜測、不覆蓋原文**：OCR 引擎只負責擷取圖片上實際出現的文字與數字，模糊或遮擋欄位留空並記錄原因。
- **確定性計算與檢核**：唯一代碼（`EXP-YYYYMMDD-UUIDv4`）、民國年轉西元、中文大寫國字換算、未稅/稅額/含稅驗算與明細加總核對，全由純 Python 邏輯執行。
- **人工覆核主導**：程式寫入《憑證主檔》時，`覆核狀態` 永遠鎖定為 `待覆核`。
- **冪等寫入與單一執行鎖**：透過 `來源識別碼／雜湊` 與 `明細序號` 回查補寫，並以鎖定檔防止多個排程同時寫壞 Google Sheets。

## 快速開始

1. 安裝相依套件：
   ```powershell
   pip install -r requirements.txt
   ```
2. 複製 `.env.example` 為 `.env`，填入 `GOOGLE_CLOUD_PROJECT`、Google Drive 資料夾 ID 與 `SPREADSHEET_ID`。
3. 設定 Google 認證：
   - **Vertex AI**：執行 `gcloud auth application-default login`（或設定 `GOOGLE_APPLICATION_CREDENTIALS`）。
   - **Drive / Sheets**：將從 GCP 下載的桌面版 OAuth JSON 放至 `credentials/oauth_client.json`（首次執行會開啟瀏覽器授權並存入 `credentials/token.json`）。
4. 本機預覽測試（不寫入雲端；`samples\` 有四張虛構的範例發票：`demo_invoice.png` 收銀機發票、`demo_receipt_faded.png` 褪色的飲料店收銀機發票、`demo_einvoice.png` 電子發票證明聯、`demo_invoice_handwritten.png` 三聯式手開發票）：
   ```powershell
   python main.py --dry-run samples\demo_invoice.png
   ```
5. 執行完整 Drive → Vertex AI → Sheets 管線：
   ```powershell
   python main.py
   ```

## 切換 AI 辨識引擎

只有「看圖認字」這一步用 AI，可在 `.env` 用 `OCR_PROVIDER` 切換，其餘檢核與寫入流程不變：

| `OCR_PROVIDER` | 需要的設定 |
| :--- | :--- |
| `vertex`（預設） | `GOOGLE_CLOUD_PROJECT`、`GOOGLE_CLOUD_LOCATION`、`VERTEX_MODEL`、`VERTEX_THINKING_LEVEL`（預設 `low`） |
| `openai` | `OPENAI_API_KEY`、`OPENAI_MODEL`（預設 `gpt-5-mini`） |

`VERTEX_THINKING_LEVEL` 控制 Gemini 3 的思考量，思考 token 照輸出價計費。同一張發票實測：預設思考 62,913 tokens、253 秒、約 US\$0.19；`low` 0 tokens、4 秒、約 US\$0.0026。Gemini 2.x 不支援此參數，程式會自動略過。

OpenAI 版使用 Responses API 的 Structured Outputs（strict JSON Schema），系統提示詞與 Vertex 版相同，回傳結果一樣經過 pydantic 驗證。

## 交給 AI 助理執行（選用）

`skills/expense-voucher-assistant/SKILL.md` 是公開版技能檔，Claude Code、Antigravity 這類 AI 代理工具都能用。把整個 `expense-voucher-assistant` 資料夾複製到工具讀技能的位置（例如 Claude Code 的 `%USERPROFILE%\.claude\skills\`、Antigravity 的 `<工作區>\.agents\skills\`），之後說「幫我處理待處理發票」「試跑範例發票」「上傳前檢查」即可。

技能檔不含任何資料夾 ID、試算表或金鑰，全部讀您自己的 `.env`；也規定 AI 不讀出 `.env`、不請您把金鑰貼到對話裡。

## 公開版與個人設定分開

本 repo 內的資料夾 ID、試算表、發票號碼、統編都是範例值。您自己的設定請放在以下檔案，`.gitignore` 已排除、不會上傳：

| 檔案 | 用途 |
| :--- | :--- |
| `.env` | 專案 ID、資料夾 ID、試算表 ID、API Key |
| `credentials/` | Google OAuth／服務帳號憑證 |
| `*.local.md` | 私人筆記（例如真實的資料夾連結） |
| 發票圖片／PDF | 除了 `samples/` 的四張虛構範例，一律不進版控 |

## 執行單元測試

```powershell
python -m pytest -v
```

## 版本歷程

| 版本 | 日期 | 內容 |
| :--- | :--- | :--- |
| v0.2.4 | 2026-11-14 | 新增公開版技能檔 `skills/expense-voucher-assistant/SKILL.md`（v1.0）：第一次安裝、假發票試跑、正式處理 Drive、預覽本機發票、上傳 GitHub 前檢查個資與金鑰；技能檔不含任何個人設定 |
| v0.2.3 | 2026-11-14 | 發票號碼正規化也去掉減號：紙本電子發票證明聯印 `AB-12345678`、收銀機發票印 `AB 12345678`，寫入與去重一律存 10 碼 `AB12345678`（與財政部平台、兌獎 App 一致）；新增三張虛構範例 `samples/demo_receipt_faded.png`（模擬熱感應紙褪色的收銀機發票）、`samples/demo_einvoice.png`（電子發票證明聯）、`samples/demo_invoice_handwritten.png`（三聯式手開發票，可驗證未稅＋稅額＝總計與國字大寫）；新增 2 個測試 |
| v0.2.2 | 2026-11-14 | 公開版整理：文件與測試中的真實路徑、Drive／試算表連結、發票號碼與統編改成範例值；新增虛構範例發票 `samples/demo_invoice.png`；`.gitignore` 排除備份檔、私人筆記、發票影像與影片腳本 |
| v0.2.1 | 2026-11-14 | Vertex AI 新增 `VERTEX_THINKING_LEVEL`（預設 `low`），避免 Gemini 3 預設思考一張發票燒掉約 6 萬 tokens；Gemini 1.x／2.x 自動不送此參數；Vertex 預設改成 `global`＋`gemini-3-flash-preview`（與實測、影片腳本一致）；新增 3 個測試 |
| v0.2.0 | 2026-11-14 | 新增 OpenAI 辨識引擎（`OCR_PROVIDER=openai`）；Vertex AI 仍為預設；OpenAI 送圖用 `detail: high`；系統提示詞新增第 7 條「台灣收銀機發票版面慣例」（賣方統編印成 `No:`），兩種引擎共用；發票號碼與統編寫入及去重前先正規化（全形轉半形、去空白、轉大寫，`normalize_id`），避免 `AB 12345678` 與 `AB12345678` 比對不到；新增 `tests/test_ocr_provider.py` |
| v0.1.0 | 2026-11-13 | 初版：Vertex AI Gemini 辨識＋Drive／Sheets 管線 |
