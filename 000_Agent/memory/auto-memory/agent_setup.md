---
name: agent-setup
description: AI 分身起始設定 — 使用者角色為創業者，主要需求研究整理與 Email 通訊，已啟用日誌功能
metadata: 
  node_type: memory
  type: project
  originSessionId: 5883c459-a324-4191-85cf-804949d4de17
---

使用者透過 AI 分身起始助手完成初始化設定（2026-05-18）。

**角色**：創業者  
**核心需求**：研究整理（資料彙整、分析摘要、知識萃取）  
**主要產出平台**：Email / 商業通訊  
**日誌功能**：已啟用，格式為 `300_Journal/YYYY-MM-DD.md`

**Why:** 使用者明確選擇這些偏好，應在研究任務、Email 草稿等場景主動套用。

**How to apply:**  
- 整理資料時用「結論 → 依據」結構  
- 起草 Email 時符合創業者語氣：清楚有說服力  
- 對話中有重要決策或洞察，提醒是否寫入 `300_Journal/`  
- 知識整理產出建議存入 `200_Reference/`

**工作目錄結構（D:\OneDrive\00 Claude Code）**：  
- `000_Agent/` — AI 規則與 skills  
- `100_Todo/` — 進行中研究草稿  
- `200_Reference/` — 參考資料  
- `300_Journal/` — 每日日誌
