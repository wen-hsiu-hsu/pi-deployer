# pi-deployer — Raspberry Pi 通用輕量部署工具

## 專案動機

目前 `webhook-server.py` 綁死在 glance 專案內，路徑、branch、部署腳本全部硬編碼。每次在 Pi 上新增一個專案就要複製一份 webhook server，維護成本隨專案數量線性增長。

**pi-deployer** 將部署邏輯抽離為獨立服務，透過設定檔管理多個專案，所有 Pi 上的 Git 專案共用同一個 webhook endpoint。

---

## 架構總覽

```
GitHub push (任意 repo)
  --> Cloudflare Tunnel (p-webhook.hsiu.soy)
    --> pi-deployer (:5000)
      --> 解析 payload 中的 repository.full_name
      --> 比對 projects.yml 找到對應專案設定
      --> 驗證 HMAC-SHA256 簽名（每專案可設獨立 secret）
      --> 篩選 branch
      --> 回傳 202 Accepted
      --> 背景執行該專案的 deploy script
        --> git pull
        --> 自訂部署命令（docker compose / systemctl / 任意腳本）
        --> 健康檢查（可選）
      --> Telegram 通知（觸發 / 成功 / 失敗 / 超時）
```

### 與現行架構差異

| 項目 | 現行 (glance 內建) | pi-deployer (獨立專案) |
|------|-------------------|----------------------|
| 部署方式 | 隨 glance 一起部署 | 獨立 systemd service |
| 專案數量 | 1 個 | 不限，設定檔驅動 |
| 路徑設定 | 硬編碼 | `projects.yml` |
| Branch 過濾 | 全域單一 | 每專案獨立 |
| Webhook Secret | 全域單一 | 全域預設 + 每專案可覆蓋 |
| 部署腳本 | 固定 `deploy.sh` | 每專案自訂 |
| 健康檢查 | 硬編碼在 deploy.sh | 可選，設定檔指定 |
| 自我重啟 | deploy.sh 內延遲重啟 | 獨立服務，不受專案部署影響 |
| Log 目錄 | 全域共用 | 每專案獨立子目錄 |

---

## 目錄結構

```
pi-deployer/
├── deployer.py              # 主程式
├── projects.yml             # 專案設定檔
├── .env.example             # 環境變數範本
├── .env                     # 實際環境變數（gitignore）
├── requirements.txt         # Python 依賴
├── scripts/
│   └── deploy-template.sh   # 通用部署腳本模板
├── logs/                    # 執行時產生，gitignore
│   ├── deployer.log         # webhook server 本身的 log
│   ├── glance/
│   │   └── deploy.log       # glance 專案的部署 log
│   └── my-api/
│       └── deploy.log       # my-api 專案的部署 log
├── systemd/
│   └── pi-deployer.service  # systemd unit file 範本
├── docs/
│   ├── setup.md             # 安裝與設定指南
│   └── migration.md         # 從 glance 遷移指南
├── .gitignore
└── README.md
```

---

## 設定檔格式 (`projects.yml`)

```yaml
# 全域預設值，每個專案可覆蓋
defaults:
  timeout: 300                    # 部署超時秒數
  branch: main                   # 預設觸發 branch
  notify: true                   # 是否發送 Telegram 通知

projects:
  # key 必須是 GitHub 的 repository.full_name（owner/repo）
  wen-hsiu-hsu/glance:
    name: "Pi Dashboard"          # 通知中顯示的名稱
    repo_dir: /home/pie/glance
    deploy_script: /home/pie/glance/scripts/deploy.sh
    branch: pi                    # 覆蓋預設值
    health_check:                 # 可選
      urls:
        - http://localhost:8081
        - http://localhost:8080
      retries: 5
      interval: 5                 # 每次重試間隔秒數
    # webhook_secret: "xxx"       # 可選，覆蓋全域 GITHUB_WEBHOOK_SECRET

  wen-hsiu-hsu/my-api:
    name: "My API"
    repo_dir: /home/pie/my-api
    deploy_script: /home/pie/my-api/deploy.sh
    branch: main
    timeout: 600                  # 這個專案部署比較慢
    health_check:
      urls:
        - http://localhost:3000/health

  wen-hsiu-hsu/homepage:
    name: "Homepage"
    repo_dir: /home/pie/homepage
    # 沒有指定 deploy_script 時，使用內建的預設部署流程：
    #   git pull --> docker compose down --> docker compose up -d
    branch: main
    deploy_mode: docker-compose   # 見「部署模式」章節
```

