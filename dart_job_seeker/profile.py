from datetime import date
import re

from .dart import Dart, DartError, DartQuotaError
from .document import CHAPTERS, METRICS, chapter_evidence, chapters, compact, metric_evidence


PERIODS = (
    ("Q1", "11013", "분기보고서", "03"),
    ("H1", "11012", "반기보고서", "06"),
    ("Q3", "11014", "분기보고서", "09"),
    ("FY", "11011", "사업보고서", "12"),
)
EMPLOYEE_FIELDS = {
    "fo_bbm": "segment", "sexdstn": "gender", "rgllbr_co": "regular",
    "cnttk_co": "fixed_term", "sm": "headcount", "avrg_cnwk_sdytrn": "average_tenure",
    "jan_salary_am": "average_pay_krw", "stlm_dt": "as_of", "rm": "note",
}
FINANCIAL_ACCOUNTS = {
    "매출액": "revenue", "수익매출액": "revenue", "영업수익": "revenue", "영업이익": "operating_profit",
    "영업이익손실": "operating_profit", "당기순이익": "net_income", "당기순이익손실": "net_income",
    "자산총계": "assets", "부채총계": "liabilities",
}


def source(receipt: str) -> str:
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"


def number(value):
    text = str(value).strip().replace(",", "")
    if re.fullmatch(r"\(\d+\)", text):
        return -int(text[1:-1])
    return int(text) if re.fullmatch(r"-?\d+", text) else None


def structured(dart: Dart, endpoint: str, params: dict, filing: dict, warnings: list):
    try:
        rows = dart.json(endpoint, **params).get("list", [])
    except DartQuotaError:
        raise
    except DartError as exc:
        warnings.append(f"{endpoint}: {exc}")
        return [], "error"
    if not rows:
        return [], "unavailable"
    if any(row.get("rcept_no") != filing["rcept_no"] for row in rows):
        warnings.append(f"{endpoint}: 구조화 데이터의 접수번호가 선택한 공시와 다릅니다.")
        return [], "receipt_mismatch"
    return rows, "ok"


def employee_data(rows):
    result = []
    for row in rows:
        entry = {label: row.get(field) for field, label in EMPLOYEE_FIELDS.items()}
        for key in ("regular", "fixed_term", "headcount", "average_pay_krw"):
            entry[key] = number(entry[key])
        regular, fixed, total = (entry[key] for key in ("regular", "fixed_term", "headcount"))
        if regular is not None and fixed is not None and total and regular + fixed == total:
            entry["regular_pct"] = round(100 * regular / total, 2)
            entry["fixed_term_pct"] = round(100 * fixed / total, 2)
        if any(entry[key] not in (None, "", "-") for key in (
            "segment", "headcount", "regular", "fixed_term", "average_tenure", "average_pay_krw",
        )):
            result.append({key: value for key, value in entry.items() if value not in (None, "", "-")})
    return result


def headcount(employees):
    totals = {"합계", "총계", "전체", "전사", "성별합계", "성별총계"}
    detail_genders = {"남": "남", "남자": "남", "남성": "남", "여": "여", "여자": "여", "여성": "여"}
    classified = [
        (compact(row.get("segment", "")), compact(row.get("gender", "")), row.get("headcount"))
        for row in employees
    ]
    grand_totals = [
        count for segment, gender, count in classified
        if segment in totals and gender in {"", "합계", "계", "전체", "남녀"}
    ]
    if len(grand_totals) == 1 and grand_totals[0] is not None:
        return grand_totals[0]

    subtotals = [(detail_genders.get(gender), count) for segment, gender, count in classified if segment in totals]
    if subtotals:
        if len(subtotals) == 2 and {gender for gender, _ in subtotals} == {"남", "여"} and all(
            count is not None for _, count in subtotals
        ):
            return sum(count for _, count in subtotals)
        return None

    labels = {(segment, detail_genders.get(gender)) for segment, gender, _ in classified}
    if classified and len(labels) == len(classified) and all(
        segment and not any(marker in segment for marker in ("합계", "소계", "총계"))
        and gender in detail_genders and count is not None
        for segment, gender, count in classified
    ):
        return sum(count for _, _, count in classified)
    return None


def finances(rows, period):
    accounts = {}
    for row in rows:
        label = row.get("account_nm", "")
        account = FINANCIAL_ACCOUNTS.get(compact(label))
        scope = row.get("fs_div")
        if not account or scope not in {"CFS", "OFS"}:
            continue
        if account in {"assets", "liabilities"}:
            basis = "period_end"
            value = number(row.get("thstrm_amount"))
        else:
            basis = "year_to_date"
            value = number(row.get("thstrm_add_amount") if period in {"H1", "Q3"} else row.get("thstrm_amount"))
        if value is not None:
            accounts.setdefault(scope, {})[account] = {
                "label": label, "value": value, "basis": basis, "currency": row.get("currency"),
            }
    return accounts


def period_month(period: str, calendar_month: str, closing_month: int) -> str:
    months = {"Q1": 3, "H1": 6, "Q3": 9, "FY": 12}
    return calendar_month if closing_month == 12 else f"{(closing_month + months[period] - 1) % 12 + 1:02d}"


def report_index(dart: Dart, corp_code: str, year: int, rows=None, *, closing_month=12):
    filings = {}
    if rows is None:
        rows = dart.filings(corp_code, date(year, 3, 31))
    for row in rows:
        title = row.get("report_nm", "")
        receipt = row.get("rcept_no", "")
        if not re.fullmatch(r"\d{14}", receipt) or "철" in row.get("rm", ""):
            continue
        if title.startswith(("[첨부정정]", "[첨부추가]")):
            continue
        for period, _, name, calendar_month in PERIODS:
            month = period_month(period, calendar_month, closing_month)
            if re.search(rf"{name}\s*\({year}\.{month}\)", title):
                existing = filings.get(period)
                if existing is None or (row.get("rcept_dt", ""), receipt) > (
                    existing.get("rcept_dt", ""), existing["rcept_no"]
                ):
                    filings[period] = row
    return filings


