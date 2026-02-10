# pi-deployer

Raspberry Pi 通用 webhook 部署服務。所有 Git 專案共用一個 endpoint，透過 `projects.yml` 管理。

## 為什麼需要這個

原本每個 Pi 上的專案各自帶一份 webhook server（例如 glance 內建的 `webhook-server.py`），路徑、branch、部署指令全部硬編碼。新增專案就要複製一份 server。pi-deployer 將部署邏輯抽離為獨立服務，一個 webhook URL 服務所有專案。

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
# 編輯 projects.yml，加入你的專案

# 啟動
python3 deployer.py
```

驗證：

```bash
curl http://localhost:5000/health
# {"status":"ok","uptime":"0m","uptime_seconds":3,"projects":3}
```

## 新增專案

假設你有一個新專案 `wenxiuxu/my-blog`，跑在 Pi 上的 `/home/pi/my-blog`，用 docker compose 部署。

### 1. 確認 Pi 上的 repo 存在

```bash
# 如果還沒 clone
cd /home/pi
git clone git@github.com:wenxiuxu/my-blog.git
```

### 2. 在 projects.yml 加入專案

```yaml
projects:
  # ... 既有專案 ...

  - name: my-blog
    repo: wenxiuxu/my-blog
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

### 4. 在 GitHub 設定 webhook

到 `github.com/wenxiuxu/my-blog` → Settings → Webhooks → Add webhook：

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
curl http://localhost:5000/status
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

---

## 設定檔

### 環境變數 (`.env`)

| 變數 | 必填 | 說明 |
|------|------|------|
| `GITHUB_WEBHOOK_SECRET` | 是 | 全域 webhook secret，可被專案級設定覆蓋 |
| `DEPLOY_TOKEN` | 是 | 手動觸發和管理 endpoint 的 Bearer token |
| `TELEGRAM_BOT_TOKEN` | 否 | Telegram Bot token，不填則不發通知 |
| `TELEGRAM_CHAT_ID` | 否 | Telegram 接收通知的 chat ID |
| `PROJECTS_CONFIG` | 否 | 設定檔路徑，預設 `./projects.yml` |
| `FLASK_HOST` | 否 | 監聽位址，預設 `0.0.0.0` |
| `FLASK_PORT` | 否 | 監聽 port，預設 `5000` |
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
  - name: glance                    # 專案識別名稱，同時作為手動觸發的 key
    repo: wenxiuxu/glance           # GitHub owner/repo，webhook 路由依據
    path: /home/pi/glance           # Pi 上的 repo 絕對路徑
    deploy_mode: docker-compose
    health_check:
      enabled: true
      url: http://localhost:8080
      retries: 5
      interval: 3

  - name: my-api
    repo: wenxiuxu/my-api
    path: /home/pi/my-api
    deploy_mode: systemd
    service_name: my-api            # systemd mode 必填

  - name: homepage
    repo: wenxiuxu/homepage
    path: /home/pi/homepage
    deploy_mode: pull-only
```

`defaults` 區塊的值會合併到每個專案，專案級設定優先。對於巢狀 dict（如 `health_check`），合併是一層深度：專案中指定的 key 會覆蓋 default 中同名的 key，未指定的 key 保留 default 的值。

### 專案欄位

| 欄位 | 必填 | 說明 |
|------|------|------|
| `name` | 是 | 專案名稱，必須符合 `[a-zA-Z0-9_-]`，用於手動觸發、log 檔名、狀態查詢 |
| `repo` | 是 | GitHub `owner/repo`，webhook 收到 push 時用此欄位比對 |
| `path` | 是 | Pi 上的 git repo 絕對路徑 |
| `branch` | 否 | 觸發部署的 branch，預設 `main` |
| `deploy_mode` | 否 | 部署模式，見下方說明 |
| `deploy_script` | 否 | 自訂部署腳本的絕對路徑，指定時忽略 `deploy_mode` |
| `timeout` | 否 | 部署超時秒數，預設 300 |
| `service_name` | 否 | `systemd` 模式的 service 名稱，預設與 `name` 相同 |
| `webhook_secret` | 否 | 專案級 webhook secret，覆蓋全域 `GITHUB_WEBHOOK_SECRET` |
| `health_check.enabled` | 否 | 是否啟用健康檢查 |
| `health_check.url` | 否 | 健康檢查 URL |
| `health_check.retries` | 否 | 重試次數，預設 3 |
| `health_check.interval` | 否 | 重試間隔秒數，預設 5 |

## 部署模式

| 模式 | 執行步驟 |
|------|---------|
| `docker-compose` | git pull → docker compose down → docker compose up -d |
| `systemd` | git pull → sudo systemctl restart \<service_name\> |
| `pull-only` | git pull（適用於靜態網站） |
| `script-only` | 只執行 `deploy_script`，不做 git pull |

`deploy_script` 優先於 `deploy_mode`：若兩者同時指定，只會執行 deploy_script（且會先 git pull）。
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
PAYLOAD='{"ref":"refs/heads/main","repository":{"full_name":"wenxiuxu/glance"},"head_commit":{"id":"abc123","message":"feat: update","author":{"name":"you"},"url":"https://github.com/wenxiuxu/glance/commit/abc123"}}'
SIG="sha256=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')"

curl -X POST http://localhost:5000/deploy \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: $SIG" \
  -d "$PAYLOAD"
```

