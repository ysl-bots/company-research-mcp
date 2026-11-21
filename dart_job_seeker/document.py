from collections import defaultdict
from dataclasses import dataclass
import re


CHAPTERS = {
    "I": ("회사의 개요", ("연혁", "종속회사", "상호 변경", "합병", "주요 사업")),
    "II": ("사업의 내용", ("사업의 개요", "주요 제품", "주요 서비스", "시장", "경쟁", "신규 사업")),
    "III": ("재무에 관한 사항", ("요약재무정보", "매출액", "영업이익", "당기순이익")),
    "IV": ("이사의 경영진단 및 분석의견", ("영업실적", "증가", "감소", "전략", "계획", "전망", "투자", "유동성")),
    "V": ("회계감사인의 감사의견 등", ("감사의견", "적정", "의견거절", "계속기업", "강조사항")),
    "XI": ("그 밖에 투자자 보호를 위하여 필요한 사항", ("소송", "제재", "우발", "작성기준일 이후", "보고기간 후")),
    "IX": ("계열회사 등에 관한 사항", ("계열회사", "기업집단", "타법인출자")),
    "VIII": ("임원 및 직원 등에 관한 사항", ("직원", "근속", "급여", "주요경력")),
}
METRICS = {
    "product_sales": ("매출", ("매출액", "매출비중", "비율", "매출실적", "매출비율")),
    "research": ("연구개발", ("연구개발비", "연구개발비용", "연구비", "비용계", "과제", "연구개발실적")),
    "orders": ("수주", ("수주잔고", "수주총액", "기납품액", "수주현황")),
    "materials": ("원재료", ("원재료", "매입", "가격")),
}
CHAPTER = re.compile(r"^(VIII|VII|XII|III|VI|XI|IV|IX|II|V|X|I)[.．]\s*\S")
SUBHEADING = re.compile(r"^\d{1,2}[.．]\s*[^\d\W]")


def compact(value: str) -> str:
    return re.sub(r"[^\w]", "", value).casefold()


def tag(node) -> str:
    return node.tag.rsplit("}", 1)[-1].upper() if isinstance(node.tag, str) else ""


def cleaned(node) -> str:
    return " ".join(" ".join(node.itertext()).split())


@dataclass(frozen=True)
class Block:
    heading: str
    text: str
    table: bool = False


def table_text(node) -> str:
    rows = []
    for row in node.iter():
        if tag(row) != "TR":
            continue
        cells = []
        for cell in row:
            if tag(cell) in {"TD", "TH", "TE", "TU"}:
                span = " ".join(f"{attr}={value}" for attr, value in cell.attrib.items()
                                if attr.upper() in {"COLSPAN", "ROWSPAN"} and value != "1")
                cells.append(f"[{span}] {cleaned(cell)}" if span else cleaned(cell))
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def chapters(root) -> dict[str, list[Block]]:
    result = defaultdict(list)
    current = heading = ""

    def walk(node):
        nonlocal current, heading
        kind = tag(node)
        if kind in {"TABLE", "P", "TITLE", "SUBTITLE", "CAPTION", "PARA", "SPAN"}:
            if kind == "PARA" and any(tag(child) in {"P", "TABLE", "TITLE", "SUBTITLE"} for child in node):
                for child in node:
                    walk(child)
                return
            value = table_text(node) if kind == "TABLE" else cleaned(node)
            if not value:
                return
            match = CHAPTER.match(value) if kind != "TABLE" else None
            if match and len(value) < 140 and (kind == "TITLE" or node is root or current != match.group(1)):
                current, heading = match.group(1), ""
                return
            if current:
                if kind in {"TITLE", "SUBTITLE"} or SUBHEADING.match(value) and len(value) < 120:
                    heading = value
                else:
                    result[current].append(Block(heading, value, kind == "TABLE"))
            return
        for child in node:
            walk(child)

    if root is not None:
        walk(root)
    return dict(result)


def excerpt(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    cut = value[:limit - 2]
    if "\n" in cut:
        cut = cut.rsplit("\n", 1)[0]
    return cut + " …"


def chapter_evidence(blocks: list[Block], chapter: str, budget: int) -> list[dict]:
    terms = tuple(map(compact, CHAPTERS[chapter][1]))

    def relevance(index):
        block = blocks[index]
        heading, body = compact(block.heading), compact(block.text)
        return sum(3 * (term in heading) + (term in body) for term in terms)

    selected = {}
    for index in sorted(range(len(blocks)), key=relevance, reverse=True):
        if budget < 160 or len(selected) >= 8:
            break
        block = blocks[index]
        value = excerpt(block.text, min(640, budget - len(block.heading) - 35))
        selected[index] = {"heading": block.heading, "excerpt": value}
        budget -= len(value) + len(block.heading) + 35
    return [selected[index] for index in sorted(selected)]


def metric_evidence(blocks: list[Block], metrics: tuple[str, ...]) -> dict:
    result = {}
    for metric in metrics:
        section, labels = METRICS[metric]
        term = compact(section)
        names = tuple(map(compact, labels))
        candidates = []
        for index, block in enumerate(blocks):
            body = compact(block.text)
            heading = compact(block.heading)
            if not block.table and metric != "research":
                continue
            score = 2 * (term in heading) + (term in body) + sum(name in body for name in names)
            if score < 2 or not (term in heading or term in body) or not any(name in body for name in names):
                continue
            context = ""
            if index and blocks[index - 1].heading == block.heading and not blocks[index - 1].table:
                context = excerpt(blocks[index - 1].text, 180)
            candidates.append((score, index, {"heading": block.heading, "context": context,
                                              "excerpt": excerpt(block.text, 850)}))
        best = sorted(candidates, key=lambda row: (-row[0], row[1]))[:2]
        result[metric] = [item for _, _, item in sorted(best, key=lambda row: row[1])]
    return result
