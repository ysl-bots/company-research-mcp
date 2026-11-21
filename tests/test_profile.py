from contextlib import closing, redirect_stderr, redirect_stdout
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from io import BytesIO, StringIO
from zipfile import ZipFile

from dart_job_seeker.batch import refresh
from dart_job_seeker.d1 import updates
from dart_job_seeker.dart import Dart, DartError, xml_root
from dart_job_seeker.profile import collect_profile, finances, headcount, report_index, select_evidence
from dart_job_seeker.storage import connect, initialize, load_profile, save_profile, search_companies


DOCUMENT = """
<DOCUMENT><BODY>
<SECTION-1><TITLE>I. 회사의 개요</TITLE><P>회사는 1999년에 설립했다.</P></SECTION-1>
<SECTION-1><TITLE>II. 사업의 내용</TITLE>
<TITLE>2. 주요 제품 및 서비스</TITLE><P>(단위: 백만원)</P>
<TABLE><TR><TH>제품</TH><TH>매출액</TH><TH>매출비중</TH></TR><TR><TD>부품</TD><TD>100</TD><TD>70%</TD></TR></TABLE>
</SECTION-1>
<SECTION-1><TITLE>IV. 이사의 경영진단 및 분석의견</TITLE><P>해외 매출 증가와 투자 계획을 보고했다.</P></SECTION-1>
<SECTION-1><TITLE>V. 회계감사인의 감사의견 등</TITLE><P>적정 의견</P></SECTION-1>
<SECTION-1><TITLE>VI. 이사회 등 회사의 기관</TITLE><P>제외 대상</P></SECTION-1>
<SECTION-1><TITLE>XI. 그 밖에 투자자 보호를 위하여 필요한 사항</TITLE><P>소송이 진행 중이다.</P></SECTION-1>
</BODY></DOCUMENT>
"""
Q1 = "20240515000001"
H1 = "20240814000001"
FY = "20250330000002"
COMPANY = {"corp_code": "00126380", "corp_name": "예 시", "corp_eng_name": "Sample Corp", "stock_code": "005930"}


class MockDart(Dart):
    def __init__(self):
        super().__init__("fixture")
        self.calls = []
        self.fail_documents = False

    def fetch(self, endpoint, **params):
        self.calls.append((endpoint, params))
        if endpoint == "corpCode.xml":
            output = BytesIO()
            fields = "".join(f"<{key}>{value}</{key}>" for key, value in COMPANY.items())
            with ZipFile(output, "w") as archive:
                archive.writestr("CORPCODE.xml", f"<result><list>{fields}</list></result>")
            return output.getvalue()
        if endpoint == "document.xml":
            if self.fail_documents:
                raise DartError("모의 연결 오류")
            receipt = params["rcept_no"]
            output = BytesIO()
            with ZipFile(output, "w") as archive:
                archive.writestr("attachment.xml", "<ATTACHMENT/>")
                archive.writestr(f"{receipt}.xml", DOCUMENT)
            return output.getvalue()
        if endpoint == "list.json":
            rows = [
                (FY, "[기재정정]사업보고서 (2024.12)"),
                ("20250401000003", "[첨부정정]사업보고서 (2024.12)"),
                ("20250328000001", "사업보고서 (2024.12)"),
                (H1, "반기보고서 (2024.06)"),
                (Q1, "분기보고서 (2024.03)"),
                ("20250314000001", "사업보고서 (2024.03)"),
            ]
            data = {"total_page": 1, "list": [
                {"rcept_no": receipt, "rcept_dt": receipt[:8], "report_nm": title, "corp_name": "예시"}
                for receipt, title in rows
            ]}
        else:
            code = params["reprt_code"]
            receipt = {"11013": Q1, "11012": H1, "11011": FY}[code]
            if endpoint == "empSttus.json":
                row = {"fo_bbm": "전사", "sexdstn": "합계", "sm": "100", "rgllbr_co": "95",
                       "cnttk_co": "5", "stlm_dt": "2024-03-31"}
            elif endpoint == "hyslrSttus.json":
                row = {"nm": "이전" if code == "11013" else "현재", "relate": "본인"}
            elif endpoint == "exctvSttus.json":
                row = {"nm": "임원", "main_career": "연구개발", "birth_ym": "비공개 처리 대상"}
            elif endpoint == "fnlttSinglAcnt.json":
                row = {"account_nm": "매출액", "fs_div": "CFS", "thstrm_amount": "100",
                       "thstrm_add_amount": "210", "currency": "KRW"}
                if code == "11011":
                    receipt = "20250328000001"
            else:
                raise AssertionError(endpoint)
            data = {"list": [{"rcept_no": receipt, **row}]}
        return json.dumps({"status": "000", **data}).encode()


