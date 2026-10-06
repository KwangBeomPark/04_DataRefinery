# Data Refinery 프로젝트 구조·이름·경로 규칙

버전 단일 출처는 `src/version.py`입니다. 표시명은 `Data Refinery`, 제품 식별자는
`App04_DataRefinery`입니다. 현재 GUI는 PySide6(Qt)이고 Tkinter 호환 경로도 유지합니다.
배포 레이아웃은 ClipOCR의 `docs/PL_SUITE_RELEASE_STANDARDS.md`를 기준으로 정리합니다.

## 설치와 영속 데이터

| 용도 | 기본 위치 |
| --- | --- |
| 앱 실행 파일·라이브러리 | `%LOCALAPPDATA%\Programs\Data Refinery` |
| 설정·로그·프리셋·최근 작업 | 위 설치 폴더의 `UserSetting` |
| 데이터셋 설정·작업 DuckDB | `UserSetting\datasets` |
| 분석용 CSV·Excel | 사용자가 선택한 공유/입력 폴더 |

런타임 경로의 기준은 `src/app_paths.py`, 설치 기본값과 제거 보존의 기준은
`installer/setup.iss`입니다. AppId는 변경하지 않습니다. `UserSetting`을 빌드 번들에
넣지 않으며 설치의 `[Dirs]`에서 생성하고 `uninsneveruninstall`로 보존합니다.
최초 이관은 복사와 해시 검증으로 수행하며 원본을 삭제하거나 새 설정을 덮어쓰지 않습니다.

## 디렉터리와 책임

```text
AGENTS.md / AI_CODE_MAP.md       공유 지침·현재 코드 맵
README.md / README.ko.md         사용자 안내
src/                            엔진·진입점·Qt 및 레거시 UI
assets/ / sample_data/          자산·대표 샘플
installer/
  setup.iss                     Inno Setup 설계도
  data_refinery.spec             PyInstaller onedir 앱
  data_refinery_launcher.spec    onefile 런처
scripts/
  build.ps1                     앱·런처·미서명 설치 검증본
  sign.ps1                      서명·공식 스테이징·선택 GitHub 게시
  smoke_installer.ps1            임시 CI 러너 설치·제거 검증
  clean_local_artifacts.ps1      로컬 생성물 미리보기·안전 정리
tests/                          회귀·배포 실패 경로 검사
docs/
  releasing.md                  배포 절차
  release-notes/                버전별 노트 원본
build/ / dist/                  임시 빌드 공간 (Git 제외)
release/                        최신 공식 서명 배포본만 (Git 제외)
tools/release-history/          보호 파일·서명 증빙·이전 공식본 (Git 제외)
tools/signtool/                 검증한 Microsoft 서명 도구 (Git 제외)
scratch/                        로컬 실험 (Git 제외)
```

`release/build`, `release/dist`, `release/installer`, `release/packaging`은 사용하지
않습니다. 설치 설계도에는 전체 onedir 입력과 스테이징 출력 경로를 컴파일 매개변수로
주입합니다. 미서명 빌드와 실패한 서명은 현재 공식 `release`를 변경하지 않습니다.

## 배포 이름과 검증 파일

- 기업 명칭: `App04_DataRefinery_Setup_v<version>.exe`
- 공개 명칭: `DataRefinery-Setup.v<version>.exe` (같은 서명 파일의 별칭)
- 설치된 앱: `App04_DataRefinery_v<version>.exe`
- 런처: `App04_DataRefinery_Launcher.exe` (`Luncher`는 과거 오타)
- 각 바이너리의 `.sha256`, 전체 `SHA256SUMS.txt`, `build-manifest.json`
- 최신 `RELEASE_NOTES_v<version>.md` 사본

NTFS 별칭은 하드 링크로 만들고 지원하지 않는 파일시스템에서는 복사합니다. 따라서
파일 목록의 합산 용량과 중복을 제외한 실제 파일 내용 용량은 구분해서 보고합니다.
체크섬은 자기 자신을 제외한 공식 파일 전체를 포함하고 UTF-8 BOM 없이 저장합니다.
매니페스트에는 두 설치 명칭과 런처를 빠짐없이 기록합니다. 단독 EXE만으로 onedir 앱을
포터블이라고 배포하지 않습니다.

## 정리와 기존 자료 보존

정리는 해당 프로젝트 안의 검증한 임시 경로에만 적용합니다. 심볼릭 링크·정션을 거부하고
UserSetting·인증서·작업 DB·서명 파일을 먼저 보호합니다. 기존 정상 서명 파일과 기록은
해시로 중복을 제거한 로컬 `tools/release-history`에 보관하고 이관 목록을 남깁니다.
GitHub의 이미 게시된 버전·파일·태그는 변경하지 않습니다. 새 릴리즈는 새 버전으로 만듭니다.

샘플 기준은 `sample_data/monthly_ledger_sample.csv`, 템플릿 원본은
`assets/templates/promotion_template.xlsx`입니다. 레거시 Tkinter·매핑 엔진 등 기능 자산은
파일 정리만을 이유로 제거하지 않습니다. 개인 `myAGENT.md` 대신 공유 `AGENTS.md`를 씁니다.
