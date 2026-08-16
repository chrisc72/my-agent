"""一次性遷移：把 data/pif_uploads/ 根目錄的平鋪附件搬進每份 PIF 的子目錄。

用法：
    python migrate_uploads.py --dry-run    # 先看報告，不動任何檔案
    python migrate_uploads.py              # 實際執行（會先備份資料庫）

可重複執行；已在子目錄中的檔案會跳過。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = BASE / "data" / "ingredients.db"
UPLOAD_ROOT = BASE / "data" / "pif_uploads"

PATH_KEYS = ("uploaded_file_path", "formula_file_path")


def safe_dir_component(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]', "_", s or "").strip().rstrip(".")
    return s or "未命名"


def pif_dir_name(doc: dict) -> str:
    name = safe_dir_component(doc.get("product_name", ""))
    doc_num = safe_dir_component(doc["document_number"]) if doc.get("document_number") else ""
    return f"{doc_num}_{name}" if doc_num else name


def target_dirs(docs: list[dict]) -> dict[int, Path]:
    """算出每份 PIF 的目錄，同名時 id 較大者加後綴（與 UI 端規則一致）。"""
    by_name: dict[str, list[int]] = {}
    for d in docs:
        by_name.setdefault(pif_dir_name(d), []).append(d["id"])
    out: dict[int, Path] = {}
    for d in docs:
        base = pif_dir_name(d)
        ids = by_name[base]
        if len(ids) > 1 and min(ids) < d["id"]:
            base = f"{base}_pif{d['id']}"
        out[d["id"]] = UPLOAD_ROOT / base
    return out


def new_file_name(old_name: str) -> str:
    """pif8_ch2_登錄.pdf -> ch02_登錄.pdf；pif60_ch3_formula_x.xlsx -> ch03_formula_x.xlsx"""
    m = re.match(r"^pif\d+_ch(\d+)_(formula_)?(.*)$", old_name)
    if not m:
        return old_name
    ch, formula, rest = m.group(1), m.group(2) or "", m.group(3)
    return f"ch{int(ch):02d}_{formula}{rest}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只印報告，不改任何東西")
    args = ap.parse_args()
    dry = args.dry_run

    if not DB_PATH.exists():
        print(f"找不到資料庫：{DB_PATH}")
        return 1
    if not UPLOAD_ROOT.exists():
        print(f"找不到附件目錄：{UPLOAD_ROOT}")
        return 1

    if not dry:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = DB_PATH.with_name(f"ingredients.db.before-migrate-{stamp}")
        shutil.copy2(DB_PATH, backup)
        print(f"已備份資料庫 -> {backup.name}\n")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    docs = [dict(r) for r in conn.execute("SELECT * FROM pif_documents")]
    dirs = target_dirs(docs)

    moved = 0
    chapters_updated = 0
    # 被任何章節引用到的來源檔；dry-run 時檔案還沒搬走，孤兒判定必須看來源而非目的地
    seen_sources: set[Path] = set()

    rows = conn.execute("SELECT pif_id, chapter_number, content_json FROM pif_chapter_data").fetchall()
    for row in rows:
        pif_id, ch_num, cj = row["pif_id"], row["chapter_number"], row["content_json"]
        if not cj or pif_id not in dirs:
            continue
        data = json.loads(cj)
        dest_dir = dirs[pif_id]
        touched = False

        def relocate(old_path: str) -> str:
            nonlocal touched, moved
            src = Path(old_path)
            if not src.is_absolute():
                src = BASE / src
            seen_sources.add(src)
            # 已在正確的子目錄裡就不動
            if src.parent == dest_dir:
                return str(src)
            dst = dest_dir / new_file_name(src.name)
            if src.exists():
                print(f"  搬移 {src.name}  ->  {dest_dir.name}/{dst.name}")
                if not dry:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst))
                moved += 1
            else:
                print(f"  ⚠️ 來源不存在，僅更新路徑：{src.name}")
            touched = True
            return str(dst)

        for key in PATH_KEYS:
            if data.get(key):
                data[key] = relocate(data[key])
        for f in data.get("files", []):
            if f.get("path"):
                f["path"] = relocate(f["path"])

        if touched:
            chapters_updated += 1
            if not dry:
                conn.execute(
                    "UPDATE pif_chapter_data SET content_json=? WHERE pif_id=? AND chapter_number=?",
                    (json.dumps(data, ensure_ascii=False), pif_id, ch_num),
                )

    if not dry:
        conn.commit()

    # 根目錄裡沒有任何章節引用的檔案 = 孤兒
    orphans = [p for p in UPLOAD_ROOT.iterdir() if p.is_file() and p not in seen_sources]
    if orphans:
        print("\n孤兒檔（無章節引用，將刪除）：")
        for p in orphans:
            print(f"  {p.name}")
            if not dry:
                p.unlink()

    conn.close()

    print(f"\n{'[DRY RUN] ' if dry else ''}搬移 {moved} 個檔案｜更新 {chapters_updated} 筆章節｜刪除 {len(orphans)} 個孤兒檔")
    if dry:
        print("確認無誤後，不加 --dry-run 再跑一次即可實際執行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
