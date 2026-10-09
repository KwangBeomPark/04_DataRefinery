# 01·02·04·05·06·07·08 — 관리자 서명·게시 실행 안내

2026-10-09 준비 검수입니다. **실제 KSP 서명·설치 합격·GitHub 새 릴리즈 완료와 구분합니다.** 사용자의 관리자 PowerShell/SimplySign 세션에서 한 프로젝트씩 실행합니다. 개인 키·PIN·OTP는 문서나 명령에 넣지 않습니다.

| 번호 | 공개 latest | 준비 버전 | 공식 설치 파일 |
| --- | --- | --- | --- |
| 01 | 1.6.1 | 1.6.2 | App01_ClipOCR-Pro_Setup_v1.6.2.exe |
| 02 | 1.4.1 | 1.4.2 | App02_SwiftDeck_Setup_v1.4.2.exe |
| 04 | 2.0.1 | 2.0.2 | App04_DataRefinery_Setup_v2.0.2.exe |
| 05 | 1.4.2 | 1.4.3 | App05_FileOps_Setup_v1.4.3.exe |
| 06 | 0.3.0 | 0.3.1 | App06_Stepwise_Setup_v0.3.1.exe |
| 07 | 0.1.16 | 0.1.17 | App07_EmlViewer_Setup_v0.1.17.exe |
| 08 | 0.5.15 | 1.0.0 | App08_OutlookTemplate_Setup_v1.0.0.exe |

각 설치 파일 하나와 manifest·SHA256SUMS 두 장부를 게시합니다. 06의 장부는 `build-manifest.v0.3.1.json`, `SHA256SUMS.v0.3.1.txt`로 기존 공식 장부와 충돌하지 않게 보존합니다. 앱/런처 서명은 설치 파일 내부 구성에도 필요합니다.

## 1. 먼저 실행할 명령

