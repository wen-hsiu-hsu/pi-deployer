# 使用指南

[← 回到 README](../README.md)

**注意**：「共用同一個 URL」只代表 GitHub 端要填的 Payload URL 相同，**不代表 webhook 只要設定一次**。每個要自動部署的 repo，仍必須各自到該 repo 的 GitHub Settings → Webhooks 新增一條指向這個 URL 的 webhook（見「新增專案」步驟 4）。pi-deployer 完全被動：沒有 organization-level webhook，也沒有輪詢或排程機制，收不到 webhook 的 repo 就不會自動部署。

## 快速開始

```bash
# 安裝
git clone <repo-url> ~/pi-deployer && cd ~/pi-deployer
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 設定
cp .env.example .env
# 編輯 .env，至少填入 GITHUB_WEBHOOK_SECRET 和 DEPLOY_TOKEN
cp projects.example.yml projects.yml
# 編輯 projects.yml，加入你的專案（此檔已 gitignore，不會進版控）

# 啟動
python3 deployer.py
```

驗證：

```bash
curl http://localhost:5000/health
# {"status":"ok","uptime":"0m","uptime_seconds":3,"projects":3}
```

## 新增專案

假設你有一個新專案 `your-github-user/my-blog`，跑在 Pi 上的 `/home/pi/my-blog`，用 docker compose 部署。

### 1. 確認 Pi 上的 repo 存在

```bash
# 如果還沒 clone
cd /home/pi
git clone git@github.com:your-github-user/my-blog.git
```

### 2. 在 projects.yml 加入專案

```yaml
projects:
  # ... 既有專案 ...

  - name: my-blog
    repo: your-github-user/my-blog
    path: /home/pi/my-blog
    deploy_mode: docker-compose
    health_check:
      enabled: true
      url: http://localhost:4000
```

三個必填欄位：
- `name` -- 專案識別名，用於手動觸發（`/deploy/my-blog`）和 log 檔名
- `repo` -- GitHub 的 `owner/repo`，必須與 GitHub webhook payload 中的 `repository.full_name` 完全一致
- `path` -- Pi 上 git repo 的絕對路徑

其餘欄位不填就會套用 `defaults` 區塊的值。

### 3. 重載設定

不需要重啟服務：

```bash
curl -X POST http://localhost:5000/reload \
  -H "Authorization: Bearer YOUR_DEPLOY_TOKEN"
```

或用 systemd：

```bash
sudo systemctl reload pi-deployer
```

### 4. 在 GitHub 設定 webhook（每個 repo 都要做，不可省略）

到 `github.com/your-github-user/my-blog` → Settings → Webhooks → Add webhook：

| 欄位 | 值 |
|------|------|
| Payload URL | `https://your-domain/deploy` |
| Content type | `application/json` |
| Secret | 與 `.env` 中的 `GITHUB_WEBHOOK_SECRET` 一致 |
| Events | Just the push event |

如果這個專案需要獨立的 secret，在 projects.yml 加上 `webhook_secret`，GitHub 端也設成對應的值。

### 5. 測試

推一個 commit 到目標 branch，或手動觸發：

```bash
curl -X POST http://localhost:5000/deploy/my-blog \
  -H "Authorization: Bearer YOUR_DEPLOY_TOKEN"
# {"status":"accepted","project":"my-blog"}
```

確認結果：

```bash
curl -H "Authorization: Bearer YOUR_DEPLOY_TOKEN" \
  http://localhost:5000/status
curl -H "Authorization: Bearer YOUR_DEPLOY_TOKEN" \
  http://localhost:5000/logs/my-blog
```

### 依專案類型選擇 deploy_mode

