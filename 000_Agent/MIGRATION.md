# AI 大腦遷移手冊

> 由 pro-kit 07 生成（2026-05-21）。未來換新電腦、換新 AI 時，照這份走能一鍵接管。

## 當前架構

| 項目 | 路徑 |
|------|------|
| 母體資料夾 | `D:\OneDrive\00 Claude Code\000_Agent\` |
| 同步管道 | OneDrive（多台 Windows 電腦） |
| GitHub repo | 見下方「推上 GitHub」步驟（尚待完成） |
| 體檢腳本 | `000_Agent\scripts\sync-health.sh` |
| 檢查頻率 | 每週一次（建議週五複盤日） |

## symlink 對照表（~/.claude/ 指向 OneDrive）

| ~/.claude/ 中的項目 | 實際位置（OneDrive） |
|---------------------|----------------------|
| `settings.json` | `000_Agent\.claude\settings.json` |
| `CLAUDE.md` | `000_Agent\.claude\CLAUDE.md` |
| `hooks\` | `000_Agent\.claude\hooks\` |
| `statusline-command.sh` | `000_Agent\.claude\statusline-command.sh` |
| `skills\` | `000_Agent\skills\` |

---

## 情境 1：推上 GitHub（尚待完成）

目前 git repo 已在本機初始化，需要手動推上 GitHub：

1. 到 [github.com/new](https://github.com/new) 建立**私有 repo**，名稱建議：`my-agent`
2. 不要勾選「Add a README file」（已有本機 commit）
3. 建好後，複製 repo 的 SSH 或 HTTPS URL
4. 在 Git Bash 執行（把 URL 換成你的）：

```bash
cd "/d/OneDrive/00 Claude Code"
git remote add origin https://github.com/你的帳號/my-agent.git
git push -u origin main
```

之後每次想備份，執行：
```bash
cd "/d/OneDrive/00 Claude Code"
git add -A
git commit -m "update: $(date '+%Y-%m-%d') 定期備份"
git push
```

---

## 情境 2：換一台新的 Windows 電腦

### 前提：OneDrive 已同步到新電腦

1. 確認 OneDrive 在新電腦同步完成，`D:\OneDrive\00 Claude Code\` 已可見
2. 在新電腦安裝 Claude Code
3. 用 **PowerShell（以系統管理員身份執行）** 建立 symlink：

```powershell
# 檔案 symlink
$base = "D:\OneDrive\00 Claude Code\000_Agent\.claude"
$target = "$env:USERPROFILE\.claude"

New-Item -ItemType SymbolicLink -Path "$target\settings.json" -Target "$base\settings.json"
New-Item -ItemType SymbolicLink -Path "$target\CLAUDE.md" -Target "$base\CLAUDE.md"
New-Item -ItemType SymbolicLink -Path "$target\statusline-command.sh" -Target "$base\statusline-command.sh"

# 目錄 Junction（不需要 Developer Mode）
New-Item -ItemType Junction -Path "$target\hooks" -Target "$base\hooks"

# skills Junction（指向 skills 資料夾）
New-Item -ItemType Junction -Path "$target\skills" -Target "D:\OneDrive\00 Claude Code\000_Agent\skills"
```

4. 跑體檢腳本確認：

```bash
bash "/d/OneDrive/00 Claude Code/000_Agent/scripts/sync-health.sh"
```

### 如果走 GitHub（沒有 OneDrive 或換品牌電腦）

```bash
git clone https://github.com/你的帳號/my-agent.git "D:\OneDrive\00 Claude Code"
```

然後執行上方的 PowerShell symlink 建立步驟。

---

## 情境 3：換新 AI 大腦（Codex / Gemini / 未來新產品）

你的 `CLAUDE.md` 是 AI 無關的規則文件。要給新 AI 讀：

1. 確認新 AI 的規則檔命名（例如 Cursor 讀 `.cursorrules`、Codex 讀 `AGENTS.md`）
2. 在工作目錄建一個 symlink：

```bash
ln -s "D:/OneDrive/00 Claude Code/CLAUDE.md" "D:/OneDrive/00 Claude Code/AGENTS.md"
```

3. skills / memory 邏輯上需要新 AI 支援同等機制才能復用（格式可能需要調整）

---

## 情境 4：備份還原（出事了）

```powershell
# 找最新備份
$backup = Get-ChildItem "$env:USERPROFILE\claude-backup-*" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host "最新備份：$backup"

# 還原
Remove-Item -Path "$env:USERPROFILE\.claude" -Recurse -Force
Move-Item -Path $backup.FullName -Destination "$env:USERPROFILE\.claude"
```

---

## 體檢腳本用法

```bash
# 手動跑（建議每週五）
bash "/d/OneDrive/00 Claude Code/000_Agent/scripts/sync-health.sh"

# 寫 log
bash "/d/OneDrive/00 Claude Code/000_Agent/scripts/sync-health.sh" >> \
  "/d/OneDrive/00 Claude Code/000_Agent/logs/sync-health.log" 2>&1
```
