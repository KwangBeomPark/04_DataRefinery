# 🏛️ Data Refinery DWH & 손익 마트: 초보자 완벽 실전 가이드 (A to Z)

> **Languages / 언어 / Języki**:
> - 🇰🇷 [한국어 실전 가이드 (Korean Guide)](file:///c:/Dev/GitHub/04_DataRefinery/docs/DWH_BEGINNER_GUIDE_ko.md)
> - 🇺🇸 [English Practical Guide](file:///c:/Dev/GitHub/04_DataRefinery/docs/DWH_BEGINNER_GUIDE_en.md)
> - 🇵🇱 [Poradnik w Języku Polskim (Polish Guide)](file:///c:/Dev/GitHub/04_DataRefinery/docs/DWH_BEGINNER_GUIDE_pl.md)

---

## 📑 목차
1. [DWH와 손익 마트, 1분 만에 이해하기](#1-dwh와-손익-마트-1분-만에-이해하기)
2. [사전 준비: 필요한 원천 파일 3종 규격](#2-사전-준비-필요한-원천-파일-3종-규격)
3. [초보자가 가장 많이 하는 실수 5가지와 방지 팁 (필독!)](#3-초보자가-가장-많이-하는-실수-5가지와-방지-팁-필독)
4. [단계별 실행 가이드 (1단계 ~ 3단계)](#4-단계별-실행-가이드-1단계--3단계)
5. [Microsoft Excel 파워쿼리 1초 직결법](#5-microsoft-excel-파워쿼리-1초-직결법)
6. [에러 발생 시 자가 해결(Troubleshooting) 매뉴얼](#6-에러-발생-시-자가-해결troubleshooting-매뉴얼)

---

## 1. DWH와 손익 마트, 1분 만에 이해하기

### 💡 왜 일반 엑셀 파일 대신 DWH를 만들어야 할까요?
* **엑셀의 104만 행 한계 극복**: ERP 실적 데이터가 수십만~수백만 행으로 늘어나면 엑셀이 멈추거나 파일이 손상됩니다. Data Refinery는 고성능 분석 엔진(**DuckDB**)을 사용하여 1,000,000행도 단 2초 만에 분석합니다.
* **마스터 데이터 무결성 보장 (Fail-Fast)**: 실적 데이터에 아직 등록되지 않은 신규 거래처나 신제품 코드가 섞여 있으면, 숫자가 누락되거나 이익이 왜곡됩니다. Data Refinery는 이를 사전에 감지하여 계산 오류를 원천 차단합니다.
* **자동 워터폴 P&L 손익계산서**: 100여 개가 넘는 복잡한 ERP 비용 계정을 표준 재무 구조(매출액 ➔ 매출원가 ➔ 물류비 ➔ 판촉비 ➔ 공헌이익 ➔ 영업이익)로 자동 롤업 합산합니다.

```
[원천 데이터 (CSV)]
 ├── 고객 마스터 (master_customers.csv)
 ├── 제품 마스터 (master_products.csv)
 └── P&L 실적 (raw_pnl_*.csv)
       │
       ▼ [Data Refinery 1-Click 파이프라인]
 ├── 1. Staging (법인 격리 및 유효성 검사)
 ├── 2. Dimensions (표준 마스터 차원 구축, SCD Type 1)
 ├── 3. Integrity (미등록 코드 Anti-Join 무결성 검증)
 ├── 4. Fact P&L (114개 계정 워터폴 손익 롤업 및 FX 환산)
 └── 5. Serving OBT View (Fan-out 없는 분석용 단일 마트)
       │
       ▼ [초고속 컬럼 저장소 (Parquet)]
[Microsoft Excel Power Query / Power BI / AI 분석]
```

---

## 2. 사전 준비: 필요한 원천 파일 3종 규격

DWH를 구축하려면 기본적으로 아래 **3가지 CSV 파일**이 준비되어야 합니다:

### ① 고객 마스터 파일 (`data/01_raw/masters/master_customers.csv`)
* `bill_to_code`, `customer_name`, `channel_group`, `country_code`, `city`

### ② 제품 마스터 파일 (`data/01_raw/masters/master_products.csv`)
* `model_code`, `product_name`, `division`, `category`

### ③ P&L 실적 파일 (`data/01_raw/pnl/*.csv`)
* `period`, `entity_code`, `bill_to_code`, `model_code`, `currency_code`, `sales_qty` + 계정 컬럼들

---

## 3. 초보자가 가장 많이 하는 실수 5가지와 방지 팁

1. **엑셀에서 저장할 때 반드시 CSV UTF-8 선택**
2. **거래처/품목 코드 앞자리 '0' 보존 (텍스트 서식 유지)**
3. **기간(Period) 포맷은 `YYYY-MM` (`2026-01`) 표준 준수**
4. **마스터 파일 선행 등록 원칙 (신규 거래처/신제품 등록 후 실적 빌드)**
5. **엑셀로 파일을 열어둔 채 빌드하지 않기 (Permission Denied 방지)**

---

## 4. Microsoft Excel 파워쿼리 1초 직결법
1. 카드 3에서 **`[⚡ Power Query M-코드 복사]`** 클릭.
2. Excel ➔ **[데이터] ➔ [데이터 가져오기] ➔ [기타 원본에서] ➔ [빈 쿼리]** 클릭.
3. 상단 **[고급 편집기]**를 누르고 기존 내용을 지운 뒤 붙여넣기(`Ctrl + V`) ➔ [완료].
4. **[닫기 및 다음으로 로드... ➔ 피벗 테이블 보고서]** 선택.