### 設定欄位說明

| 欄位 | 層級 | 必填 | 說明 |
|------|------|------|------|
| `defaults.timeout` | 全域 | 否 | 部署超時秒數，預設 300 |
| `defaults.branch` | 全域 | 否 | 預設觸發 branch，預設 `main` |
| `defaults.notify` | 全域 | 否 | 是否推送 Telegram 通知，預設 `true` |
| `projects.<full_name>` | -- | 是 | GitHub `owner/repo` 作為 key |
| `name` | 專案 | 是 | 通知中顯示的專案名稱 |
| `repo_dir` | 專案 | 是 | Pi 上的 repo 絕對路徑 |
| `deploy_script` | 專案 | 否 | 自訂部署腳本路徑，與 `deploy_mode` 二擇一 |
| `deploy_mode` | 專案 | 否 | 內建部署模式，見下方說明 |
| `branch` | 專案 | 否 | 觸發 branch，覆蓋全域預設 |
| `timeout` | 專案 | 否 | 部署超時秒數，覆蓋全域預設 |
| `notify` | 專案 | 否 | 是否推送通知，覆蓋全域預設 |
| `webhook_secret` | 專案 | 否 | 該專案專用的 webhook secret，覆蓋全域 |
| `health_check` | 專案 | 否 | 健康檢查設定 |
| `health_check.urls` | 專案 | 否 | 健康檢查 URL 列表 |
| `health_check.retries` | 專案 | 否 | 重試次數，預設 5 |
| `health_check.interval` | 專案 | 否 | 重試間隔秒數，預設 5 |
| `env_file` | 專案 | 否 | 部署腳本的額外環境變數檔案路徑 |

---

## 部署模式 (`deploy_mode`)

當專案沒有指定 `deploy_script` 時，可使用內建的部署模式，省去為每個專案寫 deploy script：

| 模式 | 動作 |
|------|------|
| `docker-compose` | `git pull` -> `docker compose down` -> `docker compose up -d` -> health check |
| `systemd` | `git pull` -> `sudo systemctl restart <service_name>` -> health check |
| `script-only` | 只執行 `deploy_script`，不做任何預設動作 |
| `pull-only` | 只做 `git pull`，適用於靜態網站等不需要重啟的專案 |

若同時指定 `deploy_script` 和 `deploy_mode`，以 `deploy_script` 為準（`script-only` 模式）。

### `docker-compose` 模式額外設定

```yaml
wen-hsiu-hsu/some-project:
  name: "Some Project"
  repo_dir: /home/pie/some-project
  deploy_mode: docker-compose
  compose_file: docker-compose.prod.yml  # 可選，預設 docker-compose.yml
  compose_services: [app, worker]        # 可選，只重啟指定 service
```

### `systemd` 模式額外設定

```yaml
wen-hsiu-hsu/some-service:
  name: "Some Service"
  repo_dir: /home/pie/some-service
  deploy_mode: systemd
  service_name: some-service.service     # 必填
```

---

## 環境變數

```bash
# .env.example

# Telegram 通知（選填，不填則跳過通知）
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=

# GitHub Webhook Secret（全域預設，可被 projects.yml 中的 webhook_secret 覆蓋）
GITHUB_WEBHOOK_SECRET=

# 伺服器設定
DEPLOYER_HOST=0.0.0.0
DEPLOYER_PORT=5000

# 設定檔路徑（預設為同目錄下的 projects.yml）
PROJECTS_CONFIG=./projects.yml

# Log 目錄
LOG_DIR=./logs
```

---

## API 規格

所有 endpoint 從外網經 `https://p-webhook.hsiu.soy` 存取。

### `POST /deploy`

GitHub webhook 觸發。根據 payload 中的 `repository.full_name` 自動路由到對應專案。

**流程：**

1. 從 payload 解析 `repository.full_name`
2. 查找 `projects.yml` 中對應的專案設定
3. 驗證 HMAC-SHA256 簽名（使用專案級或全域 secret）
4. 檢查 `ref` 是否匹配該專案的 `branch`
5. 回傳 `202 Accepted`
6. 背景執行部署

**回傳：**

| Status | 含義 |
|--------|------|
| `202` | 部署已排入佇列 |
| `200` | branch 不匹配，跳過 |
| `401` | 簽名驗證失敗 |
| `404` | `repository.full_name` 不在設定檔中 |

**回傳 body 範例：**

