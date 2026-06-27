# 開源上架前安全審查確認書

> **專案**：wechat-ai-bot  
> **路徑**：`c:\ai.wechat.bot`  
> **審查日期**：2026-06-27  
> **目標倉庫**：`https://github.com/wechat-ai-bot/wechat-ai-bot`（預定）  
> **授權**：MIT（著作權人 yumemi2020 \<yumemi2001@gmail.com\>）

本文供 **Gemini 或其他人類審查者** 二次核對：確認將進入 Git 的檔案不含機敏資料，且 `.gitignore` 已排除執行期產物。

---

## 1. 審查結論（摘要）

| 項目 | 狀態 |
|------|------|
| 真實內網 IP（如 192.168.x.x） | ✅ 無，文件中僅保留泛用範例 |
| PadPro auth key / 微信 wxid | ✅ 已清除，改為 placeholder |
| 真實資料庫密碼 | ✅ 已清除，統一為 `changeme` 或 `${MYSQL_ROOT_PASSWORD:-changeme}` |
| NAS 絕對路徑 | ✅ 已清除，Portainer 範例使用 `<YOUR_DATA_PATH>` |
| 個人網域 | ✅ 已清除（`vite.config.js` 原含 `yumemi.hourcenter.org.tw`，已改為 localhost） |
| `.env` / 執行期資料 | ✅ 不存在於工作區；已列入 `.gitignore` |
| 第三方全文複製文件 | ✅ 無 `WeChatPadProMAX_full.md`；僅連結官方網站 |

**結論：截至審查日，專案可進行 `git init` 與首次推送。** 建議審查者仍執行下方「建議複核指令」做最後確認。

---

## 2. 已刪除、不應進 Git 的項目

以下項目已從工作區移除，且由 `.gitignore` 排除：

| 路徑 / 模式 | 說明 |
|-------------|------|
| `.env` | 含真實 API key、PadPro 授權、wxid |
| `frontend/.env` | 含真實 API 位址 |
| `db_data/` | MariaDB 資料（對話、聯絡人、記憶） |
| `photos/` | 上傳圖片與接收媒體快取 |
| `frontend/node_modules/` | npm 依賴 |
| `frontend/dist/` | 建置產物 |
| `__pycache__/`、`*.pyc` | Python 快取 |
| `Running` | 執行期標記檔（已刪除並加入 `.gitignore`） |

---

## 3. 將進入 Git 的完整檔案清單（共 28 個）

```
c:\ai.wechat.bot\
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Dockerfile
├── LICENSE
├── main.py
├── README.md
├── requirements.txt
├── SECURITY.md
├── deploy\
│   ├── docker-compose.with-padpro.yml
│   ├── portainer-stack.example.yml
│   └── README.md
├── docs\
│   └── AI_PERSONA_AND_DB_STATUS.md
└── frontend\
    ├── .env.example
    ├── .gitignore
    ├── Dockerfile
    ├── index.html
    ├── package.json
    ├── package-lock.json
    ├── postcss.config.js
    ├── tailwind.config.js
    ├── vite.config.js
    ├── public\
    │   ├── manual.html
    │   └── user-guide.html
    └── src\
        ├── api.js
        ├── App.vue
        ├── main.js
        └── style.css
```

---

## 4. 機敏資料掃描結果

### 4.1 已搜尋模式（應無真實命中）

| 模式 | 結果 |
|------|------|
| `192.168.10.251` | 無 |
| `2daea24c-8f7c-4c2b-bcfb-61a39c64a1ca`（舊 PadPro key） | 無 |
| `a40445210`（舊 wxid） | 無 |
| `RootPassword123` | 無 |
| `yumemi.hourcenter.org.tw` | 無 |
| OpenAI `sk-...` 金鑰 | 無 |

### 4.2 刻意保留的公開資訊（非機密）

