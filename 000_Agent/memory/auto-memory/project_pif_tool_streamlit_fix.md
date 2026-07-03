---
name: project-pif-tool-streamlit-fix
description: pif_tool Streamlit 殭屍進程問題及解決方式
metadata: 
  node_type: memory
  type: project
  originSessionId: d61aea2c-0002-498e-8581-b8a2ac7fd470
---

pif_tool 執行 Streamlit 時，若瀏覽器出現 `AttributeError: 'IngredientDB' object has no attribute '...'`，原因是舊的 Streamlit 進程沒有完全關掉，瀏覽器仍連到舊版本（模組快取未更新）。

**Why:** `.bat` 重啟或 pycache 清除後，瀏覽器仍連著舊進程，導致載入的 `database.py` 是過時版本。

**How to apply:** 遇到此類 AttributeError 時，先殺掉所有舊進程再重啟，不需要改程式碼。

**解決步驟：**
1. 在 PowerShell 執行：
   ```powershell
   Get-Process -Name '*streamlit*' -ErrorAction SilentlyContinue | Stop-Process -Force
   Get-Process -Name 'python*' -ErrorAction SilentlyContinue | Stop-Process -Force
   ```
2. 重新執行 `啟動PIF工具.bat`
3. 瀏覽器按 `Ctrl+Shift+R` 強制重新整理

**附帶修復：** `.bat` 檔案原本含有中文導致 cmd.exe 亂碼，已改為全英文指令（2026-06-13）。
