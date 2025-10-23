import os, uuid, json
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import pandas as pd
import numpy as np
from passlib.context import CryptContext
from sklearn.ensemble import IsolationForest
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from db import (
    get_engine,
    table_exists,
    create_table,
    insert_rows,
    query_table,
    list_tables_with_counts,
    drop_table,
)
from ingest import (
    ensure_dirs,
    safe_table_name,
    read_any,
    sanitize_headers,
    pandas_to_sqlite_types,
    df_rows,
    df_html,
    list_sheets,
)

# ===============================
# App config
# ===============================
app = FastAPI(title="Excel → DB Converter (Auth + AI)", version="2.0.0")
# random secret each run unless SESSION_SECRET is set
app.add_middleware(
    SessionMiddleware,
    secret_key=(os.environ.get("SESSION_SECRET") or uuid.uuid4().hex),
    https_only=False,
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

engine = get_engine()
ensure_dirs()
os.makedirs("static/plots", exist_ok=True)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ===============================
# Upload manifest (as before)
# ===============================
MANIFEST_PATH = os.path.join("uploads", "manifest.json")

def load_manifest() -> list[dict]:
    if not os.path.exists(MANIFEST_PATH):
        return []
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_manifest(entries: list[dict]) -> None:
    os.makedirs("uploads", exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

def add_upload_record(saved_filename: str, original_name: str, size_bytes: int) -> None:
    entries = load_manifest()
    entries.insert(0, {
        "id": uuid.uuid4().hex,
        "saved_filename": saved_filename,
        "original_name": original_name,
        "size_bytes": size_bytes,
        "uploaded_at": datetime.utcnow().isoformat() + "Z",
    })
    save_manifest(entries)

def remove_upload_record(saved_filename: str) -> None:
    entries = load_manifest()
    entries = [e for e in entries if e.get("saved_filename") != saved_filename]
    save_manifest(entries)

# ===============================
# Notices (ASCII-only in cookies)
# ===============================
def _ascii_safe(text: str) -> str:
    return text.encode("ascii", "ignore").decode("ascii")

def notify_redirect(url: str, msg: str, level: str = "info"):
    resp = RedirectResponse(url=url, status_code=303)
    resp.set_cookie("notice", _ascii_safe(msg))
    resp.set_cookie("notice_level", _ascii_safe(level))
    return resp

def read_notice(request: Request):
    msg = request.cookies.get("notice")
    lvl = request.cookies.get("notice_level", "info")
    return {"message": msg, "level": lvl}

# ===============================
# Auth (very simple, session + users table)
# ===============================
from sqlalchemy import text

def ensure_users_table():
    with engine.begin() as conn:
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            created_at TEXT NOT NULL
        )
        """)

@app.on_event("startup")
def on_startup():
    ensure_dirs()
    ensure_users_table()

def get_user_by_email(email: str):
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id,email,name,password_hash,role FROM users WHERE email=:e"
        ), {"e": email}).fetchone()
        return dict(row._mapping) if row else None

def create_user(email: str, name: str, password: str):
    ensure_users_table()
    if get_user_by_email(email):
        raise ValueError("Email already registered.")
    hashed = pwd_context.hash(password)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (email,name,password_hash,role,created_at) VALUES (:e,:n,:p,'user',:c)"
        ), {"e": email, "n": name, "p": hashed, "c": datetime.utcnow().isoformat()+"Z"})

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def current_user(request: Request) -> Optional[dict]:
    uid = request.session.get("uid")
    if not uid:
        return None
    ensure_users_table()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id,email,name,role FROM users WHERE id=:i"),
                {"i": uid}
            ).fetchone()
        return dict(row._mapping) if row else None
    except Exception:
        return None

def login_required(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=303, detail="Login required")
    return user

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 303 and "Login required" in (exc.detail or ""):
        return RedirectResponse(url="/login?next=" + request.url.path, status_code=303)
    raise exc

# ===============================
# Parquet normalization helper
# ===============================
def normalize_for_parquet(df: pd.DataFrame) -> pd.DataFrame:
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_datetime64_any_dtype(s) or pd.api.types.is_timedelta64_dtype(s):
            continue
        if pd.api.types.is_object_dtype(s):
            parsed = pd.to_datetime(s, errors="coerce", dayfirst=False)
            if parsed.notna().mean() >= 0.6:
                df[c] = parsed
            else:
                df[c] = s.astype(str)
        elif pd.api.types.is_bool_dtype(s):
            df[c] = s.astype("boolean")
        elif pd.api.types.is_integer_dtype(s):
            df[c] = pd.to_numeric(s, errors="coerce").astype("Int64")
        elif pd.api.types.is_float_dtype(s):
            df[c] = pd.to_numeric(s, errors="coerce")
    return df

# ===============================
# PAGES
# ===============================
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    uploads = load_manifest()
    # hide the auth table
    tables_all = list_tables_with_counts(engine)
    tables = [t for t in tables_all if getattr(t, "name", getattr(t, "table", ""))
                                 .lower() != "users"]

    ctx = {
        "request": request,
        "uploads": uploads,
        "tables": tables,
        "user": current_user(request),
        "page_title": "Home"
    }
    ctx.update(read_notice(request))

    resp = templates.TemplateResponse("home.html", ctx)
    if ctx.get("message"):
        resp.delete_cookie("notice")
        resp.delete_cookie("notice_level")
    return resp

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: dict = Depends(login_required)):
    tables_all = list_tables_with_counts(engine)
    # Filter out the "users" table safely whether it's dict or object
    tables = []
    for t in tables_all:
        name = getattr(t, "name", None) or getattr(t, "table", None)
        if isinstance(t, dict):
            name = t.get("name") or t.get("table")
        if name and name.lower() != "users":
            tables.append(t)

    ctx = {"request": request, "tables": tables, "user": user, "page_title": "Dashboard"}
    return templates.TemplateResponse("dashboard.html", ctx)

# ---------- Auth UI ----------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    ctx = {"request": request, "next": next, "user": current_user(request)}
    ctx.update(read_notice(request))
    resp = templates.TemplateResponse("auth_login.html", ctx)
    if ctx.get("message"):
        resp.delete_cookie("notice")
        resp.delete_cookie("notice_level")
    return resp

@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/")):
    ensure_users_table()
    u = get_user_by_email(email)
    if not u or not verify_password(password, u["password_hash"]):
        return notify_redirect("/login", "Invalid credentials.", "error")
    request.session["uid"] = u["id"]
    return notify_redirect(next, f"Welcome {u['name']}!", "success")

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    ctx = {"request": request, "user": current_user(request)}
    ctx.update(read_notice(request))
    resp = templates.TemplateResponse("auth_register.html", ctx)
    if ctx.get("message"):
        resp.delete_cookie("notice")
        resp.delete_cookie("notice_level")
    return resp

@app.post("/register")
async def register(request: Request, name: str = Form(...), email: str = Form(...), password: str = Form(...)):
    try:
        create_user(email=email.strip().lower(), name=name.strip(), password=password)
    except ValueError as e:
        return notify_redirect("/register", str(e), "error")
    return notify_redirect("/login", "Account created. Please log in.", "success")

@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return notify_redirect("/", "Logged out.", "success")

# ---------- Upload / Preview / Import / Browse / Export / Delete ----------
@app.post("/upload", response_class=HTMLResponse)
async def upload(
    request: Request,
    file: UploadFile | None = File(None),
    file_path: str | None = Form(None),
    sheet_name: str | None = Form(None),
):
    # resolve path
    if file_path:
        # user is reusing an existing uploaded file
        save_path = os.path.join("uploads", os.path.basename(file_path))
        if not os.path.exists(save_path):
            raise HTTPException(400, detail="Saved upload not found. Please re-upload.")
        ext = os.path.splitext(save_path)[1].lower()

        # recover original filename from manifest (for nice suggested table names)
        saved_fn = os.path.basename(save_path)
        try:
            entries = load_manifest()
            match = next((e for e in entries if e.get("saved_filename") == saved_fn), None)
            original_name = match["original_name"] if match else saved_fn
        except Exception:
            original_name = saved_fn
    else:
        # user uploaded a new file
        if not file:
            raise HTTPException(400, detail="No file provided.")
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in {".xlsx", ".xls", ".csv"}:
            raise HTTPException(400, detail="Only .xlsx, .xls, .csv allowed.")

        uid = uuid.uuid4().hex
        saved_filename = f"{uid}{ext}"
        save_path = os.path.join("uploads", saved_filename)

        blob = await file.read()

        with open(save_path, "wb") as f:
            f.write(blob)

        original_name = file.filename
        add_upload_record(
            saved_filename=saved_filename,
            original_name=original_name,
            size_bytes=len(blob),
        )

    # sheet select
    sheets = list_sheets(save_path)
    if sheets and sheet_name is None:
        ctx = {
            "request": request,
            "file_name": original_name,
            "file_path": os.path.basename(save_path),
            "sheets": sheets,
            "user": current_user(request)
        }
        return templates.TemplateResponse("choose_sheet.html", ctx)

    # read
    df = read_any(save_path, sheet_name=sheet_name)
    if isinstance(df, dict):
        df = next(iter(df.values()))
    if df.empty:
        raise HTTPException(400, detail="Uploaded file has no rows.")
    df.columns = sanitize_headers([str(c) for c in df.columns])
    df = normalize_for_parquet(df)
    tmp_parquet = save_path + ".parquet"
    df.to_parquet(tmp_parquet, index=False)

    suggested = safe_table_name(original_name)
    ctx = {
        "request": request,
        "preview_html": df_html(df),
        "suggested_table": suggested,
        "tmp_parquet": os.path.basename(tmp_parquet),
        "rows": len(df), "cols": len(df.columns),
        "file_path": os.path.basename(save_path),
        "message": "Preview ready. Confirm import below.",
        "level": "info",
        "user": current_user(request)
    }
    return templates.TemplateResponse("preview.html", ctx)

@app.post("/import", response_class=HTMLResponse)
async def import_data(
    request: Request,
    table_name: str = Form(...),
    tmp_parquet: str = Form(...),
    append_mode: str = Form("create_or_append")
):
    path = os.path.join("uploads", tmp_parquet)
    if not os.path.exists(path):
        raise HTTPException(400, detail="Temporary data not found. Please re-upload.")
    df = pd.read_parquet(path)

    # sanitize any user-input table name to be SQL-safe (must start with a letter)
    table_name = safe_table_name(table_name)

    columns_sql, cols = pandas_to_sqlite_types(df)
    created = False
    if not table_exists(engine, table_name):
        create_table(engine, table_name, columns_sql)
        created = True

    rows_iter = list(df_rows(df, cols))
    insert_rows(engine, table_name, cols, rows_iter)

    try:
        os.remove(path)
    except FileNotFoundError:
        pass

    return notify_redirect(
        f"/browse/{table_name}",
        f"{'Created' if created else 'Updated'} table '{table_name}'. Imported {len(df)} rows.",
        "success"
    )

@app.get("/browse/{table_name}", response_class=HTMLResponse)
def browse_table(request: Request, table_name: str, page: int = 1, page_size: int = 50, q: str | None = None):
    if table_name.lower() == "users":
        raise HTTPException(status_code=404, detail="Not found")
    
    offset = max(0, (page - 1) * page_size)
    rows = query_table(engine, table_name, limit=page_size, offset=offset, search=q)
    ctx = {"request": request, "table_name": table_name, "rows": rows, "page": page, "page_size": page_size, "q": q or "", "user": current_user(request)}
    ctx.update(read_notice(request))
    resp = templates.TemplateResponse("browse.html", ctx)
    if ctx.get("message"):
        resp.delete_cookie("notice"); resp.delete_cookie("notice_level")
    return resp

@app.get("/export/{table_name}")
def export_table(table_name: str, format: str = "csv"):
    out_path = os.path.join("exports", f"{table_name}.{format}")
    with engine.connect() as conn:
        df = pd.read_sql_table(table_name, conn)
    if format == "xlsx":
        df.to_excel(out_path, index=False); media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        df.to_csv(out_path, index=False); media = "text/csv"
    return FileResponse(out_path, media_type=media, filename=os.path.basename(out_path))

@app.post("/delete-upload")
async def delete_upload(file_path: str = Form(...)):
    saved_name = os.path.basename(file_path)
    target = os.path.join("uploads", saved_name)
    removed_any = False
    for p in [target, target + ".parquet", target + ".cache.csv"]:
        try:
            if os.path.exists(p):
                os.remove(p); removed_any = True
        except Exception:
            pass
    remove_upload_record(saved_name)
    return notify_redirect("/", (f"Deleted upload '{saved_name}' and temp files." if removed_any else f"Nothing to delete for '{saved_name}'."), "success" if removed_any else "warn")

@app.post("/delete-table")
async def delete_table_ep(table_name: str = Form(...), user: dict = Depends(login_required)):
    drop_table(engine, table_name)
    return notify_redirect("/", f"Table '{table_name}' deleted.", "success")

# ===============================
# AI / Data features
# ===============================
@app.get("/insights/{table_name}", response_class=HTMLResponse)
def insights(request: Request, table_name: str, user: dict = Depends(login_required)):
    with engine.connect() as conn:
        df = pd.read_sql_table(table_name, conn)

    # --- Build a robust summary that works across pandas versions ---
    try:
        # Newer pandas: supports datetime_is_numeric
        desc = df.describe(include="all", datetime_is_numeric=True)
    except TypeError:
        # Older/other pandas: convert datetimes to int64 temporarily
        df2 = df.copy()
        for c in df2.columns:
            if pd.api.types.is_datetime64_any_dtype(df2[c]):
                df2[c] = pd.to_datetime(df2[c], errors="coerce").astype("int64")
        desc = df2.describe(include="all")

    desc = desc.fillna("").astype(str)

    # --- Quick charts (numeric columns only) ---
    plots = []
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    for col in numeric_cols[:6]:  # limit to 6 plots for speed
        fig_path = os.path.join("static", "plots", f"{table_name}_{col}.png")
        plt.figure()
        try:
            df[col].dropna().hist(bins=20)
            plt.title(f"{table_name} — {col}")
            plt.xlabel(col); plt.ylabel("Count")
            plt.tight_layout(); plt.savefig(fig_path); plots.append("/" + fig_path.replace("\\", "/"))
        finally:
            plt.close()

    ctx = {
        "request": request,
        "table_name": table_name,
        "summary_html": desc.to_html(classes='table', border=0),
        "plots": plots,
        "user": user
    }
    return templates.TemplateResponse("insights.html", ctx)

@app.get("/anomalies/{table_name}", response_class=HTMLResponse)
def anomalies(request: Request, table_name: str, user: dict = Depends(login_required)):
    with engine.connect() as conn:
        df = pd.read_sql_table(table_name, conn)
    num = df.select_dtypes(include=[np.number]).dropna()
    flagged = []
    if not num.empty:
        iso = IsolationForest(n_estimators=150, contamination="auto", random_state=42)
        preds = iso.fit_predict(num.values)
        mask = preds == -1
        flagged = num[mask].head(200).to_dict(orient="records")
    ctx = {"request": request, "table_name": table_name, "flagged": flagged, "total": len(flagged), "user": user}
    return templates.TemplateResponse("anomalies.html", ctx)

@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, user: dict = Depends(login_required)):
    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    return templates.TemplateResponse("chat.html", {"request": request, "user": user, "has_key": has_key})

# (Optional) POST /chat endpoint could call OpenAI if OPENAI_API_KEY is set.
# Keeping it UI-only for now so you can add your key later.