| 內容 | 檔案 | 說明 |
|------|------|------|
| `yumemi2020 <yumemi2001@gmail.com>` | `LICENSE` | MIT 著作權人（公開聯絡方式） |
| `127.0.0.1` | 多處 | 本地開發 placeholder |
| `changeme` | `.env.example`, compose | 假密碼範例 |
| `your-padpro-auth-key` / `your-wechat-wxid` | `.env.example`, `main.py` fallback | 假值 |
| `https://wx.knowhub.cloud/` | `README.md` 等 | PadPro 官方公開網址 |
| `http://伺服器IP:9960` | `user-guide.html` | 泛用說明，無真實 IP |

### 4.3 程式碼中的「敏感欄位名稱」（非實際密鑰）

以下為**變數名、API 欄位、文件說明**，不含真實值，屬正常開源程式碼：

- `PADPRO_AUTH_KEY`、`PADPRO_WXID`、`api_key`、`wx_id`（`main.py`、`App.vue`、`api.js`）
- `deploy/README.md` 部署說明

---

## 5. 各檔案安全狀態一覽

| 檔案 | 機敏風險 | 備註 |
|------|----------|------|
| `.env.example` | 低 | 僅假值 |
| `frontend/.env.example` | 低 | 僅 127.0.0.1 |
| `main.py` | 低 | fallback 皆為 placeholder |
| `frontend/src/api.js` | 低 | fallback `127.0.0.1:9950` |
| `frontend/src/App.vue` | 低 | 同上 |
| `frontend/vite.config.js` | 低 | 已移除個人網域 |
| `docker-compose.yml` | 低 | 密碼用 env 預設 changeme |
| `deploy/portainer-stack.example.yml` | 低 | `<YOUR_DATA_PATH>` placeholder |
| `frontend/public/manual.html` | 低 | 技術文件，無真實 IP |
| `frontend/public/user-guide.html` | 低 | 泛用「伺服器IP」說明 |
| `docs/AI_PERSONA_AND_DB_STATUS.md` | 低 | 開發筆記，無私密資料 |
| `LICENSE` / `SECURITY.md` / `README.md` | 低 | 公開文件 |

---

## 6. 本次審查期間額外修復

1. **`frontend/vite.config.js`**：`allowedHosts` 由個人網域 `yumemi.hourcenter.org.tw` 改為 `localhost` / `127.0.0.1`（可選 `all`）。
2. **`Running`**：刪除空檔，並加入 `.gitignore`。

---

## 7. 建議複核指令（給 Gemini / 審查者）

在專案根目錄執行：

```powershell
# 確認敏感字串無殘留
rg -n "192\.168\.10\.251|2daea24c|a40445210|RootPassword123|hourcenter|ai\.wechat\.project" .

# 確認不應追蹤的檔案不存在
Test-Path .env, frontend\.env, db_data, photos, frontend\node_modules, Running

# 確認 gitignore 涵蓋執行期目錄
Get-Content .gitignore

# 列出將被追蹤的檔案（git init 後）
git init
git add -n .
```

預期：

- 第一個指令：**無匹配**。
- 第二個指令：皆為 `False`。
- `git add -n .` 不應包含 `.env`、`db_data/`、`photos/` 等。

---

## 8. 已知限制（非機敏，但審查者應知）

1. **無控制台鑑權**：見 `SECURITY.md` 與 `README.md` 安全聲明。
2. **預設人設與 fallback 台詞**：`main.py` 含範例 AI 人設與斷線救援用語，為產品預設內容，非個人資料。
3. **GitHub Issues URL**：`SECURITY.md` 使用 `wechat-ai-bot/wechat-ai-bot`，需與實際倉庫一致。
4. **Portainer 範例**：部署前使用者必須自行替換 `<YOUR_DATA_PATH>` 與 build 映像。

---

## 9. 審查者簽核欄（可選）

| 審查者 | 日期 | 結果 |
|--------|------|------|
| Gemini | | ☐ 通過 ☐ 需修正 |
| 人工 | | ☐ 通過 ☐ 需修正 |

修正項目（若有）：

```
（留空或填寫）
```

---

*本文件由開源準備流程自動產生，隨專案一併提交至 `docs/`，供外部審查使用。*
