from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from html.parser import HTMLParser
import re
import sys
from urllib.request import urlopen

from .dart import DartError, DartQuotaError
from .profile import collect_profile
from .storage import initialize, load_profile, save_profile


KIND_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&marketType={}"
# 시장 코드: (KIND marketType, KIND 시장구분 표기, 출력용 이름)
MARKETS = {
    "KOSPI": ("stockMkt", "유가", "코스피"),
    "KOSDAQ": ("kosdaqMkt", "코스닥", "코스닥"),
}


class ListingTable(HTMLParser):
    def __init__(self, label: str):
        super().__init__()
        self.label = label
        self.stocks = {}
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag == "td" and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if len(self.row) >= 7:
                name, market, code, _, _, _, closing = self.row[:7]
                if market == self.label and re.fullmatch(r"[A-Z0-9]{6}", code):
                    self.stocks[code] = (name, closing)
            self.row = None


def listed_companies(market: str):
    market_type, label, name = MARKETS[market]
    with urlopen(KIND_URL.format(market_type), timeout=25) as response:
        data = response.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("KRX 상장법인 목록이 처리 가능한 크기를 초과했습니다.")
    table = ListingTable(label)
    table.feed(data.decode("cp949"))
    if not table.stocks:
        raise ValueError(f"KRX {name} 상장법인 목록을 읽을 수 없습니다.")
    return table.stocks


TARGETS = ((2025, "FY"), (2026, "H1"))


def collect_market(dart, path, workers: int, market: str, *, retry_partial: bool = False,
                   refresh: bool = False) -> int:
    listed = listed_companies(market)
    directory = {row["stock_code"]: row for row in dart.companies() if row["stock_code"] in listed}
    initialize(path)
    missing_codes = sorted(set(listed) - directory.keys())
    skipped = sorted(code for code in listed if not re.fullmatch(r"(?:0?[1-9]|1[0-2])월", listed[code][1]))
    print(f"KRX {MARKETS[market][2]} {len(listed)}개사. DART 매핑 누락 {len(missing_codes)}개, 결산월 미확인 {len(skipped)}개.", flush=True)
    if missing_codes:
        print(f"DART 매핑 누락: {', '.join(missing_codes)}", flush=True)
    if skipped:
        print("결산월 미확인: " + ", ".join(f"{code} {listed[code][0]} ({listed[code][1]})" for code in skipped), flush=True)
    candidates = [row for code, row in directory.items() if code not in skipped]

    def collect_company(company):
        code = company["corp_code"]
        closing_month = int(listed[company["stock_code"]][1][:-1])
        pending = []
        for year, period in TARGETS:
            existing = load_profile(path, code, year)
            if not refresh and existing and existing["company"].get("market") == market:
                report = next((row for row in existing["periods"] if row["period"] == period), {})
                partial = "error" in report.get("availability", {}).values()
                if report and not (retry_partial and partial):
                    continue
            pending.append((year, period))
        if not pending:
            return (0, 0, 0)
        started_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        try:
            rows = list(dart.filings(code, date(2025, closing_month, 1)))
        except DartQuotaError:
            raise
        except DartError as exc:
            print(f"{code} 공시 목록 실패: {exc}", file=sys.stderr, flush=True)
            return (0, 0, len(pending))
        saved = missing = partial = 0
        for year, period in pending:
            profile = collect_profile(dart, code, year, only={period}, rows=rows, closing_month=closing_month)
            profile["company"].update(
                name=profile["company"]["name"] or company["corp_name"], stock_code=company["stock_code"], market=market,
            )
            report = profile["periods"][0]
            if report["status"] == "filing_unavailable":
                missing += 1
            else:
                saved += 1
            if "error" in report.get("availability", {}).values():
                partial += 1
                print(f"{code} {year} {period} 일부 수집 실패: {' / '.join(report['warnings'])}", file=sys.stderr, flush=True)
            save_profile(path, company, year, started_at, profile, merge_periods=True)
        return saved, missing, partial

    totals = [0, 0, 0]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for index, counts in enumerate(executor.map(collect_company, candidates), 1):
            for key, value in enumerate(counts):
                totals[key] += value
            if index % 50 == 0:
                print(f"{index}/{len(candidates)}개사 처리 · API {dart.requests:,}회", flush=True)
    print(
        f"이번 실행 보고서 {totals[0]}건 저장 · 공시 없음 {totals[1]}건 · 부분/목록 실패 {totals[2]}건"
        f" · API {dart.requests:,}회", flush=True,
    )
    return int(bool(totals[2] or missing_codes or skipped))