class ProfileTest(unittest.TestCase):
    def test_profile_selects_revision_and_preserves_period_basis(self):
        dart = MockDart()
        result = select_evidence(collect_profile(dart, "00126380", 2024), False, "")
        q1, h1, q3, annual = result["periods"]
        self.assertEqual(q1["headcount"], 100)
        self.assertEqual(q1["employees"][0]["fixed_term_pct"], 5)
        self.assertEqual(h1["financials"]["CFS"]["revenue"]["value"], 210)
        self.assertEqual(q3["status"], "filing_unavailable")
        self.assertEqual(annual["receipt"], FY)
        self.assertEqual(annual["availability"]["financials"], "receipt_mismatch")
        self.assertEqual(set(annual["annual_evidence"]), {"I", "II", "IV", "XI"})
        self.assertIn("소송", annual["annual_evidence"]["XI"]["excerpts"][0]["excerpt"])
        metrics = q1["business_metric_evidence"]["product_sales"]
        self.assertIn("단위: 백만원", metrics[0]["context"])
        self.assertIn("부품 | 100 | 70%", metrics[0]["excerpt"])
        self.assertEqual(annual["executive_examples"], [{"name": "임원", "career": "연구개발"}])
        self.assertEqual(result["largest_shareholder_changes"][0]["period"], "H1")

    def test_optional_evidence(self):
        result = select_evidence(collect_profile(MockDart(), "00126380", 2024), True, "")
        annual = result["periods"][-1]
        self.assertEqual(set(annual["annual_evidence"]), {"I", "II", "IV", "V", "IX", "XI"})
        self.assertIn("materials", annual["business_metric_evidence"])
        self.assertEqual(annual["annual_evidence"]["V"]["excerpts"][0]["excerpt"], "적정 의견")

    def test_non_december_fiscal_ending_months(self):
        rows = [
            {"rcept_no": "20261109000267", "report_nm": "반기보고서 (2026.09)"},
            {"rcept_no": "20260810000423", "report_nm": "분기보고서 (2026.06)"},
            {"rcept_no": "20250330000001", "report_nm": "사업보고서 (2025.03)"},
        ]
        self.assertEqual(set(report_index(MockDart(), "00136721", 2026, rows, closing_month=3)), {"Q1", "H1"})
        self.assertEqual(set(report_index(MockDart(), "00136721", 2025, rows, closing_month=3)), {"FY"})

    def test_missing_cumulative_is_not_replaced_by_single_quarter(self):
        row = {"account_nm": "매출액", "fs_div": "OFS", "thstrm_amount": "100"}
        self.assertEqual(finances([row], "Q3"), {})

    def test_headcount_does_not_sum_duplicate_or_subtotal_rows(self):
        row = {"segment": "반도체", "gender": "남", "headcount": 10}
        self.assertIsNone(headcount([row, row]))
        details = [row, {**row, "gender": "여", "headcount": 2}]
        self.assertEqual(headcount(details), 12)
        subtotals = [{**item, "segment": "성별합계"} for item in details]
        self.assertEqual(headcount([*details, *subtotals]), 12)

    def test_filing_repair_preserves_escaped_and_cdata_text(self):
        raw = "<DOC><P>R&D &amp; &lt; < TV > <보수현황></P><P><![CDATA[R&D < TV >]]></P></DOC>".encode()
        root = xml_root(raw, repair_filing=True)
        self.assertEqual(root[0].text, "R&D & < < TV > <보수현황>")
        self.assertEqual(root[1].text, "R&D < TV >")
        with self.assertRaises(DartError):
            xml_root(raw)

    def test_xml_encoding_and_invalid_document(self):
        data = '<?xml version="1.0" encoding="EUC-KR"?><TITLE>사업보고서</TITLE>'.encode("cp949")
        self.assertEqual(xml_root(data).text, "사업보고서")
        with self.assertRaises(DartError):
            xml_root(b"invalid")


