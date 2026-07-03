# Memory Index

- [使用者偏好](user_preferences.md) — 繁體中文回覆、依情況調整風格、優先型別安全
- [AI 分身設定](agent_setup.md) — 創業者角色、研究整理、Email 產出、日誌啟用（2026-05-18）
- [PDF 轉換輸出路徑](project_pdf_output.md) — 預設存至 `相關資訊_非系統存取用\PDF轉成MD`，另指定除外
- [同步架構設定](project_sync_setup.md) — pro-kit 07 完成，OneDrive+Git，5 個 symlink，Codex 接管(AGENTS.md+~/.agents/skills)，MIGRATION.md 在 000_Agent/
- [auto-memory 可攜化](project_auto_memory_portable.md) — 內建記憶用 junction 接進 OneDrive，換機可同步（2026-07-04）
- [Windows symlink 建立方式](feedback_windows_symlink.md) — 用 PowerShell New-Item，不用 bash ln -s
- [pif_tool Streamlit 殭屍進程](project_pif_tool_streamlit_fix.md) — AttributeError 通常是舊進程未關，用 Stop-Process 清掉再重啟
- [Streamlit radio state 踩坑](feedback_streamlit_radio_state.md) — 導覽 radio 不要同時用 index + key + on_change，只給 key 並種初始值
