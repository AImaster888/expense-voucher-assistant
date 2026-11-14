---
name: expense-voucher-assistant
description: 核銷憑證辨識與建檔助手（公開版）。帶使用者第一次安裝設定、用 samples 假發票試跑、正式處理 Google Drive「待處理」資料夾、預覽本機發票，以及上傳 GitHub 前檢查有沒有夾帶個資或金鑰。觸發：處理待處理發票、跑發票辨識、發票建檔、核銷憑證、試跑範例發票、安裝核銷憑證系統、上傳前檢查、expense-voucher-assistant。
---

# 核銷憑證辨識與建檔助手（公開版技能）

> 技能版本：v1.0（2026-11-14）｜對應程式 v0.2.4

本技能只用相對路徑和 `.env` 裡的設定，**不寫死任何人的資料夾 ID、試算表、金鑰或個資**。每個人的真實設定放在自己的 `.env`、`credentials\` 和 `*.local.md`，這些都已列入 `.gitignore`。

## 安全規則（一定要遵守）

1. **不讀出、不顯示、不貼出** `.env`、`credentials\` 裡的任何內容。要確認設定，只看檔案在不在，或直接跑程式看錯誤訊息。
2. **不要請使用者把 API 金鑰貼到對話裡。** 請使用者自己用記事本打開 `.env` 填寫。
3. **不把金鑰、資料夾 ID、試算表 ID 寫進** 程式碼、文件、技能檔或 commit 訊息。
4. **覆核狀態永遠是「待覆核」**：不替使用者判定憑證真偽或可否核銷，也不改成「已確認」。
5. **不刪原始檔**：Drive 檔案只在 `待處理`→`處理完成`／`待人工確認`／`處理失敗` 之間搬移。
6. **同一時間只跑一個 `main.py`**（程式有 `.lock` 鎖定檔）。

以下指令都在專案資料夾（有 `main.py` 的那一層）執行。

## 情境 A：第一次安裝設定

1. 安裝套件：`pip install -r requirements.txt`
2. 複製設定檔：`copy .env.example .env`，請使用者自己打開 `.env` 填寫：
   - `OCR_PROVIDER`：`vertex`（預設）或 `openai`
   - 用 Vertex：`GOOGLE_CLOUD_PROJECT`；`VERTEX_THINKING_LEVEL` 保持 `low`（不設 low，一張發票可能燒掉幾萬個思考 token）
   - 用 OpenAI：`OPENAI_API_KEY`（到 OpenAI 後台關掉自動儲值）
   - 正式處理 Drive 時才需要：四個 Drive 資料夾 ID、`SPREADSHEET_ID`
3. Vertex 認證：`gcloud auth application-default login`。
4. Drive／Sheets 認證：把 OAuth 桌面版 JSON 放到 `credentials\oauth_client.json`，第一次執行會開瀏覽器授權。
5. 詳細步驟以 `README.md`、`USER_GUIDE.md` 為準。

## 情境 B：用假發票試跑（不需要 Drive、試算表）

只要填好 AI 金鑰（或 Vertex 專案）就能跑：

```powershell
python main.py --dry-run samples\demo_invoice_handwritten.png
```

`samples\` 的四張假發票：

| 檔案 | 版面 | 預期結果 |
| :--- | :--- | :--- |
| `demo_invoice_handwritten.png` | 三聯式手開 | 辨識完成（未稅＋稅額＝總計、國字大寫、民國轉西元都一致） |
| `demo_invoice.png` | 收銀機發票 | 需人工確認（沒印未稅、稅額，無法核對） |
| `demo_receipt_faded.png` | 褪色的收銀機發票 | 需人工確認（同上；可用來比較不同 AI） |
| `demo_einvoice.png` | 電子發票證明聯 | 需人工確認；號碼印 `GH-23456789`，存成 `GH23456789` |

回報時標出 `辨識狀態`、`金額核對結果`、`疑似重複`、`備註`，並提醒「預覽不會寫入試算表」。

## 情境 C：正式處理 Drive「待處理」資料夾

1. 確認使用者已把發票照片或 PDF 放進 Drive 的 `待處理`。
2. 執行 `python main.py`，確認結束代碼為 `0`。
3. 回報：處理檔案數、寫入憑證數、疑似重複數、待人工確認數、跳過數、失敗數，以及要人工核對的 `EXP-...` 唯一代碼。
4. 提醒使用者到試算表把核對過的列改成「已確認」，這一步由人做。

## 情境 D：預覽使用者自己的發票

```powershell
python main.py --dry-run "<發票檔案的完整路徑>"
```

使用者自己的發票影像不要複製進專案資料夾，也不要加進 git。

## 情境 E：修改程式後

先跑 `python -m pytest -q`，全部通過才回報完成。

## 情境 F：上傳 GitHub 前檢查

1. 預覽要加入的檔案：`git add -n .`
2. 清單裡**不可以**出現：`.env`（`.env.example` 可以）、`credentials\`、`token.json`、`*.local.md`、`*.bak-*`、`samples\` 以外的發票圖片或 PDF。
3. 清單沒問題才 `git add .`，再找暫存內容裡像金鑰的字串。有列出檔名就停下來請使用者處理，不要自己把內容貼出來：
   ```powershell
   git diff --cached -G "AIza|sk-|private_key|client_secret" --name-only
   ```
4. 請使用者自己確認文件裡沒有真實的資料夾 ID、試算表連結、姓名、Email、統編。
5. 確認沒問題才 commit；**push 前一定要先問使用者**。
