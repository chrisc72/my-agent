"""PIF 原料資訊頁面"""
import json
import os
import re
import sys
from pathlib import Path
import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from extractor import extract_text_from_file
from ai_analyzer import analyze_ingredient, synthesize_toxicology, parse_ifra_pdf
from web_searcher import search_toxicology, crawl_url, crawl_urls
from tw_regulations import TW_REGULATIONS, lookup_tw_regulation, format_tw_result
from tw_food_ingredients import (
    lookup_food_ingredient, format_food_result, food_list_rows, food_list_size,
)
from database import IngredientDB

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ingredients.db")
REG_SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "reg_settings.json")
db = IngredientDB(DB_PATH)

_FRAGRANCE_INCI_SET = {"fragrance", "parfum", "perfume"}

_IFRA_CATEGORY_ZH = {
    "5A": "身體乳液／身體油／乳液（留置型，手塗）",
    "5B": "臉部保濕霜／化妝水（留置型）",
    "5C": "護手霜／護手乳（留置型）",
    "5D": "嬰兒面霜、嬰兒乳液、嬰兒油、嬰兒爽身粉",
    "6":  "與唇部及口腔接觸的產品（牙膏、漱口水等）",
    "7A": "染燙髮沖洗型（有手部接觸）",
    "7B": "洗護髮留置型（髮膠、護髮素 leave-on 等）",
    "8":  "私密部位外部使用產品（濕紙巾、除臭噴霧等）",
    "9":  "身體與手部沖洗型（沐浴乳、洗髮精、護髮素、洗面乳、肥皂等）",
}


def _is_fragrance_inci(inci_name: str) -> bool:
    return inci_name.strip().lower() in _FRAGRANCE_INCI_SET


def _load_reg_settings(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"disabled_tables": []}


