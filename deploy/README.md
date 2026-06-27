# 部署範例

本目錄存放各種部署方式的參考設定。

## 現有檔案

| 檔案 | 說明 |
|------|------|
| [`docker-compose.with-padpro.yml`](docker-compose.with-padpro.yml) | 與 WeChatPadProMAX 同 Docker 網路整合的 Compose 範例 |
| [`portainer-stack.example.yml`](portainer-stack.example.yml) | Portainer Stack 範例（NAS / 宿主機 volume 部署，已去敏） |

## Portainer Stack 範例

[`portainer-stack.example.yml`](portainer-stack.example.yml) 由實際 Portainer 部署匯出並去敏，主要替換項目：

- `MYSQL_ROOT_PASSWORD` → `${MYSQL_ROOT_PASSWORD:-changeme}`
- `/volume1/docker/ai.wechat.project` → `<YOUR_DATA_PATH>`
- `my_wechat_ai_bot:v1` → `wechat-ai-bot:latest`（請自行 build 或改用 `build` 區塊）
- 新增 `env_file` 指向 `<YOUR_DATA_PATH>/.env`

### 從 Portainer 匯出 Stack（自行維護時）

1. 開啟 Portainer → **Stacks** → 選取你的 wechat-ai-bot stack
2. 點 **Editor** 或 **Export**，複製 YAML 內容
3. 另存為 `portainer-stack.example.yml`

### 去敏檢查清單

提交前請確認已移除或替換：

- 真實密碼、API 金鑰、PadPro auth key、微信 wxid
- 內網 IP（如 `192.168.x.x`）→ 改為 `127.0.0.1` 或 `<YOUR_HOST>`
- NAS / 本機絕對路徑（如 `/volume1/docker/...`）→ 改為 `<YOUR_DATA_PATH>`
- 個人網域、DuckDNS 等可識別身分的資訊

可參考專案根目錄的 [`.env.example`](../.env.example) 作為 placeholder 寫法。

### 使用範例 Stack

去敏後的 `portainer-stack.example.yml` 可在 Portainer 中：

1. **Stacks** → **Add stack**
2. 貼上 YAML，並依環境調整 volume 路徑與環境變數
3. 部署前請先建立 `.env`（見根目錄 `.env.example`）
