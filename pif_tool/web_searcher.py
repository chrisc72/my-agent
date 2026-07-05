"""上網搜尋 SCCS / CIR 毒理資料"""
import re
import time
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
TIMEOUT = 15
PDF_TIMEOUT = 20
PDF_MAX_SIZE = 5_242_880   # 5 MB
PDF_MAX_CHARS = 15_000
PER_SOURCE_BUDGET = 22.0   # 單一來源(SCCS/CIR/CosIng)的最長查詢秒數，避免慢主機拖垮整體


def _expired(deadline: float | None) -> bool:
    """是否已超過時間預算。"""
    return deadline is not None and time.monotonic() > deadline

# 含關鍵毒理數值的頁面關鍵字（找不到結論章節時的備援排序依據）
_NUMERIC_KEYWORDS = [
    "noael", "loael", "margin of safety", "systemic exposure",
    " mos ", "mos=", "mos ", "sed ",
]

# 結論章節標題（SCCS 意見書、CIR 報告）— 行首錨定，允許章節編號前綴
_CONCLUSION_HEADINGS = [
    r"(?im)^\s*(?:[\d.]+\s*)?conclusion of the sccs",
    r"(?im)^\s*(?:[\d.]+\s*)?overall\s+conclusion",
    r"(?im)^\s*(?:[\d.]+\s*)?opinion of the sccs",
    r"(?im)^\s*(?:[\d.]+\s*)?conclusions?\b",
    r"(?im)^\s*(?:[\d.]+\s*)?discussion\b",
]

# 結論之後的章節標題（作為結論段落的結束界線）
_POST_CONCLUSION_HEADINGS = [
    r"(?im)^\s*(?:[\d.]+\s*)?minority\s+opinion",
    r"(?im)^\s*(?:[\d.]+\s*)?references\b",
    r"(?im)^\s*(?:[\d.]+\s*)?bibliography\b",
    r"(?im)^\s*(?:[\d.]+\s*)?list of abbreviations",
    r"(?im)^\s*(?:[\d.]+\s*)?annex(?:es)?\b",
]


# ── PDF 工具函式 ────────────────────────────────────────────────────────────


def _find_conclusion_span(text: str) -> tuple[int, int] | None:
    """定位 SCCS/CIR 的結論章節，回傳 (起始, 結束) 字元位置；找不到回傳 None。"""
    low = text.lower()
    n = len(low)
    start = None
    for pat in _CONCLUSION_HEADINGS:
        # 只採用文件 25% 之後的匹配，避開目錄與前言中的提及；取最後一個
        hits = [m.start() for m in re.finditer(pat, low) if m.start() > n * 0.25]
        if hits:
            start = hits[-1]
            break
    if start is None:
        return None
    end = n
    for pat in _POST_CONCLUSION_HEADINGS:
        m = re.search(pat, low[start + 20:])
        if m:
            end = min(end, start + 20 + m.start())
    return start, end


