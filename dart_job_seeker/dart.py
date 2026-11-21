from datetime import date
from io import BytesIO
import json
import re
from threading import Lock
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from lxml import etree


class DartError(Exception):
    """A sanitized error safe to display in an MCP response."""


FILING_MARKUP = re.compile(
    r"<!\[CDATA\[.*?\]\]>|<!--.*?-->|"
    r"&(?!amp;|lt;|gt;|quot;|apos;|#[0-9]+;|#x[0-9A-Fa-f]+;)|"
    r"<(?![A-Z/!?])|<([A-Z][A-Za-z0-9_.:-]*)[^<>]*>",
    re.DOTALL,
)


def xml_root(data: bytes, *, repair_filing: bool = False):
    encoding = re.match(rb"""\s*<\?xml[^>]*encoding\s*=\s*['"]([^'"]+)""", data[:256], re.I)
    try:
        original = data
        if encoding and encoding[1].lower() in {b"euc-kr", b"euc_kr", b"cp949", b"ks_c_5601-1987"}:
            original = data.decode("cp949")
        try:
            return ElementTree.fromstring(original)
        except ElementTree.ParseError:
            if not repair_filing:
                raise

        # Some filings contain literal ampersands and angle brackets in prose.
        text = original if isinstance(original, str) else data.decode("utf-8-sig")
        closed_tags = set(re.findall(r"</([A-Za-z][A-Za-z0-9_.:-]*)\s*>", text))

        def repair(match):
            value = match[0]
            if value == "&":
                return "&amp;"
            if value == "<" or (match[1] and match[1] not in closed_tags and not value.endswith("/>")):
                return "&lt;" + value[1:]
            # The tag pattern can contain attribute values as well.
            if match[1]:
                return re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#[0-9]+;|#x[0-9A-Fa-f]+;)", "&amp;", value)
            return value

        repaired = FILING_MARKUP.sub(repair, text)
        try:
            return ElementTree.fromstring(repaired)
        except ElementTree.ParseError:
            utf8 = re.sub(r"^\s*<\?xml[^?]*\?>", "", repaired, count=1).encode()
            parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True, load_dtd=False)
            root = etree.fromstring(utf8, parser)
            if root is not None and root.tag == "DOCUMENT" and root.find("BODY") is not None:
                return root
            raise
    except (ElementTree.ParseError, etree.XMLSyntaxError, UnicodeError, LookupError, ValueError):
        raise DartError("OpenDART XML 형식을 읽을 수 없습니다.") from None


class DartQuotaError(DartError):
    pass


class Dart:
    def __init__(self, key: str, *, request_limit: int | None = None):
        self.key = key
        self.request_limit = request_limit
        self.requests = 0
        self._limit_hit = False
        self._request_lock = Lock()

    def reserve_request(self):
        with self._request_lock:
            if self._limit_hit or self.request_limit is not None and self.requests >= self.request_limit:
                raise DartQuotaError("OpenDART 요청 한도에 도달했습니다.")
            self.requests += 1

    def fetch(self, endpoint: str, **params) -> bytes:
        url = f"https://opendart.fss.or.kr/api/{endpoint}"
        query = urlencode({"crtfc_key": self.key, **params})
        limit = 64 * 1024 * 1024
        for attempt in range(3):
            self.reserve_request()
            try:
                with urlopen(f"{url}?{query}", timeout=25) as response:
                    data = response.read(limit + 1)
                break
            except HTTPError as exc:
                if attempt == 2 or exc.code not in {429, 500, 502, 503, 504}:
                    raise DartError(f"OpenDART HTTP 오류 ({exc.code})") from None
            except (URLError, OSError):
                if attempt == 2:
                    raise DartError("OpenDART 연결에 실패했습니다.") from None
            sleep(0.5 * 2**attempt)
        if len(data) > limit:
            raise DartError("OpenDART 응답이 처리 가능한 크기를 초과했습니다.")
        return data

    def check(self, status: str | None) -> bool:
        if status == "020":
            with self._request_lock:
                self._limit_hit = True
            raise DartQuotaError("OpenDART 서버 요청 한도에 도달했습니다 (020).")
        if status in {"013", "014"}:
            return False
        if status != "000":
            code = status if status and re.fullmatch(r"[0-9]{3}", status) else "알 수 없음"
            raise DartError(f"OpenDART 응답 오류 ({code}). 인증키, 호출 한도 또는 서비스 상태를 확인하세요.")
        return True

    def json(self, endpoint: str, **params) -> dict:
        try:
            result = json.loads(self.fetch(endpoint, **params))
        except ValueError:
            raise DartError("OpenDART JSON 형식을 읽을 수 없습니다.") from None
        if not isinstance(result, dict):
            raise DartError("OpenDART JSON 형식을 읽을 수 없습니다.")
        return result if self.check(result.get("status")) else {}

    def xml_zip(self, endpoint: str, preferred: str, **params):
        data = self.fetch(endpoint, **params)
        try:
            with ZipFile(BytesIO(data)) as archive:
                entries = [entry for entry in archive.infolist() if entry.filename.lower().endswith(".xml")]
                entry = next((item for item in entries if item.filename.rsplit("/", 1)[-1] == preferred), None)
                if entry is None and len(entries) == 1:
                    entry = entries[0]
                if entry is None or entry.file_size > 64 * 1024 * 1024:
                    raise DartError("OpenDART ZIP에서 처리 가능한 본문 XML을 찾을 수 없습니다.")
                return xml_root(archive.read(entry), repair_filing=endpoint == "document.xml")
        except BadZipFile:
            if not self.check(xml_root(data).findtext("status")):
                return None
            raise DartError("OpenDART ZIP 형식을 읽을 수 없습니다.") from None

    def companies(self):
        root = self.xml_zip("corpCode.xml", "CORPCODE.xml")
        if root is None:
            raise DartError("OpenDART 법인 목록을 가져올 수 없습니다.")
        for element in root.findall("list"):
            yield {
                key: (element.findtext(key) or "").strip()
                for key in ("corp_code", "corp_name", "corp_eng_name", "stock_code")
            }

    def filings(self, corp_code: str, first_period: date):
        page = 1
        while True:
            result = self.json(
                "list.json", corp_code=corp_code, bgn_de=first_period.strftime("%Y%m%d"),
                pblntf_ty="A", last_reprt_at="N", sort="date", sort_mth="desc",
                page_no=page, page_count=100,
            )
            yield from result.get("list", [])
            if page >= result.get("total_page", 0):
                break
            page += 1
