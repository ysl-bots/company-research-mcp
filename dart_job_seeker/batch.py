import argparse
from datetime import date, datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
import sys

from .dart import Dart, DartError, DartQuotaError
from .market import MARKETS, collect_market
from .profile import collect_profile
from .storage import database_path, initialize, save_profile


def refresh(dart: Dart, path: Path, companies: list[str], years: list[int]) -> int:
    initialize(path)
    targets = set(companies)
    directory = {row["corp_code"]: row for row in dart.companies() if row["corp_code"] in targets}
    failed = False
    for code in companies:
        company = directory.get(code)
        if company is None:
            print(f"{code}: OpenDART 법인 목록에 없습니다.", file=sys.stderr)
            failed = True
            continue
        for year in years:
            try:
                started_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
                profile = collect_profile(dart, code, year)
                errors = [
                    warning for report in profile["periods"]
                    if "error" in report.get("availability", {}).values()
                    for warning in report["warnings"]
                ]
                if errors:
                    raise DartError(" / ".join(errors))
                count = sum(report["status"] == "filed" for report in profile["periods"])
                if not count:
                    raise DartError("해당 회계연도의 정기공시를 찾을 수 없습니다.")
                profile["company"].update(
                    name=profile["company"]["name"] or company["corp_name"], stock_code=company["stock_code"],
                )
                saved = save_profile(path, company, year, started_at, profile)
                message = f"공시 {count}/4건 저장" if saved else "더 최근에 시작한 배치의 자료 사용"
                print(f"{code} {year}: {message}")
            except DartQuotaError:
                raise
            except (DartError, sqlite3.Error) as exc:
                print(f"{code} {year}: 갱신 실패. {exc}", file=sys.stderr)
                failed = True
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenDART 정기공시를 수집해 SQLite에 저장합니다.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--market", type=str.upper, choices=MARKETS, help="KOSPI 또는 KOSDAQ 전체의 2025 FY 및 2026 H1 수집")
    target.add_argument("--corp-code", action="append", help="8자리 고유번호. 반복 지정 가능")
    parser.add_argument("--year", action="append", type=int, help="회계연도. 반복 지정 가능")
    parser.add_argument("--db", type=Path, default=database_path(), help="SQLite 파일 경로")
    parser.add_argument("--workers", type=int, default=6, choices=range(1, 13))
    parser.add_argument("--request-limit", type=int, default=12_000)
    parser.add_argument("--refresh", action="store_true", help="--market으로 저장된 공시도 다시 조회")
    parser.add_argument("--retry-partial", action="store_true", help="--market으로 수집 중 오류가 남은 공시를 다시 수집")
    args = parser.parse_args()
    if args.market and args.year or not args.market and not args.year:
        parser.error("--year는 --corp-code와 함께 입력하세요.")
    if not args.market and (args.refresh or args.retry_partial):
        parser.error("--refresh와 --retry-partial은 --market과 함께 사용하세요.")
    if args.request_limit < 1:
        parser.error("--request-limit은 양수여야 합니다.")
    if any(not re.fullmatch(r"[0-9]{8}", code) for code in args.corp_code or []):
        parser.error("--corp-code는 8자리 OpenDART 고유번호여야 합니다.")
    if any(not 2015 <= year <= date.today().year for year in args.year or []):
        parser.error("--year는 2015년부터 현재 연도까지 입력할 수 있습니다.")
    key = os.environ.get("OPENDART_API_KEY", "").strip()
    if not key:
        parser.error("OPENDART_API_KEY 환경변수에 OpenDART 인증키를 설정하세요.")
    try:
        dart = Dart(key, request_limit=args.request_limit)
        if args.market:
            return collect_market(dart, args.db.expanduser(), args.workers, args.market,
                                  retry_partial=args.retry_partial, refresh=args.refresh)
        return refresh(dart, args.db.expanduser(), list(dict.fromkeys(args.corp_code)),
                       list(dict.fromkeys(args.year)))
    except (DartError, sqlite3.Error, OSError, ValueError) as exc:
        print(f"배치 실행 실패. {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
