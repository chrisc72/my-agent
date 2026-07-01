"""上網搜尋 SCCS / CIR 毒理資料"""
import re
import time
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
TIMEOUT = 15
PDF_TIMEOUT = 30
PDF_MAX_SIZE = 5_242_880   # 5 MB
PDF_MAX_CHARS = 15_000

_TOX_KEYWORDS = [
    "noael", "loael", "mos", "margin of safety", "conclusion",
    "safe", "concentration", "exposure", "toxicolog",
    "dermal", "systemic", "sensitiz", "acceptable", "final report",
]


# ── PDF 工具函式 ────────────────────────────────────────────────────────────


def extract_text_from_pdf_bytes(pdf_bytes: bytes, max_chars: int = PDF_MAX_CHARS) -> str:
    """用 PyMuPDF 從 bytes 抽取 PDF 文字，優先回傳含毒理關鍵詞的頁面。"""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        all_pages = [page.get_text() for page in doc]
        full_text = "\n".join(all_pages)

        if len(full_text) <= max_chars:
            return full_text

        key_pages = [
            p for p in all_pages
            if any(kw in p.lower() for kw in _TOX_KEYWORDS)
        ]
        if key_pages:
            candidate = "\n---\n".join(key_pages)
            if len(candidate) <= max_chars:
                return candidate
            # 結論在後段：前 1/4 背景 + 後 3/4（含 Conclusion 章節）
            front = max_chars // 4
            rear = max_chars - front
            return candidate[:front] + "\n...[中段省略]...\n" + candidate[-rear:]

        # 前 1/3 + 後 2/3（結論通常在後段）
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

    try:
        from ddgs import DDGS
        for query in queries:
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
            time.sleep(1)
            pdf_bytes = download_pdf_if_small(pdf_url)
            if pdf_bytes:
                text = extract_text_from_pdf_bytes(pdf_bytes)
                if len(text.strip()) > 200:
                    return text, [pdf_url]

        # 備援：學術期刊 DOI → Unpaywall OA PDF → PubMed 摘要
        for journal_url in journal_urls[:2]:
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

    try:
        from ddgs import DDGS
        for query in queries:
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
            time.sleep(1)
            pdf_bytes = download_pdf_if_small(pdf_url)
            if pdf_bytes:
                text = extract_text_from_pdf_bytes(pdf_bytes)
                if len(text.strip()) > 200:
                    return text, [pdf_url]

        for page_url in page_urls[:2]:
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


# ── EU CosIng 搜尋 ──────────────────────────────────────────────────────────