```json
// 202
{"status": "accepted", "project": "Pi Dashboard", "message": "Deploy queued"}

// 200
{"status": "skipped", "project": "Pi Dashboard", "message": "Not target branch (main)"}

// 404
{"status": "error", "message": "Unknown repository: wen-hsiu-hsu/unknown-repo"}
```

### `POST /deploy/<project_key>`

手動觸發特定專案的部署。`project_key` 為 `projects.yml` 中 key 的 repo 部分（例如 `glance`）。

需要基本驗證：`Authorization: Bearer <GITHUB_WEBHOOK_SECRET>`

```bash
curl -X POST https://p-webhook.hsiu.soy/deploy/glance \
  -H "Authorization: Bearer YOUR_SECRET"
```

**回傳：**

| Status | 含義 |
|--------|------|
| `202` | 部署已排入佇列 |
| `401` | 驗證失敗 |
| `404` | 專案不存在 |

### `GET /health`

webhook server 本身的健康檢查。

```json
{"status": "ok", "uptime": "2d 5h 30m", "projects": 3}
```

### `GET /status`

所有已註冊專案的狀態總覽。

```json
{
  "projects": {
    "wen-hsiu-hsu/glance": {
      "name": "Pi Dashboard",
      "last_deploy": "2025-01-15T10:30:00",
      "last_status": "success",
      "branch": "pi"
    },
    "wen-hsiu-hsu/my-api": {
      "name": "My API",
      "last_deploy": "2025-01-14T08:00:00",
      "last_status": "failed",
      "branch": "main"
    }
  }
}
```

### `GET /logs/<project_key>`

取得特定專案的最近部署 log（最後 50 行）。

```bash
curl https://p-webhook.hsiu.soy/logs/glance
```

```json
{"project": "Pi Dashboard", "logs": ["[2025-01-15 10:30:00] Starting Deployment...", "..."]}
```

### `GET /config`

回傳目前載入的設定（隱藏 secret）。用於 debug。

---

## Telegram 通知格式

所有通知使用 HTML 格式。`{name}` 為 `projects.yml` 中的 `name` 欄位。

### 部署觸發

```
<b>[{name}] 部署觸發</b>
<code>2025-01-15 10:30:00</code>
<a href="https://github.com/owner/repo/commit/abc1234">abc1234</a> feat: add new feature
```

### 部署成功

```
<b>[{name}] 部署成功</b>
<code>2025-01-15 10:30:45</code>
<a href="https://github.com/owner/repo/commit/abc1234">abc1234</a> feat: add new feature
耗時 45s
```

### 部署失敗

```
<b>[{name}] 部署失敗</b>
<code>2025-01-15 10:30:45 | exit code: 1</code>
<a href="https://github.com/owner/repo/commit/abc1234">abc1234</a> feat: add new feature
<code>最後 500 字元的 deploy log...</code>
```

### 部署超時

```
<b>[{name}] 部署超時</b>
<code>2025-01-15 10:35:00</code>
<a href="https://github.com/owner/repo/commit/abc1234">abc1234</a> feat: add new feature
超過 300 秒未完成
```

---

## 部署腳本模板 (`scripts/deploy-template.sh`)

供新專案參考的通用部署腳本。pi-deployer 會在執行前自動設定以下環境變數：

| 變數 | 說明 |
|------|------|
| `DEPLOYER_PROJECT_NAME` | 專案名稱 |
| `DEPLOYER_REPO_DIR` | repo 目錄路徑 |
| `DEPLOYER_BRANCH` | 目標 branch |
| `DEPLOYER_COMMIT_ID` | 觸發的 commit SHA |
| `DEPLOYER_LOG_DIR` | 該專案的 log 目錄 |

```bash
#!/bin/bash
set -e

REPO_DIR="${DEPLOYER_REPO_DIR}"
LOG_FILE="${DEPLOYER_LOG_DIR}/deploy.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

log() {
    echo "[$TIMESTAMP] $1" | tee -a "$LOG_FILE"
}

log "========== Starting Deployment =========="

cd "$REPO_DIR"

# 1. Git Pull
log "Pulling from GitHub..."
git pull origin "${DEPLOYER_BRANCH}" >> "$LOG_FILE" 2>&1
log "Git pull successful"

# 2. 依專案需求自訂以下步驟
# 例：Docker Compose
# docker compose down >> "$LOG_FILE" 2>&1
# docker compose up -d >> "$LOG_FILE" 2>&1

# 例：Systemd Service
# sudo systemctl restart my-service.service

# 例：Build & Restart
# npm install >> "$LOG_FILE" 2>&1
# npm run build >> "$LOG_FILE" 2>&1
# pm2 restart my-app

log "========== Deployment Completed =========="
exit 0
```

