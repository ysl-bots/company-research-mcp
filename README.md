# Company Research MCP

코스피 상장법인의 정기공시(OpenDART), 공식 웹사이트, 외부 출처 자료에서 지원동기 작성과 면접 준비에 필요한 근거를 수집해 반환하는 원격 MCP 서버입니다. 서버는 근거와 출처 링크만 반환하며, 브리핑 작성은 MCP 호스트(Claude 등)의 모델이 담당합니다. API 키 발급이나 회원가입 없이 사용할 수 있습니다.

## 연결

MCP 클라이언트에 원격 서버를 추가할 때 아래 값을 입력하세요.

| 항목 | 값 |
| --- | --- |
| 전송 방식 | Streamable HTTP |
| 서버 주소 | `https://company-research-mcp.yscoder3893.workers.dev/mcp` |
| 인증 | 없음 (읽기 전용) |

연결 후 채팅 요청 예시:

> 삼성전자 연구개발 직무 면접을 준비 중이야. 2025년 사업보고서와 2026년 반기보고서로 회사 방향성과 예상 질문을 정리하고, 근거마다 공시 링크를 붙여줘.

## 제공 기능 요약

도구는 자료의 출처별로 나뉘어 있습니다. 호스트의 모델이 질문에 맞는 도구를 골라 조합합니다.

| 도구 | 출처 | 입력 | 반환 내용 |
| --- | --- | --- | --- |
| `search_companies` | — | `query` | DART 고유번호(`corp_code`)와 조회 가능한 연도 |
| `get_dart_filings` | OpenDART 정기공시 | `corp_code`, `year?`, `include_optional?`, `role?` | 사업보고서·반기보고서의 발췌, 재무, 직원 근거 |
| `get_official_site` | 회사 공식 웹사이트 | `corp_code`, `topics?` | 기업 소개, 채용·인재, 보도자료, 블로그, 컨퍼런스, IR 자료 |
| `get_external_coverage` | 공식 웹사이트 밖의 출처 | `corp_code` | 증권사 리포트, KIND·DART 공시 페이지, 증권사 주최 컨퍼런스, 외부 IR 사이트, SNS 게시물 |

호출 순서:

1. `search_companies`로 `corp_code` 확인
2. 필요한 출처의 도구에 `corp_code` 전달
3. 모델이 반환된 근거를 조합해 답변 작성

근거의 신뢰 수준은 공시, 공식 웹사이트, 외부 출처 순으로 높습니다.

## 기능 상세

<details>
<summary><b><code>search_companies</code></b> — 회사 검색</summary>

| 입력 | 형식 | 설명 |
| --- | --- | --- |
| `query` | 문자열 (1–100자) | 한글·영문 회사명, 6자리 종목코드, 8자리 DART 고유번호 중 하나 |

- 공백과 대소문자를 구분하지 않는 부분 일치 검색
- 회사명·종목코드·고유번호가 정확히 일치하는 회사를 먼저 표시
- 최대 15건 반환
- 결과 필드: `corp_code`, 회사명, 종목코드, `available_years`(조회 가능한 연도, 최신순)
- 예: `삼성전자` → `corp_code` `00126380`

</details>

<details>
<summary><b><code>get_dart_filings</code></b> — DART 정기공시</summary>

| 입력 | 형식 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `corp_code` | 8자리 숫자 | — | `search_companies`가 반환한 고유번호 |
| `year` | 정수 | 생략 | 보고기간 종료일의 연도. 생략 시 2025년 사업보고서와 2026년 반기보고서를 함께 반환 |
| `include_optional` | 불리언 | `false` | `true`이면 원재료·감사의견·계열회사 발췌 추가 |
| `role` | 문자열 | `""` | 지원 직무(예: `연구개발`). 직무와 관련된 임원 경력 사례를 앞쪽에 배치 |

**반환하는 공시 범위**

| 구분 | 포함 항목 |
| --- | --- |
| 기본 | I·II·IV·XI장 발췌, III장 주요 재무계정, VIII장 직원 현황과 임원 경력 사례, II장의 제품 매출·연구개발·수주 표 |
| 선택 (`include_optional=true`) | II장 원재료, V장 감사의견, IX장 계열회사 발췌 |
| 제외 | VI·VII·X·XII장 원문 (최대주주가 변경된 경우에만 변경 사실 제공) |

**응답 해석 시 유의사항**