| 你的專案是... | 用這個模式 | 額外設定 |
|-------------|-----------|---------|
| docker compose 服務 | `docker-compose` | 無 |
| systemd 管理的服務 | `systemd` | 加上 `service_name`，並設定 [sudoers](#sudoers-設定) |
| 靜態網站、不需重啟 | `pull-only` | 無 |
| 以上都不適用 | 寫自訂腳本 | 加上 `deploy_script`，參考 `scripts/deploy-template.sh` |

如果內建模式不夠用但又不想整個流程自己寫，可以同時指定 `deploy_mode` 以外的 `deploy_script` -- pi-deployer 會先 git pull 再跑你的腳本。如果連 git pull 都想自己控制，用 `deploy_mode: script-only`。

## 設定檔

### 環境變數 (`.env`)

| 變數 | 必填 | 說明 |
|------|------|------|
| `GITHUB_WEBHOOK_SECRET` | 是（除非每個專案都設了 `webhook_secret`） | 全域 webhook secret，可被專案級設定覆蓋 |
| `DEPLOY_TOKEN` | 是 | 手動觸發和管理 endpoint 的 Bearer token |
| `TELEGRAM_BOT_TOKEN` | 否 | Telegram Bot token，不填則不發通知 |
| `TELEGRAM_CHAT_ID` | 否 | Telegram 接收通知的 chat ID |
| `PROJECTS_CONFIG` | 否 | 設定檔路徑，預設 `./projects.yml` |
| `FLASK_HOST` | 否 | 監聽位址，預設 `0.0.0.0` |
| `FLASK_PORT` | 否 | 監聽 port，預設 `5000` |
| `FLASK_DEBUG` | 否 | 是否啟用 Flask debug 模式，預設 `false` |
| `LOG_DIR` | 否 | 部署 log 目錄，預設 `./logs` |

### 專案設定 (`projects.yml`)

```yaml
defaults:
  branch: main
  deploy_mode: docker-compose
  timeout: 300
  health_check:
    enabled: false
    retries: 3
    interval: 5

projects:
  - name: my-dashboard                    # 專案識別名稱，同時作為手動觸發的 key
    repo: your-github-user/my-dashboard           # GitHub owner/repo，webhook 路由依據
    path: /home/pi/my-dashboard           # Pi 上的 repo 絕對路徑
    deploy_mode: docker-compose
    health_check:
      enabled: true
      url: http://localhost:8080
      retries: 5
      interval: 3

  - name: my-api
    repo: your-github-user/my-api
    path: /home/pi/my-api
    deploy_mode: systemd
    service_name: my-api            # 選填，預設與 name 相同

  - name: homepage
    repo: your-github-user/homepage
    path: /home/pi/homepage
    deploy_mode: pull-only
```

`defaults` 區塊的值會合併到每個專案，專案級設定優先。對於巢狀 dict（如 `health_check`），合併是一層深度：專案中指定的 key 會覆蓋 default 中同名的 key，未指定的 key 保留 default 的值。

### 專案欄位

| 欄位 | 必填 | 說明 |
|------|------|------|
| `name` | 是 | 專案名稱，用於手動觸發、log 檔名、狀態查詢。建議只用 `[a-zA-Z0-9_-]` 且不超過 100 字元：載入設定時不檢查，但不符合的名稱無法手動觸發或查 log |
| `repo` | 是 | GitHub `owner/repo`，webhook 收到 push 時用此欄位比對 |
| `path` | 是 | Pi 上的 git repo 絕對路徑 |
| `branch` | 否 | 觸發部署的 branch，預設 `main` |
| `deploy_mode` | 否 | 部署模式，見下方說明。`defaults` 也沒設定時為 `pull-only` |
| `deploy_script` | 否 | 自訂部署腳本的絕對路徑，指定時忽略 `deploy_mode` |
| `timeout` | 否 | 每個部署指令（git pull、docker compose、腳本）各自的超時秒數，預設 300，不含健康檢查 |
| `service_name` | 否 | `systemd` 模式的 service 名稱，預設與 `name` 相同 |
| `webhook_secret` | 否 | 專案級 webhook secret，覆蓋全域 `GITHUB_WEBHOOK_SECRET` |
| `health_check.enabled` | 否 | 是否啟用健康檢查 |
| `health_check.url` | 否 | 健康檢查 URL |
| `health_check.initial_delay` | 否 | 第一次檢查前的等待秒數，預設 0（部署完立刻檢查）。`docker compose up -d` 指令成功不代表容器內的 app 已經在監聽 port，若 app 冷啟動較慢，建議設定這個欄位避免誤判失敗 |
| `health_check.retries` | 否 | 重試次數，預設 3 |
| `health_check.interval` | 否 | 重試間隔秒數，預設 5 |

## 部署模式

| 模式 | 執行步驟 |
|------|---------|
| `docker-compose` | git pull → docker compose down → docker compose up -d --build |
| `systemd` | git pull → sudo systemctl restart \<service_name\> |
| `pull-only` | git pull（適用於靜態網站） |
| `script-only` | 只執行 `deploy_script`，不做 git pull |

`deploy_script` 優先於 `deploy_mode` 的內建步驟：兩者同時指定時只執行 deploy_script，並先 git pull；但 `deploy_mode` 是 `script-only` 時不做 git pull。`script-only` 必須搭配 `deploy_script`，否則載入設定時會報錯：啟動會失敗，熱重載則回傳錯誤並沿用舊設定。
git pull 一律使用 `--ff-only`，遇到衝突會直接失敗而不會產生 merge commit。

## API

### GitHub Webhook

**`POST /deploy`** -- 所有 GitHub repo 設定相同的 webhook URL。

處理流程：
1. 從 payload 的 `repository.full_name` 查找專案 → 找不到回 404
2. HMAC-SHA256 簽名驗證 → 失敗回 401
3. 比對 push branch → 不匹配回 200（skipped）
4. 取得並發 lock → 已被鎖定回 409
5. 回 202，背景執行部署

```bash
# 模擬 webhook（產生簽名）
SECRET="your-secret"
PAYLOAD='{"ref":"refs/heads/main","repository":{"full_name":"your-github-user/my-dashboard"},"head_commit":{"id":"abc123","message":"feat: update","author":{"name":"you"},"url":"https://github.com/your-github-user/my-dashboard/commit/abc123"}}'
SIG="sha256=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')"

curl -X POST http://localhost:5000/deploy \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: $SIG" \
  -d "$PAYLOAD"
```

### 手動觸發

**`POST /deploy/<name>`** -- 用專案的 `name` 欄位觸發，需 Bearer token。

```bash
curl -X POST http://localhost:5000/deploy/my-dashboard \
  -H "Authorization: Bearer YOUR_DEPLOY_TOKEN"
```

### 管理 Endpoint

| 方法 | 路徑 | 認證 | 說明 |
|------|------|------|------|
| GET | `/health` | 無 | 伺服器狀態（uptime、專案數） |
| GET | `/status` | Bearer | 所有專案部署狀態總覽 |
| GET | `/logs/<name>` | Bearer | 該專案最近 50 行部署 log |
| GET | `/config` | Bearer | 目前設定（secret 自動遮蔽） |
| POST | `/reload` | Bearer | 熱重載 `projects.yml` |

## systemd 部署

```bash
sudo cp systemd/pi-deployer.service /etc/systemd/system/
# 把 service 檔案中的 your-user 換成實際使用者與路徑
# 確認 ExecStart 指向 venv 內的 python，例如 /home/pi/pi-deployer/venv/bin/python
sudo systemctl daemon-reload
sudo systemctl enable --now pi-deployer

# 查看 log
sudo journalctl -u pi-deployer -f

# 重載設定（等同 POST /reload）
sudo systemctl reload pi-deployer
```

systemd 的 `ExecReload` 設定為發送 SIGHUP，pi-deployer 收到 SIGHUP 後會重新讀取 `projects.yml`，不中斷正在進行的部署。

## GitHub Webhook 設定

所有專案共用同一個 webhook URL——但這是指 URL 相同，webhook 本身仍要在每個 repo 各自新增一條，沒有一次設定、全部生效的機制。在每個 GitHub repo 的 Settings → Webhooks：

| 欄位 | 值 |
|------|------|
| Payload URL | `https://your-domain/deploy` |
| Content type | `application/json` |
| Secret | 與 `GITHUB_WEBHOOK_SECRET` 或該專案的 `webhook_secret` 一致 |
| Events | Just the push event |

## Telegram 通知

設定 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID` 後自動啟用。未設定則靜默跳過，不影響部署。

通知時機：
- **triggered** -- 部署開始（webhook 和手動觸發都會發送）
- **success** -- 部署完成，附耗時
- **failed** -- 部署失敗，附最後 500 字元 log
- **timeout** -- 任一個部署指令執行超過 `timeout` 秒（每個指令各自計時，不含健康檢查）

通知是 fire-and-forget：Telegram API 呼叫失敗只會寫 warning log，不會阻擋或影響部署流程。

## 自訂部署腳本

部署腳本會收到以下環境變數：

| 變數 | 說明 |
|------|------|
| `DEPLOYER_PROJECT_NAME` | 專案名稱 |
| `DEPLOYER_REPO_DIR` | repo 目錄絕對路徑 |
| `DEPLOYER_DEPLOY_MODE` | 部署模式 |
| `DEPLOYER_BRANCH` | 目標 branch |
| `DEPLOYER_COMMIT_SHA` | 觸發的 commit SHA（webhook 觸發時） |
| `DEPLOYER_COMMIT_AUTHOR` | commit 作者（webhook 觸發時） |
| `DEPLOYER_COMMIT_MESSAGE` | commit 訊息（webhook 觸發時） |

工作目錄會被設為該專案的 `path`。參考 `scripts/deploy-template.sh` 撰寫。

## sudoers 設定

`systemd` 模式需要 `sudo systemctl restart`。在 Pi 上設定免密碼：

```bash
sudo visudo -f /etc/sudoers.d/pi-deployer
```

```
# 只開放需要的 service，不要用萬用字元
pi ALL=(root) NOPASSWD: /usr/bin/systemctl restart my-api
```

## 依賴

```
flask>=3.0      # Web framework
pyyaml>=6.0     # YAML 解析
requests>=2.31  # Telegram API + 健康檢查
python-dotenv>=1.0  # .env 檔案載入
```

四個外部依賴，其餘全部使用 Python 標準庫。
