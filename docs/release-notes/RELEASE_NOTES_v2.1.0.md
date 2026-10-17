# Data Refinery v2.1.0

Kimball 스타 스키마 기반 경량 데이터 웨어하우스(DWH) 파이프라인 및 AI 분석용 마트 자동 생성 기능(`src/dwh/`)을 도입합니다.

## 경량 DWH 및 AI 마트 파이프라인 (`src/dwh/`)

- **다중 소스 통합 및 법인 격리**:
  - ERP 손익(P&L), SCM 재고, 유통 채널별 시장 가격 데이터를 고속 추출·정제합니다.
  - 글로벌/범유럽 데이터에서 지정된 법인(폴란드 `PL`) 및 허용 통화(`PLN`, `EUR`), 기간(예: `2026-Q1`)만 고속 필터링합니다.
  - 모든 문자열 원천 코드를 VARCHAR로 유지하여 12자리 고객 코드(`000100200301`) 등의 선행 `0` 유실을 방지합니다.

- **Kimball 버스 아키텍처 및 영속적 대리키(Surrogate Key) 관리**:
  - 공통 차원(`Dim_Date`, `Dim_Customer`, `Dim_Product`, `Dim_Account`, `Dim_Warehouse`, `Dim_Channel`)을 여러 팩트 테이블이 공유합니다.
  - `dim_key_registry` 영속 테이블을 통해 자연키별 양수 정수 대리키를 최초 1회만 발급하며 재실행 시에도 절대 재할당되지 않습니다.
  - 마스터 데이터의 대소문자 차이 충돌을 자동 감지하고 제자리 정규화하며, 유일성을 엄격히 검증합니다.

- **워터폴 손익 팩트 및 미매핑 코드 자동 격리**:
  - `Fact_PnL_Long` 및 단계별 워터폴(총매출 → 매출차감 → 순매출 → 매출원가 → 매출총이익 → 변동비 → 공헌이익 → 고정비 → 영업이익) 요약 `Fact_PnL`을 현지통화(`_local`)와 보고통화(`_reporting`)로 동시 생성합니다.
  - 마스터에 등록되지 않은 신규 코드는 파이프라인을 중단하지 않고 대리키 `-1`(`UNKNOWN`)로 매핑한 후 `logs/unmapped_codes.csv`에 원천 행 수 및 영향 금액을 격리 로깅합니다.

- **Fan-out 방지 Multipass(Drill-Across) AI 마트 뷰 및 스키마 사전**:
  - 거래선별 P&L, 창고별 재고, 채널별 가격 팩트를 공통 그레인`(date_sk, period, entity_code, product_sk, product_code, reporting_currency)`으로 CTE 선집계 후 `FULL OUTER JOIN`하여 팬아웃(데이터 뻥튀기) 없는 평면 마트 뷰 `v_ai_mart_pnl_inventory_price`를 제공합니다.
  - 팩트와 뷰 간 14개 손익 지표, 4개 재고 지표, 9개 가격 지표 및 거래선/창고/채널 카운트 전수 대사를 자동 검증합니다.
  - LLM 및 AI 에이전트의 Text-to-SQL 즉시 연동을 위한 메타데이터 사전(`ai_schema_dictionary.json`, `ai_schema_dictionary.md`)을 자동 내보내기합니다.

- **원자적 작업공간 승격 및 롤백 보호**:
  - 임시 스테이징 디렉터리(`.run_stage_<uuid>`)에서 전체 파이프라인을 완료한 후 라이브 작업공간으로 한 번에 원자적 승격하며, 실패 시 이전 상태를 무손실 롤백합니다.
  - 데모 모드와 운영 모드, 법인 간 작업공간 침범을 방지하는 안전 검증을 적용합니다.

- **CLI 진입점 (`mart-run`)**:
  - 번들된 가상 샘플 데이터 실행: `python -m src.data_refinery mart-run --demo --entity PL --period 2026-Q1 --json`
  - 운영 데이터 실행: `python -m src.data_refinery mart-run --entity PL --period 2026-Q1 --input-dir <원천폴더> --fx-rates <환율파일>`

## 배포 및 안전성

- 설치 위치는 `%LOCALAPPDATA%\Programs\Data Refinery`, 설정·로그·작업 DB는 `UserSetting`에 보존됩니다.
- 공식 단일 셋업 파일은 `App04_DataRefinery_Setup_v2.1.0.exe`이며, `build-manifest.json` 및 `SHA256SUMS.txt`와 함께 릴리즈됩니다.
