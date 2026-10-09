*다른 언어로 읽기: [English](README.md), [한국어](README.ko.md)*

# Data Refinery

![Data Refinery 처리 흐름: 원본 파일을 복구하고 분석 가능한 구조의 데이터로 정리합니다](assets/images/manual-data-refinery.png)

> **데이터를 정리·정규화하여 분석 가능한 형태로 준비합니다.**

Data Refinery는 복잡하거나 깨진 원본 데이터를 분석에 바로 쓸 수 있는
형태로 정리하는 Windows 데스크톱 앱입니다. CSV의 깨진 레코드를 복구하고,
프로모션 규칙을 정규화된 기준 데이터로 보존하며, 분석이 필요할 때 일별
시계열 데이터를 생성합니다.

이 앱은 여러 데이터 정규화 기능을 모으는 기반입니다. 향후 가격 정보와
기타 업무 데이터용 템플릿을 추가해도 프로모션 기준 데이터와 섞이지 않게
설계합니다.

## 현재 기능 (v2.0.1)

- **PySide6 (Qt) 기반 모던 데스크톱 UI** — 레거시 Tkinter에서 벗어나 Windows 고해상도(Hi-DPI)에
  완벽 대응하는 현대적인 디자인 시스템(Primary Blue 테마, 다크 헤더, 모던 카드, 배지, 스텝퍼)을 적용했습니다.
  하위 호환성을 위해 `--legacy-tk` 옵션도 유지합니다.
- **데이터셋 배포 UI/UX 대혁신 (3단계 마법사)**:
  - **1단계: 파일 선택 & 키워드 칩 필터**: 입력 폴더 내 CSV 목록을 즉시 탐색하고,
    `+ 포함 키워드(PL, sales 등)` 및 `- 제외 키워드(backup 등)` 태그 칩 필터와 체크박스 멀티 선택으로 대상을 손쉽게 선별합니다.
    베이스라인 대비 헤더 순서 및 필수 열 누락 여부를 실시간 사전 검사합니다.
  - **2단계: 1,000행 샘플 프로파일러 & Auto-Guess 추천**: 수기 입력 없이 상위 1,000행을
    고속 분석하여 기준년월(Period), 식별키(Key), 수치측도(Numeric), 일반속성(General) 역할을 자동 판별·추천합니다.
  - **3단계: 복합키 중복 진단**: 선택된 복합키 결합 시 샘플 내 중복 행 충돌 여부를 실시간 검증합니다.
- **유럽식 숫자(폴란드 쉼표 소수점) 왜곡 방지** — `1 234,56`, `12,34`와 같은 유럽/폴란드식 쉼표
  소수점이 미국식 처리로 인해 100배로 튀던 문제를 `make_numeric_sql_expr`로 완전 패치했습니다.
- **CSV 구조 복구 및 불량 행 안전성** — 따옴표 없이 셀 안 줄바꿈으로 쪼개진
  레코드를 복구하고 구분 기호·인코딩을 자동 감지하며 잘못된 상단 행을 제외합니다.
  기대 열 수를 초과하는 행은 명확한 행 번호 진단과 함께 처리를 거부하고 불완전한
  결과 파일을 생성하지 않으며, 정상 데이터는 CSV 또는 Excel 파일로 저장합니다.
- **영문·폴란드 숫자 인식** — `1,234.56`, `1 234,56`, `1.234,56` 표기를
  안전하게 처리하고, 소수점 자릿수와 Excel의 큰 숫자 정밀도를 보호합니다.
- **다국어/동유럽 인코딩 지원 및 정밀 자가 치유(Self-Healing)** — 체코어, 폴란드어 등
  중앙/동유럽 데이터(Windows-1250, ISO-8859-2, CP852)의 특수 문자를 멀티 리전 샘플링으로 자동 감지합니다.
  집계기 UI에서 수동 인코딩을 지정할 수 있으며, 인코딩 불일치 발생 시 원클릭 정밀 모드 재실행 대화상자로 유도합니다.
- **프로모션 시계열 정규화 및 실패 처리** — `Promotion_Master`, `Support_Rules` 시트가
  있는 Excel 템플릿을 검증하고 중복된 비어있지 않은 열 헤더가 있는 템플릿을 안전하게 차단합니다.
- **데이터 집계 및 실패 처리** — CSV 행 그룹화, 필터, 열별 집계 함수, 계산 열,
  표본 샘플 미리보기 명시 표기 및 재사용 가능한 프리셋을 제공합니다.
- **데이터셋 누적·검수·배포** — DuckDB로 월별 CSV 데이터를 누적하고, 수정 월은 교체하며,
  기간·행 수·금액 합계와 오류를 검수한 뒤 승인된 결과만 공유 폴더에 CSV로 배포합니다.
  Excel 분석 템플릿에는 공유 경로의 Power Query 연결을 넣어 Microsoft 365 Excel에서 새로고침할 수 있습니다.
- **실시간 다국어 지원** — 한국어, 영어, 폴란드어 간 즉각적인 전환을 지원합니다.
- **빠른 사용자별 설치** — `%LOCALAPPDATA%\Programs\Data Refinery`의 `onedir` 구조에서 실행되어 매번 단일 EXE를 푸는 지연 없이 시작합니다.
- **업데이트 안내** — 앱 시작을 지연시키지 않고 GitHub의 정식 새 버전을 확인합니다.

## 품질 및 검증 현황

- **자동화 테스트 스위트 및 CI**: 총 528개 테스트 중 527개 통과 (1개 선택적 Excel COM 테스트 기본 skip).
  1,000,000행 대용량 매핑 벤치마크 1.379초 완료. Claude Opus 5.5 및 Codex Sol 6.1 심층 교차 코드 검수 반영.

