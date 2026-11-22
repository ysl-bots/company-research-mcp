"""Validate and load the reviewed company research pilot snapshot."""
import argparse
from collections import Counter
from contextlib import closing
from calendar import monthrange
from datetime import date
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from .storage import connect, database_path

COMPANIES = {"00126380": "005930", "00164779": "000660", "00164742": "005380"}
KINDS = ("press_release", "blog", "conference", "analyst_report", "ir")
IR_LINKS = ("ir_home", "ir_library")
LIMIT = 10


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_url(url):
    parsed = urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.fragment, f"Invalid source URL: {url}")


def parse_date(value):
    parsed = date.fromisoformat(value)
    require(parsed.isoformat() == value, f"Expected YYYY-MM-DD: {value}")
    return parsed


def validate(snapshot):
    require(snapshot["schema_version"] == 1, "Unsupported snapshot schema")
    checked = parse_date(snapshot["checked_date"])
    days_in_month = monthrange(checked.year - 1, checked.month)[1]
    start = checked.replace(year=checked.year - 1, day=min(checked.day, days_in_month))
    require(start.isoformat() == snapshot["start_date"], "Invalid 12-month window")
    counts = Counter()
    resources = set()
    by_url = {}
    sessions = set()
    for item in snapshot["items"]:
        url = item["url"]
        check_url(url)
        require(url not in by_url, f"Duplicate original: {url}")
        by_url[url] = item
        kind = item["kind"]
        companies = item["companies"]
        require(companies and companies.keys() <= COMPANIES.keys(), f"Invalid company links: {url}")
        require(all(evidence.strip() for evidence in companies.values()), f"Missing company evidence: {url}")
        require(item["title"].strip(), f"Missing title: {url}")
        if kind in IR_LINKS:
            require(len(companies) == 1, f"IR reference must belong to one company: {url}")
            require("date" not in item, f"IR reference should be undated: {url}")
            for code in companies:
                require((code, kind) not in resources, f"Duplicate IR reference: {code} {kind}")
                resources.add((code, kind))
            continue
        require(kind in KINDS, f"Unknown kind: {kind}")
        material_date = parse_date(item["date"])
        require(start <= material_date <= checked, f"Outside collection window: {url}")
        for code in companies:
            counts[code, kind] += 1
            require(counts[code, kind] <= LIMIT, f"Limit exceeded: {code} {kind}")
        shared_report = kind == "analyst_report" and item["report_type"] == "industry"
        require(len(companies) == 1 or shared_report, f"Unexpected shared item: {url}")
        if kind == "analyst_report":
            require(item["report_type"] in {"stock", "industry"} and item["broker"].strip()
                    and item["analysts"] and all(name.strip() for name in item["analysts"]),
                    f"Incomplete report: {url}")
        if kind == "ir":
            require(item["material_type"].strip(), f"Missing IR material type: {url}")
        if kind == "conference":
            event, session = item["event"].strip(), item["session"].strip()
            require(event and session, f"Missing conference details: {url}")
            key = (next(iter(companies)), event.casefold(), session.casefold())
            require(key not in sessions, f"Duplicate session: {url}")
            sessions.add(key)
            if "event_end_date" in item:
                require(material_date <= parse_date(item["event_end_date"]) <= checked,
                        f"Invalid event end date: {url}")
        for asset in item.get("assets", []):
            check_url(asset["url"])
            require(asset["type"] in {"pdf", "video", "session", "organizer", "webcast"},
                    f"Unknown asset type: {url}")

    for item in by_url.values():
        for url in item.get("related_press", []):
            press = by_url.get(url)
            require(item["kind"] == "conference" and press and press["kind"] == "press_release"
                    and item["companies"].keys() <= press["companies"].keys(),
                    f"Invalid related press release: {url}")
    require(resources == {(code, kind) for code in COMPANIES for kind in IR_LINKS},
            "Every company needs an IR home and library")
    expected_gaps = {(code, kind) for code in COMPANIES for kind in KINDS if counts[code, kind] < LIMIT}
    gaps = snapshot["gaps"]
    require({(gap["corp_code"], gap["kind"]) for gap in gaps} == expected_gaps
            and len(gaps) == len(expected_gaps) and all(gap["reason"].strip() for gap in gaps),
            "Every group below the cap needs one gap reason")
    return counts


def import_snapshot(path: Path, db_path: Path) -> Counter:
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    counts = validate(snapshot)
    with closing(connect(db_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        identities = dict(db.execute(
            "SELECT corp_code, stock_code FROM companies WHERE corp_code IN (?, ?, ?)", tuple(COMPANIES)
        ))
        require(all(identities.get(code) == stock for code, stock in COMPANIES.items()),
                "The local DART companies table must contain the three expected identities")
        db.execute("""CREATE TABLE IF NOT EXISTS company_research_pilot_items (
            url TEXT PRIMARY KEY, kind TEXT NOT NULL, material_date TEXT,
            checked_date TEXT NOT NULL, payload TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS company_research_pilot_companies (
            corp_code TEXT NOT NULL REFERENCES companies(corp_code),
            url TEXT NOT NULL REFERENCES company_research_pilot_items(url),
            evidence TEXT NOT NULL, PRIMARY KEY (corp_code, url)
        )""")
        # Replace the pilot snapshot atomically, including removed links and sources.
        db.execute("DELETE FROM company_research_pilot_companies")
        db.execute("DELETE FROM company_research_pilot_items")
        for item in snapshot["items"]:
            payload = {key: value for key, value in item.items() if key != "companies"}
            db.execute("INSERT INTO company_research_pilot_items VALUES (?, ?, ?, ?, ?)", (
                item["url"], item["kind"], item.get("date"), snapshot["checked_date"],
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ))
            db.executemany("INSERT INTO company_research_pilot_companies VALUES (?, ?, ?)", (
                (code, item["url"], evidence) for code, evidence in item["companies"].items()
            ))
    return counts


def main():
    parser = argparse.ArgumentParser(description="검토된 기업 리서치 파일럿을 로컬 SQLite에 적재합니다.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--db", type=Path, default=database_path())
    args = parser.parse_args()
    try:
        counts = import_snapshot(args.file.expanduser(), args.db.expanduser())
    except (OSError, KeyError, TypeError, AttributeError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"파일럿 적재 실패: {exc}\n")
    for code in COMPANIES:
        print(code, *(f"{kind}={counts[code, kind]}" for kind in KINDS))


if __name__ == "__main__":
    main()
