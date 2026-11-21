from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    corp_code TEXT PRIMARY KEY,
    corp_name TEXT NOT NULL,
    corp_eng_name TEXT NOT NULL,
    stock_code TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
    corp_code TEXT NOT NULL REFERENCES companies(corp_code),
    fiscal_year INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (corp_code, fiscal_year)
);
"""


def database_path() -> Path:
    return Path(os.environ.get("DART_DB_PATH") or "dart.sqlite3").expanduser()


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    location = path.resolve().as_uri() + ("?mode=ro" if readonly else "?mode=rwc")
    db = sqlite3.connect(location, uri=True, timeout=10)
    if not readonly:
        db.execute("PRAGMA foreign_keys = ON")
    db.row_factory = sqlite3.Row
    return db


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as db:
        db.executescript(SCHEMA)


def save_profile(path: Path, company: dict, year: int, fetched_at: str, profile: dict, *, merge_periods: bool = False) -> bool:
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            "SELECT fetched_at, payload FROM profiles WHERE corp_code = ? AND fiscal_year = ?",
            (company["corp_code"], year),
        ).fetchone()
        if current and current["fetched_at"] >= fetched_at:
            return False
        if current and merge_periods:
            previous = json.loads(current["payload"])
            periods = {report["period"]: report for report in previous["periods"]}
            periods.update({report["period"]: report for report in profile["periods"]})
            profile = {
                **profile,
                "periods": [periods[key] for key in ("Q1", "H1", "Q3", "FY") if key in periods],
                "largest_shareholder_changes": previous["largest_shareholder_changes"],
            }
        payload = json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
        db.execute(
            """INSERT INTO companies (corp_code, corp_name, corp_eng_name, stock_code)
               VALUES (:corp_code, :corp_name, :corp_eng_name, :stock_code)
               ON CONFLICT(corp_code) DO UPDATE SET
                   corp_name = excluded.corp_name, corp_eng_name = excluded.corp_eng_name,
                   stock_code = excluded.stock_code""",
            company,
        )
        db.execute(
            """INSERT INTO profiles (corp_code, fiscal_year, fetched_at, payload) VALUES (?, ?, ?, ?)
               ON CONFLICT(corp_code, fiscal_year) DO UPDATE SET
                   fetched_at = excluded.fetched_at, payload = excluded.payload""",
            (company["corp_code"], year, fetched_at, payload),
        )
    return True


def load_profile(path: Path, corp_code: str, year: int) -> dict | None:
    with closing(connect(path, readonly=True)) as db:
        row = db.execute(
            "SELECT payload, fetched_at FROM profiles WHERE corp_code = ? AND fiscal_year = ?",
            (corp_code, year),
        ).fetchone()
    if row is None:
        return None
    profile = json.loads(row["payload"])
    profile["fetched_at"] = row["fetched_at"]
    return profile


def search_companies(path: Path, query: str) -> list[dict]:
    term = "".join(query.casefold().split())
    with closing(connect(path, readonly=True)) as db:
        rows = db.execute(
            """SELECT c.*, p.fiscal_year FROM companies c
               JOIN profiles p USING (corp_code) ORDER BY p.fiscal_year DESC"""
        )
        matches = {}
        for row in rows:
            corp_code = row["corp_code"]
            if corp_code not in matches:
                names = tuple("".join(row[key].casefold().split()) for key in ("corp_name", "corp_eng_name"))
                codes = (corp_code, row["stock_code"])
                if term not in codes and not any(term in name for name in names):
                    continue
                company = {key: row[key] for key in ("corp_code", "corp_name", "corp_eng_name", "stock_code")}
                company["available_years"] = []
                matches[corp_code] = (term not in (*codes, *names), not bool(row["stock_code"]), company)
            matches[corp_code][2]["available_years"].append(row["fiscal_year"])
    ordered = sorted(matches.values(), key=lambda match: (match[0], match[1], match[2]["corp_name"], match[2]["corp_code"]))
    return [company for _, _, company in ordered[:15]]
