---
name: auto-memory-portable
description: Claude Code 內建 auto-memory 已可攜化，junction 接進 OneDrive 母體（2026-07-04）
metadata: 
  node_type: memory
  type: project
  originSessionId: 4f518d03-32fe-46a5-8692-5aaa3c563111
---

Claude Code 內建的「越用越懂你」auto-memory（`~/.claude/projects/D--OneDrive-00-Claude-Code/memory/`）原本在 C 槽、不會跨機同步。已於 2026-07-04 用 junction 接進 OneDrive 母體,現在會自動同步。

**Why:** 換電腦時,skills/CLAUDE.md 都靠 OneDrive 同步了,但 auto-memory 卡在 C 槽隱藏資料夾會遺失,造成新電腦的我「忘記」使用者偏好與踩坑。

**How to apply:**
- 本體(真檔)：`D:\OneDrive\00 Claude Code\000_Agent\memory\auto-memory\`
- junction：`~/.claude/projects/D--OneDrive-00-Claude-Code/memory` → 上述本體
- 換電腦重建指令（PowerShell 系統管理員）：
  `New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\projects\D--OneDrive-00-Claude-Code\memory" -Target "D:\OneDrive\00 Claude Code\000_Agent\memory\auto-memory"`
- **限制**：只有新電腦的工作目錄也是 `D:\OneDrive\00 Claude Code`（專案 hash 才會是 `D--OneDrive-00-Claude-Code`）junction 路徑才對得上；換磁碟機代號要改路徑
- 舊資料備份在 `~/.claude/projects/D--OneDrive-00-Claude-Code/memory_localbak_20260704`（確認穩定後可刪）

跟 [[project_sync_setup]] 是同一套可攜化架構。建 junction 指令細節見 [[feedback_windows_symlink]]。