def extract_text_from_pdf_bytes(pdf_bytes: bytes, max_chars: int = PDF_MAX_CHARS) -> str:
    """用 PyMuPDF 從 bytes 抽取 PDF 文字。

    超過字數上限時，優先完整保留 Conclusion 章節（SCCS/CIR 判定的核心），
    再往前補入含 NOAEL / MoS 的評估內文與文件開頭，避免結論落入被省略的中段。
    """
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        all_pages = [page.get_text() for page in doc]
        full_text = "\n".join(all_pages)

        if len(full_text) <= max_chars:
            return full_text

        # 優先策略：鎖定結論章節並完整保留
        span = _find_conclusion_span(full_text)
        if span:
            c_start, c_end = span
            concl = full_text[c_start:c_end].strip()[:7000]

            remaining = max_chars - len(concl) - 200  # 預留標題與分隔字元
            pre_budget = max(0, int(remaining * 0.6))
            pre = full_text[max(0, c_start - pre_budget):c_start].strip()
            front_budget = max(0, remaining - len(pre))
            front = full_text[:front_budget].strip()

            parts = ["【評估結論（Conclusion 章節，優先擷取）】\n" + concl]
            if pre:
                parts.append("【安全評估內文（含 NOAEL / MoS）】\n" + pre)
            if front:
                parts.append("【文件開頭（成分辨識與委任事項）】\n" + front)
            return "\n\n".join(parts)[:max_chars]

        # 備援一：找不到結論章節時，優先保留含 NOAEL / MoS 的頁面（多在後段）
        num_pages = [
            p for p in all_pages
            if any(kw in p.lower() for kw in _NUMERIC_KEYWORDS)
        ]
        if num_pages:
            candidate = "\n---\n".join(num_pages)
            if len(candidate) <= max_chars:
                return candidate
            return candidate[-max_chars:]

        # 備援二：前 1/3 + 後 2/3（結論通常在後段）
        front = max_chars // 3
        rear = max_chars - front
        return full_text[:front] + "\n...[中段省略]...\n" + full_text[-rear:]
    except Exception:
        return ""


def download_pdf_if_small(url: str) -> bytes | None:
    """串流下載 PDF，超過大小限制或非 PDF 類型則回傳 None。"""
    try:
        resp = requests.get(
            url,
            headers={**HEADERS, "Accept": "application/pdf,*/*"},
            timeout=PDF_TIMEOUT,
            stream=True,
        )
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        url_lower = url.lower().split("?")[0]
        if "pdf" not in content_type.lower() and not url_lower.endswith(".pdf"):
            return None
        chunks, total = [], 0
        for chunk in resp.iter_content(chunk_size=65_536):
            chunks.append(chunk)
            total += len(chunk)
            if total > PDF_MAX_SIZE:
                return None
        return b"".join(chunks)
    except Exception:
        return None


# ── 學術期刊 DOI 輔助函式 ────────────────────────────────────────────────────


def _extract_doi(url: str) -> str:
    """從 URL 中提取 DOI（格式 10.XXXX/...）。"""
    m = re.search(r"10\.\d{4,}/[^\s\"'>]+", url)
    return m.group(0).rstrip(".,)") if m else ""


def _try_unpaywall(doi: str) -> bytes | None:
    """查 Unpaywall API，若有開放授權 PDF 則下載並回傳 bytes。"""
    try:
        resp = requests.get(
            f"https://api.unpaywall.org/v2/{doi}?email=pif-tool@cirdoi.query",
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        data = resp.json()
        if not data.get("is_oa"):
            return None
        loc = data.get("best_oa_location") or {}
        pdf_url = loc.get("url_for_pdf") or loc.get("url")
        if not pdf_url:
            return None
        time.sleep(1)
        return download_pdf_if_small(pdf_url)
    except Exception:
        return None


def _fetch_pubmed_abstract(doi: str) -> tuple[str, str]:
    """用 PubMed E-utilities（免費，不需帳號）透過 DOI 取得摘要全文。
    回傳 (abstract_text, pubmed_url)，找不到則回傳 ("", "")。
    """
    try:
        search_resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={"db": "pubmed", "term": f"{doi}[doi]", "retmode": "json"},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        ids = search_resp.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return "", ""
        pmid = ids[0]
        fetch_resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "text"},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        text = fetch_resp.text.strip()
        return text, f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    except Exception:
        return "", ""


_SALT_PREFIXES = {
    "dipotassium", "disodium", "trisodium", "tetrasodium",
    "ammonium", "potassium", "sodium", "calcium", "zinc", "magnesium",
    "bis", "tri", "di",
}