---

## systemd Service (`systemd/pi-deployer.service`)

```ini
[Unit]
Description=Pi Deployer - Universal Webhook Deploy Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pie
Group=pie
WorkingDirectory=/home/pie/pi-deployer
EnvironmentFile=/home/pie/pi-deployer/.env
ExecStart=/usr/bin/python3 /home/pie/pi-deployer/deployer.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

---

## 安全性設計

### Webhook 簽名驗證

- 每個請求都必須通過 HMAC-SHA256 簽名驗證
- 支援全域 secret 或每專案獨立 secret
- 手動觸發 endpoint 使用 Bearer token 驗證

### 設定檔中的 secret 處理

- `webhook_secret` 可以寫在 `projects.yml` 中（適合每專案不同 secret 的場景）
- 也可以只用全域 `GITHUB_WEBHOOK_SECRET` 環境變數
- `projects.yml` 中的 secret 優先於全域環境變數
- 若兩者都沒有設定，該專案的 webhook 請求一律回傳 401

### 未知 repo 處理

- payload 中的 `repository.full_name` 不在 `projects.yml` 中時回傳 404
- 不洩漏已註冊的專案列表

### 部署腳本權限

- 部署腳本必須有執行權限（`chmod +x`）
- pi-deployer 以 `pie` user 執行，部署腳本繼承該權限
- 若部署腳本需要 `sudo`（例如 `systemctl restart`），需在 sudoers 中開放

### Log 安全

- Log 中不記錄 secret 或 token
- `/config` endpoint 回傳設定時自動遮蔽 secret 欄位

---

## 並發控制

同一個專案不允許同時執行兩個部署。機制：

1. 每個專案維護一個 lock（`threading.Lock`）
2. 若專案正在部署中，新的 webhook 回傳 `409 Conflict`
3. Telegram 通知「部署跳過：上一次部署仍在進行中」

```json
{"status": "conflict", "project": "Pi Dashboard", "message": "Deploy already in progress"}
```

---

## 設定檔熱重載

支援不重啟服務的情況下重新載入 `projects.yml`：

- `POST /reload`：重新讀取設定檔（需 Bearer token 驗證）
- 收到 `SIGHUP` 信號時自動重載
- 重載後 Telegram 通知「設定檔已重載，共 N 個專案」

---

## Cloudflare Tunnel 設定

pi-deployer 沿用現有的 Cloudflare Tunnel。需修改 `/etc/cloudflared/config.yml`：

```yaml
# 不需要改動，因為 hostname 和 port 都不變
# 如果要改 hostname（例如從 p-webhook 改為 pi-deploy），則需要：
ingress:
  - hostname: dashboard.hsiu.soy
    service: http://localhost:8001
  - hostname: p-webhook.hsiu.soy       # 維持原本的 hostname
    service: http://localhost:5000      # 維持原本的 port
  - service: http_status:404
```

---

## GitHub Webhook 設定

所有專案共用同一個 webhook URL：`https://p-webhook.hsiu.soy/deploy`

在每個 GitHub repo 的 **Settings -> Webhooks -> Add webhook**：

| 欄位 | 值 |
|------|------|
| Payload URL | `https://p-webhook.hsiu.soy/deploy` |
| Content type | `application/json` |
| Secret | 與 `GITHUB_WEBHOOK_SECRET` 或該專案的 `webhook_secret` 一致 |
| Events | Just the push event |

pi-deployer 會根據 payload 自動分辨是哪個 repo，不需要為每個專案設定不同的 URL。

---

## 從 glance 遷移步驟

### Phase 1：部署 pi-deployer（不影響現有服務）

```bash
# 1. 在 Pi 上 clone 新專案
cd /home/pie
git clone <pi-deployer-repo-url> pi-deployer
cd pi-deployer

# 2. 複製環境變數
cp .env.example .env
# 編輯 .env，填入 TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GITHUB_WEBHOOK_SECRET
# 這些值可以直接從 /home/pie/glance/.env 複製

# 3. 安裝依賴
pip3 install -r requirements.txt --break-system-packages

# 4. 建立 projects.yml（先只加 glance）
# 參考上方設定檔格式

# 5. 安裝 systemd service
sudo cp systemd/pi-deployer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pi-deployer
sudo systemctl start pi-deployer

# 6. 驗證服務運行
curl http://localhost:5000/health
```

