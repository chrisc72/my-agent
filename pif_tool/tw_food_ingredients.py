"""
台灣衛福部食藥署「可供食品使用原料一覽表」查詢。

用於安全評估的證據權重（WoE）：若原料列於此表，代表其具長期歷史食用安全性。
經口暴露的系統性風險高於皮膚塗抹，故「可合法食用」是 SAR 中的支持論點。

檔案同時包含「未確認安全性尚不得使用之原料」負面表列。此清單屬食品法規，
不等於化粧品禁用成分，因此不對外揭露，僅用於抑制部位不符時的錯誤 WoE 加分。
"""
import glob
import os
import re
from functools import lru_cache

import pandas as pd

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "毒理參考資料")
_FILE_PATTERN = "可供食品使用原料一覽表-*.xlsx"

_FOOD_CATEGORY = "可供食品使用之原料"

# 二名法 regex 會誤把「屬名 種名」形態的化學品名稱當成學名，
# 例如 Rooster Comb Extract (containing Sodium hyaluronate) 會產出 "sodium hyaluronate"。
# 因此只掃 `學名` 欄，`外文名稱` 一律走完整字串比對。
_BINOMIAL_RE = re.compile(r"\b([A-Z][a-z]{2,})\s+([a-z]{2,})\b")
_NOT_SPECIES = {"var", "subsp", "ssp", "spp", "cv", "forma", "ex", "et", "and", "the", "of"}

# INCI 部位詞 → `部位` 欄的中文關鍵字（以 substring 比對，
# 故「由種子製取」「果實（含果皮)」等寫法可自然對上）
_PART_MAP = {
    "leaf": ["葉"], "leaves": ["葉"],
    "root": ["根"], "roots": ["根"], "rhizome": ["根莖"],
    "stem": ["莖"], "stems": ["莖"], "stalk": ["莖", "葉柄"],
    "flower": ["花"], "flowers": ["花"], "blossom": ["花"], "petal": ["花瓣"],
    "bud": ["芽", "花蕾", "花苞"], "sprout": ["芽"], "shoot": ["嫩莖", "芽"],
    "fruit": ["果實"], "fruits": ["果實"], "berry": ["果實"],
    "pericarp": ["果皮"], "peel": ["果皮"], "rind": ["果皮"], "pulp": ["果肉"],
    "seed": ["種子", "種仁"], "seeds": ["種子", "種仁"],
    "kernel": ["種仁", "核仁", "種子"], "germ": ["胚芽"], "bran": ["麩皮", "米糠"],
    "bark": ["樹皮", "根皮"], "wood": ["木"], "twig": ["枝"], "branch": ["枝"],
    "tuber": ["塊莖", "塊根"], "bulb": ["鱗莖", "球莖"],
    "pod": ["莢"], "husk": ["殼"], "shell": ["殼"],
    "pollen": ["花粉"], "stigma": ["花柱頭"], "sepal": ["花萼"], "cone": ["毬果"],
    "herb": ["全草"], "thallus": ["藻體"], "mycelium": ["菌絲體"],
}

# 劑型／製法詞，不代表部位
_FORM_WORDS = {
    "extract", "extracts", "oil", "butter", "juice", "water", "powder",
    "wax", "ferment", "filtrate", "lysate", "callus", "culture", "meristem",
    "distillate", "sap", "resin", "gum", "protein", "acid", "ester",
    "starch", "flour", "meal", "hydrolysate", "unsaponifiables",
}


def _binomials(text: str) -> set[str]:
    if not isinstance(text, str):
        return set()
    return {
        f"{genus.lower()} {species}"
        for genus, species in _BINOMIAL_RE.findall(text)
        if species not in _NOT_SPECIES
    }


def _norm_name(text: str) -> str:
    return re.sub(r"[\s\-_,.()（）]+", " ", str(text).lower()).strip()


@lru_cache(maxsize=1)
def _load():
    """回傳 (rows, binomial_index, name_index)。找不到來源檔時回傳空索引。"""
    matches = sorted(glob.glob(os.path.join(_DATA_DIR, _FILE_PATTERN)))
    if not matches:
        return [], {}, {}

    df = pd.read_excel(matches[-1])
    rows = df.to_dict("records")

    binomial_index: dict[str, list[int]] = {}
    name_index: dict[str, list[int]] = {}

    for i, row in enumerate(rows):
        for b in _binomials(row.get("學名")):
            binomial_index.setdefault(b, []).append(i)

        names = []
        foreign = row.get("外文名稱")
        if isinstance(foreign, str):
            names += [n for n in re.split(r"[,;、；\n]", foreign)]
        chinese = row.get("中文名稱")
        if isinstance(chinese, str):
            names += [n for n in re.split(r"[;；\n]", chinese)]
        for n in names:
            key = _norm_name(n)
            if key:
                name_index.setdefault(key, []).append(i)

    return rows, binomial_index, name_index


def food_list_size() -> int:
    """可供食品使用之原料筆數（供 UI 顯示）。"""
    rows, _, _ = _load()
    return sum(1 for r in rows if str(r.get("大分類", "")).startswith(_FOOD_CATEGORY))


def food_list_rows() -> list[dict]:
    """僅回傳可供食品使用之原料（不含未確認安全性清單）。"""
    rows, _, _ = _load()
    return [r for r in rows if str(r.get("大分類", "")).startswith(_FOOD_CATEGORY)]


