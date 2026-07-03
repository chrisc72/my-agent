---
name: project-pdf-output
description: PDF 轉換輸出的預設存放路徑
metadata: 
  node_type: memory
  type: project
  originSessionId: 68d6e8da-1ad4-40a8-bdfb-31ae05f4fb04
---

PDF 轉換成文字或 MD 檔案時，預設存放至 `D:\OneDrive\00 Claude Code\相關資訊_非系統存取用\PDF轉成MD`，除非使用者另外指定路徑。

**Why:** 使用者統一管理 PDF 轉換輸出的位置。
**How to apply:** 執行任何 PDF 轉換任務時，若未指定 `-o` 路徑，自動使用此預設目錄。
