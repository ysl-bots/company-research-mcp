"""Build the reviewed adaptive snapshot and update the original research collection."""
import argparse
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from dart_job_seeker.research_batch import EXPORT, RECORDS, ROOT, WORK, atomic_json, export, validate_record

ROOT_DIR = WORK / "adaptive"
RESULTS = ROOT_DIR / "review.json"
EXCLUDED_HOSTS = ("irgo.co.kr", "alphasquare.co.kr", "dart.fss.or.kr", "sentv.co.kr", "awakeplus.co.kr", "kimjonghoon.com")


def reject(row, item):
    parsed = urlsplit(item["url"])
    host = parsed.hostname or ""
    path = parsed.path.rstrip("/").lower()
    query = parse_qs(parsed.query)
    title = item["title"]
    if row["stock_code"] == "138930" and item["kind"] == "conference":
        return "실적발표 자료는 IR로 집계하며 개최 안내는 별도 자료로 집계하지 않음"
    if row["corp_name"] == "코스맥스비티아이" and item["kind"] == "ir" and host.endswith("cosmax.com"):
        return "PDF 표지의 발행 법인은 코스맥스이며 대상 법인은 코스맥스비티아이"
    if host == "www.lotteshoppingir.com" and "fy083q" in path:
        return "PDF 표지에 2008년 실적 자료로 표시됨"
    if any(host == x or host.endswith("." + x) for x in EXCLUDED_HOSTS):
        return "원문 발행 사이트 확인 필요"
    if urlsplit(row.get("ir_home") or "")._replace(fragment="").geturl().rstrip("/") == item["url"].rstrip("/"):
        return "IR 홈"
    if row.get("ir_library") and item["url"].rstrip("/") == row["ir_library"].rstrip("/"):
        return "IR 자료 목록"
    if not path or path.endswith(("board_list.asp", "ir_data_bbs_list.dev", "/disclosureinfo", "/pgpf01",
                                 "/pressrelease/kr", "/uploaded/ir", "/dataroom/report/irdata", "/resource.do",
                                 "ir/ko_report_03.html", "investment/data.asp", "/sub03_03.php",
                                 "/ir/list", "/ir.do", "/calender/uhppci0143m0.jsp", "/financial-information/earnings-release",
                                 "/notices", "/ir/main", "/pr-center/notice")):
        return "개별 원문 URL 미확인"
    if path.endswith("selectboardlist.do") or query.get("actionMode") == ["list2"] or (
        path == "" and set(query) <= {"p", "page"}
    ):
        return "목록 URL"
    if "kind.krx.co.kr" == host and "/dst/irreference/" not in path:
        return "공시 원문"
    if any(x in title for x in ("결산공고", "합병보고총회에 갈음", "창립 50주년 기념행사", "생필품 기탁", "연탄에 사랑을")):
        return "수집 범위 밖 게시물"
    if any(x in item["evidence"] for x in ("후보로만", "직접 열리지 않아", "개별 원문은 확인하지 못")):
        return "개별 원문 접근 근거 미확보"
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--merge", action="store_true")
    parser.add_argument("--require-access", action="store_true")
    args = parser.parse_args()
    access_file = ROOT_DIR / "access.json"
    access = json.loads(access_file.read_text()) if args.require_access else {}
    browser_downloads = set()
    browser_file = ROOT_DIR / "browser-downloads.jsonl"
    if browser_file.exists():
        browser_downloads = {r['url'] for r in map(json.loads, browser_file.read_text().splitlines())
                             if r.get("download") and not r.get("failed") and r.get("url")}
    cohort = {
        row["corp_code"]: row for row in json.loads((WORK / "haiku-retry" / "results.json").read_text())["companies"]
        if not row["items"]
    }
    assert len(cohort) == 303
    groups = defaultdict(list)
    for path in sorted(ROOT_DIR.glob("*.checked.json")):
        for row in json.loads(path.read_text()):
            assert row["corp_code"] in cohort
            groups[row["corp_code"]].append((path.name, row))
    reviewed = []
    discarded = []
    start, end = date(2025, 11, 21), date(2026, 11, 21)
    for code, batch in sorted(groups.items()):
        reference = {**cohort[code], **batch[0][1], "items": []}
        mappings = {}
        # Prefer manually reviewed primary sources when a URL appears in more than one pass.
        for name, row in sorted(batch, key=lambda entry: entry[0].startswith(("direct-", "manual-"))):
            for item in row["items"]:
                reason = reject(row, item)
                if not reason and args.require_access:
                    evidence = access.get(item["url"], {})
                    if not evidence.get("ok") and item["url"] not in browser_downloads:
                        reason = "원문 접근 재검증 실패"
                    elif item["kind"] == "analyst_report" and not evidence.get("pdf"):
                        reason = "증권사 원문 PDF 재검증 실패"
                if reason:
                    discarded.append({"corp_code": code, "url": item["url"], "reason": reason})
                else:
                    mappings[(item["kind"], item["url"].rstrip("/"))] = item
            for key in ("ir_home", "ir_library"):
                if name.startswith(("direct-", "manual-")) and row.get(key):
                    reference[key] = row[key]
        reference["items"] = list(mappings.values())
        reference["sources_checked"] = list(dict.fromkeys(
            source for _, row in batch for source in row.get("sources_checked", [])
        ))
        reference["missing_reasons"] = {
            kind: next((row["missing_reasons"].get(kind) for _, row in reversed(batch)
                        if row["missing_reasons"].get(kind)), "공개 개별 원문을 확인하지 못함.")
            for kind in reference["missing_reasons"]
        }
        reference["adaptive_methods"] = [name for name, _ in batch]
        reference["discovery_method"] = "codex_adaptive"
        reference = validate_record(reference, [cohort[code]], end, start)
        if args.merge:
            path = RECORDS / f"{code}.json"
            old = validate_record(json.loads(path.read_text()), [cohort[code]], end, start)
            previous_adaptive = old.get("discovery_method") == "codex_adaptive"
            if reference["items"] and (not old["items"] or previous_adaptive):
                atomic_json(path, reference)
            elif previous_adaptive and not reference["items"]:
                atomic_json(path, cohort[code])
        reviewed.append(reference)
    result = {
        "target_count": len(cohort), "completed_count": len(groups),
        "checked_date": end.isoformat(), "start_date": start.isoformat(),
        "methods": [
            "공식 IR·뉴스 자료실에서 날짜가 부여된 개별 게시물과 첨부 다운로드를 확인",
            "종목코드와 보고서 제목으로 증권사 발행 원문 및 산업 리포트의 독립 기업분석을 역추적",
            "공식 파트너·행사 주최 측 발표와 기업의 영문·모바일 자료실을 확인",
            "목록·복제본을 반환한 법인은 원문 중심으로 재검색하고 시간 초과는 법인 단위로 재시도",
            "원문 URL의 응답 또는 브라우저 다운로드를 확인하고 PDF 표지 불일치 사례를 수정·제외",
        ],
        "recovered_count": sum(bool(row["items"]) for row in reviewed),
        "item_count": sum(len(row["items"]) for row in reviewed),
        "pending": sorted(set(cohort) - groups.keys()),
        "excluded": discarded, "companies": reviewed,
    }
    atomic_json(RESULTS, result)
    if args.merge:
        atomic_json(ROOT / "company_research_adaptive.json", result)
        export(ROOT / "dart.sqlite3", end, start)
    print(json.dumps({key: value for key, value in result.items() if key not in {"companies", "excluded"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