## 데이터 모델 방향

프로모션 규칙은 기간 단위의 작은 기준 테이블로 보존합니다. 일별 지원금
파일은 분석용 결과이며 기준 규칙을 대체하지 않습니다. 이후 가격 이력 등
다른 정규화 기능도 같은 원칙을 따릅니다. 즉, 기준 사실은 작게 유지하고
시계열 데이터는 분석이 필요할 때 생성합니다.

## 다운로드 및 실행

최신 릴리즈의 설치 파일 하나만 내려받으면 됩니다. 현재 Windows 사용자
계정의 `%LOCALAPPDATA%\Programs\Data Refinery`에 설치되며 Python이나 별도 라이브러리는 필요하지 않습니다.

👉 **[최신 설치 파일 다운로드](https://github.com/KwangBeomPark/04_DataRefinery/releases/latest)**

1. `App04_DataRefinery_Setup_v2.0.1.exe` 파일을 다운로드합니다.
2. 설치 파일을 실행하면 시작 메뉴와 바탕화면에 **Data Refinery** 바로가기가 만들어집니다.
3. 깨진 구분 파일은 **CSV 구조 복구**, 프로모션 자료는 **프로모션 템플릿**,
   CSV 요약은 **데이터 집계·슬라이서**, 기간별 누적 및 공유 배포는 **데이터셋 배포** 탭에서 처리합니다.
4. 결과는 원본 파일과 같은 폴더에 `YYYYMMDD_HHMM` 형식의 날짜·시간을 붙여 저장됩니다.

설치 폴더에는 실행 파일과 번들 라이브러리를 둡니다. 설정·로그·작업 DB는 별도로 저장합니다.

| 용도 | 경로 |
| --- | --- |
| 앱 설치 | `%LOCALAPPDATA%\Programs\Data Refinery` |
| 설정·최근 작업·진단 로그 | `%LOCALAPPDATA%\Programs\Data Refinery\UserSetting` |
| 데이터셋 설정·로컬 DuckDB 작업 DB | `%LOCALAPPDATA%\Programs\Data Refinery\UserSetting\datasets` |
| 배포 CSV·연결된 Excel 템플릿 | 제작자가 지정한 공유 배포 폴더 |

`UserSetting`은 앱 설치 폴더 안의 **사용자 설정·관리 자료 전용 하위 폴더**입니다. 기존 설정·로그·프리셋·데이터셋 작업 DB는 최초 실행 시
`UserSetting`으로 복사하고 검증합니다. 원본 폴더는 백업으로 남기며 새 위치의 자료를 우선합니다.
공유 CSV 경로와 Excel 연결은 변경하지 않습니다. 프로모션 템플릿은 해당 탭에서
내보낼 수 있으며, 원본은 `assets/templates/promotion_template.xlsx` 한 곳에서 관리합니다.

내 자료 없이 집계를 시험하려면 **데이터 집계·슬라이서**에서
`sample_data/monthly_ledger_sample.csv`를 선택하세요. `디비전`을 행 그룹에,
`매출`을 값에 넣고 미리보기 후 CSV 또는 Excel로 저장할 수 있습니다.

## 개발

앱 코드는 `src`, 번들 자산은 `assets`, 빌드 스크립트는 `scripts`, 설치 설계는
`installer`, 공식 서명 배포본은 `release`에 둡니다. 자세한 규칙은 [프로젝트 구조·이름 규칙](docs/project-structure.md)과
[공개 코드맵](docs/CODE_MAP.md)을 확인하세요. 앱 버전은 `src/version.py` 한 곳에서 정의하고,
Python 소스 파일은 기존 소문자·밑줄 이름을 유지합니다.

개발 중 앱 실행:

```powershell
python -m src.data_refinery
```

테스트 실행:

```powershell
python -m unittest discover -s tests -v
```

Windows 설치 파일 빌드:

```powershell
.\scripts\build.ps1
```

미서명 검증본은 `dist/staging`, 최신 공식 서명 배포본은 `release`에 둡니다.
SimplySign에 로그인한 관리자 PowerShell에서 `scripts/sign.ps1`로 서명·검증합니다.
새 버전의 GitHub 게시까지 하려면 `-Publish`를 붙입니다. [배포 절차](docs/releasing.md)를 참고하세요.

처리 오류가 나타나면 오류 ID와 함께 [문제 신고 안내](SUPPORT.md)를 확인해 주세요. 진단 로그는 PC에만 저장되며 원본 파일 내용은 기록하지 않습니다.


공통 설치·설정·배포 정비의 기준과 현재 예외는 [6개 앱 공통 정비 기준](docs/SUITE_STANDARDIZATION.md)을 참고하세요.

설치 업그레이드 보호와 남은 실제 서명·동작 검수는 [2단계 검수](docs/STANDARDIZATION_PHASE2_REVIEW.md)에 기록했습니다.

[공개 코드맵](docs/CODE_MAP.md), [사용자 자료 백업·복원](docs/USER_DATA.md), [3–5단계 검수](docs/STANDARDIZATION_PHASE3_5_REVIEW.md)를 참고하세요. 기본 백업은 작업 DB를 포함한 UserSetting 전체이며 외부 원본·공유 배포 폴더는 별도로 보관합니다. 화면 상단 메뉴는 실제 항목에 맞춰 업데이트 / 정보로 표시합니다.


2026-10-09 source release preparation: version 2.0.2 is not published yet. New builds use only `App04_DataRefinery_Setup_v<version>.exe` plus manifest/checksums. Existing v2.0.1 downloads stay unchanged. See [current release checklist](RELEASE_CHECKLIST.md).
