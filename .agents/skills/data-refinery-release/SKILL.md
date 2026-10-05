---
name: data-refinery-release
description: Data Refinery (04_DataRefinery) 프로젝트의 새 버전을 릴리즈 빌드하고, 디지털 서명을 위해 관리자 권한 PowerShell에서 복사/붙여넣기로 즉시 실행할 수 있는 명령어를 자동으로 준비해 주는 전용 릴리즈 스킬입니다. "릴리즈해줘", "버전업해서 릴리즈", "배포해줘", "release 빌드" 요청 시 반드시 이 스킬을 활성화하세요.
---

# Data Refinery Release Automation Skill

이 스킬은 **Data Refinery (`04_DataRefinery`)** 프로젝트의 전체 릴리즈 파이프라인(버전 관리, 빌드, 서명 스크립트 준비, 체크섬 생성)을 자동으로 수행하고, 보안상 직접 실행이 불가능한 디지털 서명 단계를 사용자가 **관리자 권한 PowerShell**에서 원클릭으로 복붙 실행할 수 있도록 안내하는 전용 스킬입니다.

---

## 🛠️ 릴리즈 파이프라인 실행 절차

사용자가 "릴리즈해줘", "버전 올려서 릴리즈", "배포 준비해줘" 등을 요청하면 다음 단계에 따라 빈틈없이 수행합니다.

### 1단계: 버전 확인 및 업데이트 (`__version__`)
1. `src/data_refinery.py` 파일의 `__version__ = "X.Y.Z"`를 확인합니다.
2. 사용자가 버전을 명시하지 않은 경우:
   - 버그 픽스/단순 개선: 패치 버전 증가 (예: `1.13.0` -> `1.13.1`)
   - 새 기능/아키텍처 변경: 마이너 버전 증가 (예: `1.12.0` -> `1.13.0`)
3. `src/data_refinery.py`의 `__version__`을 새 버전으로 변경합니다.

### 2단계: 릴리즈 노트 자동 작성
1. `release/RELEASE_NOTES_v<version>.md` 파일을 생성합니다.
2. 최근 커밋 및 변경된 소스 코드의 핵심 변경 사항(기능 추가, 버그 수정, 안정성 개선)을 깔끔하게 요약하여 작성합니다.

### 3단계: 바이너리 및 인스톨러 빌드 (SkipSign 모드)
1. 백엔드 빌드를 수행합니다:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -SkipSign -SkipTests
   ```
2. 빌드 결과물을 확인합니다:
   - 메인 실행 파일: `release\dist\App04_DataRefinery_v<version>\App04_DataRefinery_v<version>.exe`
   - 설치 프로그램: `release\dist\installer\App04_DataRefinery_Setup_v<version>.exe`

### 4단계: 관리자 모드 전용 디지털 서명 스크립트 준비
1. 디지털 서명은 Windows 정책상 UAC 관리자 권한(Elevated)이 필수이므로, 에이전트 환경에서 직접 서명하지 않고 독립 실행 스크립트를 생성합니다.
2. `release\build\sign_v<version>.ps1` 파일을 생성합니다.

### 5단계: 사용자에게 관리자 모드 실행 복붙 명령어 제공 (가장 중요 🚨)
빌드와 스크립트 준비가 끝나면, 사용자에게 다음 형식으로 **관리자 권한 PowerShell에서 바로 복사하여 붙여넣을 수 있는 원클릭 명령어**를 제공합니다:

```markdown
### 🔑 디지털 서명 실행 안내
빌드 및 서명 스크립트 준비가 완료되었습니다!
관리자 권한 PowerShell을 열고 아래 명령어를 복사하여 실행해 주세요:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Dev\GitHub\04_DataRefinery\release\build\sign_v<version>.ps1"
```
```
