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
            for migration in [
                "ALTER TABLE components ADD COLUMN percentage_range TEXT",
                "ALTER TABLE raw_materials ADD COLUMN ingredient_code TEXT",
            ]:
                try:
                    conn.execute(migration)
                except sqlite3.OperationalError:
                    pass

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
                        comp.get("percentage"),
                        comp.get("percentage_range"),
                        comp.get("tw_regulation", ""),
                        comp.get("sccs_raw", ""),
                        comp.get("cir_raw", ""),
                        comp.get("toxicology_summary", ""),
                        comp.get("sources", ""),
                        now,
                    )
                )

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
                          file_type: str, extracted_text: str) -> int:
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO material_files (material_id, filename, file_type, extracted_text, uploaded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (material_id, filename, file_type, extracted_text, now)
            )
            return cur.lastrowid

    def get_material_files(self, material_id: int) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, filename, file_type, uploaded_at FROM material_files "
                "WHERE material_id = ? ORDER BY uploaded_at",
                (material_id,)
            ).fetchall()
            return [dict(r) for r in rows]

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

    # ── PIF 文件管理 ────────────────────────────────────

    def create_pif_document(self, product_name: str) -> int:
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO pif_documents (product_name, status, created_at, updated_at) VALUES (?, 'draft', ?, ?)",
                (product_name, now, now),
            )
            return cur.lastrowid

    def get_pif_documents(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM pif_documents ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

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
                        component_id, noael_manual, dap_override)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        pif_id, i,
                        ing.get("inci_name", ""),
                        ing.get("cas_number", ""),
                        ing.get("percentage"),
                        ing.get("function", ""),
                        ing.get("component_id"),
                        ing.get("noael_manual"),
                        ing.get("dap_override"),
                    ),
                )

    def get_component_by_id(self, component_id: int) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM components WHERE id=?", (component_id,)).fetchone()
            return dict(row) if row else None

    def lookup_component_by_inci_cas(self, inci_name: str, cas_number: str = "") -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = None
            if inci_name and inci_name.strip():
                row = conn.execute(
                    "SELECT * FROM components WHERE LOWER(inci_name)=LOWER(?) LIMIT 1",
                    (inci_name.strip(),),
                ).fetchone()
            if not row and cas_number and cas_number.strip():
                row = conn.execute(
                    "SELECT * FROM components WHERE cas_number=? LIMIT 1",
                    (cas_number.strip(),),
                ).fetchone()
            if not row and inci_name and inci_name.strip():
                row = conn.execute(
                    "SELECT * FROM components WHERE LOWER(inci_name) LIKE LOWER(?) LIMIT 1",
                    (f"%{inci_name.strip()}%",),
                ).fetchone()
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
            if inci_name and inci_name.strip():
                row = conn.execute(
                    single_sql.format(condition="LOWER(c.inci_name) = LOWER(?)"),
                    (inci_name.strip(),),
                ).fetchone()
            if not row and cas_number and cas_number.strip():
                row = conn.execute(
                    single_sql.format(condition="c.cas_number = ?"),
                    (cas_number.strip(),),
                ).fetchone()
            if not row and inci_name and inci_name.strip():
                row = conn.execute(
                    any_sql.format(condition="LOWER(c.inci_name) = LOWER(?)"),
                    (inci_name.strip(),),
                ).fetchone()
            if not row and cas_number and cas_number.strip():
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