### 手動觸發

**`POST /deploy/<name>`** -- 用專案的 `name` 欄位觸發，需 Bearer token。

```bash
curl -X POST http://localhost:5000/deploy/glance \
  -H "Authorization: Bearer YOUR_DEPLOY_TOKEN"
```

### 管理 Endpoint

| 方法 | 路徑 | 認證 | 說明 |
|------|------|------|------|
| GET | `/health` | 無 | 伺服器狀態（uptime、專案數） |
| GET | `/status` | 無 | 所有專案部署狀態總覽 |
| GET | `/logs/<name>` | Bearer | 該專案最近 50 行部署 log |
| GET | `/config` | 無 | 目前設定（secret 自動遮蔽） |
| POST | `/reload` | Bearer | 熱重載 `projects.yml` |

## systemd 部署

```bash
sudo cp systemd/pi-deployer.service /etc/systemd/system/
# 依實際路徑和使用者編輯 service 檔案
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

所有專案使用同一個 webhook URL。在每個 GitHub repo 的 Settings → Webhooks：

| 欄位 | 值 |
|------|------|
| Payload URL | `https://your-domain/deploy` |
| Content type | `application/json` |
| Secret | 與 `GITHUB_WEBHOOK_SECRET` 或該專案的 `webhook_secret` 一致 |
| Events | Just the push event |

## Telegram 通知

