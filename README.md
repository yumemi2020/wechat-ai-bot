# wechat-ai-bot

以 [WeChatPadProMAX](https://wx.knowhub.cloud/) 為微信閘道，串接本地或雲端大語言模型（OpenAI 相容 API），並提供 Vue 3 網頁控制台，實現微信 AI 代答、人設管理、照片庫與長期記憶等功能。

> **免責聲明**：本專案為開源社群專案，與 WeChatPadProMAX 官方無關。使用微信相關功能須自行取得 PadPro 授權，並遵守微信服務條款與當地法規。作者不對帳號封禁、資料外洩或任何濫用後果負責。

## 📸 功能展示 (Demo)

### 1. 多媒體無縫解析（語音可直接於前端網頁播放）

![多媒體解析](image/2026-06-27%20163243.png)

*說明：突破傳統文字客服限制，支援網頁端直接檢視圖片，並將 SILK 語音即時轉檔為 MP3 播放。*

### 2. 多重人格精準分流，可定義不同 AI 人格設計回應

![多重人格](image/2026-06-27%20163142.png)

*說明：支援為不同的聯絡人綁定獨立的 AI 人格。*

### 3. 可定義不同 AI 人格所使用的照片庫

![照片庫](image/2026-06-27%20163112.png)

*說明：支援為不同的聯絡人綁定獨立的 AI 專屬照片庫。*

### 4. 可串接不同 AI 模型，也可使用本地模型（支援 llama.cpp）

![AI 模型支援](image/2026-06-27%20163133.png)

*說明：控制台可新增多組 LLM 設定，支援 OpenAI、DeepSeek、Groq、Ollama 等雲端 API，亦可指向 LM Studio、llama.cpp 等本地 OpenAI 相容端點，並可一鍵切換目前啟用的模型。*

### 5. 長期記憶庫摘要，可有效紀錄長期對話於資料庫中

![記憶庫](image/2026-06-27%20163149.png)

*說明：系統會依對話內容自動萃取聯絡人資訊並寫入資料庫；亦可於控制台手動編輯每位聯絡人的記憶摘要，供後續 AI 回覆參考。*

### 6. 可切換人工回應訊息

![人工回應](image/2026-06-27%20163201.png)

*說明：可於網頁控制台手動輸入並代發訊息，標記為人工回覆，與 AI 自動代答清楚區隔。*

### 7. 可切換 AI 回應訊息並支援多種人格

![AI 回應](image/2026-06-27%20163208.png)

*說明：可全域或逐聯絡人開啟 AI 代答；啟用後系統依當前綁定的人設與對話上下文自動產生回覆，不同聯絡人可呈現不同人格風格。*

### 8. 多媒體無縫解析，可點選圖片觀看

![圖片預覽](image/2026-06-27%20163234.png)

*說明：對話串中的圖片訊息可直接於網頁點選放大預覽，無需回到微信客戶端查看。*

### 9. 對話訊息無縫切換

![對話切換](image/2026-06-27%20163220.png)

*說明：左側聯絡人列表可即時切換對話視窗，新訊息透過 WebSocket 推播更新，網頁端與手機微信的對話內容保持同步。*

## 架構

```mermaid
flowchart LR
  subgraph WeChat
    Friend[微信好友]
  end
  subgraph PadPro["WeChatPadProMAX"]
    Engine[微信引擎]
  end
  subgraph Bot["wechat-ai-bot"]
    API[FastAPI 後端 :9950]
    UI[Vue 控制台 :9960]
    DB[(MariaDB)]
  end
  subgraph LLM["LLM 服務"]
    Model[本地 / 雲端 API]
  end
  Friend <-->|微信| Engine
  Engine -->|Webhook POST| API
  API -->|SendTextMsg 等| Engine
  API <-->|chat/completions| Model
  API <-->|持久化| DB
  UI <-->|REST / WebSocket| API
```

## 功能

- **AI 代答**：Webhook 接收訊息，依人設與上下文自動回覆（文字、圖片、語音辨識）
- **全域 / 聯絡人開關**：可逐人啟用或停用 AI
- **多人設庫**：建立、切換、指派不同 System Prompt 人設
- **動態 LLM 設定**：支援 OpenAI、DeepSeek、Groq、Ollama 等 OpenAI 相容端點
- **照片庫**：上傳關鍵字標記圖片，AI 可於對話中選圖發送
- **長期記憶**：自動萃取聯絡人資訊並持久化至資料庫
- **對話紀錄**：訊息、設定、人設皆存於 MariaDB（`db_data/` volume，已列入 `.gitignore`）
- **掃碼登入**：控制台產生 QR Code，透過 PadPro 完成微信登入
- **手動發送**：網頁端代發文字、圖片給指定聯絡人

## 前置需求

- [Docker](https://docs.docker.com/get-docker/) 與 Docker Compose
- 已部署的 **WeChatPadProMAX** 服務（需與本專案容器互通，見下方網路說明）
- 可連線的 **LLM API**（LM Studio、Ollama、OpenAI 等 OpenAI 相容介面）

## 快速開始

```bash
# 1. 複製環境變數範例
cp .env.example .env
cp frontend/.env.example frontend/.env

# 2. 編輯 .env，填入 PadPro 位址、授權碼、wxid、LLM 設定等

# 3. 若與 PadPro 同機 Docker 部署，確認外部網路存在：
docker network create wechatpadpromax_wechat_net   # 若 PadPro 安裝腳本尚未建立

# 4. 啟動
docker compose up -d --build
```

| 服務 | 埠號 | 說明 |
|------|------|------|
| 後端 API | **9950** | FastAPI、Webhook（`/webhook`） |
| 前端控制台 | **9960** | Vue 開發伺服器 |
| MariaDB | 3306（內部） | 僅容器網路內可達 |

瀏覽器開啟 `http://<你的主機>:9960` 即可使用控制台。

### PadPro 網路

根目錄 `docker-compose.yml` 預設加入外部網路 `wechatpadpromax_wechat_net`，以便與 PadPro 容器互通。詳細說明與替代啟動方式見 [`deploy/docker-compose.with-padpro.yml`](deploy/docker-compose.with-padpro.yml)。

### Portainer 部署

若使用 [Portainer](https://www.portainer.io/) 管理容器，可參考 [`deploy/portainer-stack.example.yml`](deploy/portainer-stack.example.yml)。將其中的 `<YOUR_DATA_PATH>` 替換為宿主機專案路徑後，於 Portainer **Stacks → Add stack** 貼上部署。更多說明見 [`deploy/README.md`](deploy/README.md)。

## 環境變數

主要設定見 [`.env.example`](.env.example) 與 [`frontend/.env.example`](frontend/.env.example)。

| 變數 | 說明 |
|------|------|
| `LOCAL_API_BASE` | LLM API 根路徑（OpenAI 相容 `/v1`） |
| `PADPRO_URL` | WeChatPadProMAX 服務位址 |
| `PADPRO_AUTH_KEY` | PadPro 授權碼 |
| `PADPRO_WXID` | 本帳號微信 ID |
| `PADPRO_WEBHOOK_PUBLIC_URL` | 對外 Webhook URL（如 `http://主機:9950/webhook`） |
| `DATABASE_URL` | MySQL 連線字串 |
| `VITE_API_BASE` | 前端連後端 API 位址 |

## 文件

啟動後可於瀏覽器開啟：

| 文件 | 路徑 | 對象 |
|------|------|------|
| 使用者指南 | `http://<主機>:9960/user-guide.html` | 操作說明 |
| 技術手冊 | `http://<主機>:9960/manual.html` | 架構、API、除錯 |
| 開發筆記 | [`docs/AI_PERSONA_AND_DB_STATUS.md`](docs/AI_PERSONA_AND_DB_STATUS.md) | 資料表與實作現況 |
| 開源審查確認書 | [`docs/OPEN_SOURCE_READINESS.md`](docs/OPEN_SOURCE_READINESS.md) | 上架前機敏資料核對清單 |

PadPro 官方文件：[WeChatPadProMAX](https://wx.knowhub.cloud/)

## 安全聲明

**目前控制台無登入驗證。** 任何能存取 `:9960` / `:9950` 的人皆可操作帳號與 AI 設定。僅建議在**內網、VPN 或受信任環境**使用。若需對外暴露，請自行加上反向代理（HTTPS）、IP 白名單或鑑權層。詳見 [SECURITY.md](SECURITY.md)。

## 授權

[MIT License](LICENSE)

## 🙏 致謝與核心依賴 (Acknowledgements)

本系統的微信底層通訊與協議解析，強烈依賴並感謝以下專案的支持：

* **[WeChatPadPro](https://github.com/WeChatPadPro/WeChatPadPro)** - 提供穩定、強大且高效的 WeChat iPad 協議網關服務，是本專案能實現全自動化收發的關鍵基石。