관리자 PowerShell을 직접 열고 SimplySign 로그인을 유지한 상태에서 아래 **한 명령만** 실행해 결과를 확인합니다. 이 명령은 선택 인증서·관리자·Microsoft 서명 도구와 스마트카드 서비스를 확인하며 파일을 서명하거나 게시하지 않습니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Dev\GitHub\04_DataRefinery\scripts\Check-SuiteSigningSession.ps1"
```

`SESSION_PREFLIGHT_OK`가 나와도 실제 개인 키 서명 성공까지 증명하는 것은 아닙니다. 인증서는 기존 배포와 같은 공개 지문 `E9C72CF5090840A1805296525D56BE680622A7FD`를 선택했습니다. 다른 인증서로 서명하려면 변경 이유·기존 updater의 게시자 호환을 먼저 확인합니다.

## 2. 프로젝트별 서명

서명 전 검수한 소스를 커밋하여 `git status --porcelain` 출력이 비어 있어야 합니다. 준비 검사에서 생성한 dirty/미서명 산출물은 공식 입력으로 재사용하지 않습니다. 다음 명령은 새 스테이징에서 검사·빌드를 수행하므로 시간이 걸립니다. 하나가 실패하면 다음 프로젝트·게시로 진행하지 않고 로그와 stage를 보존합니다.

### 01 ClipOCR-Pro

```powershell
Set-Location -LiteralPath 'C:\Dev\GitHub\01_ClipOCR-Pro'
.\scripts\release.ps1 -CertificateThumbprint 'E9C72CF5090840A1805296525D56BE680622A7FD' -SignToolPath 'C:\Dev\GitHub\04_DataRefinery\tools\signtool\signtool.exe'
```

### 02 SwiftDeck

```powershell
Set-Location -LiteralPath 'C:\Dev\GitHub\02_SwiftDeck'
$env:SIGNTOOL_PATH = 'C:\Dev\GitHub\04_DataRefinery\tools\signtool\signtool.exe'
.\scripts\release.ps1 -CertificateThumbprint 'E9C72CF5090840A1805296525D56BE680622A7FD'
```

### 04 DataRefinery

```powershell
Set-Location -LiteralPath 'C:\Dev\GitHub\04_DataRefinery'
.\scripts\build.ps1
.\scripts\sign.ps1 -CertificateThumbprint 'E9C72CF5090840A1805296525D56BE680622A7FD' -SignToolPath 'C:\Dev\GitHub\04_DataRefinery\tools\signtool\signtool.exe'
.\scripts\sign.ps1 -VerifyOnly -CertificateThumbprint 'E9C72CF5090840A1805296525D56BE680622A7FD' -SignToolPath 'C:\Dev\GitHub\04_DataRefinery\tools\signtool\signtool.exe'
```

빌드는 Python 3.13 및 고정 의존성을 요구합니다. 05와 함께 검사 시 기본 Python 3.13.14를 확인했습니다. 재실행 전 기존 서명 세트/동일 버전 artifact를 지우지 않습니다.

### 05 FileOps Hub

```powershell
Set-Location -LiteralPath 'C:\Dev\GitHub\05_FileOperation'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\sign.ps1 -CertificateThumbprint 'E9C72CF5090840A1805296525D56BE680622A7FD' -SignToolPath 'C:\Dev\GitHub\04_DataRefinery\tools\signtool\signtool.exe'
```

현재 `release/`의 접근 제한은 변경하지 않았습니다. 사용자 세션에서도 실패하면 해당 로그를 보존하며 권한 변경·`Overwrite` 우회로 진행하지 않습니다.

### 06 Stepwise

```powershell
Set-Location -LiteralPath 'C:\Dev\GitHub\06_Stepwise'
.\scripts\build.ps1
```

성공 로그의 `-BuildRoot "..."`에 표시된 **이번 새 build ID**를 다음 명령에 넣습니다. 예전 dirty 준비 빌드나 가장 최근처럼 보이는 폴더를 임의 선택하지 않습니다.

```powershell
.\scripts\sign.ps1 -BuildRoot '<이번 성공 로그에 표시된 BuildRoot 전체 경로>' -CertificateThumbprint 'E9C72CF5090840A1805296525D56BE680622A7FD' -SignToolPath 'C:\Dev\GitHub\04_DataRefinery\tools\signtool\signtool.exe'
```

### 07 EML Viewer

```powershell
Set-Location -LiteralPath 'C:\Dev\GitHub\07_eml-viewer'
.\scripts\sign_and_release.ps1 -CertificateThumbprint 'E9C72CF5090840A1805296525D56BE680622A7FD' -SignToolPath 'C:\Dev\GitHub\04_DataRefinery\tools\signtool\signtool.exe'
```

### 08 Outlook Template

```powershell
Set-Location -LiteralPath 'C:\Dev\GitHub\08_OutlookTemplate'
.\scripts\release.ps1 -CertificateThumbprint 'E9C72CF5090840A1805296525D56BE680622A7FD' -SignToolPath 'C:\Dev\GitHub\04_DataRefinery\tools\signtool\signtool.exe'
.\scripts\release.ps1 -VerifyOnly
```

## 3. 서명 후 게시까지

각 저장소의 `RELEASE_CHECKLIST.md`에 실제 검증/게시 명령과 남은 게이트가 있습니다. 파일 서명 성공 직후 무조건 게시하지 않습니다.

1. 설치 EXE와 설치된 앱·런처·제거 프로그램의 서명·게시자·타임스탬프를 대조하고 공식 세 파일의 이름·버전·커밋·hash·크기를 검증합니다.
2. 격리 Windows에서 구버전 실행 중 정상 종료 요청, 종료 거부/잠금 시 설치 차단, 취소/실패, 업그레이드 및 제거 후 UserSetting 보존을 확인합니다. 전체 구버전 사용자 폴더를 지우는 정책은 적용하지 않습니다.
3. 구버전 updater 첫 전환을 확인합니다. 특히 02 및 05의 과거 파일명/metadata 계약은 첫 새 설치를 수동으로 해야 할 수 있습니다. 08 0.5.x INI는 정상 실행에서 자동 이관되는 것으로 판단하지 않습니다.
4. 원격 main/새 태그가 검수 커밋인지 확인한 뒤 새 draft에 정확히 세 파일을 업로드합니다. 원격 SHA-256·크기·서명 다운로드 검수를 통과한 후 공개합니다. 기존 릴리즈/태그/asset은 보존합니다.

06/07 현재 설치 정의에는 제거 프로그램 서명 콜백이 없습니다. 따라서 앱/설치 EXE의 서명 준비와 설치된 `unins000.exe`의 서명·Windows 정책상 제거 실행 가능 여부를 구분합니다. 정책이 제거를 차단하면 게시 합격으로 표시하지 않습니다. 실제 KSP/설치/게시 실패는 로그·stage를 보존하고 원인을 확인합니다.

## 현재 검수 범위

01/02는 정적·AHK·서명 실패 보호·updater publisher 검사, 04는 전체 Python 536개 실행(535 통과/1 제외)·추가 런처 8·PS39, 05는 Python250, 06는 Python133/PS85 및 실제 미서명 앱·설치 preview, 07은 Python214+36subtests/PS42 및 실제 미서명 전체 PyInstaller/Inno, 08은 AHK4suite/PS49 및 실제 AHK/Inno preview를 검수했습니다. 최신 세부 결과는 각 체크리스트를 따릅니다. mock 서명이나 unsigned preview는 실제 인증서 서명이 아닙니다.

Gemini High의 내용 있는 완료는 01/02 61.67초, 04/05 65.60초, 06/07 54.54초입니다. 08 요청은 시간 초과·빈 응답이므로 완료로 계산하지 않았고 Codex 독립 교차 검수로 확인했습니다. 토큰 보고량과 계정 크레딧 차감은 구분합니다.
