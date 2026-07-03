---
name: feedback-streamlit-radio-state
description: Streamlit radio/selectbox 導覽不要同時用 index + key + on_change，會把 state 寫壞
metadata: 
  node_type: memory
  type: feedback
  originSessionId: ff323422-fe96-4bae-b5b7-ac23525ccd79
---

pif_tool 的章節導覽 radio 曾因為同時用 `index=`、`key=`、`on_change`（鏡射到自訂 `current_chapter`）三套機制，在某次 rerun 時把 session_state 寫成 `format_func` 產生的顯示字串，導致 `chapters.index(...)` 丟出 `ValueError: '⚠️ 第3章…' is not in list`。

**正確做法**：用 Streamlit 官方標準寫法——只給 `key=`，開頭 `if "key" not in st.session_state: st.session_state["key"] = 預設值` 種一次初始值，**不要傳 `index`**，也不要用 `on_change` 鏡射到另一個 key。之後直接讀 `st.session_state["key"]`。Streamlit 會自動靠 key 跨重跑保存選取值，其他按鈕觸發 rerun 也不會重設。

**Why**：`index` 與「已存在於 session_state 的 key」同時提供是 Streamlit 的衝突情境（會發 warning），時序一亂就會把格式化字串塞進 state；多一層 `current_chapter` 鏡射只是再加一個崩壞面。

**補充（2026-07-01 復發）**：光「只給 key + 種初始值」還不夠。Streamlit 會在某次 rerun 沒重繪某個帶 key 的 widget 時，把該 key 從 session_state purge 掉；一旦 `chapter_selector` 被清掉，開頭種初始值的 `if not in` 就重觸發、章節被重設回 1（症狀：按「計算 INCI 成分表」後跳回第一章）。**修法**：把「目前在第幾章」存到一個獨立的非 widget 鍵 `chapter_current`（普通鍵不會被 purge），radio 每次 rerun 用 `st.session_state.setdefault("chapter_selector", st.session_state["chapter_current"])` 還原預設值，radio 回傳後寫回 `chapter_current`，下游讀 `chapter_current`。仍不傳 `index`、不用 `on_change`，所以不會重演上面的 ValueError。

**How to apply**：改任何 Streamlit 頁面的 radio/selectbox 導覽時，沿用「種初始值 + 只給 key」的單一機制，別疊 index/on_change；若該 widget 可能在某些 rerun 不被渲染（跨頁、進入別的模式分支），導覽狀態要另存在非 widget 鍵避免被 purge。相關：[[project-pif-tool-streamlit-fix]]。
