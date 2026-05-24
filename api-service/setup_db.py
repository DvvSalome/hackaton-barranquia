"""Apply Kairós schema to Supabase.

Usage:
    python setup_db.py                 # apply schema
    python setup_db.py --check         # check which tables exist
"""
from __future__ import annotations

import sys
import httpx
from pathlib import Path

from config import SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_KEY

TABLES = [
    "profiles", "check_ins", "agent_signals",
    "check_in_turns", "habits", "habit_logs",
    "assessment_results",
]

PROJECT_REF = SUPABASE_URL.replace("https://", "").split(".")[0] if SUPABASE_URL else ""


def _headers(key: str) -> dict:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def check_tables() -> dict[str, bool]:
    """Check which tables already exist."""
    from supabase import create_client
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY or SUPABASE_KEY)
    results = {}
    for table in TABLES:
        try:
            client.table(table).select("id").limit(1).execute()
            results[table] = True
        except Exception:
            results[table] = False
    return results


def apply_via_management_api(sql: str, pat: str) -> tuple[bool, str]:
    """Execute SQL via Supabase Management API (requires personal access token)."""
    url = f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query"
    r = httpx.post(
        url,
        json={"query": sql},
        headers={"Authorization": f"Bearer {pat}", "Content-Type": "application/json"},
        timeout=30,
    )
    if r.status_code < 300:
        return True, "OK"
    return False, f"{r.status_code}: {r.text[:200]}"


def main() -> None:
    if not SUPABASE_URL:
        print("ERROR: SUPABASE_URL not configured in .env")
        sys.exit(1)

    print(f"Project: {PROJECT_REF}")

    # Check mode
    if "--check" in sys.argv:
        print("\nTable status:")
        for table, exists in check_tables().items():
            status = "EXISTS" if exists else "MISSING"
            print(f"  {'OK' if exists else '--'} {table}: {status}")
        return

    # Check existing tables
    print("\nChecking existing tables...")
    existing = check_tables()
    missing = [t for t, ok in existing.items() if not ok]
    if not missing:
        print("All tables already exist.")
        return

    print(f"Missing tables: {', '.join(missing)}")

    # Try Management API if PAT provided
    pat = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--pat=")), None)
    if pat:
        schema_sql = Path(__file__).parent.joinpath("schema.sql").read_text()
        ok, msg = apply_via_management_api(schema_sql, pat)
        if ok:
            print("Schema applied via Management API.")
            return
        print(f"Management API failed: {msg}")

    # Fallback: print instructions
    dashboard_url = f"https://supabase.com/dashboard/project/{PROJECT_REF}/sql"
    schema_path = Path(__file__).parent / "schema.sql"
    print(f"""
To create the tables, run the SQL in schema.sql:
  1. Open: {dashboard_url}
  2. Paste the contents of: {schema_path}
  3. Click "Run"

Or re-run with your Management API personal access token:
  python setup_db.py --pat=sbp_your_token_here
  (Get token: https://supabase.com/dashboard/account/tokens)
""")


if __name__ == "__main__":
    main()