- 보고서마다 제목, 공시 원문 링크(`source`), 공시 상태(`status`)가 포함됩니다. 해당 기간의 보고서가 아직 공시되지 않았으면 `status`에 미공시 상태가 표시됩니다.
- 12월 결산법인이 아닌 회사는 그 회사의 결산월을 기준으로 보고서를 조회합니다.
- 사업보고서의 재무 수치는 1년 누적, 반기보고서의 금액은 6개월 누적 기준입니다. 두 값을 직접 비교하지 마세요.
- 기재정정 공시가 있으면 가장 최근 정정본을 사용합니다. 본문과 구조화 데이터의 접수번호가 서로 다르면 해당 데이터에 불일치 상태가 표시됩니다.
- 제품 매출·연구개발비·수주 표는 회사마다 양식이 달라 원문 표를 그대로 발췌하며, 발췌 분량에는 상한이 있습니다.
- `fetched_at`은 서버가 해당 자료를 수집한 시각(UTC)입니다.

</details>

<details>
<summary><b><code>get_official_site</code></b> — 회사 공식 웹사이트</summary>

| 입력 | 형식 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `corp_code` | 8자리 숫자 | — | DART 고유번호 |
| `topics` | 문자열 배열 | 전체 | 반환할 항목. `overview`, `talent`, `press_release`, `blog`, `conference`, `ir` 중 선택 |

| 응답 필드 | 내용 |
| --- | --- |
| `checked_date` | 보도자료·블로그·컨퍼런스·IR 자료의 수집 기준일 |
| `overview` | `homepage_url`(공식 홈페이지), `status`, `items`(기업 소개 근거) |
| `talent` | `careers_url`(공식 채용 페이지), `careers_source_url`(근거 원문 페이지), `careers_portal_reference`(외부 채용 포털, 참고용), `status`, `items`(인재상·채용 절차·업무문화 근거) |
| `press_release`, `blog`, `conference` | `items`(자료 목록), `missing_reason`(자료를 확보하지 못한 사유) |
| `ir` | 위 필드에 더해 `ir_home`(IR 홈), `ir_library`(IR 자료실) |

공식 웹사이트 자료로 분류하는 기준은 자료 URL의 도메인입니다. 회사 홈페이지·채용 페이지·IR 홈과 같은 도메인(서브도메인 포함)에 있는 자료만 이 도구가 반환합니다. 예를 들어 `news.samsung.com`은 `samsung.com`의 서브도메인이므로 삼성전자 공식 자료로 분류됩니다. KIND, DART, 외부 IR 대행 사이트, SNS에 있는 자료는 회사 웹사이트로 등록되어 있어도 `get_external_coverage`로 분류됩니다.

`talent`의 채용 절차와 업무문화 자료는 회사가 공식 발표한 인재상과 별개입니다. 회사가 인재상을 명시한 페이지를 찾지 못한 경우 `status`가 `unverified`로 표시되므로, 채용 절차나 업무문화 자료를 인재상으로 인용하지 마세요.

</details>

<details>
<summary><b><code>get_external_coverage</code></b> — 외부 출처</summary>

| 입력 | 형식 | 설명 |
| --- | --- | --- |
| `corp_code` | 8자리 숫자 | DART 고유번호 |

| 응답 필드 | 내용 |
| --- | --- |
| `checked_date` | 수집 기준일 |
| `items` | 외부 출처 자료 목록. 항목마다 `kind`(자료 유형), `url`, `date`, `evidence`(요약) 포함. 증권사 리포트는 `broker`, `analysts` 포함 |
| `analyst_report_missing_reason` | 증권사 리포트를 확보하지 못한 사유 |

- 포함 대상: 증권사 애널리스트 리포트, KIND·DART 공시 페이지, 증권사 주최 컨퍼런스, 외부 IR 대행 사이트, SNS 게시물
- 회사의 공식 입장이 아닌 제3자 자료이므로 인용할 때 출처를 밝히세요.
- 수집된 외부 자료가 적어 대부분 기업에서 `items`가 빈 목록입니다.

</details>

## 근거 확인 상태

`get_official_site`의 `overview.status`와 `talent.status` 값:

| 상태 | 의미 |
| --- | --- |
| `reviewed` | 공식 페이지 원문과 대조해 검토를 마친 근거 |
| `candidate` | 공식 웹사이트에서 자동 수집했으나 검토하지 않은 근거 |
| `unverified` | 근거 자료를 찾지 못했거나 출처를 확인하지 못한 상태 |

`candidate` 근거를 인용하기 전에는 출처 링크를 열어 원문 내용을 직접 확인하세요.

## 데이터 범위

- 대상 기업: 현재 코스피 상장법인
- 공시 기간: 2025년 사업보고서, 2026년 반기보고서
- 공식 웹사이트·외부 출처 자료: 2025년 11월 21일 ~ 2026년 11월 21일 게시 자료, 수집을 마친 기업부터 순차 추가
- 출처: [OpenDART](https://opendart.fss.or.kr/), 각 기업 공식 웹사이트, 증권사·거래소 등 외부 사이트
- 모든 도구는 읽기 전용이며, 서버는 수집한 원문 안의 지시문을 실행하지 않습니다.