def _search_pubmed_by_name(inci_name: str) -> tuple[str, str]:
    """按成分名稱搜尋 PubMed CIR 相關評估報告（不需 DOI）。
    嘗試順序：完整名稱 → 去掉鹽型前綴 → 最長的單詞（化學主體）。
    """
    words = inci_name.strip().split()
    names_to_try: list[str] = [inci_name]

    # 去掉鹽型前綴（如 Dipotassium, Sodium…）取化學主體部分
    if len(words) >= 2 and words[0].lower() in _SALT_PREFIXES:
        names_to_try.append(" ".join(words[1:]))

    # 取最長的單詞（通常是主要化學名稱的根字）
    if words:
        longest = max(words, key=len)
        if longest.lower() not in {n.lower() for n in names_to_try}:
            names_to_try.append(longest)

    for name in names_to_try:
        try:
            query = f'"{name}"[tiab] AND ("Cosmetic Ingredient Review"[All] OR "CIR"[tiab])'
            search_resp = requests.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={"db": "pubmed", "term": query, "retmode": "json", "sort": "relevance"},
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            ids = search_resp.json().get("esearchresult", {}).get("idlist", [])
            if not ids:
                continue
            pmid = ids[0]
            fetch_resp = requests.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params={"db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "text"},
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            text = fetch_resp.text.strip()
            if len(text) > 100:
                return text, f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        except Exception:
            continue

    return "", ""


# ── 直接爬取官方網站 ────────────────────────────────────────────────────────


_JOURNAL_DOMAINS = ("sagepub", "tandfonline", "doi.org", "journals.lww", "ncbi.nlm.nih.gov/pmc")


def search_cir_direct(inci_name: str, cas_number: str = "") -> tuple[str, list[str]]:
    """用 DDG 搜尋 cir-safety.org 的 PDF 並直接下載。
    搜尋優先順序：
      1. cir-safety.org 上的 PDF（新版報告，2015 後）
      2. 學術期刊 DOI → Unpaywall OA PDF → PubMed 摘要（舊版報告）
    CIR 網站為 PowerApps JS 渲染，無法直接爬取，改走 DDG 找 PDF URL。
    """
    queries = [
        f'"{inci_name}" safety assessment site:cir-safety.org',
        f'CIR "{inci_name}" "safety assessment" cosmetics filetype:pdf',
    ]
    if cas_number:
        queries.append(f'CIR cosmetics "{cas_number}" safety assessment')

    pdf_urls: list[str] = []
    journal_urls: list[str] = []
    deadline = time.monotonic() + PER_SOURCE_BUDGET

    try:
        from ddgs import DDGS
        for query in queries:
            if _expired(deadline):
                break
            try:
                with DDGS() as ddgs:
                    results = list(ddgs.text(query, max_results=6, region="wt-wt"))
                for r in results:
                    href = r.get("href", "")
                    if "cir-safety.org" in href and ".pdf" in href.lower():
                        pdf_urls.append(href)
                    elif any(d in href for d in _JOURNAL_DOMAINS):
                        doi = _extract_doi(href)
                        if doi and href not in journal_urls:
                            journal_urls.append(href)
                if pdf_urls:
                    break
            except Exception:
                continue

        # 優先：cir-safety.org PDF
        for pdf_url in pdf_urls[:3]:
            if _expired(deadline):
                break
            time.sleep(1)
            pdf_bytes = download_pdf_if_small(pdf_url)
            if pdf_bytes:
                text = extract_text_from_pdf_bytes(pdf_bytes)
                if len(text.strip()) > 200:
                    return text, [pdf_url]

        # 備援：學術期刊 DOI → Unpaywall OA PDF → PubMed 摘要
        for journal_url in journal_urls[:2]:
            if _expired(deadline):
                break
            doi = _extract_doi(journal_url)
            if not doi:
                continue
            # 嘗試 Unpaywall 開放授權 PDF
            pdf_bytes = _try_unpaywall(doi)
            if pdf_bytes:
                text = extract_text_from_pdf_bytes(pdf_bytes)
                if len(text.strip()) > 200:
                    return text, [journal_url]
            # 嘗試 PubMed 摘要（免費，不需帳號）
            time.sleep(1)
            abstract, pmid_url = _fetch_pubmed_abstract(doi)
            if len(abstract.strip()) > 100:
                tagged = f"[來源：PubMed 摘要，非報告全文，建議查閱原始報告]\n{abstract}"
                return tagged, [pmid_url or journal_url]

        # 最終備援：DDG 未找到期刊 URL 時，直接用成分名稱搜 PubMed（不需 DOI）
        if not _expired(deadline):
            time.sleep(1)
            abstract, pmid_url = _search_pubmed_by_name(inci_name)
            if len(abstract.strip()) > 100:
                tagged = f"[來源：PubMed 摘要，非報告全文，建議查閱原始報告]\n{abstract}"
                return tagged, [pmid_url]
    except Exception:
        pass

    return "", []


def search_sccs_direct(inci_name: str, cas_number: str = "") -> tuple[str, list[str]]:
    """在 SCCS Europa.eu 搜尋意見書 PDF（DDG 精確搜尋 + 直接下載）。"""
    queries = [
        f'site:health.europa.eu SCCS "{inci_name}"',
        f'SCCS opinion "{inci_name}" site:europa.eu',
    ]
    if cas_number:
        queries.append(f'SCCS cosmetics "{cas_number}" opinion europa.eu')

    pdf_urls: list[str] = []
    page_urls: list[str] = []
    deadline = time.monotonic() + PER_SOURCE_BUDGET

    try:
        from ddgs import DDGS
        for query in queries:
            if _expired(deadline):
                break
            try:
                with DDGS() as ddgs:
                    results = list(ddgs.text(query, max_results=6, region="wt-wt"))
                for r in results:
                    href = r.get("href", "")
                    if "europa.eu" not in href:
                        continue
                    if ".pdf" in href.lower():
                        pdf_urls.append(href)
                    else:
                        page_urls.append(href)
                if pdf_urls:
                    break
            except Exception:
                continue

        for pdf_url in pdf_urls[:3]:
            if _expired(deadline):
                break
            time.sleep(1)
            pdf_bytes = download_pdf_if_small(pdf_url)
            if pdf_bytes:
                text = extract_text_from_pdf_bytes(pdf_bytes)
                if len(text.strip()) > 200:
                    return text, [pdf_url]

        for page_url in page_urls[:2]:
            if _expired(deadline):
                break
            try:
                resp = requests.get(page_url, headers=HEADERS, timeout=TIMEOUT)
                psoup = BeautifulSoup(resp.text, "html.parser")
                for a in psoup.find_all("a", href=True):
                    href = a["href"]
                    if ".pdf" not in href.lower():
                        continue
                    full_pdf = (
                        href if href.startswith("http")
                        else "https://health.europa.eu" + href
                    )
                    time.sleep(1)
                    pdf_bytes = download_pdf_if_small(full_pdf)
                    if pdf_bytes:
                        text = extract_text_from_pdf_bytes(pdf_bytes)
                        if len(text.strip()) > 200:
                            return text, [full_pdf]
            except Exception:
                continue
    except Exception:
        pass

    return "", []


# ── DuckDuckGo Fallback ─────────────────────────────────────────────────────

_TRUSTED_TOX_DOMAINS = (
    "cir-safety.org", "europa.eu", "pubmed.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov", "sagepub", "tandfonline", "journals.lww",
    "sciencedirect.com", "fda.gov", "echa.europa.eu", "who.int",
)


def _is_trusted_tox_url(url: str) -> bool:
    return any(d in url for d in _TRUSTED_TOX_DOMAINS)


def search_sccs(inci_name: str, cas_number: str = "") -> tuple[str, list[str]]:
    """DuckDuckGo fallback：搜尋 SCCS 資料，回傳 (摘要文字, 來源URL清單)。"""
    query = f"SCCS safety opinion cosmetics \"{inci_name}\""
    if cas_number:
        query += f" OR \"{cas_number}\""
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5, region="wt-wt"))

        ec_results = [r for r in results if "europa.eu" in r.get("href", "")]
        # 補充可信賴 domain 的結果；排除非毒理學相關網站（如 Baidu）
        other_trusted = [
            r for r in results
            if r not in ec_results and _is_trusted_tox_url(r.get("href", ""))
        ][:2]
        all_results = ec_results + other_trusted

        if not all_results:
            return "", []

        snippets, urls = [], []
        for r in all_results[:4]:
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            if body:
                snippets.append(f"[{title}]\n{body}\nURL: {href}")
                if href:
                    urls.append(href)

        return "\n\n".join(snippets), urls
    except Exception as e:
        return f"SCCS 搜尋失敗：{e}", []


