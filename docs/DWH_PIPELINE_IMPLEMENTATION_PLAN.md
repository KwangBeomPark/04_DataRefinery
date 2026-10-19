# Data Refinery DWH 파이프라인 구현 및 다단계 검수 계획서

## 1. 개요 및 목표
글로벌 ERP/SCM 원천 데이터에서 폴란드 법인(PL) 데이터를 고속 격리·정제하여, 로컬 경량 데이터 웨어하우스(DuckDB) 기반의 스타 스키마(Star Schema)와 BI/AI(LLM Text-to-SQL) 분석용 데이터 마트를 구축하는 고성능 자동화 ETL 파이프라인을 구축합니다.

---

## 2. 아키텍처 및 디렉토리 구조
```text
04_DataRefinery/
├── config/
│   ├── settings.yaml            # 법인 필터(PL), 통화(PLN, EUR), 디렉토리 경로, DuckDB 경로
│   └── account_mapping.yaml     # 100여 개 세부 비용 계정 롤업 및 P&L 워터폴 매핑 규칙
├── data/
│   ├── 01_raw/
│   │   ├── masters/             # master_customers.csv, master_products.csv
│   │   ├── pnl/                 # raw_pnl_*.csv
│   │   ├── inventory/           # raw_inventory.csv (확장용)
│   │   └── price/               # raw_market_price.csv (확장용)
│   ├── 02_staging/              # stg_*.parquet
│   ├── 03_warehouse/            # warehouse.duckdb (staging, core, mart 스키마)
│   └── 04_mart_export/          # Excel Power Query 연동용 Parquet 파일
├── logs/                        # 실행 로그 및 Fail-Fast 미매핑 코드 리포트
├── src/
│   ├── config_loader.py         # YAML 설정 로더 및 유효성 검증
│   ├── extractor.py             # Polars 기반 고속 필터링 및 Staging 적재
│   ├── dimension_builder.py     # 공식 마스터 기반 Dim 생성 및 Surrogate Key 발급 (SCD Type 1)
│   ├── fact_builder.py          # Anti-Join 기반 Strict Fail-Fast 무결성 검증 및 팩트 생성
│   └── loader.py                # DuckDB 3단계 스키마 관리, OBT View, Parquet Export
├── scripts/
│   └── generate_sample_data.py  # 테스트용 더미 마스터 및 100+ 컬럼 P&L 생성기
├── main.py                      # 전체 파이프라인 CLI 엔트리포인트 (친절한 콘솔 UX)
└── tests/
    └── test_dwh_core_pipeline.py# 전체 파이프라인 무결성 검증 테스트
```

---

## 3. 핵심 비즈니스 규칙 및 구현 원칙
1. **마스터 파일 기반 차원 생성**: `bill_to_code`, `model_code`를 자연키로 정렬/중복제거 후 1부터 시작하는 `Int32` 대리키 부여.
2. **SCD Type 1 정책**: 마스터 변경 시 최신 속성으로 단순 덮어쓰기 (`CREATE OR REPLACE TABLE`).
3. **Strict Fail-Fast 외래키 무결성**: 마스터에 없는 코드가 1건이라도 발견되면 즉시 파이프라인 중단(`ValueError`), 누적 에러를 `logs/unmapped_errors_{timestamp}.csv`로 덤프.
4. **100여 개 비용 계정 롤업 외부화**: `account_mapping.yaml`에 따라 워터폴(총매출, 매출원가, 매출총이익, 물류비, 판촉비, 공헌이익, 고정비, 영업이익) 자동 합산.
5. **Excel Power Query 연동**: 드라이버 설치 없이 즉시 읽을 수 있는 `.parquet` 자동 내보내기.

---

## 4. 다단계 교차 검수 계획 (총 8회)
- **기능 검수 (Functional Review)**:
  - [1차] Claude Opus 5.5 검수 ➔ 수정/보완
  - [2차] Codex Sol 6.1 검수 ➔ 수정/보완
  - [3차] Claude Opus 5.5 검수 ➔ 수정/보완
  - [4차] Codex Sol 6.1 검수 ➔ 최종 기능 승인
- **UI / UX 검수 (UI/UX Review & Refinement)**:
  - [1차] Claude Opus 5.5 검수 ➔ 수정/보완
  - [2차] Codex Sol 6.1 검수 ➔ 수정/보완
  - [3차] Claude Opus 5.5 검수 ➔ 수정/보완
  - [4차] Codex Sol 6.1 검수 ➔ 최종 UI/UX 승인
- **최종 검증 및 아침 보고서 작성**
