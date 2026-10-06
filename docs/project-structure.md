# Data Refinery 프로젝트 구조·이름·경로 규칙

현재 소스와 빌드 설정을 기준으로 관리합니다. 앱 버전은 `src/version.py`에서만 정의합니다.

## 설치와 데이터 저장 위치

| 용도 | Windows 경로 |
| --- | --- |
| **앱 설치: 실행 파일·번들 라이브러리·제거 프로그램** | **`%LOCALAPPDATA%\Programs\Data Refinery`** |
| 업데이트 설정·최근 작업·진단 로그 | `%LOCALAPPDATA%\Programs\Data Refinery\UserSetting` |
| 데이터셋 설정·DuckDB 작업 DB | `%LOCALAPPDATA%\Programs\Data Refinery\UserSetting\datasets` |
| 분석용 공개 CSV·Excel 템플릿 | 사용자가 지정한 공유 배포 폴더 |

설치 설정의 기준은 `release/installer/DataRefinery.iss`의
`DefaultDirName={localappdata}\Programs\{#MyAppName}`입니다. 일반 사용자별 설치를 유지합니다.
설정·로그 폴더를 설치 폴더로 안내하거나, 누적 DB를 앱 설치 폴더에 저장하지 않습니다.
모든 앱 관리 자료는 설치 경로 아래 `UserSetting`에 둡니다. 경로는 `src/app_paths.py`에서
공통 관리합니다. 기존 AppData 설정·로그·프리셋·작업 DB는 최초 실행 시 임시 사본으로
복사하고 해시 검증 후 반영합니다. 원본은 백업으로 보존하며, 기존 새 위치의 자료는
덮어쓰지 않습니다. DB 사용 중·WAL 존재·복사 실패·자료 변경은 이관을 차단합니다.
완료 표식으로 반복 이관을 방지합니다. 설치 AppId와 공유 CSV 경로도 유지합니다.
`UserSetting`은 설치 번들에서 제외하고 앱 제거 시 보존합니다.

## 파일 이름

| 구분 | 현재 규칙 |
| --- | --- |
| 화면 표시 이름 | `Data Refinery` |
| 설치 파일 | `App04_DataRefinery_Setup_v<version>.exe` |
| 앱 실행 파일과 빌드 번들 폴더 | `App04_DataRefinery_v<version>` (`.exe`는 실행 파일) |
| 설치·실행 런처 | `App04_DataRefinery_Launcher.exe` |
| 설치 파일 체크섬 | 설치 파일 이름 + `.sha256` |
| Python 소스·테스트 | 기존 `snake_case.py`, `test_*.py` |

`Luncher`는 과거 오타이며 새 런처 빌드는 `Launcher`를 사용합니다. 이미 배포된 서명 파일은
이번 정리에서 변경하지 않습니다. 설치된 앱의 버전 포함 이름도 유지합니다. 고정 실행 파일명은
런처의 기존 버전 탐색·업데이트·외부 앱 연동을 함께 변경할 때 도입할 수 있습니다.
PyInstaller는 `onedir` 번들을 만들므로 포터블 배포를 추가한다면 의존성 폴더까지 ZIP으로 묶어야 합니다.

## 현재 저장소 구조

```text
AGENTS.md                   공유 개발 지침
AI_CODE_MAP.md              현재 코드와 실행 흐름
README.md / README.ko.md    사용자 안내
SUPPORT.md                  오류 지원·로그 위치
PRODUCT_QUALITY_PLAN.md     기존 품질 점검 기록·미검증 항목
requirements.txt            고정 의존성
run_app.bat                 개발 실행 진입점
src/                       처리 엔진·앱 진입점·레거시 UI
  version.py               유일한 앱 버전 정의
  qt/                      기본 PySide6 UI
assets/                    앱 아이콘·문서 이미지·프로모션 템플릿
sample_data/               대표 집계 CSV·프리셋
tests/                     회귀 테스트·입력 fixture
scripts/                   빌드·서명·설치 검증·벤치마크
docs/                      사용법·구조 규칙·수동 QA
release/
  README.md                배포 체크리스트
  RELEASE_NOTES_v*.md      버전별 변경 기록
  installer/              Inno Setup 설치 설계도
  packaging/              PyInstaller 설정
  build/                  로컬 빌드 중간 파일·가상환경·서명 기록 (Git 제외)
  dist/                   로컬 번들·설치 결과물 (Git 제외)
tools/, scratch/          로컬 개발 자료 (Git 제외)
```

현재 빌드 경로를 유지합니다. `release/`를 공식 결과물 전용으로 바꾸는 폴더 이동은
스크립트·CI·spec·설치 설정·릴리스 지침을 함께 수정할 때 수행합니다.

## 정리 원칙과 이번 정리 범위

- SHA-256이 동일한 집계 CSV는 `sample_data/monthly_ledger_sample.csv` 하나만 유지합니다.
- 프로모션 템플릿은 `assets/templates/promotion_template.xlsx` 하나만 유지합니다.
- 참조되지 않는 옛 `manual.png`, `header_icon.png`, `icon.png`는 제거합니다.
- 샘플 집계 결과, 테스트 캐시, bytecode, 이전 layout-check 및 빌드 중간 파일은 정리 대상입니다.
  이 환경의 삭제 정책이 직접 삭제를 차단한 로컬 생성물은 보존하며,
  `scripts/clean_local_artifacts.ps1`로 목록을 검토한 뒤 수동으로 정리할 수 있습니다.
- 서명된 배포 파일·체크섬·서명 로그·서명 스크립트·빌드 가상환경은 보존합니다.
- 레거시 Tkinter는 `--legacy-tk`로 실행 가능하므로 관련 소스·테스트는 보존합니다.
- 매핑 엔진과 별도 매핑 화면 소스는 기능 자산으로 보존하며, 현재 기본 앱의 탭은 네 개입니다.
- 사용자 AppData·공유 폴더·인증서·개인 설정을 프로젝트 청소 대상으로 포함하지 않습니다.
- 개인용 `myAGENT.md`의 지침은 저장소에서 공유하는 `AGENTS.md`로 통합합니다.

캐시는 `.gitignore`로 제외하며, 코드맵·개발 지침은 개인 global ignore가 있더라도 저장소에 공유합니다.
