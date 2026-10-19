# Data Refinery v2.4.0

사용자 중심의 4대 핵심 라이프사이클 탭 체제(DWH 손익 마트 생성 ➔ 팀 공유 및 배포 ➔ 초고속 피벗 집계 ➔ 데이터 정제 도구함)와 DWH 전문 용어 병기 시스템을 도입하고, Windows 작업표시줄(Taskbar) 전용 아이콘 AppUserModelID 등록을 완료했습니다.

## 주요 변경 사항

### 1. 4개 탭 통합 정보 구조(IA) 개편
- **1. 🏛️ 손익 마트 생성 (DWH)**: 앱 실행 시 기본으로 열리는 메인 홈 화면으로, 원천 검증부터 Kimball Star Schema 빌드 및 엑셀 파워쿼리 연동까지 원스톱으로 수행합니다.
- **2. 🚀 팀 공유 및 배포 (Publisher)**: 완성된 마트를 사내 공유 폴더에 파일 잠금 충돌(File Lock) 없이 무중단(Atomic Swap)으로 안전하게 배포하고 시계열로 누적합니다.
- **3. 📊 초고속 피벗 집계 (Slicer)**: 수백만 행의 대용량 마트에서 임원/회의용 요약 피벗표를 3초 만에 썰어 엑셀(`.xlsx`)로 다운로드합니다.
- **4. 🛠️ 데이터 정제 도구 (Data Prep)**: 가끔 발생하는 불량 데이터를 치료하는 전용 도구함으로, 내부에 2개 서브탭을 배치하여 메인 화면의 복잡도를 획기적으로 낮췄습니다:
  - `📄 깨진 CSV 구조 복구` (`CsvRepairTab`)
  - `🏷️ 프로모션 행사 계약 분할` (`PromotionTab`)

### 2. DWH 전문 용어 병기 및 3개 국어(한국어, 영어, 폴란드어) 100% 동기화
- 비전공 실무자가 행동 중심으로 직관적으로 이해할 수 있는 업무 용어와 IT/데이터 거버넌스 표준 DWH 용어를 결합(Dual-labeling)했습니다:
  - **한국어 (ko)**:
    - `1. 🏛️ 손익 마트 생성 (DWH)`
    - `2. 🚀 팀 공유 및 배포 (Publisher)`
    - `3. 📊 초고속 피벗 집계 (Slicer)`
    - `4. 🛠️ 데이터 정제 도구 (Data Prep)`
  - **영어 (en)**:
    - `1. P&L Mart Builder (DWH)`
    - `2. Team Share & Publish (Publisher)`
    - `3. Fast Pivot & Slicer (Slicer)`
    - `4. Data Cleaning Tools (Data Prep)`
  - **폴란드어 (pl)**:
    - `1. Generator Hurtowni (DWH)`
    - `2. Publikacja Zespołowa (Publisher)`
    - `3. Szybka Tabela Przestawna (Slicer)`
    - `4. Narzędzia Naprawy Danych (Data Prep)`
- 다국어 전환 시 화면 전역 한글 잔존 0건 정규식 검증 통과.

### 3. Windows 작업표시줄 AppUserModelID 공식 등록
- `src/qt/app.py`, `src/data_refinery.py`, `installer/setup.iss` 모두에 Windows Shell `AppUserModelID: "KwangBeomPark.DataRefinery.App"`를 등록했습니다.
- Windows 작업표시줄에서 일반 Python 호스트나 빈 창 아이콘 대신 **공식 Fluent Glass Ribbon 전용 아이콘**이 독립된 데스크톱 앱으로 선명하게 고정/표시됩니다.

### 4. 코드 품질 및 안정성
- Claude Opus 5.5 (1차 UI/UX 검수) & Codex Sol 6.1 (2차 기술 검수) 교차 승인 통과.
- 전체 584개 단위/통합 테스트 및 1,000,000행 벤치마크(2.07s) 통과.
- 레거시 Tkinter 모드 및 하위 호환성 레퍼런스 무결성 보존.

## 배포 및 설치
- 설치 위치: `%LOCALAPPDATA%\Programs\Data Refinery`
- 사용자 설정·로그·데이터셋 DB: `%LOCALAPPDATA%\Programs\Data Refinery\UserSetting` (업데이트 시 안전 보존)
- 공식 배포 파일: `App04_DataRefinery_Setup_v2.4.0.exe`