def search_cosing_direct(inci_name: str, cas_number: str = "") -> tuple[str, list[str]]:
    """
    查詢 EU CosIng 資料庫的成分法規狀態。
    策略：先嘗試 CosIng REST API，失敗則 DDG 搜尋 fallback。
    回傳 (text, [url])，查無資料回傳 ("", [])。
    """
    urls_found: list[str] = []

    # Step 1: 嘗試 CosIng REST API
    try:
        api_url = "https://ec.europa.eu/growth/tools-databases/cosing/ref/api/ingredients"
        params: dict = {"name": inci_name, "pageNumber": 0, "pageSize": 5}
        resp = requests.get(api_url, params=params, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            items = data if isinstance(data, list) else data.get("ingredients", data.get("content", []))
            if items:
                parts = []
                for item in items[:3]:
                    name = item.get("inciName") or item.get("name", "")
                    functions = item.get("functions", [])
                    restrictions = item.get("restrictions", [])
                    status = item.get("status", "")
                    fn_str = "、".join(
                        (f.get("name") or f) if isinstance(f, dict) else str(f)
                        for f in functions
                    ) if functions else "無"
                    rest_parts = []
                    for r in restrictions:
                        annex = r.get("annexNumber") or r.get("annex", "")
                        ref = r.get("refNumber") or r.get("reference", "")
                        limit = r.get("maximumConcentration") or r.get("concentrationLimit", "")
                        cond = r.get("conditions") or r.get("conditionsOfUse", "")
                        rest_parts.append(
                            f"Annex {annex} Ref {ref}"
                            + (f"，最大濃度 {limit}" if limit else "")
                            + (f"，條件：{cond}" if cond else "")
                        )
                    rest_str = "；".join(rest_parts) if rest_parts else "無 Annex 限制"
                    parts.append(
                        f"INCI: {name}\n功能：{fn_str}\n狀態：{status}\nAnnex 限制：{rest_str}"
                    )
                text = "【EU CosIng API 資料】\n" + "\n\n".join(parts)
                urls_found.append(f"{api_url}?name={inci_name}")
                return text, urls_found
    except Exception:
        pass

    # Step 2: DDG fallback
    try:
        from ddgs import DDGS
        query = f'site:ec.europa.eu "cosing" "{inci_name}"'
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
        ec_results = [r for r in results if "ec.europa.eu" in r.get("href", "")]
        if not ec_results and cas_number:
            query2 = f'EU CosIng cosmetics ingredient "{cas_number}" annex'
            with DDGS() as ddgs:
                results2 = list(ddgs.text(query2, max_results=3))
            ec_results = [r for r in results2 if "ec.europa.eu" in r.get("href", "")]

        if ec_results:
            target_url = ec_results[0]["href"]
            resp = requests.get(target_url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)[:2500]
            if text:
                urls_found.append(target_url)
                return f"【EU CosIng 網頁摘要】\n{text}", urls_found
        elif results:
            snippets = "\n".join(
                f"- {r.get('title','')}: {r.get('body','')}" for r in results[:3]
            )
            return f"【EU CosIng 搜尋摘要（非完整資料）】\n{snippets}", []
    except Exception:
        pass

    return "", []


# ── 備援搜尋：Europe PMC（不依賴 DuckDuckGo）─────────────────────────────────

EUROPEPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def search_europepmc(inci_name: str, cas_number: str = "",
                     max_results: int = 4) -> tuple[str, list[str]]:
    """
    不依賴搜尋引擎的備援：查 Europe PMC（EBI 官方 REST API，免金鑰、穩定）。
    CIR 安全評估報告多發表於期刊、被 Europe PMC / PubMed 收錄，故可作為
    DuckDuckGo 全數落空時的毒理文獻來源。回傳 (摘要文字, 來源URL清單)。
    """
    query = f'{inci_name} AND (toxicology OR "safety assessment" OR SCCS OR CIR OR NOAEL)'
    try:
        resp = requests.get(
            EUROPEPMC_URL,
            params={"query": query, "format": "json",
                    "pageSize": max_results, "resultType": "core"},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("resultList", {}).get("result", [])
    except Exception:
        return "", []

    if not results:
        return "", []

    parts: list[str] = []
    urls: list[str] = []
    for x in results:
        title = (x.get("title") or "").strip()
        year = x.get("pubYear", "")
        doi = x.get("doi", "")
        source = x.get("source", "")
        pid = x.get("id", "")
        abstract = re.sub(r"<[^>]+>", "", (x.get("abstractText") or "").strip())
        if doi:
            url = f"https://doi.org/{doi}"
        elif source and pid:
            url = f"https://europepmc.org/article/{source}/{pid}"
        else:
            url = ""
        block = f"[{year}] {title}"
        if abstract:
            block += f"\n摘要：{abstract[:1500]}"
        if url:
            block += f"\n來源：{url}"
            urls.append(url)
        parts.append(block)

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

    # Step 2: 直接從官網下載 PDF
    sccs_text, sccs_urls = search_sccs_direct(inci_name, cas_number)
    cir_text, cir_urls = search_cir_direct(inci_name, cas_number)

    # Step 3: 失敗時 fallback DuckDuckGo 摘要
    if not sccs_text:
        sccs_text, sccs_urls = search_sccs(inci_name, cas_number)
    if not cir_text:
        cir_text, cir_urls = search_cir(inci_name, cas_number)

    # Step 3.5: DuckDuckGo 全數落空時，改用 Europe PMC（不依賴搜尋引擎的備援）
    if not sccs_text and not cir_text:
        pmc_text, pmc_urls = search_europepmc(inci_name, cas_number)
        if pmc_text:
            cir_text, cir_urls = pmc_text, pmc_urls

    # Step 4: 查詢 EU CosIng
    cosing_text, cosing_urls = search_cosing_direct(inci_name, cas_number)

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
