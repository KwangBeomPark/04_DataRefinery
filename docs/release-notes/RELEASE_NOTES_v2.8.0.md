# Data Refinery v2.8.0

1탭(손익 마트 생성)에 **3단계 통합 워크플로(차원 분리 → 계정 매핑 → 목적별 마트 추출)**와 **대용량 와이드 테이블(218열+) · 복합/자동 행 식별키 · 중복 행 합산(Group & Sum)**을 탑재하고, 앱 실행 시 **백그라운드 자동 업데이트 확인과 서명 검증 기반 원클릭 무음 업그레이드 및 자동 재실행** 기능을 추가했습니다.

## 주요 변경 사항

### 1. 1탭 3단계 통합 워크플로 및 계정 매핑 · 마트 레시피 엔진
- **3단계 서브탭 작업공간 (`src/qt/widgets/mart_workspace.py`)**: `Step 1. 원천 적재 및 차원 분리 (DB 생성)` → `Step 2. 계정 과목 매핑 테이블 관리` → `Step 3. 목적별 데이터 마트 추출` 흐름으로 데이터베이스 구축부터 매핑 관리, 레시피별 추출까지 한 화면에서 연속 처리할 수 있습니다.
- **계정 과목 매핑 스튜디오 (`src/mart/mapping.py`)**: 대분류 · 중분류 · 소분류 · 부호(`+`/`-`) · 소계 제외 여부를 관리하고, 키워드 기반 자동 분류 추천, Excel(XLSX)/CSV 내보내기 및 가져오기, DuckDB 내 `mart_account_mapping` 테이블 동기화를 지원합니다.
- **목적별 마트 레시피 3모드 및 자동 대사 (`src/mart/recipes.py`)**:
  - `wide_rollup`: 대분류·중분류·소분류 기준 요약 손익 피벗 추출
  - `long_detail`: 원가·변고정비 등 계정 상세 분석용 Long형 추출
  - `wide_raw`: 원천 계정 열을 유지한 분석용 Wide형 추출
  - 추출 시 `Decimal` 오차 0 검증 기반 자동 대사(Reconciliation) 및 원클릭 프리셋을 제공합니다.
- **설계도 복제 (`새 파일/새 DB`)**: 이미 구성한 스타 스키마·매핑·레시피 설정을 새로운 원천 폴더와 작업 DB에 그대로 복제하여 반복 설정 부담을 줄였습니다.

### 2. 대용량 와이드 테이블(218열+) · 복합/자동 행 식별키 · 중복 행 합산
- **TSV 및 확장 스키마 지원**: `.tsv` 구분자 자동 감지, 256열 확장 용량 한도, 과학적 지수 표기(`1E-05`)에 대한 무손실 `DECIMAL` 정밀도 추론(`infer_decimal_precision_scale`)을 적용했습니다.
- **유연한 행 식별키 정책**: 다중 컬럼 복합 행 식별키와 자동 행 고유키(`__auto_row_key__`)를 지원하며, 원천 데이터 중복 행 감지 시 `중복 행 합산 (Group & Sum)` 모드로 원클릭 전환할 수 있습니다.
- **레거시 DWH 설정 자동 시딩**: 번들 실행이나 임의 작업 디렉토리에서 실행해도 기존 DWH 설정(`dwh_legacy`)을 `UserSetting` 경로로 안전하게 초기화합니다.

### 3. 인앱 자동 업데이트 확인 및 서명 검증 원클릭 무음 업그레이드
- **백그라운드 자동 확인 및 원클릭 배지 (`src/qt/main_window.py`)**: 앱 시작 시 백그라운드에서 GitHub 최신 릴리스를 24시간 주기로 자동 확인(메뉴 내 `새 버전 자동 확인` 토글 제공)하며, 새 버전 발견 시 상단에 `⚡ v<version> 업데이트 설치` 배지 버튼을 표시합니다.
- **디지털 서명 검증 및 자동 재실행 (`src/qt/widgets/update_dialog.py`, `installer/setup.iss`)**: 설치 파일 다운로드 완료 직후와 실행 직전 공식 배포 인증서(`TRUSTED_SIGNER_THUMBPRINT`) 서명을 검증한 뒤, 무음 설치(`/SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /AUTORELAUNCH=1`) 후 새 버전 앱을 자동으로 재실행합니다.

### 4. 품질 및 테스트 안정성 강화
- 레거시 Tkinter 테스트 종료 시 스레드 회수 및 메인 스레드 GC 정리를 강화하여 통합 테스트 안정성을 개선했습니다 (`src/data_refinery.py`, `src/background_jobs.py`).

## 배포 및 설치
- 설치 위치: `%LOCALAPPDATA%\Programs\Data Refinery`
- 사용자 설정·로그·데이터셋 DB: `%LOCALAPPDATA%\Programs\Data Refinery\UserSetting` (업데이트 시 안전 보존)
- 공식 배포 파일: `App04_DataRefinery_Setup_v2.8.0.exe`
