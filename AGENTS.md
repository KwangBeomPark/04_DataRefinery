# AI 작업 공통 지침

이 문서는 AI가 이 프로젝트를 다룰 때 지켜야 하는 공통 개발 원칙이다.

## 1. 기본 소통 원칙
- 사용자와의 모든 소통, 변경 요약, 검증 결과는 기본적으로 한국어로 진행한다.
- 불필요한 대규모 변경을 지양하고 최소 범위의 안전한 변경을 우선한다.
- 비밀번호, API 키, 회사 내부 도메인 및 개인 경로는 절대 코드나 커밋에 노출하지 않는다.

## 사내 통합 레지스트리 관리 지침 (필수 준수)
- **중앙 관리 원칙**:
  - 사내 레지스트리 관련 키 정의, `.reg` 파일, 환경설정 배포 스크립트는 절대 개별 프로젝트 내부에 생성하지 않는다.
  - 모든 레지스트리 설정은 반드시 상위 중앙 관리 폴더인 `../00_Registry/` (또는 `C:\Dev\GitHub\00_Registry\`)를 참조하여 수정하거나 추가한다.
- **사내 민감정보 격리 (GitHub Public 보호)**:
  - 사내 이메일 도메인(`@company.com`), 내부 SMTP 메일서버, 회사명, 사내 전용 로컬 경로 등의 민감 정보는 `00_Registry`에만 격리 보존하며, 본 프로젝트 소스는 순수 오픈소스 형태로 청결을 유지한다.
- **표준 규격 준수**:
  - 신규 설정이나 키 추가가 필요한 경우 `../00_Registry/ENTERPRISE_SUITE_REGISTRY_SPEC.md`의 네임스페이스 표준(`PL_Suite\Common` 및 `PL_Suite\<App_Name>`)을 반드시 준수한다.


## 프로젝트 이름·경로·정리 규칙
- 앱 설치 경로는 `%LOCALAPPDATA%\Programs\Data Refinery`이다. 설정·로그·관리 자료는 설치 폴더 안의 `UserSetting` 하위 폴더에 둔다.
- 데이터셋 설정·DuckDB 작업 DB는 `%LOCALAPPDATA%\Programs\Data Refinery\UserSetting\datasets`에 둔다. `src/app_paths.py`에서 기존 자료를 최초 실행 시 복사·검증하고 원본을 보존한다. 새 설정과 DB를 덮어쓰지 않는다.
- 앱 버전은 `src/version.py` 한 곳에서 정의한다. UI·CLI·spec·빌드·설치 검증이 이 정의를 사용해야 한다.
- 배포 설치 파일은 `App04_DataRefinery_Setup_v<version>.exe`를 표준 공식 명칭으로 사용한다. GitHub 릴리즈에는 매니페스트 및 체크섬과 함께 이 단일 설치 파일(총 3개 자산)만 직접 업로드한다. 사내·로컬 배포 호환용으로 동일 내용의 별칭 `DataRefinery-Setup.v<version>.exe`를 생성할 수 있으나, GitHub 공개 릴리즈 자산에는 업로드하지 않는다. 런처 철자는 `Launcher`로 통일한다.
- 상세 규칙은 `docs/project-structure.md`, 배포 절차는 `docs/releasing.md`를 따른다.
- 설치 설계도와 PyInstaller spec은 루트 `installer/`에 둔다. 공식 진입점은 `scripts/build.ps1`와 `scripts/sign.ps1` 두 개이다.
- `build/`와 `dist/`는 재생성 가능한 임시 공간이고, `release/`에는 최신 서명 배포 파일·체크섬·매니페스트·릴리즈 노트만 둔다. 미서명 빌드가 기존 공식 배포본을 변경하면 안 된다.
- 릴리즈 노트 원본은 `docs/release-notes/`, 로컬 서명 증빙과 보호 파일은 Git에서 제외된 `tools/release-history/`에 보관한다. 이미 게시한 버전의 태그·파일은 덮어쓰지 않는다.
- 기본 UI는 `src/qt/`의 PySide6이다. `--legacy-tk` 호환 경로가 남아 있으므로 관련 소스를 무조건 삭제하지 않는다.
- 정리 시 인증서, 사용자 DB, 공유 CSV/Excel 연결, 서명된 배포물 및 서명 기록을 보존한다.
- 구조·경로 변경 시 README 두 언어와 AI_CODE_MAP.md를 현재 구현에 맞춰 갱신한다.
