"""使用 Claude API 分析原料文件並萃取成分資訊"""
import base64
import json
import re
import os
import time
import anthropic
import fitz  # PyMuPDF


def get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    return anthropic.Anthropic(api_key=api_key)


# 應維持原大寫的 INCI 縮寫 / 代碼（可依需要擴充）
_INCI_KEEP_UPPER = {
    "CI", "PEG", "PPG", "PVP", "PVM", "VP", "VA", "EDTA", "DEA", "TEA", "MEA",
    "PCA", "AMP", "SD", "BHT", "BHA", "TBHQ", "MIT", "CIT", "SLS", "SLES",
    "PTFE", "DMDM", "HC", "AMPD", "PABA",
}


def _cap_token(token: str) -> str:
    """處理單一 token（已用空白或連字號切開）。"""
    if not token:
        return token
    # 縮寫維持大寫
    if token.upper() in _INCI_KEEP_UPPER:
        return token.upper()
    # 含數字的代碼（PEG-40 的 40、C12-15、CI 號碼、1,2- 等）維持原樣
    if any(ch.isdigit() for ch in token):
        return token
    # 一般單字：首字母大寫，其餘小寫
    return token[:1].upper() + token[1:].lower()


def _titlecase_inci(name: str) -> str:
    """
    將 INCI 名稱轉為每個字首字母大寫，並保留特殊縮寫 / 代碼的原大寫。
    例：'SODIUM CHLORIDE' -> 'Sodium Chloride'；'PEG-40 HYDROGENATED CASTOR OIL'
        -> 'PEG-40 Hydrogenated Castor Oil'；'CI 77891' -> 'CI 77891'。
    """
    if not isinstance(name, str) or not name.strip():
        return name
    # 以空白與連字號為分隔（保留分隔符），逐 token 處理後接回
    parts = re.split(r"([ \-])", name.strip())
    return "".join(p if p in (" ", "-") else _cap_token(p) for p in parts)


def _create_with_retry(client: anthropic.Anthropic, **kwargs):
    """呼叫 messages.create，遇到 529 Overloaded 時自動退避重試最多 5 次。"""
    waits = [5, 15, 30, 60, 120]
    for attempt in range(5):
        try:
            return client.messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt < 4:
                time.sleep(waits[attempt])
                continue
            raise


_ANALYSIS_SYSTEM = """你是專業化妝品配方分析師，專長在從原料規格書中萃取成分資訊。
你的任務是分析文件內容，判斷原料是否為複方，並提取各成分的 INCI 名稱、CAS Number 及組成比例。
回應必須為有效的 JSON，不含任何其他文字。"""

_TOX_SYSTEM = """你是化妝品法規與毒理學專家。
請根據提供的官方文件內容整理原料的毒理安全資訊。

嚴格規則：
1. 若有提供 SCCS 或 CIR 文件全文，優先直接引用文件中的具體數值（NOAEL、安全使用濃度、MOS 等），並標明「[來源：SCCS/CIR 文件]」。
2. 當文件有明確評估結論或數字時，必須直接使用，不得以訓練記憶取代或推算。
3. 僅當提供的資料確實未涵蓋某項資訊時，才可補充訓練知識，但必須標註「[基於訓練資料，建議查閱最新官方文件確認]」。
4. 若提供的是搜尋摘要（非文件全文），請標注「[資料來源為搜尋摘要，建議直接查閱原始報告]」。
5. 若文件有明確評估年份，請記錄以供時效性判斷。
6. 若有 EU CosIng 資料，明確標示成分在 EU Cosmetics Regulation 各 Annex 中的狀態：Annex II（禁用）、III（限量使用）、IV（色素）、V（防腐劑）、VI（防曬劑）。查無限制則標明「EU CosIng 查無 Annex 限制，可自由使用，但仍需安全評估佐證」。來源標注「[來源：EU CosIng]」。
7. NOAEL 常有多個（不同研究、終點、給藥途徑）。請選擇 SCCS/CIR 實際用於計算 MoS 的那一個「關鍵 NOAEL」——通常來自關鍵毒性研究、以 mg/kg bw/day 表示的全身性 NOAEL，切勿取文件中第一個出現的數值。

以繁體中文輸出，格式清晰。"""