def search_cir(inci_name: str, cas_number: str = "") -> tuple[str, list[str]]:
    """DuckDuckGo fallback：搜尋 CIR 資料庫，回傳 (摘要文字, 來源URL清單)。"""
    query = f"CIR cosmetic ingredient review safety \"{inci_name}\""
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5, region="wt-wt"))

        cir_results = [r for r in results if "cir-safety.org" in r.get("href", "")]
        # 補充可信賴期刊 domain；排除 Baidu 等非相關網站
        other_trusted = [
            r for r in results
            if r not in cir_results and _is_trusted_tox_url(r.get("href", ""))
        ][:2]
        all_results = cir_results + other_trusted

        if not all_results:
            return "", []

        snippets, urls = [], []
        for r in all_results[:4]:
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            if body:
                snippets.append(f"[{title}]\n{body}\nURL: {href}")
                if href:
                    urls.append(href)

        return "\n\n".join(snippets), urls
    except Exception as e:
        return f"CIR 搜尋失敗：{e}", []


# ── EU CosIng 搜尋（官方現行 EU Search API）──────────────────────────────────

# 舊 REST API（growth/tools-databases/cosing/ref/api）已改為 Angular SPA，
# 原路徑現只回傳 HTML。改用該 SPA 前端實際呼叫的 EU Search API（POST）。
# apiKey 取自 CosIng 網站前端公開設定（assets/env-json-config.json），非私密憑證。
COSING_SEARCH_URL = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
COSING_API_KEY = "285a77fd-1257-4271-8507-f0c6b2961203"

