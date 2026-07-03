---
name: windows-symlink-method
description: Windows 上建立 symlink 要用 PowerShell，不能用 bash ln -s
metadata: 
  node_type: memory
  type: feedback
  originSessionId: e5c7c285-ea59-4457-bd1e-606afa7818d3
---

在這台 Windows 電腦上，Git Bash 的 `ln -s` 不會建真正的 symlink，只會建複製（ls -la 顯示 `-rw-` 而非 `lrwxrwxrwx`）。

**Why:** Windows 的 symlink 機制與 POSIX 不同，Git Bash 的 `ln -s` 在跨磁碟機路徑時無法建立 NTFS symlink。

**How to apply:** 凡是需要在 Windows `~/.claude/` 建 symlink，一律用 PowerShell：
- 檔案：`New-Item -ItemType SymbolicLink -Path "..." -Target "..."`
- 目錄：`New-Item -ItemType Junction -Path "..." -Target "..."`

Junction 不需要 Developer Mode；SymbolicLink 在這台電腦測試可用。
