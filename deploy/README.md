# 部署範例

本目錄存放各種部署方式的參考設定。

## 檔案說明

| 檔案 | 說明 |
|------|------|
| [`docker-compose.with-padpro.yml`](docker-compose.with-padpro.yml) | 與 WeChatPadProMAX 同 Docker 網路整合的 Compose 範例 |
| [`portainer-stack.example.yml`](portainer-stack.example.yml) | Portainer Stack 範例（NAS / 宿主機 volume 部署） |

## Portainer 部署

1. 將 [`portainer-stack.example.yml`](portainer-stack.example.yml) 中的 `<YOUR_DATA_PATH>` 替換為宿主機上的專案路徑
2. 複製根目錄 [`.env.example`](../.env.example) 為 `.env` 並填入設定
3. 確認 PadPro 外部網路已建立：`docker network create wechatpadpromax_wechat_net`
4. Portainer → **Stacks** → **Add stack** → 貼上 YAML → Deploy

後端映像需先 build：

```bash
docker build -t wechat-ai-bot:latest <YOUR_DATA_PATH>
```

部署前請依環境調整 volume 路徑、環境變數與映像名稱，可參考 [`.env.example`](../.env.example) 的 placeholder 寫法。
