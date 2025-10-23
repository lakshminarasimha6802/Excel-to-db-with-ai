Excel → DB (with Auth + AI)

FastAPI web app to upload Excel/CSV, preview + clean, import into SQLite, browse/export, get quick insights & anomaly detection, and manage access with user accounts. Clean UI with sidebar, top-right user menu (logout), and toast notifications.

Built for Windows + VS Code, but works anywhere Python runs.

✨ Features

📤 Upload .xlsx / .xls / .csv

🗂 Sheet selector for multi-sheet workbooks

👀 Preview table (HTML) before import

🏷 Smart column name sanitization

🧱 Import into SQLite (autocreates table if needed)

🔎 Browse with pagination + simple search

📤 Export table to CSV or XLSX

📈 Insights: describe() summary + quick histograms (up to 6)

🕵️ Anomaly detection (Isolation Forest) on numeric columns

👥 Auth: Register / Login / Logout (sessions)

🔒 Hides the users table from UI automatically

🧭 Nice UI: sidebar, navbar, avatar dropdown with working Logout

🧰 Requirements

Python 3.11+ (3.12 is fine)

pip

Git

(Optional) VS Code with Python extension

🚀 Quick Start (Windows PowerShell)
# 1) Clone your repo
git clone https://github.com/<your-username>/Excel-to-db-with-ai.git
cd Excel-to-db-with-ai

# 2) Create & activate virtualenv
python -m venv .venv
.venv\Scripts\activate

# 3) Upgrade pip (recommended)
python -m pip install --upgrade pip

# 4) Install dependencies
pip install -r requirements.txt

# 5) Run the app
uvicorn app:app --reload


Open: http://127.0.0.1:8000

First visit shows Register. After creating an account, Login → takes you to Home (uploads + tables).

⚙️ Configuration
Session secret (controls “stay logged in”)

By default the app generates a fresh secret each run (so you start logged out when you restart the server).
If you want sessions to persist across restarts, set a fixed secret:

# run once, then open a NEW terminal
setx SESSION_SECRET "super-long-random-string"


Then start the app again in a new terminal.

Optional: OpenAI (for future /chat integration)
setx OPENAI_API_KEY "sk-..."

📁 Project Structure
.
├─ app.py                  # FastAPI app (routes, auth, UI)
├─ db.py                   # SQLite helpers (engine, DDL/DML)
├─ ingest.py               # File reading, sanitizing, helpers
├─ templates/              # Jinja2 templates (home, preview, etc.)
│  ├─ base.html            # Layout (sidebar, topbar, user menu)
│  ├─ home.html
│  ├─ preview.html
│  ├─ choose_sheet.html
│  ├─ browse.html
│  ├─ insights.html
│  ├─ anomalies.html
│  ├─ auth_login.html
│  └─ auth_register.html
├─ static/
│  ├─ style.css            # UI styles (includes user menu dropdown)
│  └─ plots/               # generated charts (gitignored)
├─ uploads/                # uploaded files + temp parquet (gitignored)
├─ exports/                # exported CSV/XLSX (gitignored)
├─ requirements.txt
└─ .gitignore


Note: We intentionally do not commit uploads/, exports/, .venv/, __pycache__/, .parquet files, etc.

🔐 Auth UX

Home (/)** shows Register when logged out.

Already have an account? Click Login (top-right).

Logged in → Home shows Upload + Tables.

Users table is hidden from lists and blocked from browse/insights/export.

The top-right user circle opens a dropdown with Dashboard / Home / Logout.
Logout is a real POST form and works with keyboard & mouse.

🧪 Usage Walkthrough

Register a new user → redirected to login with a toast “Account created…”.

Login → redirected to Home with a “Welcome …” toast.

Upload an Excel/CSV → if multi-sheet, pick the sheet.

Preview shows the table; click Import to create/update the SQLite table.

Browse the table; use search/page controls.

Check Insights for summary + histograms; Anomalies for outliers.

Export any table to CSV/XLSX.

Logout from the user dropdown.

🛠 Troubleshooting
“no such table: users”

Fixed by app startup hook. If you ever see it:

Ensure you’re on the latest app.py with:

@app.on_event("startup")
def on_startup():
    ensure_dirs()
    ensure_users_table()

“Expected indented block” / red squiggles

Python is indentation-sensitive. Make sure blocks after if/else/def are indented consistently (spaces).

“ImportError: pyarrow / fastparquet”

Pandas needs a parquet engine to write temp .parquet files:

pip install --only-binary :all: pyarrow

bcrypt warning or login hash issues

Pin bcrypt to a compatible version:

pip install "bcrypt==4.0.1"


(Already in requirements.txt.)

Windows “Permission denied: .venv\Scripts\python.exe”

Another process is holding Python open (pip/uvicorn).
Stop other terminals and run commands one by one, not chained.

SQLite error: table name starts with digit

We sanitize names (via safe_table_name) to ensure table names start with a letter, e.g. tbl_sales_2025. If you edited names manually, keep them alphanumeric + underscores, starting with a letter.

📦 Git Hygiene

.gitignore (already included; if not, use this):

# Python
.venv/
__pycache__/
*.pyc

# App data
uploads/
exports/
static/plots/
*.parquet

# OS/Editor
.DS_Store
Thumbs.db
.vscode/
.env


To keep empty folders in Git (optional), add a .gitkeep file to uploads/, exports/, static/plots/.

🧹 Reset / Clean Local State (optional)
# from project root
Remove-Item -Recurse -Force uploads\*, exports\*, static\plots\* 2>$null


(Leaves folders; deletes contents.)

🧪 Dev Tips (VS Code)

Use Python: Select Interpreter → pick .venv

Run with debug:

F5 → choose “Python: FastAPI” (or create a launch.json with uvicorn)

Autoreload is on with --reload

📄 License

You can use MIT (or add your preferred license).

📝 Changelog (highlights)

Auth added; users table hidden from UI

Session secret can be fixed or randomized per run

Parquet temp support via pyarrow

Insights fallback for older pandas (no datetime_is_numeric)

Clickable user dropdown with working Logout

🙋 Support

If someone running the project hits an error, ask them to:

Pull latest main

Recreate venv + pip install -r requirements.txt

Paste the full console traceback + steps to reproduce