_ANNEX_LABELS = {
    "II": "Annex II（禁用物質）",
    "III": "Annex III（限用物質）",
    "IV": "Annex IV（准用色素）",
    "V": "Annex V（准用防腐劑）",
    "VI": "Annex VI（准用防曬劑）",
}


def _decode_cosing_restriction(codes: list) -> str:
    """將 cosmeticRestriction（如 'V/29'、'III/98\r\nV/3'）解碼為可讀的 Annex 標示。"""
    out = []
    for raw in codes:
        for code in re.split(r"\s+", str(raw).strip()):   # 一格可能塞多個、以換行分隔
            if not code:
                continue
            annex, _, ref = code.partition("/")
            label = _ANNEX_LABELS.get(annex.upper(), f"Annex {annex}")
            out.append(label + (f" Ref {ref}" if ref else ""))
    return "；".join(out)


def _cosing_first(md: dict, key: str) -> str:
    v = md.get(key) or []
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v)


def _cosing_clean(values) -> list[str]:
    """濾掉空值與佔位符（<empty>、-），並去重。"""
    seen: list[str] = []
    for v in values or []:
        s = str(v).strip()
        if s and s.lower() != "<empty>" and s != "-" and s not in seen:
            seen.append(s)
    return seen


def _cosing_name_variants(md: dict) -> set[str]:
    """成分所有可比對名稱（含斜線組合名拆解），小寫。"""
    out: set[str] = set()
    for key in ("inciName", "nameOfCommonIngredientsGlossary", "chemicalName"):
        for n in (md.get(key) or []):
            n = str(n).strip()
            if not n:
                continue
            out.add(n.lower())
            for part in re.split(r"[/\r\n]", n):
                if part.strip():
                    out.add(part.strip().lower())
    return out


