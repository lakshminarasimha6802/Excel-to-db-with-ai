from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

DB_URL = "sqlite:///app.db"

def get_engine() -> Engine:
    eng = create_engine(DB_URL, future=True)
    return eng

def table_exists(engine: Engine, table_name: str) -> bool:
    q = text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t")
    with engine.connect() as conn:
        r = conn.execute(q, {"t": table_name}).fetchone()
    return r is not None

def create_table(engine: Engine, table_name: str, columns_sql: str):
    sql = f"CREATE TABLE IF NOT EXISTS {table_name} ({columns_sql})"
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)

def insert_rows(engine: Engine, table_name: str, columns, rows):
    placeholders = ",".join([":" + c for c in columns])
    sql = f"INSERT INTO {table_name} ({','.join(columns)}) VALUES ({placeholders})"
    with engine.begin() as conn:
        for row in rows:
            payload = {c: row[i] for i, c in enumerate(columns)}
            conn.execute(text(sql), payload)

def query_table(engine: Engine, table_name: str, limit=50, offset=0, search=None):
    base = f"SELECT * FROM {table_name}"
    params = {}
    if search:
        # Simple LIKE across all columns by concatenating them (SQLite)
        # This is basic and may be slow on huge tables—fine for MVP
        with engine.connect() as conn:
            cols = [r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table_name})").all()]
        concat = " || ' ' || ".join([f"IFNULL(CAST({c} AS TEXT),'')" for c in cols])
        base = f"SELECT * FROM {table_name} WHERE ({concat}) LIKE :s"
        params["s"] = f"%{search}%"
    base += " LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset
    with engine.connect() as conn:
        rows = conn.execute(text(base), params).mappings().all()
    return [dict(r) for r in rows]

# ---------- NEW: list/drop tables & counts ----------

INTERNAL_TABLES = {"sqlite_sequence"}  # skip internal tables

def list_tables_with_counts(engine: Engine):
    """Return list of {'name': str, 'rows': int} for all user tables."""
    with engine.connect() as conn:
        tables = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).scalars().all()
    user_tables = [t for t in tables if t not in INTERNAL_TABLES]
    out = []
    with engine.connect() as conn:
        for t in user_tables:
            try:
                count = conn.exec_driver_sql(f"SELECT COUNT(*) FROM {t}").scalar_one()
            except Exception:
                count = 0
            out.append({"name": t, "rows": int(count)})
    return out

def drop_table(engine: Engine, table_name: str):
    with engine.begin() as conn:
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table_name}")
