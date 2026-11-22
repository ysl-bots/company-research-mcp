import argparse
from contextlib import closing
from itertools import islice
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .market import MARKETS, TARGETS
from .storage import connect, database_path


COMPANY_UPSERT = """
INSERT INTO companies (corp_code, corp_name, corp_eng_name, stock_code)
VALUES (?, ?, ?, ?)
ON CONFLICT(corp_code) DO UPDATE SET
    corp_name = excluded.corp_name,
    corp_eng_name = excluded.corp_eng_name,
    stock_code = excluded.stock_code
WHERE companies.corp_name != excluded.corp_name
   OR companies.corp_eng_name != excluded.corp_eng_name
   OR companies.stock_code != excluded.stock_code
"""

PROFILE_UPSERT = """
INSERT INTO profiles (corp_code, fiscal_year, fetched_at, payload)
VALUES (?, ?, ?, ?)
ON CONFLICT(corp_code, fiscal_year) DO UPDATE SET
    fetched_at = excluded.fetched_at,
    payload = excluded.payload
WHERE profiles.fetched_at < excluded.fetched_at
"""


def updates(path: Path):
    targets = dict(TARGETS)
    with closing(connect(path, readonly=True)) as db:
        rows = db.execute("""
            SELECT c.*, p.fiscal_year, p.fetched_at, p.payload
            FROM profiles p JOIN companies c USING (corp_code)
            WHERE p.fiscal_year IN (?, ?)
            ORDER BY p.corp_code, p.fiscal_year
        """, tuple(targets))
        for row in rows:
            profile = json.loads(row["payload"])
            if profile["company"].get("market") not in MARKETS:
                continue
            period = next((r for r in profile["periods"] if r["period"] == targets[row["fiscal_year"]]), None)
            if period is None:
                continue
            profile["periods"] = [period]
            profile["largest_shareholder_changes"] = []
            payload = json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
            if len(payload.encode()) > 1_900_000:
                raise ValueError(f"{row['corp_code']} {row['fiscal_year']}: D1 행 크기 한도에 근접합니다.")
            yield [
                {"sql": COMPANY_UPSERT, "params": [row[key] for key in
                 ("corp_code", "corp_name", "corp_eng_name", "stock_code")]},
                {"sql": PROFILE_UPSERT, "params": [
                    row["corp_code"], str(row["fiscal_year"]), row["fetched_at"], payload,
                ]},
            ]


def send_updates(endpoint: str, token: str, batch: list[dict]):
    body = json.dumps({"batch": batch}, ensure_ascii=False).encode()
    request = Request(endpoint, data=body, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
    })
    for attempt in range(3):
        try:
            with urlopen(request, timeout=60) as response:
                result = json.load(response)
            break
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise ValueError(f"D1 HTTP 요청 실패 ({exc.code})") from None
        except (URLError, OSError):
            if attempt == 2:
                raise ValueError("D1에 연결할 수 없습니다.") from None
        sleep(2**attempt)

    results = result.get("result") or []
    if not result.get("success") or len(results) != len(batch) or any(not row.get("success") for row in results):
        codes = [item.get("code") for item in (result.get("errors") or [])]
        raise ValueError(f"D1 쓰기 요청 실패. 마이그레이션 적용 여부와 권한을 확인하세요. 오류 코드: {codes}")


def main() -> int:
    parser = argparse.ArgumentParser(description="로컬 코스피·코스닥 공시 또는 기업 조사 자료를 D1으로 동기화합니다.")
    parser.add_argument("--db", type=Path, default=database_path())
    parser.add_argument("--dry-run", action="store_true", help="D1에 접속하지 않고 동기화 대상과 크기를 확인")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--research-only", action="store_true", help="기업 소개와 채용 자료만 동기화")
    modes.add_argument("--materials-snapshot", type=Path, help="검토된 5종 기업 리서치 스냅샷 동기화")
    args = parser.parse_args()
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    database = os.getenv("DART_D1_DATABASE_ID", "")
    token = os.getenv("CLOUDFLARE_API_TOKEN", "")
    if not args.dry_run and (
        not re.fullmatch(r"[0-9a-fA-F]{32}", account)
        or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", database)
        or not token
    ):
        parser.error("CLOUDFLARE_ACCOUNT_ID, DART_D1_DATABASE_ID, CLOUDFLARE_API_TOKEN을 설정하세요.")
    endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account}/d1/database/{database}/query"
    count = largest_payload = 0
    try:
        if args.materials_snapshot:
            from .materials_d1 import material_updates

            rows = (row for row in material_updates(args.db.expanduser(), args.materials_snapshot.expanduser()))
        elif args.research_only:
            from .research_d1 import research_updates

            rows = research_updates(args.db.expanduser())
        else:
            rows = updates(args.db.expanduser())
        with closing(rows):
            while group := list(islice(rows, 10)):
                largest_payload = max(largest_payload, *(len(pair[1]["params"][-1].encode()) for pair in group))
                if not args.dry_run:
                    send_updates(endpoint, token, [statement for pair in group for statement in pair])
                count += len(group)
    except (OSError, KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        print(f"D1 동기화 중단 ({count}개 처리): {exc}", file=sys.stderr)
        return 1
    print(f"{'D1 동기화 대상' if args.dry_run else 'D1 동기화 완료'} {count}개 · 최대 JSON {largest_payload:,}바이트")
    return 0


if __name__ == "__main__":
    sys.exit(main())
