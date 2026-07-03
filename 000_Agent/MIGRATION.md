# AI 大腦遷移手冊

> 由 pro-kit 07 生成（2026-05-21）。未來換新電腦、換新 AI 時，照這份走能一鍵接管。

## 當前架構

| 項目 | 路徑 |
|------|------|
| 母體資料夾 | `D:\OneDrive\00 Claude Code\000_Agent\` |
| 同步管道 | OneDrive（多台 Windows 電腦） |
| GitHub repo | https://github.com/chrisc72/my-agent |
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
| `projects\D--OneDrive-00-Claude-Code\memory\` | `000_Agent\memory\auto-memory\`（junction，內建 auto-memory 可攜化，2026-07-04） |

## Codex 雙棲接管（2026-07-04 補做）

| 項目 | 說明 |
|------|------|
| `AGENTS.md`（repo 根） | `CLAUDE.md` 的真檔複製，Codex 讀專案規則。住在 OneDrive 母體內，用真檔非 symlink |
| `~/.agents/skills` | junction → `000_Agent\skills`，Codex 全域技能掃描（連結在 OneDrive 外，換機需重建） |
| `~/.codex/config.toml` | 選配，MCP 要用才建 |

---

## 情境 1：GitHub 備份（已完成，remote 已設）

repo 已推上 https://github.com/chrisc72/my-agent。之後每次想備份，執行：
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

# Codex 技能掃描路徑（~/.agents/skills，供 Codex 雙棲用）
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.agents"
New-Item -ItemType Junction -Path "$env:USERPROFILE\.agents\skills" -Target "D:\OneDrive\00 Claude Code\000_Agent\skills"

# 內建 auto-memory 可攜化（換機同步「越用越懂你」的記憶）
# 注意：只有工作目錄同為 D:\OneDrive\00 Claude Code 時，專案 hash 才會是 D--OneDrive-00-Claude-Code
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.claude\projects\D--OneDrive-00-Claude-Code"
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\projects\D--OneDrive-00-Claude-Code\memory" -Target "D:\OneDrive\00 Claude Code\000_Agent\memory\auto-memory"
```

> `AGENTS.md`（Codex 規則檔）是 OneDrive 同步的**真檔**，換機不用重建；只有上面這些 `~/.claude\` 與 `~/.agents\` 的連結需要在每台新電腦重建。

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
2. **Codex 已接管完成**：`AGENTS.md` 就在 repo 根目錄，內容是 `CLAUDE.md` 的複製（住在 OneDrive 母體內，用真檔不用 symlink，否則 OneDrive 會把連結吃掉）。改規則時兩邊都要更新，`sync-health.sh` 的 [5/6] 會偵測漂移並提醒。
3. **Codex 技能**：`~/.agents/skills` junction 指向 `000_Agent\skills`（見情境 2 的重建指令），Codex 全域可讀。
4. **Codex MCP（選配）**：Claude 的 JSON 設定要改寫成 `~/.codex/config.toml` 的 `[mcp_servers.xxx]` 格式才能用。
5. 其他新 AI（Gemini `GEMINI.md`、Cursor `.cursorrules` 等）：同樣把 `CLAUDE.md` 複製成對應檔名即可。

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