### Phase 2：切換 webhook（一行改動）

```bash
# 不需要改 GitHub webhook URL（同一個 hostname + port）
# 只需要停掉舊服務，啟動新服務

# 停止舊的 glance-webhook service
sudo systemctl stop glance-webhook.service
sudo systemctl disable glance-webhook.service

# 確認 pi-deployer 正在運行
sudo systemctl status pi-deployer
```

### Phase 3：清理 glance 專案

```bash
# 從 glance 專案中移除 webhook 相關檔案（可選）
# - webhook-server.py
# - 相關 systemd 設定

# 更新 glance 的 deploy.sh：
# - 移除末尾的 webhook service 重啟指令
#   （pi-deployer 是獨立服務，不需要自我重啟）
```

### Phase 4：新增更多專案

```bash
# 編輯 projects.yml，加入新專案
# 然後重載設定
curl -X POST https://p-webhook.hsiu.soy/reload \
  -H "Authorization: Bearer YOUR_SECRET"

# 在新專案的 GitHub repo 加上 webhook（同一個 URL）
```

---

## sudoers 設定

pi-deployer 本身不需要 root 權限。但部署腳本可能需要：

```bash
sudo visudo -f /etc/sudoers.d/pie-deployer
```

```
# 允許 pie 用戶無密碼重啟任何以 pi-deployer 管理的 service
# 根據實際需求調整，只開放必要的命令

# Docker（通常 pie 已在 docker group，不需要 sudo）
# pie ALL=(root) NOPASSWD: /usr/bin/docker compose *

# systemd（根據需要開放特定 service）
pie ALL=(root) NOPASSWD: /usr/bin/systemctl restart glance-webhook.service
pie ALL=(root) NOPASSWD: /usr/bin/systemctl restart some-other.service
```

---

## Python 依賴 (`requirements.txt`)

```
flask>=3.0
pyyaml>=6.0
```

僅使用兩個外部依賴，其餘全部使用 Python 標準庫。保持輕量，適合 Raspberry Pi 環境。

---

## 開發與測試

### 本機開發

```bash
# 安裝依賴
pip3 install -r requirements.txt

# 使用測試設定啟動
PROJECTS_CONFIG=./projects.example.yml python3 deployer.py
```

### 模擬 GitHub webhook

```bash
# 產生簽名
SECRET="your-secret"
PAYLOAD='{"ref":"refs/heads/main","repository":{"full_name":"wen-hsiu-hsu/my-api","html_url":"https://github.com/wen-hsiu-hsu/my-api"},"head_commit":{"id":"abc1234567890","message":"test deploy"}}'
SIGNATURE="sha256=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')"

curl -X POST http://localhost:5000/deploy \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: $SIGNATURE" \
  -d "$PAYLOAD"
```

### 測試項目清單

- [ ] 正確解析 `projects.yml`
- [ ] 全域 defaults 正確合併到專案設定
- [ ] 已知 repo 的 webhook 正確觸發部署
- [ ] 未知 repo 回傳 404
- [ ] 簽名驗證失敗回傳 401
- [ ] Branch 不匹配回傳 200 + skipped
- [ ] 並發部署回傳 409
- [ ] 手動觸發 endpoint 正確驗證 Bearer token
- [ ] Telegram 通知在所有狀態下正確發送
- [ ] 部署超時正確處理
- [ ] 設定檔熱重載正確運作
- [ ] 每專案獨立 log 目錄
- [ ] 每專案獨立 webhook secret
- [ ] `docker-compose` 部署模式正確執行
- [ ] `systemd` 部署模式正確執行
- [ ] `pull-only` 部署模式正確執行
- [ ] 健康檢查重試邏輯正確
- [ ] `/config` endpoint 遮蔽 secret
- [ ] `/status` endpoint 回傳所有專案狀態
- [ ] `/logs/<project>` endpoint 回傳正確 log

---

## 未來擴充（不在 MVP 範圍）

以下功能不在初始版本中，但架構設計時預留擴充空間：

- **Discord 通知**：新增通知 channel 支援
- **部署佇列**：多專案同時觸發時排隊而非拒絕
- **Rollback**：部署失敗時自動回滾到上一個成功的 commit
- **Web UI**：簡單的狀態儀表板
- **多 Pi 支援**：一個 webhook server 協調多台 Pi 的部署
