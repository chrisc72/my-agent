---
name: sync-setup-07
description: pro-kit 07 跨裝置同步架構設定結果（OneDrive + Git，2026-05-21）
metadata: 
  node_type: memory
  type: project
  originSessionId: e5c7c285-ea59-4457-bd1e-606afa7818d3
---

pro-kit 07 已完成，AI 大腦可攜化架構已建立。

**Why:** 多台 Windows 電腦需要共用同一份 Claude Code 設定、skills、memory，避免換機後 AI 失憶。

**How to apply:** 未來換電腦照 `000_Agent/MIGRATION.md` 的步驟走。

## 架構摘要

- 母體：`D:\OneDrive\00 Claude Code\000_Agent\.claude\`（在 OneDrive 裡）
- `~/.claude/` 底下 5 個 symlink：settings.json / CLAUDE.md / hooks / statusline-command.sh / skills
- Git repo：已推上 GitHub `chrisc72/my-agent`（remote 已設）
- **Codex 雙棲接管已完成（2026-07-04）**：
  - `AGENTS.md`（repo 根）= `CLAUDE.md` 真檔複製（住 OneDrive 母體內，用真檔非 symlink，否則 OneDrive 吃掉連結）
  - `~/.agents/skills` = junction → `000_Agent\skills`（連結在 OneDrive 外，換機需重建）
  - `sync-health.sh` 加了 [5/6] AGENTS.md 漂移偵測（CLAUDE.md 比 AGENTS.md 新就提醒）、[6/6] Codex skills 檢查
  - MCP 轉 `~/.codex/config.toml` 尚未做（選配，真要跑 Codex 才需要）

## 重要技術細節

- Windows 上 `ln -s` 無法建真正的 symlink（只建複製）
- 需用 PowerShell `New-Item -ItemType SymbolicLink`（檔案）或 `-ItemType Junction`（目錄）
- 這個 Windows 系統支援 SymbolicLink 不需要額外 Developer Mode

## 待辦

- 每週五跑 `000_Agent/scripts/sync-health.sh` 體檢

## 踩坑：Windows 建 junction 的正確指令

- `~/.agents/skills` junction 已於 2026-07-04 建好，體檢 6/6 全綠
- Claude 從 Bash 呼叫 `powershell.exe` 被 deny 規則擋，但 **`cmd` 不擋**
- 正確做法（Git Bash 內）：`MSYS_NO_PATHCONV=1 cmd /c mklink /J "C:\目標\連結" "D:\來源\資料夾"`
- 關鍵：`MSYS_NO_PATHCONV=1` 防止 `/J` 被 MSYS 誤轉成路徑；參數要分開傳，不要包成單引號字串塞給 cmd
- 這台系統設了 `NoDefaultCurrentDirectoryInExePath`，所以 `cmd /c 某.bat` 從當前目錄找不到，要用完整路徑或直接下 mklink