def search_cosing_direct(inci_name: str, cas_number: str = "") -> tuple[str, list[str]]:
    """
    查詢 EU CosIng 資料庫的成分法規狀態（Annex、限量、功能）。
    使用 CosIng 官網前端所用的 EU Search API（POST）。
    回傳 (text, [url])，查無資料回傳 ("", [])。
    """
    try:
        # pageSize 取較大值：母體化合物有時排在其衍生物之後（如 Salicylic Acid）
        resp = requests.post(
            COSING_SEARCH_URL,
            params={"apiKey": COSING_API_KEY, "text": inci_name, "pageSize": 30},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return "", []
        results = resp.json().get("results", [])
    except Exception:
        return "", []

    inci_l = inci_name.lower().strip()
    cas_c = cas_number.strip()

    # 取第一筆名稱或 CAS 精確相符的 CosIng 條目，避免抓到衍生物或化學品庫雜訊
    md = None
    for res in results:
        if res.get("database") != "GROWTH_COSING":
            continue
        m = res.get("metadata", {})
        cas_list = [str(c).strip() for c in (m.get("casNo") or [])]
        if inci_l in _cosing_name_variants(m) or (cas_c and cas_c in cas_list):
            md = m
            break
    if md is None:
        return "", []

    inci = _cosing_first(md, "inciName") or _cosing_first(md, "nameOfCommonIngredientsGlossary")
    cas = next(iter(_cosing_clean(md.get("casNo"))), "—")
    fn = "、".join(_cosing_clean(md.get("functionName"))) or "無"
    status = _cosing_first(md, "status")
    restr_codes = _cosing_clean(md.get("cosmeticRestriction"))
    annex_str = _decode_cosing_restriction(restr_codes) if restr_codes else "查無 Annex 限制"
    maxc = "、".join(_cosing_clean(md.get("maximumConcentration")))
    cond = "；".join(_cosing_clean(md.get("wordingOfConditions")))
    other = "；".join(_cosing_clean(md.get("otherRestrictions")))

    block = (f"INCI：{inci}\nCAS：{cas}\n功能：{fn}\n狀態：{status}\n"
             f"Annex 限制：{annex_str}")
    if maxc:
        block += f"\n最大濃度：{maxc}"
    if cond:
        block += f"\n使用條件：{cond}"
    if other:
        block += f"\n其他限制：{other}"

    text = "【EU CosIng（官方資料庫）】\n" + block
    url = f"https://ec.europa.eu/growth/tools-databases/cosing/?searchType=simple&text={inci_name}"
    return text, [url]


# ── 備援搜尋：Europe PMC（不依賴 DuckDuckGo）─────────────────────────────────

EUROPEPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


# 毒理／化妝品安全訊號字（供 Europe PMC 結果相關性計分）
_PMC_SAFETY_SIGNALS = [
    "safety assessment", "toxicolog", "toxicity", "noael",
    "risk assessment", "cosmetic", "sccs", "cir",
]


def _pmc_relevance_score(title: str, abstract: str, name_tokens: list[str]) -> int:
    """
    依標題/摘要計算與「該成分安全評估」的相關性分數。
    分數越高越像 CIR/SCCS 型評估報告；0 分代表僅順口提及、應淘汰。
    """
    tl = title.lower()
    ab = abstract.lower()
    name_in_title = all(tok in tl for tok in name_tokens) if name_tokens else False
    name_in_abstract = all(tok in ab for tok in name_tokens) if name_tokens else False
    title_has_safety_assess = "safety assessment" in tl
    title_has_signal = any(s in tl for s in _PMC_SAFETY_SIGNALS)
    abstract_has_signal = any(s in ab for s in _PMC_SAFETY_SIGNALS)

    if name_in_title and title_has_safety_assess:
        return 4  # 標題即「Safety Assessment of <成分>」——最典型的 CIR/SCCS 報告
    if name_in_title and title_has_signal:
        return 3  # 標題含成分名＋毒理/化妝品訊號
    if name_in_title and abstract_has_signal:
        return 2  # 標題點名成分、摘要談安全
    if name_in_abstract and abstract_has_signal:
        return 1  # 僅摘要提及成分＋安全訊號（弱相關）
    return 0      # 僅順口提及成分、無安全脈絡 → 淘汰


def search_europepmc(inci_name: str, cas_number: str = "",
                     max_results: int = 4) -> tuple[str, list[str]]:
    """
    不依賴搜尋引擎的備援：查 Europe PMC（EBI 官方 REST API，免金鑰、穩定）。
    CIR 安全評估報告多發表於期刊、被 Europe PMC / PubMed 收錄，故可作為
    DuckDuckGo 全數落空時的毒理文獻來源。回傳 (摘要文字, 來源URL清單)。

    查詢策略：成分名限定 TITLE/ABSTRACT 欄位（論文須「以此成分為主題」而非
    順口提及），毒理 OR 群組偏向化妝品安全文獻，維持預設相關性排序。抓較多候選
    後再依標題/摘要計分過濾，只保留與該成分安全評估真正相關者。
    """
    name = inci_name.strip()
    if not name:
        return "", []

    # 成分名限定標題/摘要欄位；有 CAS 時加為 OR 備選比對
    name_terms = [f'"{name}"']
    cas = cas_number.strip()
    if cas:
        name_terms.append(f'"{cas}"')
    name_clause = " OR ".join(
        f"(TITLE:{t} OR ABSTRACT:{t})" for t in name_terms
    )
    safety_clause = ('("safety assessment" OR toxicology OR toxicity OR NOAEL '
                     'OR "risk assessment" OR cosmetic OR SCCS OR CIR)')
    query = f"({name_clause}) AND {safety_clause}"

    try:
        resp = requests.get(
            EUROPEPMC_URL,
            params={"query": query, "format": "json",
                    "pageSize": 15, "resultType": "core"},  # 多抓候選供過濾；預設相關性排序
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("resultList", {}).get("result", [])
    except Exception:
        return "", []

    if not results:
        return "", []

    # 以成分名（去標點、小寫）逐字比對，避免離題論文冒充安全資料
    name_tokens = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if len(w) > 2]

    scored: list[tuple[int, str, list[str]]] = []
    for x in results:
        title = (x.get("title") or "").strip()
        abstract = re.sub(r"<[^>]+>", "", (x.get("abstractText") or "").strip())
        score = _pmc_relevance_score(title, abstract, name_tokens)
        if score <= 0:
            continue  # 誠實把關：僅順口提及成分者一律淘汰

        year = x.get("pubYear", "")
        doi = x.get("doi", "")
        source = x.get("source", "")
        pid = x.get("id", "")
        if doi:
            url = f"https://doi.org/{doi}"
        elif source and pid:
            url = f"https://europepmc.org/article/{source}/{pid}"
        else:
            url = ""
        block = f"[{year}] {title}"
        if abstract:
            block += f"\n摘要：{abstract[:1500]}"
        block_urls: list[str] = []
        if url:
            block += f"\n來源：{url}"
            block_urls.append(url)
        scored.append((score, block, block_urls))

    if not scored:
        return "", []  # 過濾後無足夠相關文獻，寧可留白也不餵離題論文

    # 依相關性分數穩定排序（同分維持 API 原本的相關性順序），取前 N 筆
    scored.sort(key=lambda s: s[0], reverse=True)
    top = scored[:max_results]
    parts = [b for _, b, _ in top]
    urls = [u for _, _, us in top for u in us]

    header = ("【Europe PMC 學術文獻檢索】"
              "（CIR/毒理評估報告多發表於期刊，以下為相關同儕審查文獻，非官方 Opinion 全文）\n")
    return header + "\n\n".join(parts), urls


# ── 主搜尋函式 ──────────────────────────────────────────────────────────────


def search_toxicology(
    inci_name: str,
    cas_number: str = "",
    db=None,
    use_cache: bool = True,
) -> dict:
    """
    整合 SCCS + CIR 搜尋，回傳結構化資料。
    優先順序：DB 快取（365天）→ 官網 PDF 直接下載 → DuckDuckGo 摘要 fallback
    """
    # Step 1: DB 快取
    if db and use_cache:
        try:
            cached = db.get_tox_pdf_cache(inci_name)
            if cached:
                return {
                    "sccs_raw": cached.get("sccs_text", ""),
                    "cir_raw": cached.get("cir_text", ""),
                    "cosing_raw": cached.get("cosing_text", ""),
                    "sources": [
                        u for u in [
                            cached.get("sccs_url"),
                            cached.get("cir_url"),
                            cached.get("cosing_url"),
                        ] if u
                    ],
                }
        except Exception:
            pass

    # use_cache=False（強制重查）時先刪舊快取，避免新搜尋找不到內容時舊垃圾繼續回流
    if not use_cache and db:
        try:
            db.delete_tox_pdf_cache(inci_name)
        except Exception:
            pass

    # Step 2-4: SCCS / CIR / CosIng 三個來源彼此獨立，平行查詢以縮短總耗時。
    # 各來源內部先試官網 PDF 直抓，失敗再退回 DuckDuckGo 摘要。
    def _sccs_pipeline() -> tuple[str, list[str]]:
        text, urls = search_sccs_direct(inci_name, cas_number)
        if not text:
            text, urls = search_sccs(inci_name, cas_number)
        return text, urls

    def _cir_pipeline() -> tuple[str, list[str]]:
        text, urls = search_cir_direct(inci_name, cas_number)
        if not text:
            text, urls = search_cir(inci_name, cas_number)
        return text, urls

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_sccs = ex.submit(_sccs_pipeline)
        f_cir = ex.submit(_cir_pipeline)
        f_cosing = ex.submit(search_cosing_direct, inci_name, cas_number)
        sccs_text, sccs_urls = f_sccs.result()
        cir_text, cir_urls = f_cir.result()
        cosing_text, cosing_urls = f_cosing.result()

    # Step 3.5: SCCS/CIR 全數落空時，改用 Europe PMC（不依賴搜尋引擎的備援）
    if not sccs_text and not cir_text:
        pmc_text, pmc_urls = search_europepmc(inci_name, cas_number)
        if pmc_text:
            cir_text, cir_urls = pmc_text, pmc_urls

    all_sources = list(dict.fromkeys(sccs_urls + cir_urls + cosing_urls))

    # Step 5: 寫入快取（SCCS/CIR 需超過 500 字元，CosIng 超過 200 字元即快取）
    if db and (len(sccs_text) > 500 or len(cir_text) > 500 or len(cosing_text) > 200):
        try:
            db.save_tox_pdf_cache(
                inci_name, cas_number,
                sccs_text=sccs_text,
                sccs_url=sccs_urls[0] if sccs_urls else "",
                cir_text=cir_text,
                cir_url=cir_urls[0] if cir_urls else "",
                cosing_text=cosing_text,
                cosing_url=cosing_urls[0] if cosing_urls else "",
            )
        except Exception:
            pass

    return {
        "sccs_raw": sccs_text,
        "cir_raw": cir_text,
        "cosing_raw": cosing_text,
        "sources": all_sources,
    }


# ── 使用者自訂網址爬取 ──────────────────────────────────────────────────────


def crawl_url(url: str) -> str:
    """爬取單一網址的主要文字內容，回傳含來源標記的字串。"""
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [line for line in text.splitlines() if line.strip()]
        content = "\n".join(lines)[:6000]
        return f"[來源網址：{url}]\n{content}"
    except Exception as e:
        return f"[無法爬取 {url}：{e}]"


def crawl_urls(urls: list[str]) -> str:
    """爬取多個網址並合併回傳，供 AI 參考。"""
    if not urls:
        return ""
    parts = [crawl_url(u.strip()) for u in urls if u.strip()]
    return "\n\n---\n\n".join(parts)
