# 設計考量

[← 回到 README](../README.md)

以下是程式碼中不容易直接看出的決策和取捨。

## 為什麼拆模組而不是單檔

整個服務約 800 行，拆成 6 個檔案（deployer / config / deploy / verify / notify / health），每個檔案只負責一件事。最大的是 `deployer.py`（routes，約 340 行），最小的 `health.py` 不到 50 行。原因：

- 部署、驗證、通知、健康檢查的邏輯都不依賴 Flask，換掉 web 層也不用動它們
- verify.py 和 notify.py 沒有狀態，只是單純的函式，要替換很容易（例如把 Telegram 換成其他通知管道）
- config.py 封裝了全域狀態的存取，避免其他模組直接操作 `_config` dict

## 兩組查找索引

config.py 建立兩組 dict：

- `_projects_by_repo` -- key 是 `owner/repo`，webhook 路由用
- `_projects_by_key` -- key 是 `name`，手動觸發和 log 查詢用

這是因為 GitHub webhook payload 只帶 `repository.full_name`（如 `your-github-user/my-dashboard`），但人類操作時會用短名稱（如 `my-dashboard`）。兩組索引避免每次 O(n) 遍歷。

## 並發 lock 的競態條件防護

使用 `lock.acquire(blocking=False)` 而非先 `lock.locked()` 再 `acquire()`。後者有 TOCTOU 競態：兩個請求同時通過 `locked()` 檢查後都成功 acquire，導致同一專案跑兩個部署。非阻塞 acquire 是原子操作，天生避免此問題。

Lock 在 route handler 中取得，傳入背景 thread，由 thread 的 `finally` 釋放。這確保即使部署拋出未預期的例外，lock 也會被釋放。

## Secret 的優先順序鏈

webhook 簽名驗證時：

1. 先看專案 config 的 `webhook_secret`
2. 再看全域環境變數 `GITHUB_WEBHOOK_SECRET`
3. 兩者都沒有 → 直接回 401（不允許無 secret 的部署）

這讓你可以：大部分專案共用同一個 secret（只設環境變數），少數需要獨立 secret 的專案在 YAML 中覆蓋。

## Branch 比對邏輯

GitHub push event 的 `ref` 欄位格式是 `refs/heads/main`，而不是單純的 `main`。程式碼會檢查並 strip `refs/heads/` 前綴後再比對。如果 `ref` 不以 `refs/heads/` 開頭（理論上不應發生），會直接拿原始值比對。

## deploy_script 與 deploy_mode 的關係

兩者不是互斥的「選項」，而是有明確的優先順序：

- 指定了 `deploy_script` → 執行 script，取代內建的部署步驟；除非 `deploy_mode` 是 `script-only`，否則仍會先 git pull
- 只指定 `deploy_mode` → 按模式執行內建步驟
- `deploy_mode: script-only` → 只執行 script，**不做 git pull**

`script-only` 存在的意義是讓 deploy script 自己決定要不要 pull、怎麼 pull。

## 健康檢查的時機

健康檢查在部署步驟**之後**執行，不是之前。順序是：

```
git pull → deploy action → health check
```

這是刻意的：health check 驗證的是「部署後服務是否正常」，而不是「部署前環境是否 ready」。如果 health check 失敗，整個部署會被標記為 failed 並發送通知。

## 設定熱重載的邊界情況

重載設定時：
- 已刪除的專案：如果沒有正在進行的部署，移除對應的 lock
- 已刪除但正在部署的專案：保留 lock 直到部署結束（不強制中斷）
- 新增的專案：lock 會在第一次需要時 lazy 建立

正在進行的部署不會因為重載而中斷或改變行為，因為 deploy thread 持有的是重載前的 project dict；重載會換上一份全新的設定，不會改動舊的 dict。

## Log 檔案結構

Log 是扁平的每專案一個檔案（`logs/my-dashboard.log`），不是每次部署一個檔案。每次部署的結果 append 到同一個檔案，用分隔線區隔。`/logs/<name>` endpoint 回傳最後 50 行。

注意：目前沒有 log rotation 機制。長期運行需要配合 logrotate 或定期清理。

## 安全防護

- **簽名驗證**：使用 `hmac.compare_digest` 而非 `==`，防止時序攻擊
- **Bearer token 驗證**：同樣使用 `hmac.compare_digest`
- **Project key 驗證**：正則 `[a-zA-Z0-9_-]` + 長度上限 100，防止路徑遍歷
- **Log 檔案 symlink 防護**：用 `os.path.realpath` 確認解析後路徑仍在 log 目錄內
- **環境變數清理**：注入 subprocess 的 commit info 會移除不可列印字元並截斷至 500 字元
- **Config 檔案大小限制**：超過 1MB 拒絕載入
- **`/config` 遮蔽 secret**：回應中 `webhook_secret`、`secret`、`token`、`password` 這幾個 key 的非空值會顯示為 `***`。部署指令的輸出則會原樣寫進 log

## Daemon Thread 的取捨

部署 thread 設為 `daemon=True`，代表：
- 主程序退出時不會等待正在進行的部署完成
- 好處：`systemctl stop` 不會卡住
- 代價：強制停止時可能中斷正在進行的部署

這是合理的取捨，因為部署操作本身是冪等的（再跑一次 webhook 就好），而讓 stop 指令無回應是更糟的情況。

## 為什麼不用 Celery / Redis / 訊息佇列

這是跑在 Raspberry Pi 上的服務，記憶體和 CPU 都有限。threading.Lock + threading.Thread 的方案：
- 零額外依賴
- 零額外 process
- 足以應付 webhook 的低頻率（每天幾次到幾十次 push）