class BatchStorageTest(unittest.TestCase):
    def test_batch_keeps_good_data_when_refresh_fails(self):
        with TemporaryDirectory() as directory, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            path = Path(directory) / "cache ? 공시.sqlite3"
            dart = MockDart()
            self.assertEqual(refresh(dart, path, [COMPANY["corp_code"]], [2024, 2025]), 1)
            self.assertEqual(sum(endpoint == "corpCode.xml" for endpoint, _ in dart.calls), 1)
            self.assertEqual(search_companies(path, "samplecorp"), [{**COMPANY, "available_years": [2024]}])
            self.assertEqual(search_companies(path, "005930")[0]["corp_code"], COMPANY["corp_code"])
            self.assertIsNone(load_profile(path, COMPANY["corp_code"], 2025))
            original = load_profile(path, COMPANY["corp_code"], 2024)
            self.assertEqual(original["periods"][-1]["receipt"], FY)
            self.assertIn("fetched_at", original)

            dart.fail_documents = True
            self.assertEqual(refresh(dart, path, [COMPANY["corp_code"]], [2024]), 1)
            self.assertEqual(load_profile(path, COMPANY["corp_code"], 2024), original)

    def test_updates_same_record_and_rejects_older_batch(self):
        with TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            path = Path(directory) / "dart.sqlite3"
            dart = MockDart()
            code = COMPANY["corp_code"]
            self.assertEqual(refresh(dart, path, [code], [2024]), 0)
            previous = load_profile(path, code, 2024)
            self.assertEqual(refresh(dart, path, [code], [2024]), 0)
            latest = load_profile(path, code, 2024)
            self.assertGreater(latest["fetched_at"], previous["fetched_at"])
            self.assertFalse(save_profile(path, COMPANY, 2024, previous["fetched_at"], {}))
            self.assertEqual(search_companies(path, code)[0]["available_years"], [2024])
            with closing(connect(path, readonly=True)) as db, self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM profiles")

            first = select_evidence(load_profile(path, code, 2024), False, "")
            self.assertNotIn("V", first["periods"][-1]["annual_evidence"])
            second = select_evidence(load_profile(path, code, 2024), True, "")
            self.assertIn("V", second["periods"][-1]["annual_evidence"])

    def test_d1_projection_preserves_local_periods_and_uploads_bound_data(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "source.sqlite3"
            target = Path(directory) / "target.sqlite3"
            initialize(path)
            initialize(target)
            profile = collect_profile(MockDart(), COMPANY["corp_code"], 2024)
            unrelated = {**profile, "company": {"market": "KOSDAQ"}}
            first = "2026-11-21T00:00:00.000000+00:00"
            second = "2026-11-21T01:00:00.000000+00:00"
            save_profile(path, COMPANY, 2025, first, unrelated)
            annual = {"company": {"market": "KOSPI"}, "periods": [{"period": "FY", "title": "글로벌 R&D '투자'"}]}
            save_profile(path, COMPANY, 2025, second, annual, merge_periods=True)
            current = load_profile(path, COMPANY["corp_code"], 2025)
            self.assertEqual(len(current["periods"]), 4)
            batches = list(updates(path))
            self.assertEqual(len(batches), 1)
            with closing(connect(target)) as db, db:
                for statement in batches[0] * 2:
                    db.execute(statement["sql"], statement["params"])
            stored = load_profile(target, COMPANY["corp_code"], 2025)
            self.assertEqual(stored["periods"], annual["periods"])

    def test_readonly_lookup_does_not_create_database(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "missing.sqlite3"
            with self.assertRaises(sqlite3.OperationalError):
                load_profile(path, COMPANY["corp_code"], 2024)
            self.assertFalse(path.exists())

    def test_role_selects_executives_beyond_first_eight(self):
        rows = [{"name": str(index), "career": "영업"} for index in range(8)]
        rows.append({"name": "연구 임원", "career": "연구개발 담당"})
        profile = {"periods": [{"executive_examples": rows}]}
        selected = select_evidence(profile, False, "연구개발")["periods"][0]["executive_examples"]
        self.assertEqual(len(selected), 8)
        self.assertEqual(selected[0]["name"], "연구 임원")


if __name__ == "__main__":
    unittest.main()