def _requested_parts(inci_name: str) -> list[str]:
    """從 INCI 名稱第 3 個 token 起找部位詞，回傳對應的中文部位關鍵字。"""
    tokens = [t.lower().strip(",.") for t in inci_name.split()]
    parts = []
    for t in tokens[2:]:
        if t in _FORM_WORDS:
            continue
        parts += _PART_MAP.get(t, [])
    return parts


def _cell(row: dict, key: str) -> str:
    val = row.get(key)
    return str(val).strip() if isinstance(val, str) else ""


def lookup_food_ingredient(inci_name: str) -> dict:
    """
    查詢 INCI 是否列於「可供食品使用原料一覽表」。

    status:
      food_approved  可採計為食用安全史 WoE
      part_mismatch  物種有列表，但本品使用部位不在可食部位內 → 不採計
      unlisted       未收載（含僅命中未確認安全性清單的情形）
    """
    empty = {
        "status": "unlisted", "matched_rows": [], "allowed_parts": [],
        "requested_parts": [], "part_unspecified": False, "note": "",
    }
    if not isinstance(inci_name, str) or not inci_name.strip():
        return empty

    rows, binomial_index, name_index = _load()
    if not rows:
        return empty

    tokens = inci_name.split()
    candidates: list[int] = []
    if len(tokens) >= 2:
        candidates += binomial_index.get(" ".join(tokens[:2]).lower(), [])
    candidates += name_index.get(_norm_name(inci_name), [])
    if not candidates:
        return empty

    seen = set()
    matched = [rows[i] for i in candidates if not (i in seen or seen.add(i))]
    food_rows = [r for r in matched if str(r.get("大分類", "")).startswith(_FOOD_CATEGORY)]
    if not food_rows:
        return empty

    allowed_parts = [p for p in (_cell(r, "部位") for r in food_rows) if p]
    requested = _requested_parts(inci_name)

    if not requested:
        return {
            "status": "food_approved",
            "matched_rows": food_rows,
            "allowed_parts": allowed_parts,
            "requested_parts": [],
            "part_unspecified": bool(allowed_parts),
            "note": (
                f"INCI 未標示使用部位；本表就該物種列出之可食部位為「{'、'.join(allowed_parts)}」，"
                "請確認原料實際使用部位相符"
            ) if allowed_parts else "",
        }

    accepted = [
        r for r in food_rows
        if not _cell(r, "部位") or any(p in _cell(r, "部位") for p in requested)
    ]
    if accepted:
        return {
            "status": "food_approved",
            "matched_rows": accepted,
            "allowed_parts": [p for p in (_cell(r, "部位") for r in accepted) if p],
            "requested_parts": requested,
            "part_unspecified": False,
            "note": "",
        }

    return {
        "status": "part_mismatch",
        "matched_rows": [],
        "allowed_parts": allowed_parts,
        "requested_parts": requested,
        "part_unspecified": False,
        "note": (
            f"本物種列於可供食品使用原料表之部位為「{'、'.join(allowed_parts)}」，"
            "與本原料使用部位不符，不採計為食用安全史"
        ),
    }


def format_food_result(res: dict) -> str:
    """格式化為易讀字串，供 AI context 與 UI 顯示。"""
    if res["status"] == "part_mismatch":
        return f"【台灣可供食品使用原料表】\n未採計：{res['note']}"
    if res["status"] != "food_approved":
        return "未收載於台灣「可供食品使用原料一覽表」"

    lines = ["【台灣可供食品使用原料一覽表】", "收載狀態: 列於可供食品使用之原料"]
    for row in res["matched_rows"]:
        lines.append("")
        lines.append(f"分類: {_cell(row, '次分類')}")
        if _cell(row, "中文名稱"):
            lines.append(f"中文名稱: {_cell(row, '中文名稱')}")
        if _cell(row, "學名"):
            lines.append(f"學名: {_cell(row, '學名')}")
        if _cell(row, "部位"):
            lines.append(f"可食部位: {_cell(row, '部位')}")
        if _cell(row, "備註"):
            lines.append(f"使用限制: {_cell(row, '備註')}")
        if _cell(row, "檔案下載_URL"):
            lines.append(f"官方公告: {_cell(row, '檔案下載_URL')}")
    if res["note"]:
        lines += ["", f"注意: {res['note']}"]
    lines += ["", "來源: 衛生福利部食品藥物管理署「可供食品使用原料一覽表」"]
    return "\n".join(lines)


if __name__ == "__main__":
    print(f"可供食品使用之原料：{food_list_size()} 項")
    for name in [
        "Camellia Sinensis Leaf Extract",
        "Glycyrrhiza Glabra Root Extract",
        "Aloe Barbadensis Leaf Juice",
        "Citrus Limon Peel Oil",
        "Zingiber Officinale Root Extract",
        "Panax Ginseng Root Extract",
        "Panax Ginseng Fruit Extract",
        "Centella Asiatica Extract",
        "Sodium Hyaluronate",
        "Phenoxyethanol",
    ]:
        r = lookup_food_ingredient(name)
        print(f"\n{'=' * 60}\n{name} → {r['status']}"
              + (f" (part_unspecified)" if r["part_unspecified"] else ""))
        print(format_food_result(r))
