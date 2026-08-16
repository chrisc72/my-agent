"""SQLite 本地資料庫操作"""
import json
import sqlite3
from pathlib import Path
from datetime import datetime


class IngredientDB:
    def __init__(self, db_path: str = "data/ingredients.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init()

    def _init(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS raw_materials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ingredient_code TEXT,
                    product_name TEXT NOT NULL,
                    supplier TEXT,
                    is_compound INTEGER DEFAULT 0,
                    source_file TEXT,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS components (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    material_id INTEGER NOT NULL,
                    inci_name TEXT NOT NULL,
                    cas_number TEXT,
                    percentage REAL,
                    percentage_range TEXT,
                    tw_regulation TEXT,
                    sccs_data TEXT,
                    cir_data TEXT,
                    toxicology_summary TEXT,
                    sources TEXT,
                    analyzed_at TEXT,
                    FOREIGN KEY (material_id) REFERENCES raw_materials(id)
                );
                CREATE TABLE IF NOT EXISTS material_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    material_id INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    file_type TEXT,
                    extracted_text TEXT,
                    uploaded_at TEXT,
                    FOREIGN KEY (material_id) REFERENCES raw_materials(id)
                );
            """)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS pif_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_number TEXT DEFAULT '',
                    product_name TEXT NOT NULL,
                    product_type TEXT DEFAULT '',
                    formulation_type TEXT DEFAULT '',
                    claims TEXT DEFAULT '',
                    manufacturer TEXT DEFAULT '',
                    responsible_party TEXT DEFAULT '',
                    status TEXT DEFAULT 'draft',
                    created_at TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS pif_chapter_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pif_id INTEGER NOT NULL,
                    chapter_number INTEGER NOT NULL,
                    content_json TEXT DEFAULT '{}',
                    updated_at TEXT,
                    FOREIGN KEY (pif_id) REFERENCES pif_documents(id),
                    UNIQUE(pif_id, chapter_number)
                );
                CREATE TABLE IF NOT EXISTS pif_product_ingredients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pif_id INTEGER NOT NULL,
                    sort_order INTEGER DEFAULT 0,
                    inci_name TEXT NOT NULL,
                    cas_number TEXT DEFAULT '',
                    percentage REAL,
                    function TEXT DEFAULT '',
                    component_id INTEGER,
                    noael_manual REAL,
                    dap_override REAL,
                    FOREIGN KEY (pif_id) REFERENCES pif_documents(id),
                    FOREIGN KEY (component_id) REFERENCES components(id)
                );
            """)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS regulation_docs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    doc_type TEXT DEFAULT '法規文件',
                    extracted_text TEXT,
                    uploaded_at TEXT
                );
            """)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS toxicology_pdf_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    inci_name TEXT NOT NULL UNIQUE,
                    cas_number TEXT DEFAULT '',
                    sccs_text TEXT DEFAULT '',
                    sccs_url TEXT DEFAULT '',
                    cir_text TEXT DEFAULT '',
                    cir_url TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
            """)
            for migration in [
                "ALTER TABLE components ADD COLUMN percentage_range TEXT",
                "ALTER TABLE raw_materials ADD COLUMN ingredient_code TEXT",
                "ALTER TABLE material_files ADD COLUMN file_data BLOB",
                "ALTER TABLE toxicology_pdf_cache ADD COLUMN cosing_text TEXT DEFAULT ''",
                "ALTER TABLE toxicology_pdf_cache ADD COLUMN cosing_url TEXT DEFAULT ''",
                "ALTER TABLE components ADD COLUMN ifra_data TEXT",
                "ALTER TABLE pif_documents ADD COLUMN document_number TEXT DEFAULT ''",
                "ALTER TABLE pif_product_ingredients ADD COLUMN bioavailability_override REAL",
                "ALTER TABLE pif_product_ingredients ADD COLUMN cramer_class TEXT",
                "ALTER TABLE pif_documents ADD COLUMN current_chapter INTEGER DEFAULT 1",
            ]:
                try:
                    conn.execute(migration)
                except sqlite3.OperationalError:
                    pass

            # 回填：把文件的 updated_at 補成「該文件章節的最後修改時間」
            # 修正歷史批次匯入造成的時間戳全部擠在一起、無法正確置頂的問題。
            # 冪等：執行後不再有比文件更新的章節，重跑不會再變動。
            conn.execute("""
                UPDATE pif_documents
                SET updated_at = (
                    SELECT MAX(c.updated_at) FROM pif_chapter_data c
                    WHERE c.pif_id = pif_documents.id
                )
                WHERE EXISTS (
                    SELECT 1 FROM pif_chapter_data c
                    WHERE c.pif_id = pif_documents.id
                      AND c.updated_at > pif_documents.updated_at
                )
            """)

    # ── 原料基本資訊 ────────────────────────────────────

    def create_material(self, ingredient_code: str, product_name: str, supplier: str) -> int:
        """建立空白原料記錄（不含成分），回傳 material_id。"""
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO raw_materials (ingredient_code, product_name, supplier, is_compound, source_file, created_at) "
                "VALUES (?, ?, ?, 0, '', ?)",
                (ingredient_code, product_name, supplier, now)
            )
            return cur.lastrowid

    def update_material_info(self, material_id: int, ingredient_code: str,
                             product_name: str, supplier: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE raw_materials SET ingredient_code=?, product_name=?, supplier=? WHERE id=?",
                (ingredient_code, product_name, supplier, material_id)
            )

    def get_materials_summary(self) -> list[dict]:
        """回傳所有原料，附帶文件數、成分數、毒理成分數。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT m.*,
                       COUNT(DISTINCT f.id) AS file_count,
                       COUNT(DISTINCT c.id) AS component_count,
                       COUNT(DISTINCT CASE WHEN c.toxicology_summary IS NOT NULL
                             AND c.toxicology_summary != '' THEN c.id END) AS tox_count
                FROM raw_materials m
                LEFT JOIN material_files f ON f.material_id = m.id
                LEFT JOIN components c ON c.material_id = m.id
                GROUP BY m.id
                ORDER BY m.ingredient_code NULLS LAST, m.created_at DESC
            """).fetchall()
            return [dict(r) for r in rows]

    def get_all_materials(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM raw_materials ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_material(self, material_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM material_files WHERE material_id = ?", (material_id,))
            conn.execute("DELETE FROM components WHERE material_id = ?", (material_id,))
            conn.execute("DELETE FROM raw_materials WHERE id = ?", (material_id,))

    # ── 成分分析 ────────────────────────────────────────

    def get_components(self, material_id: int) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM components WHERE material_id = ?", (material_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def save_components(self, material_id: int, is_compound: bool, components: list[dict]):
        """覆寫指定原料的成分與毒理資料。"""
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM components WHERE material_id = ?", (material_id,))
            conn.execute("UPDATE raw_materials SET is_compound=? WHERE id=?",
                         (int(is_compound), material_id))
            for comp in components:
                conn.execute(
                    "INSERT INTO components (material_id, inci_name, cas_number, percentage, percentage_range, "
                    "tw_regulation, sccs_data, cir_data, toxicology_summary, sources, analyzed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        material_id,
                        comp.get("inci_name", ""),
                        comp.get("cas_number", ""),
                        (float(p) if (p := comp.get("percentage")) is not None else None),
                        comp.get("percentage_range"),
                        comp.get("tw_regulation", ""),
                        comp.get("sccs_raw", ""),
                        comp.get("cir_raw", ""),
                        comp.get("toxicology_summary", ""),
                        comp.get("sources", ""),
                        now,
                    )
                )

    def save_ifra_data(self, material_id: int, inci_name: str, ifra_json: str) -> None:
        """儲存（或清除）指定原料成分的 IFRA 用量資料。ifra_json 為空字串表示清除。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE components SET ifra_data=? WHERE material_id=? AND inci_name=?",
                (ifra_json if ifra_json else None, material_id, inci_name),
            )

    def get_ifra_data(self, material_id: int, inci_name: str) -> dict | None:
        """取得指定原料成分的 IFRA 用量資料，未上傳回傳 None。"""
        import json as _json
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT ifra_data FROM components WHERE material_id=? AND inci_name=?",
                (material_id, inci_name),
            ).fetchone()
        if row and row[0]:
            try:
                return _json.loads(row[0])
            except Exception:
                return None
        return None

    def search_by_inci(self, keyword: str) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT c.*, m.product_name, m.supplier FROM components c "
                "JOIN raw_materials m ON c.material_id = m.id "
                "WHERE LOWER(c.inci_name) LIKE LOWER(?)",
                (f"%{keyword}%",)
            ).fetchall()
            return [dict(r) for r in rows]

    # ── 原料文件 ────────────────────────────────────────

    def add_material_file(self, material_id: int, filename: str,
                          file_type: str, extracted_text: str,
                          file_data: bytes | None = None) -> int:
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO material_files (material_id, filename, file_type, extracted_text, file_data, uploaded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (material_id, filename, file_type, extracted_text, file_data, now)
            )
            return cur.lastrowid

    def get_material_files(self, material_id: int) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, filename, file_type, uploaded_at, (file_data IS NOT NULL) AS has_file_data "
                "FROM material_files WHERE material_id = ? ORDER BY uploaded_at",
                (material_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_material_file_data(self, file_id: int) -> bytes | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT file_data FROM material_files WHERE id = ?", (file_id,)
            ).fetchone()
            return row[0] if row else None

    def get_material_file_texts(self, material_id: int) -> list[dict]:
        """含 extracted_text，供 AI 分析用。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, filename, file_type, extracted_text FROM material_files "
                "WHERE material_id = ? ORDER BY uploaded_at",
                (material_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_material_file(self, file_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM material_files WHERE id = ?", (file_id,))

    # ── 法規文件 ────────────────────────────────────────

    def add_regulation_doc(self, filename: str, doc_type: str, extracted_text: str) -> int:
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO regulation_docs (filename, doc_type, extracted_text, uploaded_at) "
                "VALUES (?, ?, ?, ?)",
                (filename, doc_type, extracted_text, now),
            )
            return cur.lastrowid

    def get_regulation_docs(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, filename, doc_type, uploaded_at FROM regulation_docs ORDER BY uploaded_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_regulation_doc_texts(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, filename, doc_type, extracted_text FROM regulation_docs ORDER BY uploaded_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_regulation_doc(self, doc_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM regulation_docs WHERE id = ?", (doc_id,))

    def rename_regulation_doc(self, doc_id: int, new_filename: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE regulation_docs SET filename = ? WHERE id = ?",
                (new_filename, doc_id),
            )

    # ── PIF 文件管理 ────────────────────────────────────

    def create_pif_document(self, product_name: str, document_number: str = "") -> int:
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO pif_documents (document_number, product_name, status, created_at, updated_at) VALUES (?, ?, 'draft', ?, ?)",
                (document_number, product_name, now, now),
            )
            return cur.lastrowid

    def get_pif_documents(self) -> list[dict]:
        import re

        def _nk(s: str) -> list:
            return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM pif_documents").fetchall()
            docs = [dict(r) for r in rows]

        if not docs:
            return docs

        # 最近修改的文件置頂（唯一一筆）
        most_recent = max(docs, key=lambda d: d.get("updated_at") or "")
        rest = [d for d in docs if d["id"] != most_recent["id"]]

        # 其餘：有編號依自然順序升序，無編號依名稱升序
        rest_sorted = sorted(rest, key=lambda d: (
            0 if d.get("document_number") else 1,
            _nk(d.get("document_number") or ""),
            _nk(d.get("product_name") or ""),
        ))

        return [most_recent] + rest_sorted

    def get_pif_document(self, pif_id: int) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM pif_documents WHERE id=?", (pif_id,)).fetchone()
            return dict(row) if row else None

    def update_pif_document(self, pif_id: int, fields: dict):
        now = datetime.now().isoformat()
        fields = {**fields, "updated_at": now}
        set_clause = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [pif_id]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"UPDATE pif_documents SET {set_clause} WHERE id=?", values)

    def set_current_chapter(self, pif_id: int, chapter_number: int):
        # 只更新目前章節，不動 updated_at，避免每次換章都把文件推到「最近修改」置頂。
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE pif_documents SET current_chapter=? WHERE id=?",
                (chapter_number, pif_id),
            )

    def delete_pif_document(self, pif_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM pif_product_ingredients WHERE pif_id=?", (pif_id,))
            conn.execute("DELETE FROM pif_chapter_data WHERE pif_id=?", (pif_id,))
            conn.execute("DELETE FROM pif_documents WHERE id=?", (pif_id,))

    # ── PIF 章節資料 ────────────────────────────────────

    def get_chapter_data(self, pif_id: int, chapter_number: int) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT content_json FROM pif_chapter_data WHERE pif_id=? AND chapter_number=?",
                (pif_id, chapter_number),
            ).fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return {}

    def save_chapter_data(self, pif_id: int, chapter_number: int, content: dict):
        now = datetime.now().isoformat()
        content_json = json.dumps(content, ensure_ascii=False)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO pif_chapter_data (pif_id, chapter_number, content_json, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(pif_id, chapter_number)
                   DO UPDATE SET content_json=excluded.content_json, updated_at=excluded.updated_at""",
                (pif_id, chapter_number, content_json, now),
            )
            conn.execute(
                "UPDATE pif_documents SET updated_at=? WHERE id=?",
                (now, pif_id),
            )

    def get_all_pif_completion_counts(self) -> dict[int, int]:
        """一次 query 取得所有 PIF 文件的完成章節數，回傳 {pif_id: done_count}。"""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT pif_id, COUNT(*) FROM pif_chapter_data "
                "WHERE content_json IS NOT NULL AND content_json != '{}' GROUP BY pif_id"
            ).fetchall()
            counts: dict[int, int] = {r[0]: r[1] for r in rows}
            # 第 3 章由成分表驅動：有成分即算完成
            ing_rows = conn.execute(
                "SELECT DISTINCT pif_id FROM pif_product_ingredients"
            ).fetchall()
            for r in ing_rows:
                pid = r[0]
                # 只在 pif_chapter_data 尚未計入第 3 章時才加 1
                if pid not in counts:
                    counts[pid] = 1
                else:
                    # 確認第 3 章是否已計入
                    already = conn.execute(
                        "SELECT 1 FROM pif_chapter_data WHERE pif_id=? AND chapter_number=3 "
                        "AND content_json IS NOT NULL AND content_json != '{}'",
                        (pid,),
                    ).fetchone()
                    if not already:
                        counts[pid] += 1
            return counts

    def get_chapter_completion_status(self, pif_id: int) -> dict[int, bool]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT chapter_number FROM pif_chapter_data "
                "WHERE pif_id=? AND content_json IS NOT NULL AND content_json != '{}'",
                (pif_id,),
            ).fetchall()
            filled = {r[0] for r in rows}
            count = conn.execute(
                "SELECT COUNT(*) FROM pif_product_ingredients WHERE pif_id=?", (pif_id,)
            ).fetchone()[0]
            if count > 0:
                filled.add(3)
            return {ch: ch in filled for ch in range(1, 17)}

    # ── PIF 第三章：產品成分 ─────────────────────────────

    def get_pif_ingredients(self, pif_id: int) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM pif_product_ingredients WHERE pif_id=? ORDER BY sort_order, id",
                (pif_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_pif_ingredients(self, pif_id: int, ingredients: list[dict]):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM pif_product_ingredients WHERE pif_id=?", (pif_id,))
            for i, ing in enumerate(ingredients):
                conn.execute(
                    """INSERT INTO pif_product_ingredients
                       (pif_id, sort_order, inci_name, cas_number, percentage, function,
                        component_id, noael_manual, dap_override,
                        bioavailability_override, cramer_class)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        pif_id, i,
                        ing.get("inci_name", ""),
                        ing.get("cas_number", ""),
                        ing.get("percentage"),
                        ing.get("function", ""),
                        ing.get("component_id"),
                        ing.get("noael_manual"),
                        ing.get("dap_override"),
                        ing.get("bioavailability_override"),
                        ing.get("cramer_class"),
                    ),
                )

    def clear_pif_chapter3(self, pif_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM pif_product_ingredients WHERE pif_id=?", (pif_id,))

    def get_component_by_id(self, component_id: int) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM components WHERE id=?", (component_id,)).fetchone()
            return dict(row) if row else None

    def lookup_component_by_inci_cas(
        self, inci_name: str, cas_number: str = "",
        material_codes: list[str] | None = None,
    ) -> dict | None:
        """依 INCI 名稱／CAS 比對成分。

        material_codes 有值時，先把比對範圍限縮在「parent 原料的 ingredient_code
        落在 material_codes 內」的成分（避免同 INCI 名稱跨原料抓錯，例如多支香精
        的 INCI 都叫 Fragrance）；範圍內找不到才 fallback 回全域查詢。
        """
        codes = [str(c).strip() for c in (material_codes or []) if str(c).strip()]

        def _query(conn, scoped: bool):
            inci = inci_name.strip() if isinstance(inci_name, str) else ""
            cas = cas_number.strip() if isinstance(cas_number, str) else ""
            if scoped:
                placeholders = ",".join("?" * len(codes))
                base = (
                    "SELECT c.* FROM components c "
                    "JOIN raw_materials rm ON rm.id = c.material_id "
                    f"WHERE rm.ingredient_code IN ({placeholders}) AND "
                )
                scope_params = tuple(codes)
            else:
                base = "SELECT * FROM components WHERE "
                scope_params = ()
            row = None
            if inci:
                row = conn.execute(
                    base + "LOWER(inci_name)=LOWER(?) LIMIT 1",
                    scope_params + (inci,),
                ).fetchone()
            if not row and cas:
                row = conn.execute(
                    base + "cas_number=? LIMIT 1",
                    scope_params + (cas,),
                ).fetchone()
            if not row and inci:
                row = conn.execute(
                    base + "LOWER(inci_name) LIKE LOWER(?) LIMIT 1",
                    scope_params + (f"%{inci}%",),
                ).fetchone()
            return row

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = _query(conn, scoped=True) if codes else None
            if not row:
                row = _query(conn, scoped=False)
            return dict(row) if row else None

    def get_single_material_component_by_inci(
        self, inci_name: str, cas_number: str = ""
    ) -> dict | None:
        """查詢 INCI 相符且有毒理摘要的成分記錄。優先回傳父原料為單一原料的紀錄，找不到時 fallback 至複方原料的紀錄。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            order_clause = (
                "ORDER BY CASE WHEN c.toxicology_summary IS NOT NULL "
                "AND c.toxicology_summary != '' THEN 0 ELSE 1 END LIMIT 1"
            )
            single_sql = (
                "SELECT c.* FROM components c "
                "JOIN raw_materials m ON c.material_id = m.id "
                "WHERE m.is_compound = 0 AND {condition} " + order_clause
            )
            any_sql = (
                "SELECT c.* FROM components c "
                "JOIN raw_materials m ON c.material_id = m.id "
                "WHERE c.toxicology_summary IS NOT NULL AND c.toxicology_summary != '' "
                "AND {condition} " + order_clause
            )
            row = None
            if isinstance(inci_name, str) and inci_name.strip():
                row = conn.execute(
                    single_sql.format(condition="LOWER(c.inci_name) = LOWER(?)"),
                    (inci_name.strip(),),
                ).fetchone()
            if not row and isinstance(cas_number, str) and cas_number.strip():
                row = conn.execute(
                    single_sql.format(condition="c.cas_number = ?"),
                    (cas_number.strip(),),
                ).fetchone()
            if not row and isinstance(inci_name, str) and inci_name.strip():
                row = conn.execute(
                    any_sql.format(condition="LOWER(c.inci_name) = LOWER(?)"),
                    (inci_name.strip(),),
                ).fetchone()
            if not row and isinstance(cas_number, str) and cas_number.strip():
                row = conn.execute(
                    any_sql.format(condition="c.cas_number = ?"),
                    (cas_number.strip(),),
                ).fetchone()
            return dict(row) if row else None

    def get_raw_material_by_code(self, ingredient_code: str) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM raw_materials WHERE ingredient_code=?",
                (ingredient_code.strip(),),
            ).fetchone()
            return dict(row) if row else None

    def get_components_by_material_id(self, material_id: int) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM components WHERE material_id=?",
                (material_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── PIF 附錄：COA / SDS 匯出用 ──────────────────────────

    def get_pif_material_coa_sds(self, pif_id: int) -> list[dict]:
        """取得某 PIF 所有原料的 COA/SDS/IFRA 文件（含 file_data）。
        每個原料可能有多筆（SDS + COA + IFRA 等）；file_data=None 表示尚未補傳。
        檔名含 IFRA 者即使被標成「其他」也會納入（回溯相容既有資料）。
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT DISTINCT
                    rm.id          AS material_id,
                    rm.ingredient_code,
                    rm.product_name AS material_name,
                    mf.id          AS file_id,
                    mf.filename,
                    mf.file_type,
                    mf.file_data
                FROM pif_product_ingredients ppi
                JOIN components c  ON c.id  = ppi.component_id
                JOIN raw_materials rm ON rm.id = c.material_id
                LEFT JOIN material_files mf
                    ON mf.material_id = rm.id
                    AND (mf.file_type IN ('COA', 'SDS', 'IFRA')
                         OR UPPER(mf.filename) LIKE '%IFRA%')
                WHERE ppi.pif_id = ?
                ORDER BY rm.ingredient_code, mf.file_type
            """, (pif_id,)).fetchall()
            return [dict(r) for r in rows]

    # ── 毒理 PDF 快取 ────────────────────────────────────

    _CACHE_TTL_DAYS = 365

    def get_tox_pdf_cache(self, inci_name: str) -> dict | None:
        """取得 PDF 快取，TTL 365 天內有效。"""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=self._CACHE_TTL_DAYS)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM toxicology_pdf_cache "
                "WHERE LOWER(inci_name) = LOWER(?) AND created_at > ?",
                (inci_name.strip(), cutoff),
            ).fetchone()
            return dict(row) if row else None

    def save_tox_pdf_cache(
        self, inci_name: str, cas_number: str,
        sccs_text: str, sccs_url: str,
        cir_text: str, cir_url: str,
        cosing_text: str = "", cosing_url: str = "",
    ):
        """寫入或更新 PDF 快取（UPSERT by inci_name）。"""
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO toxicology_pdf_cache
                   (inci_name, cas_number, sccs_text, sccs_url, cir_text, cir_url,
                    cosing_text, cosing_url, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(inci_name) DO UPDATE SET
                       cas_number=excluded.cas_number,
                       sccs_text=excluded.sccs_text,
                       sccs_url=excluded.sccs_url,
                       cir_text=excluded.cir_text,
                       cir_url=excluded.cir_url,
                       cosing_text=excluded.cosing_text,
                       cosing_url=excluded.cosing_url,
                       created_at=excluded.created_at""",
                (inci_name.strip(), cas_number or "",
                 sccs_text, sccs_url or "",
                 cir_text, cir_url or "",
                 cosing_text or "", cosing_url or "", now),
            )

    def delete_tox_pdf_cache(self, inci_name: str):
        """刪除指定 INCI 名稱的快取，讓下次搜尋重新取得最新資料。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM toxicology_pdf_cache WHERE LOWER(inci_name) = LOWER(?)",
                (inci_name.strip(),),
            )

    def get_pif_material_inci_map(self, pif_id: int) -> list[dict]:
        """取得某 PIF 的 INCI→原料 對應關係，供附錄索引頁使用。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT
                    ppi.inci_name,
                    ppi.percentage,
                    rm.id AS material_id,
                    rm.ingredient_code,
                    rm.product_name AS material_name
                FROM pif_product_ingredients ppi
                JOIN components c  ON c.id  = ppi.component_id
                JOIN raw_materials rm ON rm.id = c.material_id
                WHERE ppi.pif_id = ?
                ORDER BY rm.ingredient_code, ppi.sort_order
            """, (pif_id,)).fetchall()
            return [dict(r) for r in rows]
