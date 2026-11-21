"""Import curated, cited company and talent evidence into local SQLite."""
from contextlib import closing
from datetime import date
import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit

from .storage import connect, database_path


TABLE = """
CREATE TABLE IF NOT EXISTS company_materials (
    corp_code TEXT PRIMARY KEY CHECK(length(corp_code) = 8),
    corp_name TEXT NOT NULL,
    checked_date TEXT NOT NULL,
    payload TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""
REQUIRED = {"schema_version", "checked_date", "collection", "companies"}
COMPANY_FIELDS = {"corp_code", "corp_code_status", "legal_name", "stock_code", "identity_status", "homepage_url", "careers_url", "scope", "sources"}
SOURCE_FIELDS = {"url", "heading", "kind", "status", "claims", "html_method", "image_method", "scope"}


def _is_https_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.hostname)


def validate(data: object) -> list[dict]:
    if not isinstance(data, dict) or set(data) != REQUIRED:
        raise ValueError(f"top-level keys must be exactly {sorted(REQUIRED)}")
    if data["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    if not isinstance(data["checked_date"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["checked_date"]):
        raise ValueError("checked_date must be YYYY-MM-DD")
    date.fromisoformat(data["checked_date"])
    if not isinstance(data["collection"], dict) or not isinstance(data["companies"], list) or not data["companies"]:
        raise ValueError("collection must be an object and companies a non-empty array")
    seen = set()
    for company in data["companies"]:
        if not isinstance(company, dict) or set(company) != COMPANY_FIELDS:
            raise ValueError(f"company keys must be exactly {sorted(COMPANY_FIELDS)}")
        code = company["corp_code"]
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{8}", code) or code in seen:
            raise ValueError("corp_code must be a unique 8-digit ASCII string")
        seen.add(code)
        for key in ("legal_name", "stock_code", "identity_status", "homepage_url", "careers_url", "scope"):
            if not isinstance(company[key], str) or not company[key].strip():
                raise ValueError(f"{key} must be a non-empty string")
        if not re.fullmatch(r"[0-9]{6}", company["stock_code"]):
            raise ValueError("stock_code must be a 6-digit ASCII string")
        for key in ("homepage_url", "careers_url"):
            if not _is_https_url(company[key]):
                raise ValueError(f"{key} must be an https URL")
        if company["identity_status"] not in {"verified", "unverified"}:
            raise ValueError("identity_status must be verified or unverified")
        if company["corp_code_status"] not in {"verified", "unverified"}:
            raise ValueError("corp_code_status must be verified or unverified")
        if not isinstance(company["sources"], list) or not company["sources"]:
            raise ValueError("sources must be a non-empty array")
        for source in company["sources"]:
            if not isinstance(source, dict) or set(source) != SOURCE_FIELDS:
                raise ValueError(f"source keys must be exactly {sorted(SOURCE_FIELDS)}")
            for key in ("url", "heading", "kind", "status", "html_method", "image_method", "scope"):
                if not isinstance(source[key], str) or not source[key].strip():
                    raise ValueError(f"source {key} must be a non-empty string")
            if not _is_https_url(source["url"]):
                raise ValueError("source url must be an https URL")
            if source["status"] not in {"verified", "unverified"} or not isinstance(source["claims"], list):
                raise ValueError("source status/claims invalid")
            if any(not isinstance(claim, str) or not claim.strip() for claim in source["claims"]):
                raise ValueError("claims must contain non-empty strings")
    return data["companies"]


def import_materials(path: Path, db_path: Path | None = None) -> int:
    # Parse and validate every byte before opening a write transaction.
    data = json.loads(path.read_text(encoding="utf-8"))
    companies = validate(data)
    with closing(connect(db_path or database_path())) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(TABLE)
        for company in companies:
            payload = json.dumps({**company, "checked_date": data["checked_date"]}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            db.execute("""INSERT INTO company_materials(corp_code, corp_name, checked_date, payload) VALUES(?,?,?,?)
                ON CONFLICT(corp_code) DO UPDATE SET corp_name=excluded.corp_name,
                    checked_date=excluded.checked_date, payload=excluded.payload
                WHERE company_materials.payload != excluded.payload OR company_materials.checked_date != excluded.checked_date""",
                       (company["corp_code"], company["legal_name"], data["checked_date"], payload))
    return len(companies)


def main() -> int:
    parser = argparse.ArgumentParser(description="공식 회사·인재 자료를 로컬 SQLite에 가져옵니다.")
    parser.add_argument("file", type=Path, help="검증된 자료 JSON")
    parser.add_argument("--db", type=Path, default=database_path(), help="SQLite 파일 경로")
    args = parser.parse_args()
    try:
        count = import_materials(args.file.expanduser(), args.db.expanduser())
        print(f"회사 자료 {count}건 저장")
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, sqlite3.Error) as exc:
        print(f"자료 가져오기 실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