設定 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID` 後自動啟用。未設定則靜默跳過，不影響部署。

通知時機：
- **triggered** -- 收到 webhook，開始部署
- **success** -- 部署完成，附耗時
- **failed** -- 部署失敗，附最後 500 字元 log
- **timeout** -- 超過 timeout 秒數未完成

通知是 fire-and-forget：Telegram API 呼叫失敗只會寫 warning log，不會阻擋或影響部署流程。

---

## 設計考量

以下是程式碼中不容易直接看出的決策和取捨。

### 為什麼拆模組而不是單檔

原始規格寫的是一個 `deployer.py`，但實作拆為 6 個檔案（deployer / config / deploy / verify / notify / health），每個都在 100 行左右。原因：

- 每個模組可獨立測試，不需要啟動 Flask app
- verify.py 和 notify.py 零狀態、純函式，最容易測試和替換
- config.py 封裝了全域狀態的存取，避免其他模組直接操作 `_config` dict

### 兩組查找索引

config.py 建立兩組 dict：

- `_projects_by_repo` -- key 是 `owner/repo`，webhook 路由用
- `_projects_by_key` -- key 是 `name`，手動觸發和 log 查詢用

這是因為 GitHub webhook payload 只帶 `repository.full_name`（如 `wenxiuxu/glance`），但人類操作時會用短名稱（如 `glance`）。兩組索引避免每次 O(n) 遍歷。

### 並發 lock 的競態條件防護

使用 `lock.acquire(blocking=False)` 而非先 `lock.locked()` 再 `acquire()`。後者有 TOCTOU 競態：兩個請求同時通過 `locked()` 檢查後都成功 acquire，導致同一專案跑兩個部署。非阻塞 acquire 是原子操作，天生避免此問題。

Lock 在 route handler 中取得，傳入背景 thread，由 thread 的 `finally` 釋放。這確保即使部署拋出未預期的例外，lock 也會被釋放。

### Secret 的優先順序鏈

webhook 簽名驗證時：

1. 先看專案 config 的 `webhook_secret`
2. 再看全域環境變數 `GITHUB_WEBHOOK_SECRET`
3. 兩者都沒有 → 直接回 401（不允許無 secret 的部署）

這讓你可以：大部分專案共用同一個 secret（只設環境變數），少數需要獨立 secret 的專案在 YAML 中覆蓋。

### Branch 比對邏輯

GitHub push event 的 `ref` 欄位格式是 `refs/heads/main`，而不是單純的 `main`。程式碼會檢查並 strip `refs/heads/` 前綴後再比對。如果 `ref` 不以 `refs/heads/` 開頭（理論上不應發生），會直接拿原始值比對。

### deploy_script 與 deploy_mode 的關係

兩者不是互斥的「選項」，而是有明確的優先順序：

- 指定了 `deploy_script` → 先 git pull，再執行 script（忽略 deploy_mode）
- 只指定 `deploy_mode` → 按模式執行內建步驟
- `deploy_mode: script-only` → 只執行 script，**不做 git pull**

`script-only` 存在的意義是讓 deploy script 自己決定要不要 pull、怎麼 pull。

### 健康檢查的時機

健康檢查在部署步驟**之後**執行，不是之前。順序是：

```
git pull → deploy action → health check
```

這是刻意的：health check 驗證的是「部署後服務是否正常」，而不是「部署前環境是否 ready」。如果 health check 失敗，整個部署會被標記為 failed 並發送通知。

### 設定熱重載的邊界情況

重載設定時：
- 已刪除的專案：如果沒有正在進行的部署，移除對應的 lock
- 已刪除但正在部署的專案：保留 lock 直到部署結束（不強制中斷）
- 新增的專案：lock 會在第一次需要時 lazy 建立

正在進行的部署不會因為重載而中斷或改變行為，因為 deploy thread 持有的是重載前的 config 副本。

### Log 檔案結構

Log 是扁平的每專案一個檔案（`logs/glance.log`），不是每次部署一個檔案。每次部署的結果 append 到同一個檔案，用分隔線區隔。`/logs/<name>` endpoint 回傳最後 50 行。

注意：目前沒有 log rotation 機制。長期運行需要配合 logrotate 或定期清理。

### 安全防護

- **簽名驗證**：使用 `hmac.compare_digest` 而非 `==`，防止時序攻擊
- **Bearer token 驗證**：同樣使用 `hmac.compare_digest`
- **Project key 驗證**：正則 `[a-zA-Z0-9_-]` + 長度上限 100，防止路徑遍歷
- **Log 檔案 symlink 防護**：用 `os.path.realpath` 確認解析後路徑仍在 log 目錄內
- **環境變數清理**：注入 subprocess 的 commit info 會移除不可列印字元並截斷至 500 字元
- **Config 檔案大小限制**：超過 1MB 拒絕載入
- **Secret 不進 log**：`/config` endpoint 自動遮蔽所有 secret 相關欄位

### Daemon Thread 的取捨

部署 thread 設為 `daemon=True`，代表：
- 主程序退出時不會等待正在進行的部署完成
- 好處：`systemctl stop` 不會卡住
- 代價：強制停止時可能中斷正在進行的部署

這是合理的取捨，因為部署操作本身是幂等的（再跑一次 webhook 就好），而讓 stop 指令無回應是更糟的情況。

### 為什麼不用 Celery / Redis / 訊息佇列

這是跑在 Raspberry Pi 上的服務，記憶體和 CPU 都有限。threading.Lock + threading.Thread 的方案：
- 零額外依賴
- 零額外 process
- 足以應付 webhook 的低頻率（每天幾次到幾十次 push）
- 整個服務記憶體佔用在 30MB 以內

## 目錄結構

```
pi-deployer/
├── deployer.py          # Flask app + routes + 入口點
├── config.py            # 設定檔載入 / 合併 / 熱重載
├── deploy.py            # 部署執行引擎（4 種模式）
├── verify.py            # HMAC-SHA256 + Bearer token 驗證
├── notify.py            # Telegram 通知
├── health.py            # HTTP 健康檢查（帶重試）
├── projects.yml         # 專案設定檔
├── .env.example         # 環境變數範本
├── requirements.txt     # Python 依賴
├── scripts/
│   └── deploy-template.sh   # 自訂部署腳本模板
└── systemd/
    └── pi-deployer.service  # systemd unit file
```

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