def owner_signature(rows):
    owners = set()
    for row in rows:
        relation = compact(row.get("relate", "") or "")
        if relation in {"본인", "최대주주", "최대주주본인"} and row.get("nm"):
            owners.add("".join(row["nm"].split()))
    return owners


def executive_data(rows):
    fields = {"nm": "name", "ofcps": "position", "chrg_job": "responsibility", "main_career": "career"}
    return [
        {label: " ".join(str(row[field]).split())[:350] for field, label in fields.items() if row.get(field)}
        for row in rows
    ]


def select_evidence(profile: dict, include_optional: bool, role: str) -> dict:
    """Filter a fresh profile decoded from SQLite for a single MCP response."""
    term = compact(role)
    for report in profile["periods"]:
        if not include_optional:
            report.get("business_metric_evidence", {}).pop("materials", None)
            for chapter in ("V", "IX"):
                report.get("annual_evidence", {}).pop(chapter, None)
        if "executive_examples" in report:
            rows = report["executive_examples"]
            if term:
                rows = sorted(rows, key=lambda row: term in compact(" ".join(row.values())), reverse=True)
            report["executive_examples"] = rows[:8]
    return profile


def collect_profile(dart: Dart, corp_code: str, year: int, *, only=None, rows=None, closing_month=12):
    filings = report_index(dart, corp_code, year, rows, closing_month=closing_month)
    if only:
        filings = {period: row for period, row in filings.items() if period in only}
    result = {
        "company": {"corp_code": corp_code, "name": next(iter(filings.values()), {}).get("corp_name")},
        "fiscal_year": year,
        "periods": [],
        "largest_shareholder_changes": [],
        "notes": [
            f"{closing_month}월 결산 기준. H1은 반기보고서, FY는 연간 사업보고서입니다. 각 보고서의 결산 월을 확인하세요.",
            "재무 손익은 누적, 자산·부채·직원 수는 보고서 기준일 값입니다. 제품 매출·연구비·수주는 원문 표의 기간, 단위, 범위를 확인하세요.",
            "인원 증감은 공시상 인력 규모 변동입니다. 채용 계획을 확인하려면 별도 근거가 필요합니다.",
            "원문 발췌와 임원 사례는 정해진 분량까지만 제공합니다.",
        ],
    }
    previous_owners = set()
    previous_report = None
    for period, code, _, calendar_month in PERIODS:
        if only and period not in only:
            continue
        month = period_month(period, calendar_month, closing_month)
        filing = filings.get(period)
        report = {"period": period, "period_end_month": f"{year}-{month}", "report_code": code}
        result["periods"].append(report)
        if not filing:
            report["status"] = "filing_unavailable"
            previous_owners = set()
            previous_report = None
            continue

        receipt = filing["rcept_no"]
        warnings = []
        report.update(status="filed", receipt=receipt, source=source(receipt),
                      filed_on=filing.get("rcept_dt"), title=filing["report_nm"], warnings=warnings)
        params = {"corp_code": corp_code, "bsns_year": year, "reprt_code": code}
        employees, employee_status = structured(dart, "empSttus.json", params, filing, warnings)
        financials, financial_status = structured(dart, "fnlttSinglAcnt.json", params, filing, warnings)
        owners, owner_status = structured(dart, "hyslrSttus.json", params, filing, warnings)
        report["availability"] = {
            "employees": employee_status, "financials": financial_status, "largest_shareholder": owner_status,
        }
        report["employees"] = employee_data(employees)
        if employee_status == "ok" and not report["employees"]:
            report["availability"]["employees"] = "unavailable"
        report["headcount"] = headcount(report["employees"])
        report["financials"] = finances(financials, period)
        current_owners = owner_signature(owners)
        if previous_owners and current_owners and previous_owners != current_owners:
            result["largest_shareholder_changes"].append({
                "previous_period": previous_report["period"], "period": period,
                "previous": sorted(previous_owners), "current": sorted(current_owners),
                "sources": [previous_report["source"], report["source"]],
            })
        previous_owners, previous_report = current_owners, report

        try:
            root = dart.xml_zip("document.xml", f"{receipt}.xml", rcept_no=receipt)
            extracted = chapters(root)
            report["availability"]["document"] = "ok" if extracted else "unavailable"
            if root is not None and not extracted:
                report["availability"]["document"] = "error"
                warnings.append("공시 원문에서 장 구조를 찾을 수 없습니다.")
        except DartQuotaError:
            raise
        except DartError as exc:
            warnings.append(str(exc))
            report["availability"]["document"] = "error"
            extracted = {}

        report["business_metric_evidence"] = metric_evidence(extracted.get("II", []), tuple(METRICS))
        if not report["financials"]:
            report["financial_evidence"] = chapter_evidence(extracted.get("III", []), "III", 950)
        if not report["employees"]:
            report["employee_evidence"] = chapter_evidence(extracted.get("VIII", []), "VIII", 950)
        if period == "FY":
            executives, executive_status = structured(dart, "exctvSttus.json", params, filing, warnings)
            report["availability"]["executives"] = executive_status
            report["executive_examples"] = executive_data(executives)
            wanted = ["I", "II", "IV", "XI", "V", "IX"]
            if not executives:
                wanted.append("VIII")
            report["annual_evidence"] = {
                chapter: {"title": CHAPTERS[chapter][0], "excerpts": chapter_evidence(
                    extracted.get(chapter, []), chapter, 2200,
                )}
                for chapter in wanted
            }

    return result