def analyze_ingredient(files: list[dict]) -> dict:
    """
    分析一份或多份原料相關文件，判斷是否複方並萃取成分清單。
    files: [{"filename": str, "text": str}, ...]
    回傳 dict：{product_name, supplier, is_compound, file_types, components: [...]}
    """
    client = get_client()

    # 每份文件依比例分配 token 空間，總上限 16000 字元
    per_file_limit = max(2000, 16000 // len(files))
    sections = []
    for i, f in enumerate(files, 1):
        truncated = f["text"][:per_file_limit]
        sections.append(f"=== 檔案 {i}：{f['filename']} ===\n{truncated}")
    merged = "\n\n".join(sections)

    filenames_str = "、".join(f["filename"] for f in files)
    first_filename = files[0]["filename"] if files else "unknown"

    prompt = f"""請分析以下 {len(files)} 份原料相關文件，萃取成分資訊。

文件列表：{filenames_str}

文件內容：
{merged}

請以 JSON 格式回傳：
{{
  "product_name": "原料商品名稱（英文或中文）",
  "supplier": "供應商名稱（如有）",
  "is_compound": true 或 false,
  "file_types": {{"檔案名稱（含副檔名）": "COA 或 SDS 或 TDS 或 成分組成表 或 規格書 或 其他"}},
  "components": [
    {{
      "inci_name": "INCI名稱（必須是正式英文INCI命名）",
      "cas_number": "CAS Number，格式 XXXXX-XX-X，不確定填 null",
      "percentage": 數字或 null（w/w %，文件明確記載精確值時填入；若只有範圍則填 null）,
      "percentage_range": "若文件只記載範圍（如 5-10%），填 '最小值-最大值' 字串（例：'5-10'）；否則填 null",
      "confidence": "high/medium/low",
      "note": "備註（如推算方式、不確定點）"
    }}
  ],
  "raw_composition_text": "文件中關於組成的原始描述片段"
}}

規則：
- file_types 鍵為完整檔案名稱（含副檔名），值判斷類型：COA（品質分析報告）、SDS（安全資料表）、TDS（技術資料表）、成分組成表、規格書、其他
- 若有多份文件，整合所有資訊，以 COA 或成分組成表的比例數據為優先
- 如果是單一原料，components 只有一項，percentage 填 100
- 如果是複方，盡量列出所有細項成分
- INCI 名稱必須是正式命名（參考 INCI Dictionary）
- percentage 總和應接近 100（若有資料），不確定的成分填 null
- 若文件只提供範圍（如 5-10%），percentage 填 null，percentage_range 填 "5-10"（不含 % 符號）
- confidence 評估依據：文件明確記載→high，推算或估計→medium，來自知識庫推測→low"""

    response = _create_with_retry(
        client,
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=[{"type": "text", "text": _ANALYSIS_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()

    # 提取 JSON（有時模型會加上 ```json ... ```）
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            for comp in data.get("components", []):
                if isinstance(comp, dict) and comp.get("inci_name"):
                    comp["inci_name"] = _titlecase_inci(comp["inci_name"])
            return data
        except json.JSONDecodeError:
            pass

    return {
        "product_name": first_filename,
        "supplier": "",
        "is_compound": False,
        "file_types": {},
        "components": [],
        "raw_response": raw,
        "error": "JSON 解析失敗，請查看 raw_response",
    }


def synthesize_toxicology(inci_name: str, cas_number: str,
                           tw_regulation_text: str,
                           sccs_raw: str, cir_raw: str,
                           tox_doc_text: str = "",
                           cosing_raw: str = "") -> str:
    """
    整合台灣法規 + 上傳毒理文件 + SCCS + CIR 資料，產生毒理安全摘要。
    """
    client = get_client()

    context_parts = []
    if tw_regulation_text:
        context_parts.append(f"【台灣衛福部法規】\n{tw_regulation_text}")
    if tox_doc_text:
        context_parts.append(f"【上傳毒理文件（優先參考）】\n{tox_doc_text}")
    if sccs_raw:
        label = "【SCCS PDF 全文摘要】" if len(sccs_raw) > 2000 else "【SCCS 搜尋摘要】"
        context_parts.append(f"{label}\n{sccs_raw[:12000]}")
    if cir_raw:
        label = "【CIR PDF 全文摘要】" if len(cir_raw) > 2000 else "【CIR 搜尋摘要】"
        context_parts.append(f"{label}\n{cir_raw[:10000]}")
    if cosing_raw:
        context_parts.append(f"【EU CosIng 化妝品成分資料庫】\n{cosing_raw[:3000]}")

    context = "\n\n".join(context_parts) if context_parts else "（無外部搜尋資料）"

    prompt = f"""請整理以下原料的安全資訊：

INCI 名稱：{inci_name}
CAS Number：{cas_number or '未知'}

參考資料：
{context}

請以以下格式輸出（繁體中文）：

**台灣法規限制：**
[從台灣法規資料提取，如無則標示「不在限制名單」]

**EU CosIng 法規狀態：**
[若有 CosIng 資料，標示 Annex 狀態；若查無限制，填「EU Cosmetics Regulation 查無 Annex 限制，可自由使用，但仍需安全評估佐證」；若無 CosIng 資料則填「無 CosIng 資料」]

**NOAEL / 安全濃度：**
[從 SCCS/CIR 資料提取，如無則根據訓練知識說明，需標明「基於訓練資料」]
關鍵NOAEL（用於MoS）：[僅填數值與單位，例：100 mg/kg bw/day；並註明來源關鍵研究或文件。若文獻未提供可用於 MoS 的明確 NOAEL，此行填「無」]

**SCCS 評估結論：**
[如有 SCCS Opinion，必須引用第 4 節（Conclusion）的原文判定；若提供文件未含 Conclusion 章節，請明確說明並標注文件截斷位置]

**CIR 評估結論：**
[如有 CIR Final Report，摘要其結論]

**主要安全關切：**
[重要毒理關切點，無則填「無特殊關切」]

**資料來源：**
[列出具體來源文件或網址]"""

    response = _create_with_retry(
        client,
        model="claude-sonnet-4-6",
        max_tokens=3000,
        system=[{"type": "text", "text": _TOX_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


_IFRA_SYSTEM = """你是化妝品法規專家，專長解析 IFRA（International Fragrance Association）標準文件。
請從文件中找出 Category 5（含 5A/5B/5C/5D）、6、7（含 7A/7B）、8、9 的最高用量限制（maximum usage level / maximum concentration）。
回應必須為有效的 JSON，不含任何其他文字，格式如下：
{
  "fragrance_name": "香精名稱或文件中的產品名稱，找不到則填空字串",
  "categories": {
    "5A": "0.50%",
    "5B": "1.00%",
    ...
  }
}
注意：
- categories 的 key 只能是 "5A"、"5B"、"5C"、"5D"、"6"、"7A"、"7B"、"8"、"9"
- 若文件中某 category 沒有列出，不要加入該 key
- 若某 category 限制為「禁止使用」或 0，填 "0%"
- limit 值直接取文件原始數值（如 "0.50%"、"1.00%"），不要換算
- PDF 文字擷取後表格格式可能混亂（欄位錯位、換行不規則），請依 category 編號（如 5A、5 A、Cat.5A 等）搜尋對應的百分比數字
- 表格可能以行排列（每行一個 category）或以欄排列（category 在首行，百分比在下行）
- 遇到類似 "5A ... 0.50" 或 "0.50 ... 5A" 的片段，都應提取為 "5A": "0.50%"
- 如果在文件中確實找不到任何 Category 5–9 的數值，categories 回傳空物件 {}"""


def parse_ifra_document(pdf_text: str) -> dict:
    """
    解析 IFRA 標準文件 PDF 文字，提取 Category 5–9 的最高用量限制。
    回傳 {"fragrance_name": str, "categories": {cat: limit_str, ...}}
    失敗回傳 {"error": str, "raw": str}
    """
    client = get_client()
    prompt = f"以下是 IFRA 標準文件的文字內容，請依指示提取 Category 5–9 用量限制：\n\n{pdf_text[:12000]}"
    try:
        response = _create_with_retry(
            client,
            model="claude-sonnet-4-6",
            max_tokens=1000,
            temperature=0,
            system=_IFRA_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {"error": f"JSON 解析失敗：{e}", "raw": raw if "raw" in dir() else ""}
    except Exception as e:
        return {"error": str(e), "raw": ""}


def parse_ifra_document_vision(pdf_bytes: bytes) -> dict:
    """
    使用 Claude vision 解析圖片型 IFRA PDF（掃描版或無文字層的 PDF）。
    將每頁渲染為 PNG 圖片後送給 Claude 視覺模式解析。
    回傳格式同 parse_ifra_document。
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    content: list[dict] = []
    for page in doc:
        pix = page.get_pixmap(dpi=150)
        img_b64 = base64.standard_b64encode(pix.tobytes("png")).decode()
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
        })
    if not content:
        return {"error": "PDF 無法渲染為圖片", "raw": ""}
    content.append({
        "type": "text",
        "text": "以上是 IFRA 標準文件的頁面截圖，請依指示提取 Category 5–9 用量限制，回傳 JSON。",
    })
    client = get_client()
    raw = ""
    try:
        response = _create_with_retry(
            client,
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=_IFRA_SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {"error": f"JSON 解析失敗：{e}", "raw": raw}
    except Exception as e:
        return {"error": str(e), "raw": ""}


def parse_ifra_pdf(pdf_bytes: bytes) -> dict:
    """
    自動偵測 PDF 類型（文字型 / 圖片型）並解析 IFRA 用量資料。
    文字量 >= 200 字元 → 文字模式；否則 → vision 模式。
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    if len(text.strip()) >= 200:
        return parse_ifra_document(text)
    return parse_ifra_document_vision(pdf_bytes)
