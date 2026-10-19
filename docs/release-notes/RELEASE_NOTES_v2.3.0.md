# Data Refinery v2.3.0

폴란드 법인(PL) 로컬 경량 데이터 웨어하우스(Kimball Star Schema DWH & DuckDB) 파이프라인과 비정규화 OBT AI Mart 평면 뷰, 그리고 비개발자 실무자도 1분 만에 엑셀 파워쿼리(Power Query)로 연동할 수 있는 신규 전용 GUI 탭(`DWH & 손익 마트`)을 공식 도입합니다.

## 주요 변경 사항

### 1. Kimball Star Schema DWH & AI Mart 코어 파이프라인 구축
- **단일 DuckDB 3단계 스키마 격리**: `data/03_warehouse/warehouse.duckdb` 내 `staging`, `core`, `mart` 스키마를 논리적으로 분리하고 트랜잭션(`BEGIN` ~ `COMMIT`)으로 원자적 적재를 보장합니다.
- **공식 마스터 기반 Conformed Dimensions (SCD Type 1)**:
  - 실적 데이터에서 임의로 추출하지 않고 `data/01_raw/masters/`의 공식 파일(`master_customers.csv`, `master_products.csv`)을 기준으로 차원을 생성합니다.
  - 단일 청구처 코드(`bill_to_code`), 모델 코드(`model_code`) 기준 1부터 시작하는 정수형 대리키(`Int32`)를 부여하며 최신 속성으로 덮어씁니다.
- **Strict Fail-Fast 외래키 무결성 (Anti-Join 검증)**:
  - P&L 실적에 마스터에 없는 유령 코드가 1건이라도 발견되면 즉시 파이프라인을 중단(`ValueError`)합니다.
  - 미매핑 코드 전체 리스트는 `logs/unmapped_errors_{timestamp}_{uuid}.csv`로 자동 덤프되어 마스터 보완을 지원합니다 (NULL 외래키도 완벽 감지).
- **114개 엔터프라이즈 비용 계정 롤업 외부화**:
  - `config/account_mapping.yaml` 기반으로 100여 개 세부 비용을 표준 P&L 워터폴(매출액 ➔ 매출원가 ➔ 매출총이익 ➔ 변동비 ➔ 공헌이익 ➔ 고정비 ➔ 영업이익)로 자동 합산·정규화합니다.
  - 원천 소계(`is_subtotal: true`)는 이중 합산에서 원천 제외되며, `round(2)` 부동소수점 오차 방지가 적용되었습니다.
- **초고속 Zero-Copy & 벡터화 아키텍처**:
  - DuckDB `read_parquet(?)` Zero-Copy 로딩과 Polars 벡터화 FX Join을 결합하여 전체 파이프라인 E2E 처리를 0.43초대에 주파합니다.
- **엑셀 Power Query 연동용 Parquet 및 AI 메타데이터 자동 내보내기**:
  - `data/04_mart_export/`에 비정규화 평면 뷰(`mart.v_ai_pnl_summary.parquet`)를 자동 생성하여 엑셀에서 드라이버 없이 즉시 열람 가능합니다.
  - LLM Text-to-SQL 및 에이전트 연동용 스키마 메타데이터 사전(`ai_schema_dictionary.json`, `.md`)을 생성합니다.

### 2. 신규 GUI 탭 `DWH & 손익 마트 (DWH & P&L Mart)` 도입
- **직관적인 1 ➔ 2 ➔ 3 단계 카드 구조**:
  - **1단계: 원천 데이터 및 마스터 확인**: 공식 마스터 및 P&L 원천 파일 자동 감지, 미보유 시 `[✨ 샘플 원천 데이터 자동 생성]` 버튼으로 현실적인 100+ 컬럼 테스트 데이터 즉시 생성 지원.
  - **2단계: 로컬 DWH 빌드 (DuckDB Star Schema)**: 원천 누락 시 빌드 버튼 자동 비활성화(Step-Gating) 및 5단계 진행상황(Staging ➔ Dimensions ➔ Integrity ➔ Fact P&L ➔ OBT Parquet) 실시간 체크리스트 시각화. Fail-Fast 발생 시 에러 카드 및 `[📄 오류 리포트 열기 (CSV)]` 즉시 제공.
  - **3단계: 엑셀 Power Query 연동 및 배포**: 빌드된 Parquet 크기/행수 표시, `[📂 배포 폴더 열기]`, `[📋 Parquet 경로 복사]`, 그리고 엑셀에서 파워쿼리로 1분 만에 연결하는 단계별 가이드 안내.

### 3. 업무 흐름 중심의 5개 탭 전면 재배치
1. `CSV 구조 복구` (`tab_csv`)
2. `프로모션 템플릿` (`tab_promotion`)
3. `DWH & 손익 마트` (`tab_dwh` - 신규!)
4. `데이터셋 배포` (`tab_dataset`)
5. `데이터 집계·슬라이서` (`tab_aggregator`)

### 4. 다국어(ko / en / pl) 및 콘솔 CLI UX 고도화
- 영문(`en`) 및 폴란드어(`pl`) 모드 전환 시 UI 전역 잔존 한글 0건 정규식 검증 통과.
- `main.py` CLI에 `--lang ko/en/pl` 및 `--dry-run`(무결성 점검 모드) 지원.

### 5. 코드 품질 및 안정성
- 전체 단위/통합 테스트(584개) 및 1,000,000행 벤치마크 테스트 통과.
- Inno Setup 패키징 및 인스톨러 무결성 스모크 테스트 통과.

## 배포 및 설치
- 설치 위치: `%LOCALAPPDATA%\Programs\Data Refinery`
- 사용자 설정·로그·데이터셋 DB: `%LOCALAPPDATA%\Programs\Data Refinery\UserSetting` (업데이트 시 안전하게 보존)
- 공식 배포 파일: `App04_DataRefinery_Setup_v2.3.0.exe`