def _save_reg_settings(path: str, settings: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


_reg_settings = _load_reg_settings(REG_SETTINGS_PATH)
_disabled_tables: list[str] = _reg_settings.get("disabled_tables", [])

_reg_doc_rows = db.get_regulation_doc_texts()
_reg_extra_context = ""
if _reg_doc_rows:
    _parts = [
        f"[{d['filename']} / {d.get('doc_type', '法規文件')}]\n{(d.get('extracted_text') or '')[:8000]}"
        for d in _reg_doc_rows
        if d.get("extracted_text")
    ]
    if _parts:
        _reg_extra_context = (
            "【使用者上傳的台灣法規參考文件（請優先依這些法規限制進行評估）】\n\n"
            + "\n\n---\n\n".join(_parts)
        )


def _prepend_reg_context(tox_doc_text: str) -> str:
    if not _reg_extra_context:
        return tox_doc_text
    return _reg_extra_context + ("\n\n" + tox_doc_text if tox_doc_text else "")

FILE_TYPE_ICONS = {
    "COA": "🟢", "SDS": "🔴", "TDS": "🔵",
    "IFRA": "🌸",
    "毒理資料": "🟣",
    "成分組成表": "🟡", "規格書": "🟠",
}


# ── 法規表格檢視 Dialog ───────────────────────────────────────────────────────

@st.dialog("台灣法規資料表", width="large")
def show_regulation_table(table_name: str):
    entries = [r for r in TW_REGULATIONS if r["table"] == table_name]
    df = pd.DataFrame([{
        "INCI 名稱": ", ".join(r["inci_names"][:3]),
        "CAS": ", ".join(r["cas_numbers"][:2]) if r["cas_numbers"] else "",
        "限量標準": r["limit"],
        "適用範圍": r["product_type"],
        "限制說明": r["restrictions"],
        "注意事項": r["warnings"],
    } for r in entries])
    source = entries[0]["source"] if entries else ""
    st.caption(f"共 {len(df)} 項 ｜ 來源：{source}")
    st.dataframe(df, use_container_width=True, hide_index=True)


def _md_linebreaks(text: str) -> str:
    """markdown 會吃掉單一換行，補兩個空白讓每列獨立成行。"""
    return text.replace("\n", "  \n")


@st.dialog("可供食品使用原料一覽表", width="large")
def show_food_ingredient_table():
    rows = food_list_rows()

    def _txt(row, key):
        val = row.get(key)
        return val if isinstance(val, str) else ""

    df = pd.DataFrame([{
        "分類": _txt(r, "次分類"),
        "中文名稱": _txt(r, "中文名稱"),
        "外文名稱": _txt(r, "外文名稱"),
        "學名": _txt(r, "學名"),
        "可食部位": _txt(r, "部位"),
        "使用限制": _txt(r, "備註"),
    } for r in rows])
    st.caption(f"共 {len(df)} 項 ｜ 來源：衛生福利部食品藥物管理署「可供食品使用原料一覽表」")
    st.dataframe(df, use_container_width=True, hide_index=True)


@st.dialog("法規文件內容", width="large")
def show_regulation_doc(filename: str, text: str):
    st.caption(filename)
    st.text_area("文件內容", value=(text or "（無法取得文字內容）")[:6000],
                 height=400, disabled=True)


@st.dialog("修改文件名稱")
def rename_regulation_doc_dialog(doc_id: int, current_name: str):
    new_name = st.text_input("新名稱", value=current_name)
    if st.button("確認", type="primary"):
        if new_name.strip():
            db.rename_regulation_doc(doc_id, new_name.strip())
            st.rerun()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _detect_file_type(filename: str, text: str) -> str:
    fn = filename.upper()
    if "IFRA" in fn:
        return "IFRA"
    if any(k in fn for k in ["SDS", "MSDS", "SAFETY DATA"]):
        return "SDS"
    if any(k in fn for k in ["COA", "CERTIFICATE", "ANALYSIS"]):
        return "COA"
    if any(k in fn for k in ["TDS", "TECHNICAL DATA"]):
        return "TDS"
    if any(k in fn for k in ["TOX", "TOXICOL", "SAFETY ASSESSMENT", "毒理", "NOAEL", "SCCS", "CIR"]):
        return "毒理資料"
    if any(k in fn for k in ["SPEC", "SPECIFICATION"]):
        return "規格書"
    if any(k in fn for k in ["COMPOSITION", "INGREDIENT", "成分"]):
        return "成分組成表"
    snippet = text[:500].upper()
    if "IFRA" in snippet or "INTERNATIONAL FRAGRANCE ASSOCIATION" in snippet:
        return "IFRA"
    if "SAFETY DATA SHEET" in snippet or "物質安全資料表" in snippet:
        return "SDS"
    if "CERTIFICATE OF ANALYSIS" in snippet:
        return "COA"
    if "TECHNICAL DATA SHEET" in snippet:
        return "TDS"
    if "SAFETY ASSESSMENT" in snippet or "TOXICOLOG" in snippet or "NOAEL" in snippet:
        return "毒理資料"
    return "其他"


def _parse_range(range_str) -> tuple:
    if not range_str:
        return None, None, None
    m = re.match(r"([\d.]+)\s*[-–~]\s*([\d.]+)", str(range_str).strip())
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return lo, hi, round((lo + hi) / 2, 4)
    return None, None, None


def _blank_component() -> dict:
    """空白細項成分列，供手動輸入使用。"""
    return {
        "inci_name": "",
        "cas_number": "",
        "percentage": None,
        "percentage_range": None,
        "confidence": "high",
        "note": "",
    }


def _load_db_analysis(mid: int, mat: dict):
    """從資料庫載入已儲存的成分分析，寫入 session state。"""
    db_comps = db.get_components(mid)
    if not db_comps:
        return
    st.session_state[f"analysis_{mid}"] = {
        "product_name": mat["product_name"],
        "is_compound": bool(mat.get("is_compound")),
        "components": [
            {
                "inci_name": c["inci_name"],
                "cas_number": c.get("cas_number"),
                "percentage": c.get("percentage"),
                "percentage_range": c.get("percentage_range"),
                "confidence": "high",
                "note": "",
            }
            for c in db_comps
        ],
        "_from_db": True,
    }
    if any(c.get("toxicology_summary") for c in db_comps):
        st.session_state[f"tox_{mid}"] = [
            {
                "inci_name": c["inci_name"],
                "cas_number": c.get("cas_number"),
                "percentage": c.get("percentage"),
                "percentage_range": c.get("percentage_range"),
                "tw_regulation": format_tw_result(
                    lookup_tw_regulation(
                        inci_name=c["inci_name"],
                        cas_number=c.get("cas_number") or "",
                        disabled_tables=_disabled_tables,
                    )
                ),
                "food_status": lookup_food_ingredient(c["inci_name"]),
                "sccs_raw": c.get("sccs_data", ""),
                "cir_raw": c.get("cir_data", ""),
                "toxicology_summary": c.get("toxicology_summary", ""),
                "sources": c.get("sources", ""),
            }
            for c in db_comps
        ]


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ 設定")
    api_key_input = st.text_input(
        "Anthropic API Key",
        value=os.environ.get("ANTHROPIC_API_KEY", ""),
        type="password",
        help="到 console.anthropic.com 取得 API Key",
    )
    if api_key_input:
        os.environ["ANTHROPIC_API_KEY"] = api_key_input
    st.markdown("---")
    st.markdown("**台灣法規來源**")
    _reg_tables = [
        ("防腐劑表", "防腐劑"),
        ("防曬劑表", "防曬劑"),
        ("成分使用限制表", "成分使用限制"),
        ("色素表", "色素"),
        ("禁止使用成分表", "禁止使用"),
    ]
    _counts = {key: sum(1 for r in TW_REGULATIONS if r["table"] == key) for _, key in _reg_tables}
    for _label, _key in _reg_tables:
        if st.button(f"📋 {_label}（{_counts[_key]}項）", use_container_width=True, key=f"reg_{_key}"):
            show_regulation_table(_key)
    if st.button(f"🥗 可供食品使用原料表（{food_list_size()}項）",
                 use_container_width=True, key="reg_food"):
        show_food_ingredient_table()

    _uploaded_reg_docs = db.get_regulation_doc_texts()
    for _rdoc in _uploaded_reg_docs:
        _c1, _c2, _c3 = st.columns([5, 1, 1])
        with _c1:
            if st.button(f"📋 {_rdoc['filename']}", key=f"view_rdoc_{_rdoc['id']}", use_container_width=True):
                show_regulation_doc(_rdoc['filename'], _rdoc.get('extracted_text') or "")
        with _c2:
            if st.button("✏️", key=f"rename_rdoc_{_rdoc['id']}", help="修改名稱"):
                rename_regulation_doc_dialog(_rdoc['id'], _rdoc['filename'])
        with _c3:
            if st.button("🗑", key=f"del_rdoc_{_rdoc['id']}", help="刪除"):
                db.delete_regulation_doc(_rdoc["id"])
                st.rerun()

    with st.expander("📥 上傳法規文件"):
        _reg_uploads = st.file_uploader(
            "選擇法規文件（可多選）",
            type=["pdf", "docx", "doc", "xlsx", "xls"],
            accept_multiple_files=True,
            key="reg_file_upload",
        )
        _reg_doc_type = st.selectbox(
            "文件類型",
            ["成分使用限制", "防腐劑表", "防曬劑表", "其他法規"],
            key="reg_doc_type_sel",
        )
        if _reg_uploads:
            if st.button("📥 確認上傳", key="confirm_reg_upload", type="primary"):
                for _rf in _reg_uploads:
                    _rt = extract_text_from_file(_rf)
                    db.add_regulation_doc(_rf.name, _reg_doc_type, _rt)
                st.success(f"已上傳 {len(_reg_uploads)} 份法規文件")
                st.rerun()

    st.markdown(
        "\n**毒理來源**\n"
        "- SCCS（歐盟科學委員會）\n"
        "- CIR（美國化妝品成分審查）"
    )


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_list, tab_search = st.tabs(["📋 原料清單", "🔍 直接查詢"])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1：原料清單
# ═══════════════════════════════════════════════════════════════════════════════
with tab_list:
    st.header("原料清單")

    # ── Toolbar ──────────────────────────────────────────────────────────────
    tc1, tc2, tc3, tc4 = st.columns([1.2, 1.4, 5, 0.4])
    with tc1:
        if st.button("➕ 新增原料", use_container_width=True):
            st.session_state["show_add"] = not st.session_state.get("show_add", False)
    with tc2:
        if st.button("📥 匯入 Excel", use_container_width=True):
            st.session_state["show_import"] = not st.session_state.get("show_import", False)
    with tc4:
        if st.button("🔄", help="重整列表"):
            st.rerun()

    # ── 新增原料 form ─────────────────────────────────────────────────────────
    if st.session_state.get("show_add"):
        with st.container(border=True):
            st.markdown("**新增原料**")
            ac1, ac2 = st.columns(2)
            with ac1:
                new_code = st.text_input("原料編號", placeholder="例：RM-001", key="new_code")
            with ac2:
                new_name = st.text_input("原料商品名稱 *", placeholder="例：Hyaluronic Acid", key="new_name")
            bc1, bc2, _ = st.columns([1, 1, 5])
            with bc1:
                if st.button("✅ 確認新增", type="primary", key="confirm_add"):
                    if not new_name.strip():
                        st.error("請輸入原料商品名稱")
                    else:
                        new_mid = db.create_material(
                            new_code.strip(), new_name.strip(), ""
                        )
                        st.session_state["selected_mid"] = new_mid
                        st.session_state.pop("select_material", None)
                        st.session_state["show_add"] = False
                        st.rerun()
            with bc2:
                if st.button("取消", key="cancel_add"):
                    st.session_state["show_add"] = False
                    st.rerun()

    # ── 匯入 Excel ────────────────────────────────────────────────────────────
    if st.session_state.get("show_import"):
        with st.container(border=True):
            st.markdown("**匯入 Excel**")
            st.caption(
                "Excel 欄位名稱包含以下關鍵字即可自動對應：\n"
                "- 原料編號（或 code / no）\n"
                "- 原料商品名稱（或 name）"
            )
            xl_file = st.file_uploader("選擇 Excel 檔案", type=["xlsx", "xls"], key="xl_import")
            if xl_file:
                try:
                    df_xl = pd.read_excel(xl_file)
                    col_map: dict[str, str] = {}
                    for col in df_xl.columns:
                        cu = str(col).upper()
                        if ("編號" in col or "CODE" in cu or col.upper().endswith("NO")) and "code" not in col_map:
                            col_map["code"] = col
                        if ("名稱" in col or "NAME" in cu) and "name" not in col_map:
                            col_map["name"] = col

                    if "name" not in col_map:
                        st.error(f"找不到原料名稱欄位，現有欄位：{list(df_xl.columns)}")
                    else:
                        st.caption(
                            f"欄位對應：原料編號=`{col_map.get('code', '未偵測')}`、"
                            f"名稱=`{col_map['name']}`"
                        )
                        st.dataframe(df_xl[[c for c in col_map.values() if c]].head(5),
                                     use_container_width=True)
                        st.caption(f"共 {len(df_xl)} 筆，預覽前 5 筆")

                        if st.button(f"✅ 確認匯入 {len(df_xl)} 筆", type="primary", key="confirm_import"):
                            imported = 0
                            for _, row in df_xl.iterrows():
                                name = str(row.get(col_map["name"], "")).strip()
                                if not name or name.lower() == "nan":
                                    continue
                                code = str(row.get(col_map.get("code", ""), "")).strip()
                                db.create_material(
                                    "" if code == "nan" else code,
                                    name,
                                    "",
                                )
                                imported += 1
                            st.success(f"已匯入 {imported} 筆原料")
                            st.session_state["show_import"] = False
                            st.rerun()
                except Exception as e:
                    st.error(f"讀取 Excel 失敗：{e}")

    # ── 原料清單主表 ──────────────────────────────────────────────────────────
    materials = db.get_materials_summary()

    if not materials:
        st.info("原料清單是空的，請點擊「新增原料」建立第一筆，或「匯入 Excel」批次建立。")
    else:
        df_mat = pd.DataFrame(materials)
        df_mat["is_compound"] = df_mat["is_compound"].map({1: "複方", 0: "單一"})
        df_mat["created_at"] = pd.to_datetime(df_mat["created_at"]).dt.strftime("%Y-%m-%d")

        def _progress(row):
            if row["file_count"] == 0:
                return "⬜ 待上傳"
            if row["component_count"] == 0:
                return "📄 待分析"
            if row.get("tox_count", 0) == 0:
                return "🤖 待毒理"
            return "✅ 完整"

        df_mat["進度"] = df_mat.apply(_progress, axis=1)

        search_q = st.text_input("搜尋原料", placeholder="輸入原料名稱、編號或供應商…", key="mat_search")
        if search_q:
            mask = (
                df_mat["product_name"].str.contains(search_q, case=False, na=False)
                | df_mat["ingredient_code"].fillna("").str.contains(search_q, case=False)
                | df_mat["supplier"].fillna("").str.contains(search_q, case=False)
            )
            orig_indices = df_mat[mask].index.tolist()
            df_mat = df_mat[mask].reset_index(drop=True)
            materials_view = [materials[i] for i in orig_indices]
        else:
            materials_view = materials

        display_cols = {
            "ingredient_code": "原料編號",
            "product_name": "原料商品名稱",
            "進度": "進度",
            "is_compound": "類型",
            "file_count": "文件",
            "component_count": "成分筆數",
            "created_at": "建立日期",
        }
        cols_exist = [c for c in display_cols if c in df_mat.columns]
        st.caption("點選列即可選取原料，管理文件與分析")
        table_event = st.dataframe(
            df_mat[cols_exist].rename(columns=display_cols),
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
        )

        # 從表格點選更新 selected_mid
        sel_rows = table_event.selection.rows if table_event and table_event.selection else []
        if sel_rows:
            mid = materials_view[sel_rows[0]]["id"]
            st.session_state["selected_mid"] = mid
        else:
            mid = st.session_state.get("selected_mid")

        # ── 原料詳情 ──────────────────────────────────────────────────────────
        if mid:
            mat = next((m for m in materials if m["id"] == mid), None)
            if not mat:
                st.rerun()

            # 從 DB 載入已儲存的分析（若 session state 還沒有）
            if f"analysis_{mid}" not in st.session_state:
                _load_db_analysis(mid, mat)

            st.markdown("---")

            # Header
            code_label = mat.get("ingredient_code") or "（無編號）"
            st.subheader(f"📦 {code_label}　｜　{mat['product_name']}")

            # ── 編輯基本資訊 ──────────────────────────────────────────────────
            with st.expander("✏️ 編輯基本資訊"):
                ec1, ec2 = st.columns(2)
                with ec1:
                    edit_code = st.text_input(
                        "原料編號", value=mat.get("ingredient_code") or "", key=f"ec_{mid}"
                    )
                with ec2:
                    edit_name = st.text_input(
                        "原料商品名稱", value=mat.get("product_name") or "", key=f"en_{mid}"
                    )
                if st.button("💾 儲存基本資訊", key=f"save_info_{mid}"):
                    db.update_material_info(
                        mid, edit_code.strip(), edit_name.strip(), ""
                    )
                    st.success("已更新")
                    st.rerun()

            # ─────────────────────────────────────────────────────────────────
            # 相關文件
            # ─────────────────────────────────────────────────────────────────
            st.markdown("### 📎 相關文件")
            mat_files = db.get_material_files(mid)

            if mat_files:
                for mf in mat_files:
                    ftype = mf.get("file_type") or "其他"
                    icon = FILE_TYPE_ICONS.get(ftype, "⚪")
                    date_str = str(mf.get("uploaded_at", ""))[:10]
                    col_fn, col_dl, col_rm = st.columns([6, 1, 1])
                    with col_fn:
                        st.markdown(
                            f"{icon} **{mf['filename']}**&emsp;`{ftype}`&emsp;"
                            f"<small style='color:gray'>{date_str}</small>",
                            unsafe_allow_html=True,
                        )
                    with col_dl:
                        if mf.get("has_file_data"):
                            file_bytes = db.get_material_file_data(mf["id"])
                            if file_bytes:
                                st.download_button(
                                    "⬇",
                                    data=file_bytes,
                                    file_name=mf["filename"],
                                    key=f"dl_{mf['id']}",
                                    help="下載原始檔案",
                                )
                    with col_rm:
                        if st.button("移除", key=f"rmf_{mf['id']}"):
                            db.delete_material_file(mf["id"])
                            st.rerun()
            else:
                st.caption("尚無上傳文件")

            with st.expander("➕ 上傳文件（SDS / COA / TDS / 毒理資料 / 其他）"):
                new_uploads = st.file_uploader(
                    "選擇文件（可多選）",
                    type=["pdf", "docx", "doc", "xlsx", "xls", "md"],
                    accept_multiple_files=True,
                    key=f"up_{mid}",
                )
                if new_uploads:
                    if st.button("📥 確認上傳", key=f"do_up_{mid}", type="primary"):
                        with st.spinner(f"讀取 {len(new_uploads)} 份文件..."):
                            for nf in new_uploads:
                                raw_bytes = nf.read()
                                nf.seek(0)
                                text = extract_text_from_file(nf)
                                ftype = _detect_file_type(nf.name, text)
                                db.add_material_file(mid, nf.name, ftype, text, file_data=raw_bytes)
                        st.success(f"已上傳 {len(new_uploads)} 份文件")
                        st.rerun()

            # ─────────────────────────────────────────────────────────────────
            # AI 成分分析
            # ─────────────────────────────────────────────────────────────────
            st.markdown("### 🤖 AI 成分分析")

            analysis_key = f"analysis_{mid}"
            tox_key = f"tox_{mid}"
            n_files = len(mat_files)

            if n_files == 0:
                st.caption("可上傳相關文件交由 AI 分析，或直接手動輸入成分。")
            else:
                already_analyzed = analysis_key in st.session_state
                btn_label = f"🔄 重新分析（{n_files} 份文件）" if already_analyzed else f"🤖 分析成分（{n_files} 份文件）"
                if st.button(btn_label, key=f"do_analyze_{mid}", type="primary"):
                    if not os.environ.get("ANTHROPIC_API_KEY"):
                        st.error("請先在左側 Sidebar 輸入 API Key")
                    else:
                        with st.spinner("讀取文件..."):
                            file_texts = db.get_material_file_texts(mid)
                            files_for_ai = [
                                {"filename": f["filename"], "text": f["extracted_text"] or ""}
                                for f in file_texts
                            ]
                        with st.spinner(f"AI 合併分析 {n_files} 份文件中（約 15–40 秒）..."):
                            result = analyze_ingredient(files_for_ai)
                        st.session_state[analysis_key] = result
                        st.session_state.pop(tox_key, None)
                        st.rerun()

            # 手動輸入入口（尚無 AI 結果、DB 也還沒有成分時）
            if analysis_key not in st.session_state:
                if st.button("✏️ 手動輸入成分（免上傳文件）", key=f"manual_{mid}"):
                    st.session_state[analysis_key] = {
                        "product_name": mat["product_name"],
                        "is_compound": False,
                        "components": [_blank_component()],
                        "_from_db": False,
                        "_manual": True,
                    }
                    st.session_state.pop(tox_key, None)
                    st.rerun()

            # 顯示分析結果
            if analysis_key in st.session_state:
                result = st.session_state[analysis_key]

                if result.get("error"):
                    st.error(f"分析錯誤：{result['error']}")
                    st.code(result.get("raw_response", ""))
                else:
                    is_compound = result.get("is_compound", False)
                    if result.get("_from_db"):
                        status_note = "（已儲存至資料庫）"
                    elif result.get("_manual"):
                        status_note = "（手動輸入，尚未儲存）"
                    else:
                        status_note = "（尚未儲存）"
                    badge = "🔀 複方原料" if is_compound else "🧪 單一原料"
                    st.caption(f"類型：{badge}　{status_note}")

                    if result.get("raw_composition_text") and not result.get("_from_db"):
                        with st.expander("📋 文件原始組成描述"):
                            st.text(result["raw_composition_text"][:500])

                    components = result.get("components", [])
                    if components:
                        # 自動填入比例範圍中間值
                        has_range = False
                        for comp in components:
                            rng = comp.get("percentage_range")
                            if rng and comp.get("percentage") is None:
                                _, _, midpoint = _parse_range(rng)
                                if midpoint is not None:
                                    comp["percentage"] = midpoint
                                    has_range = True

                        df_comp = pd.DataFrame(components)
                        if "percentage_range" not in df_comp.columns:
                            df_comp["percentage_range"] = None

                        st.markdown("**成分組成表（可直接修改）**")
                        if has_range:
                            st.info("部分成分只提供比例範圍，百分比已自動填入中間值。")

                        cols_show = [
                            c for c in
                            ["inci_name", "cas_number", "percentage_range", "percentage", "confidence", "note"]
                            if c in df_comp.columns
                        ]
                        edited_df = st.data_editor(
                            df_comp[cols_show].copy(),
                            column_config={
                                "inci_name": st.column_config.TextColumn("INCI 名稱", width="large"),
                                "cas_number": st.column_config.TextColumn("CAS Number"),
                                "percentage_range": st.column_config.TextColumn(
                                    "比例範圍（原始）", disabled=True, width="small"
                                ),
                                "percentage": st.column_config.NumberColumn(
                                    "w/w %", format="%.4f", min_value=0, max_value=100
                                ),
                                "confidence": st.column_config.SelectboxColumn(
                                    "確信度", options=["high", "medium", "low"]
                                ),
                                "note": st.column_config.TextColumn("備註", width="medium"),
                            },
                            num_rows="dynamic",
                            use_container_width=True,
                            key=f"editor_{mid}",
                        )

                        total_pct = edited_df["percentage"].dropna().sum() if "percentage" in edited_df else 0
                        if total_pct > 0:
                            st.caption(f"已填比例合計：{total_pct:.2f}%")

                        save_comp_label = "✅ 已儲存成分" if result.get("_from_db") else "💾 儲存成分（不含毒理）"
                        if st.button(save_comp_label, key=f"save_comp_{mid}", type="secondary"):
                            comps_to_save = [
                                c for c in edited_df.to_dict("records")
                                if str(c.get("inci_name") or "").strip()
                            ]
                            if not comps_to_save:
                                st.warning("請至少填入一個 INCI 名稱再儲存。")
                            else:
                                auto_compound = len(comps_to_save) > 1
                                db.save_components(mid, auto_compound, comps_to_save)
                                st.session_state[analysis_key]["_from_db"] = True
                                st.session_state[analysis_key]["is_compound"] = auto_compound
                                st.session_state[analysis_key]["components"] = comps_to_save
                                st.success("✅ 已儲存成分")
                                st.rerun()

                        # ─── IFRA 香精用量（僅 Fragrance/Parfum/Perfume 出現時）──────
                        fragrance_comps = [
                            c for c in components
                            if _is_fragrance_inci(c.get("inci_name", ""))
                        ]
                        if fragrance_comps:
                            st.markdown("### 🌸 IFRA 香精用量限制")
                            st.caption(
                                "偵測到 Fragrance / Parfum 成分，可上傳此香精的 IFRA 文件，"
                                "系統將解析 Category 5–9 的最高用量限制。"
                            )
                            for fc in fragrance_comps:
                                fc_inci = fc["inci_name"]
                                existing_ifra = db.get_ifra_data(mid, fc_inci)
                                if existing_ifra and existing_ifra.get("categories"):
                                    fname = existing_ifra.get("fragrance_name", "")
                                    if fname:
                                        st.markdown(f"**{fc_inci}**（{fname}）")
                                    else:
                                        st.markdown(f"**{fc_inci}**")
                                    rows = [
                                        {
                                            "Category": cat,
                                            "中文說明": _IFRA_CATEGORY_ZH.get(cat, ""),
                                            "最高用量限制": limit,
                                        }
                                        for cat, limit in sorted(existing_ifra["categories"].items())
                                    ]
                                    st.dataframe(rows, use_container_width=True, hide_index=True)
                                    if st.button("🔄 重新上傳 IFRA", key=f"re_ifra_{mid}_{fc_inci}"):
                                        db.save_ifra_data(mid, fc_inci, "")
                                        st.rerun()
                                else:
                                    ifra_file = st.file_uploader(
                                        f"📄 上傳 {fc_inci} 的 IFRA 文件（PDF）",
                                        type=["pdf"],
                                        key=f"ifra_up_{mid}_{fc_inci}",
                                    )
                                    if ifra_file:
                                        if st.button(
                                            "🔍 解析 IFRA 用量",
                                            key=f"parse_ifra_{mid}_{fc_inci}",
                                            type="primary",
                                        ):
                                            if not os.environ.get("ANTHROPIC_API_KEY"):
                                                st.error("請先輸入 API Key")
                                            else:
                                                with st.spinner("AI 解析 IFRA 文件中..."):
                                                    pdf_bytes = ifra_file.read()
                                                    parsed = parse_ifra_pdf(pdf_bytes)
                                                if "error" in parsed:
                                                    st.error(f"解析失敗：{parsed['error']}")
                                                    with st.expander("🔍 排查資訊"):
                                                        st.json(parsed)
                                                elif not parsed.get("categories"):
                                                    st.warning(
                                                        "AI 未能在文件中找到 Category 5–9 用量資料。\n"
                                                        "請確認上傳的 PDF 包含 IFRA 用量限制表格，"
                                                        "或試試重新上傳。"
                                                    )
                                                    with st.expander("🔍 AI 回傳的原始資料"):
                                                        st.json(parsed)
                                                else:
                                                    db.save_ifra_data(
                                                        mid, fc_inci,
                                                        json.dumps(parsed, ensure_ascii=False),
                                                    )
                                                    st.success("✅ IFRA 用量已儲存")
                                                    st.rerun()

                        # ─── 毒理資料 ─────────────────────────────────────────
                        st.markdown("### 🔬 毒理資料")

                        extra_tox_text = st.text_area(
                            "📝 補充毒理資訊（選填）",
                            placeholder="可在此貼入或輸入額外的毒理資料、安全評估摘要、文獻摘錄等，AI 搜尋時會優先參考",
                            height=120,
                            key=f"extra_tox_input_{mid}",
                            help="此欄位內容會在搜尋毒理資料時，優先提供給 AI 作為參考依據",
                        )

                        ref_urls_input = st.text_area(
                            "🔗 補充參考網址（選填，每行一個網址）",
                            placeholder="https://example.com/safety-report\nhttps://...",
                            height=100,
                            key=f"ref_urls_input_{mid}",
                            help="AI 找不到足夠毒理資訊時，可在此貼上參考資料網址，系統會自動爬取頁面內容提供給 AI",
                        )

                        tox_files_upload = st.file_uploader(
                            "📎 上傳補充毒理文件（選填）",
                            type=["pdf", "docx"],
                            accept_multiple_files=True,
                            key=f"tox_files_upload_{mid}",
                            help="可上傳 PDF 或 Word (.docx) 格式的毒理安全報告，AI 分析時會優先參考",
                        )

                        if tox_key not in st.session_state:
                            if st.button(
                                "🔬 搜尋毒理資料（SCCS / CIR）",
                                key=f"do_tox_{mid}",
                                type="primary",
                            ):
                                if not os.environ.get("ANTHROPIC_API_KEY"):
                                    st.error("請先輸入 API Key")
                                else:
                                    confirmed = edited_df.to_dict("records")
                                    tox_results = []
                                    progress = st.progress(0, text="搜尋毒理資料中...")
                                    total = len(confirmed)
                                    all_file_texts = db.get_material_file_texts(mid)
                                    tox_doc_parts = [
                                        f["extracted_text"] for f in all_file_texts
                                        if f.get("file_type") == "毒理資料" and f.get("extracted_text")
                                    ]
                                    tox_doc_text = "\n\n".join(tox_doc_parts)
                                    if extra_tox_text.strip():
                                        tox_doc_text = extra_tox_text.strip() + (
                                            "\n\n" + tox_doc_text if tox_doc_text else ""
                                        )
                                    if tox_files_upload:
                                        file_parts = []
                                        for uf in tox_files_upload:
                                            extracted = extract_text_from_file(uf)
                                            if extracted.strip():
                                                file_parts.append(f"[來源文件：{uf.name}]\n{extracted}")
                                        if file_parts:
                                            tox_doc_text = (
                                                "【使用者上傳補充毒理文件】\n"
                                                + "\n\n---\n\n".join(file_parts)
                                                + ("\n\n" + tox_doc_text if tox_doc_text else "")
                                            )
                                    ref_urls = [
                                        u for u in ref_urls_input.splitlines() if u.strip().startswith("http")
                                    ]
                                    if ref_urls:
                                        crawl_progress = st.progress(0, text="爬取參考網址中...")
                                        crawled_parts = []
                                        for ci, url in enumerate(ref_urls):
                                            crawl_progress.progress(
                                                (ci + 1) / len(ref_urls),
                                                text=f"爬取 ({ci+1}/{len(ref_urls)})：{url[:60]}...",
                                            )
                                            crawled_parts.append(crawl_url(url.strip()))
                                        crawl_progress.empty()
                                        crawled_text = "\n\n---\n\n".join(crawled_parts)
                                        tox_doc_text = (
                                            "【使用者提供參考網址內容】\n" + crawled_text
                                            + ("\n\n" + tox_doc_text if tox_doc_text else "")
                                        )
                                    for i, comp in enumerate(confirmed):
                                        inci = comp.get("inci_name", "")
                                        cas = comp.get("cas_number") or ""
                                        progress.progress(
                                            (i + 1) / total,
                                            text=f"({i+1}/{total}) 搜尋 {inci}...",
                                        )
                                        orig = next(
                                            (c for c in components if c.get("inci_name") == inci), {}
                                        )
                                        existing = db.get_single_material_component_by_inci(inci, cas)
                                        if existing and existing.get("toxicology_summary"):
                                            tw_entries_reuse = lookup_tw_regulation(
                                                inci_name=inci, cas_number=cas, disabled_tables=_disabled_tables
                                            )
                                            tw_text_reuse = format_tw_result(tw_entries_reuse)
                                            tox_results.append({
                                                **comp,
                                                "percentage_range": orig.get("percentage_range"),
                                                "tw_regulation": tw_text_reuse,
                                                "food_status": lookup_food_ingredient(inci),
                                                "sccs_raw": existing.get("sccs_data", ""),
                                                "cir_raw": existing.get("cir_data", ""),
                                                "cosing_raw": "",
                                                "toxicology_summary": existing.get("toxicology_summary", ""),
                                                "sources": existing.get("sources", ""),
                                                "_reused_from_db": True,
                                            })
                                            continue
                                        tw_entries = lookup_tw_regulation(inci_name=inci, cas_number=cas, disabled_tables=_disabled_tables)
                                        tw_text = format_tw_result(tw_entries)
                                        food_res = lookup_food_ingredient(inci)
                                        web = search_toxicology(inci, cas, db=db)
                                        synthesis = synthesize_toxicology(
                                            inci, cas, tw_text, web["sccs_raw"], web["cir_raw"],
                                            _prepend_reg_context(tox_doc_text),
                                            web.get("cosing_raw", ""),
                                            format_food_result(food_res) if food_res["status"] == "food_approved" else "",
                                        )
                                        tox_results.append({
                                            **comp,
                                            "percentage_range": orig.get("percentage_range"),
                                            "tw_regulation": tw_text,
                                            "food_status": food_res,
                                            "sccs_raw": web["sccs_raw"],
                                            "cir_raw": web["cir_raw"],
                                            "cosing_raw": web.get("cosing_raw", ""),
                                            "toxicology_summary": synthesis,
                                            "sources": "\n".join(web["sources"]),
                                            "_reused_from_db": False,
                                        })
                                    progress.empty()
                                    st.session_state[tox_key] = tox_results
                                    st.rerun()

                        if tox_key in st.session_state:
                            tox_results = st.session_state[tox_key]
                            for i, item in enumerate(tox_results):
                                inci = item.get("inci_name", "未知")
                                pct = item.get("percentage")
                                pct_str = f" — {pct:.2f}%" if pct else ""
                                cas = item.get("cas_number") or "N/A"
                                reused_label = " 🗄️ 複用資料庫" if item.get("_reused_from_db") else ""
                                with st.expander(f"📋 {inci}{pct_str}  (CAS: {cas}){reused_label}", expanded=False):
                                    tw = item.get("tw_regulation", "")
                                    if tw and "未在台灣" not in tw:
                                        st.markdown("### 🇹🇼 台灣法規")
                                        st.info(tw)
                                    else:
                                        st.caption("台灣法規：不在限制名單")
                                    food_res = item.get("food_status") or {}
                                    if food_res.get("status") == "food_approved":
                                        st.markdown("### 🥗 食用安全史")
                                        st.success(_md_linebreaks(format_food_result(food_res)))
                                    edit_mode_key = f"edit_mode_{mid}_{i}"
                                    hdr_col, btn_col = st.columns([5, 1])
                                    with hdr_col:
                                        st.markdown("### 毒理分析")
                                    if not st.session_state.get(edit_mode_key, False):
                                        with btn_col:
                                            if st.button("✏️ 編輯", key=f"start_edit_{mid}_{i}", use_container_width=True):
                                                st.session_state[edit_mode_key] = True
                                                st.rerun()
                                        st.markdown(item.get("toxicology_summary", "（無資料）"))
                                    else:
                                        edited_text = st.text_area(
                                            "毒理摘要",
                                            value=item.get("toxicology_summary", ""),
                                            height=400,
                                            key=f"tox_edit_area_{mid}_{i}",
                                            label_visibility="collapsed",
                                        )
                                        save_col, cancel_col = st.columns(2)
                                        with save_col:
                                            if st.button("💾 儲存", key=f"save_edit_{mid}_{i}", type="primary", use_container_width=True):
                                                current = list(st.session_state[tox_key])
                                                current[i] = {**item, "toxicology_summary": edited_text}
                                                st.session_state[tox_key] = current
                                                st.session_state[edit_mode_key] = False
                                                st.rerun()
                                        with cancel_col:
                                            if st.button("✕ 取消", key=f"cancel_edit_{mid}_{i}", use_container_width=True):
                                                st.session_state[edit_mode_key] = False
                                                st.rerun()
                                    sources = item.get("sources", "")
                                    if sources:
                                        st.markdown("**參考來源：**")
                                        for url in sources.split("\n"):
                                            if url.startswith("http"):
                                                st.markdown(f"- {url}")
                                    st.divider()
                                    col_a, col_b = st.columns(2)
                                    with col_a:
                                        if st.button(
                                            "🔄 重新分析此成分",
                                            key=f"reanalyze_{mid}_{i}",
                                            help="使用補充毒理資訊重新讓 AI 分析此成分，保留現有 SCCS/CIR 原始資料",
                                            use_container_width=True,
                                        ):
                                            if not os.environ.get("ANTHROPIC_API_KEY"):
                                                st.error("請先輸入 API Key")
                                            else:
                                                has_supplementary = bool(
                                                    extra_tox_text.strip()
                                                    or tox_files_upload
                                                    or any(
                                                        u.strip().startswith("http")
                                                        for u in ref_urls_input.splitlines()
                                                    )
                                                )
                                                if not has_supplementary:
                                                    existing = db.get_single_material_component_by_inci(
                                                        item.get("inci_name", ""),
                                                        item.get("cas_number") or "",
                                                    )
                                                    if existing and existing.get("toxicology_summary"):
                                                        current = list(st.session_state[tox_key])
                                                        current[i] = {
                                                            **item,
                                                            "tw_regulation": existing.get("tw_regulation", item.get("tw_regulation", "")),
                                                            "toxicology_summary": existing["toxicology_summary"],
                                                            "sources": existing.get("sources", item.get("sources", "")),
                                                            "sccs_raw": existing.get("sccs_data", item.get("sccs_raw", "")),
                                                            "cir_raw": existing.get("cir_data", item.get("cir_raw", "")),
                                                            "_reused_from_db": True,
                                                        }
                                                        st.session_state[tox_key] = current
                                                        st.success(f"已從資料庫複用 {inci} 的毒理資料")
                                                        st.rerun()
                                                update_extra = extra_tox_text.strip()
                                                if tox_files_upload:
                                                    file_parts = []
                                                    for uf in tox_files_upload:
                                                        extracted = extract_text_from_file(uf)
                                                        if extracted.strip():
                                                            file_parts.append(f"[來源文件：{uf.name}]\n{extracted}")
                                                    if file_parts:
                                                        update_extra = (
                                                            "【使用者上傳補充毒理文件】\n"
                                                            + "\n\n---\n\n".join(file_parts)
                                                            + ("\n\n" + update_extra if update_extra else "")
                                                        )
                                                update_ref_urls = [
                                                    u for u in ref_urls_input.splitlines()
                                                    if u.strip().startswith("http")
                                                ]
                                                if update_ref_urls:
                                                    crawl_prog = st.progress(0, text="爬取參考網址中...")
                                                    crawled_parts = []
                                                    for ci, url in enumerate(update_ref_urls):
                                                        crawl_prog.progress(
                                                            (ci + 1) / len(update_ref_urls),
                                                            text=f"爬取 ({ci+1}/{len(update_ref_urls)})：{url[:60]}...",
                                                        )
                                                        crawled_parts.append(crawl_url(url.strip()))
                                                    crawl_prog.empty()
                                                    crawled_text = "\n\n---\n\n".join(crawled_parts)
                                                    update_extra = (
                                                        "【使用者提供參考網址內容】\n" + crawled_text
                                                        + ("\n\n" + update_extra if update_extra else "")
                                                    )
                                                with st.spinner(f"重新分析 {inci}..."):
                                                    new_summary = synthesize_toxicology(
                                                        item["inci_name"],
                                                        item.get("cas_number") or "",
                                                        item.get("tw_regulation", ""),
                                                        item.get("sccs_raw", ""),
                                                        item.get("cir_raw", ""),
                                                        _prepend_reg_context(update_extra),
                                                        item.get("cosing_raw", ""),
                                                    )
                                                current = list(st.session_state[tox_key])
                                                current[i] = {**item, "toxicology_summary": new_summary, "_reused_from_db": False}
                                                st.session_state[tox_key] = current
                                                st.rerun()
                                    with col_b:
                                        if st.button(
                                            "🗑️ 刪除並重新搜尋",
                                            key=f"rescrape_{mid}_{i}",
                                            help="清除此成分所有毒理資料（含 SCCS/CIR）並從頭重新執行網路搜尋與 AI 分析",
                                            use_container_width=True,
                                        ):
                                            st.session_state[f"confirm_rescrape_{mid}_{i}"] = True
                                            st.rerun()
                                    if st.session_state.get(f"confirm_rescrape_{mid}_{i}"):
                                        st.warning("⚠️ 此操作將清除此成分所有毒理資料（含 SCCS/CIR），並從頭重新上網搜尋。確定繼續？")
                                        c_yes, c_no = st.columns(2)
                                        with c_yes:
                                            if st.button("✅ 確認重新搜尋", key=f"confirm_yes_{mid}_{i}", type="primary", use_container_width=True):
                                                st.session_state.pop(f"confirm_rescrape_{mid}_{i}", None)
                                                if not os.environ.get("ANTHROPIC_API_KEY"):
                                                    st.error("請先輸入 API Key")
                                                else:
                                                    inci_name = item.get("inci_name", "")
                                                    cas_num = item.get("cas_number") or ""
                                                    extra_for_rescrape = extra_tox_text.strip()
                                                    if tox_files_upload:
                                                        file_parts_rs = []
                                                        for uf in tox_files_upload:
                                                            extracted = extract_text_from_file(uf)
                                                            if extracted.strip():
                                                                file_parts_rs.append(f"[來源文件：{uf.name}]\n{extracted}")
                                                        if file_parts_rs:
                                                            extra_for_rescrape = (
                                                                "【使用者上傳補充毒理文件】\n"
                                                                + "\n\n---\n\n".join(file_parts_rs)
                                                                + ("\n\n" + extra_for_rescrape if extra_for_rescrape else "")
                                                            )
                                                    with st.spinner(f"重新搜尋並分析 {inci_name}..."):
                                                        tw_entries_rs = lookup_tw_regulation(inci_name=inci_name, cas_number=cas_num, disabled_tables=_disabled_tables)
                                                        tw_text_rs = format_tw_result(tw_entries_rs)
                                                        food_res_rs = lookup_food_ingredient(inci_name)
                                                        web_rs = search_toxicology(inci_name, cas_num, db=db, use_cache=False)
                                                        new_summary = synthesize_toxicology(
                                                            inci_name,
                                                            cas_num,
                                                            tw_text_rs,
                                                            web_rs.get("sccs_raw", ""),
                                                            web_rs.get("cir_raw", ""),
                                                            _prepend_reg_context(extra_for_rescrape),
                                                            web_rs.get("cosing_raw", ""),
                                                            format_food_result(food_res_rs) if food_res_rs["status"] == "food_approved" else "",
                                                        )
                                                    current = list(st.session_state[tox_key])
                                                    current[i] = {
                                                        **item,
                                                        "tw_regulation": tw_text_rs,
                                                        "food_status": food_res_rs,
                                                        "sccs_raw": web_rs.get("sccs_raw", ""),
                                                        "cir_raw": web_rs.get("cir_raw", ""),
                                                        "cosing_raw": web_rs.get("cosing_raw", ""),
                                                        "toxicology_summary": new_summary,
                                                        "sources": "\n".join(web_rs.get("sources", [])),
                                                        "_reused_from_db": False,
                                                    }
                                                    st.session_state[tox_key] = current
                                                    st.rerun()
                                        with c_no:
                                            if st.button("取消", key=f"confirm_no_{mid}_{i}", use_container_width=True):
                                                st.session_state.pop(f"confirm_rescrape_{mid}_{i}", None)
                                                st.rerun()

                            st.divider()
                            btn_col1, btn_col2 = st.columns(2)
                            with btn_col1:
                                if st.button(
                                    "🔄 更新所有成分毒理資料",
                                    key=f"update_tox_{mid}",
                                    type="primary",
                                    use_container_width=True,
                                    help="套用補充毒理資訊，對此原料下的所有成分重新讓 AI 評估（不重新爬 SCCS/CIR）",
                                ):
                                    if not os.environ.get("ANTHROPIC_API_KEY"):
                                        st.error("請先輸入 API Key")
                                    else:
                                        update_ref_urls = [
                                            u for u in ref_urls_input.splitlines()
                                            if u.strip().startswith("http")
                                        ]
                                        update_extra = extra_tox_text.strip()
                                        if tox_files_upload:
                                            file_parts = []
                                            for uf in tox_files_upload:
                                                extracted = extract_text_from_file(uf)
                                                if extracted.strip():
                                                    file_parts.append(f"[來源文件：{uf.name}]\n{extracted}")
                                            if file_parts:
                                                update_extra = (
                                                    "【使用者上傳補充毒理文件】\n"
                                                    + "\n\n---\n\n".join(file_parts)
                                                    + ("\n\n" + update_extra if update_extra else "")
                                                )
                                        if update_ref_urls:
                                            crawl_prog = st.progress(0, text="爬取參考網址中...")
                                            crawled_parts = []
                                            for ci, url in enumerate(update_ref_urls):
                                                crawl_prog.progress(
                                                    (ci + 1) / len(update_ref_urls),
                                                    text=f"爬取 ({ci+1}/{len(update_ref_urls)})：{url[:60]}...",
                                                )
                                                crawled_parts.append(crawl_url(url.strip()))
                                            crawl_prog.empty()
                                            crawled_text = "\n\n---\n\n".join(crawled_parts)
                                            update_extra = (
                                                "【使用者提供參考網址內容】\n" + crawled_text
                                                + ("\n\n" + update_extra if update_extra else "")
                                            )
                                        current_tox = st.session_state[tox_key]
                                        updated = []
                                        prog = st.progress(0, text="重新評估毒理資料中...")
                                        total_update = len(current_tox)
                                        for idx, item in enumerate(current_tox):
                                            prog.progress(
                                                (idx + 1) / total_update,
                                                text=f"({idx+1}/{total_update}) 更新 {item['inci_name']}...",
                                            )
                                            new_summary = synthesize_toxicology(
                                                item["inci_name"],
                                                item.get("cas_number") or "",
                                                item.get("tw_regulation", ""),
                                                item.get("sccs_raw", ""),
                                                item.get("cir_raw", ""),
                                                _prepend_reg_context(update_extra),
                                                item.get("cosing_raw", ""),
                                            )
                                            updated.append({**item, "toxicology_summary": new_summary})
                                        prog.empty()
                                        st.session_state[tox_key] = updated
                                        st.rerun()
                            with btn_col2:
                                if st.button(
                                    "💾 儲存成分與毒理至資料庫",
                                    key=f"save_tox_{mid}",
                                    type="secondary",
                                    use_container_width=True,
                                ):
                                    editable_fields = ["inci_name", "cas_number", "percentage", "confidence", "note"]
                                    merged = []
                                    for i, item in enumerate(tox_results):
                                        if i < len(edited_df):
                                            row = edited_df.iloc[i]
                                            updates = {f: row[f] for f in editable_fields if f in row.index}
                                            merged.append({**item, **updates})
                                        else:
                                            merged.append(item)
                                    st.session_state[tox_key] = merged
                                    db.save_components(mid, is_compound, merged)
                                    st.session_state[analysis_key]["_from_db"] = True
                                    st.success(f"✅ 已儲存「{mat['product_name']}」的成分與毒理資料")
                                    st.rerun()
                    else:
                        st.warning("未能解析出成分，請確認文件內容是否包含成分資訊。")

            # ── 刪除原料 ──────────────────────────────────────────────────────
            st.divider()
            col_del, _ = st.columns([1, 5])
            with col_del:
                if st.button("🗑️ 刪除此原料", key=f"del_mat_{mid}", type="secondary"):
                    db.delete_material(mid)
                    st.session_state.pop("selected_mid", None)
                    st.session_state.pop(f"analysis_{mid}", None)
                    st.session_state.pop(f"tox_{mid}", None)
                    st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2：直接查詢
# ═══════════════════════════════════════════════════════════════════════════════
with tab_search:
    st.header("直接查詢台灣法規 / 毒理資料")
    st.caption("輸入 INCI 名稱或 CAS Number，即時查詢台灣衛福部法規（離線）。毒理摘要需 API Key。")

    col_q1, col_q2 = st.columns(2)
    with col_q1:
        query_inci = st.text_input("INCI 名稱", placeholder="例：Phenoxyethanol")
    with col_q2:
        query_cas = st.text_input("CAS Number", placeholder="例：122-99-6")

    if st.button("🔍 查詢台灣法規", use_container_width=True):
        if not query_inci and not query_cas:
            st.warning("請輸入 INCI 名稱或 CAS Number")
        else:
            entries = lookup_tw_regulation(inci_name=query_inci, cas_number=query_cas, disabled_tables=_disabled_tables)
            if entries:
                for e in entries:
                    st.markdown(f"### 【{e['table']}表】 {', '.join(e['inci_names'][:2])}")
                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.metric("限量標準", e["limit"])
                    with col_b:
                        if e["product_type"]:
                            st.info(f"適用範圍：{e['product_type']}")
                    if e["restrictions"]:
                        st.info(f"限制說明：{e['restrictions']}")
                    if e["warnings"]:
                        st.warning(f"注意事項：{e['warnings']}")
                    st.caption(f"來源：{e['source']}")
                    st.divider()
            else:
                st.success("✅ 此成分**不在**台灣衛福部限制名單中（可自由使用，但仍需確保安全性）")

            if query_inci:
                food_res_q = lookup_food_ingredient(query_inci)
                if food_res_q["status"] == "food_approved":
                    st.markdown("### 🥗 食用安全史")
                    st.success(_md_linebreaks(format_food_result(food_res_q)))

    if st.button("🔬 同時搜尋 SCCS/CIR 毒理（需 API Key）", use_container_width=True):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            st.error("請先在 Sidebar 輸入 API Key")
        elif not query_inci and not query_cas:
            st.warning("請輸入 INCI 名稱或 CAS Number")
        else:
            inci = query_inci or ""
            cas = query_cas or ""
            with st.spinner(f"搜尋 {inci or cas} 毒理資料中..."):
                tw_entries = lookup_tw_regulation(inci_name=inci, cas_number=cas, disabled_tables=_disabled_tables)
                tw_text = format_tw_result(tw_entries)
                food_res = lookup_food_ingredient(inci)
                food_text = format_food_result(food_res) if food_res["status"] == "food_approved" else ""
                web = search_toxicology(inci, cas, db=db)
                synthesis = synthesize_toxicology(inci, cas, tw_text, web["sccs_raw"], web["cir_raw"], _prepend_reg_context(""), web.get("cosing_raw", ""), food_text)

            if tw_text and "未在台灣" not in tw_text:
                st.markdown("### 🇹🇼 台灣法規")
                st.info(tw_text)

            if food_text:
                st.markdown("### 🥗 食用安全史")
                st.success(_md_linebreaks(food_text))

            st.markdown("### 毒理分析")
            st.markdown(synthesis)

            if web["sources"]:
                st.markdown("**參考來源：**")
                for url in web["sources"][:5]:
                    st.markdown(f"- {url}")
