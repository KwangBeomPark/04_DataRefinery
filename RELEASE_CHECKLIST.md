# Data Refinery 다음 릴리스 체크리스트

2026-10-09 사용자 요청의 서명 릴리스 준비를 반영했습니다. 기존 사용자 변경을 보존하며 이름/런처/검증 계약과 안전한 설치 정리 소스를 수정했습니다. 실제 서명·설치·게시·커밋·push·태그는 실행하지 않았습니다. KSP 로그인은 사용자가 완료했다고 알렸지만 이 문서는 개인키 접근 성공의 증거가 아닙니다.

## 서명 전 확인

- [x] 새 설치 파일은 `App04_DataRefinery_Setup_v<version>.exe` 한 개로 통일했습니다. 빌드·서명·검증·런처 계약을 일치시켰고 과거 `-Setup` 이름은 런처 읽기 호환으로 유지했습니다.
- [x] 버전은 `src/version.py`의 `2.0.2`입니다. 변경 전 로컬/원격 `v2.0.2` 태그 조회가 모두 비어 있었고 GitHub 릴리스 목록에도 없었습니다. 서명 직전에 다시 확인하며 이미 게시한 v2.0.1은 변경하지 않습니다.
- [x] 설치 전 `{app}\_internal` 전체 삭제를 제거했습니다. 사용자 자료는 보존하고 신버전 payload는 기존 `ignoreversion` 교체로 설치합니다. 실제 복사 실패/취소 시 전체 런타임 롤백과 잔여 DLL 충돌은 아직 Windows 실제 설치 검수 대상입니다.
- [ ] 현재 변경을 검토·커밋하고 clean `main`에서 다시 전체 빌드합니다. 아래 명령은 이 선행 게이트 이후 사용자 서명 세션에서 실행하는 절차입니다.

## 이름 변경 영향

| 확인 대상 | 현재 의존성과 다음 작업 |
| --- | --- |
| `installer/setup.iss` | 현재 `OutputBaseFilename=App04_DataRefinery_Setup_v{#AppVersion}`입니다. 설치 AppId·설치 경로·UserSetting 위치는 별개이며 유지합니다. |
| `scripts/build.ps1`, `scripts/sign.ps1` | 현재 단일 설치 파일 생성·검증 계약입니다. 공식 폴더는 설치 EXE 한 개와 `build-manifest.json`, `SHA256SUMS.txt` 정확히 세 파일을 요구하고 manifest artifacts는 설치 파일 하나, aliases는 빈 객체입니다. 체크섬은 설치 파일 한 줄입니다. 과거 두 이름 공식 세트는 이 새 검증 계약으로 검증되지 않을 수 있으며 기존 파일을 삭제하거나 재작성해 맞추지 않습니다. |
| `src/data_refinery_launcher.py` | 현재 `_Setup` 이름과 과거 `-Setup` 이름을 태그/URL 검증과 함께 인식합니다. 앱 EXE `App04_DataRefinery_v<version>.exe`는 유지합니다. 빌드/서명은 내부 런처도 만들지만 새 공식 세 파일 세트에는 별도 런처를 올리지 않습니다. |
| 앱 업데이트 | `src/update_checker.py`는 새 릴리스 URL을 안내합니다. 최신 게시본을 통한 실제 다운로드·실행과 구버전 런처를 따로 확인합니다. |
| VBA·공유 배포 | 읽은 Finance Add-in CSV의 App04 설치 패턴은 `App04*`입니다. 제안 이름은 이 패턴을 만족하지만 배포된 워크시트 값·공유 폴더·로컬 캐시·실제 Office 실행은 확인이 필요합니다. |
| 기존 공개 버전 | 기존 두 이름·서명·체크섬·장부를 그대로 보존합니다. 새 버전 한 개 정책 때문에 과거 GitHub 자산을 삭제하지 않습니다. |

## 사용자 할 일

- [ ] 미커밋 변경과 새 추적 파일을 검토합니다. 사전 사용자 변경은 포함 여부를 직접 판단하고 덮어쓰지 않습니다. 현재 이름 통합 구현과 런처 호환, 버전·릴리스 노트·이 체크리스트까지 검토한 뒤 clean `main` 커밋을 확정합니다.
- [ ] 실제 설치 실패/취소와 DLL 교체·잠금·구형 EXE 성공 후 정리·UserSetting 보존을 격리 Windows에서 검수합니다. 사전 `_internal` 전체 삭제는 제거했지만 소스/fixture 검사는 전체 설치 롤백의 증거가 아닙니다.
- [ ] 앱·DB 작업을 종료하고 활성 UserSetting을 백업·검증합니다. 기본 백업에는 datasets/작업 DB가 포함되며 `SettingsOnly`는 제외합니다. 외부 CSV/Excel·공유 배포 파일·사용자 지정 외부 저장소는 별도 보관합니다. [백업 안내](docs/USER_DATA.md).
- [ ] Python 3.13, 고정 의존성, Inno Setup 6.7+ 또는 7, 유효한 Microsoft SignTool을 준비합니다. SimplySign 로그인·인증서 선택·PIN/OTP는 사용자 세션에서 처리합니다.
- [ ] 확정한 커밋에서 전체 검사와 빌드를 실행합니다. `-SkipTests` 기록은 서명에 사용할 수 없습니다.

```powershell
# 승인 커밋·동일 소스 전체 빌드·사용자 서명 세션 준비 후 실행
powershell -NoProfile -File scripts/build.ps1
```

- [ ] SimplySign이 로그인된 사용자 직접 실행 관리자 PowerShell에서 서명합니다. 실제 옵션은 `CertificateThumbprint`, `SignToolPath`, 선택적 `TimestampServer`이며 내부 Inno 콜백 옵션을 직접 호출하지 않습니다. 스크립트는 clean `main`, 현재 커밋/버전의 `build/release-input.json`, `sourceClean=true`, `testsPassed=true`, bundle/launcher 해시를 요구합니다. 이미 로컬 또는 원격 태그가 존재하는 버전은 새 서명을 거부합니다.

```powershell
.\scripts\sign.ps1 -CertificateThumbprint '<선택한 공개 인증서 지문>' -SignToolPath '<Microsoft SignTool 전체 경로>'
.\scripts\sign.ps1 -VerifyOnly -CertificateThumbprint '<같은 인증서 지문>' -SignToolPath '<Microsoft SignTool 전체 경로>'
```

- [ ] 앱·런처·설치 파일·제거 프로그램의 게시자, 유효한 서명, 타임스탬프, SignTool 결과를 확인합니다. 매니페스트/체크섬과 버전·소스 커밋·실제 파일을 대조합니다. 설치 파일 한 개 정책은 메타데이터·런처까지 제거하라는 뜻이 아닙니다.
- [ ] 격리된 실제 Windows 환경에서 구버전 실행 중 정상 종료, 종료 거부 시 설치 차단, 활성 DB 보호, 설치 실패·취소, 업그레이드 후 구형 EXE 정리, UserSetting 보존, 제거와 대표 복구·집계·데이터셋 배포 작업을 검수합니다. 자동 회귀나 미서명 fixture 컴파일은 이 검수를 대신하지 않습니다. [실제 설치 검수](docs/INSTALL_UPGRADE_ACCEPTANCE.md).
- [ ] 같은 커밋의 Windows release check 성공과 서명 세트를 확인한 뒤 게시합니다. 아래 `PublishOnly`는 새 서명 없이 검증한 공식 세트를 게시/복구합니다. `-Publish`는 서명 절차와 게시를 함께 실행하는 다른 옵션입니다.

```powershell
.\scripts\sign.ps1 -PublishOnly -CertificateThumbprint '<같은 인증서 지문>' -SignToolPath '<Microsoft SignTool 전체 경로>'
```

- [ ] GitHub 태그의 커밋·stable/latest 상태와 새 설치 파일 한 개·매니페스트·체크섬 세 자산의 이름·크기·SHA-256을 확인합니다. 내부 런처는 별도 업로드하지 않습니다. 업로드 실패 시 기존 자산을 덮어쓰지 않으며 게시가 끝났다고 보고하지 않습니다.

## 정리 검토

아래는 사용자와 검토할 후보이며 삭제 승인이 아닙니다. 파일 내용·연결·마지막 검수 근거를 확인한 뒤 개별 경로만 선택합니다. 기존 `scripts/clean_local_artifacts.ps1 -Apply`도 실행하지 않았습니다.

| 후보 | 이유와 삭제 조건 |
| --- | --- |
| `.ruff_cache/`, 확인된 `__pycache__/`·`.pyc` | 재생성 가능한 검사/파이썬 캐시입니다. 앱·검사가 실행 중이지 않고 필요한 진단 기록이 별도 보존된 경우 개별 삭제를 검토합니다. |
| `build/standardization/` 내부의 확인된 오래된 QA 사본 | 같은 입력으로 재생성할 수 있는 fixture만 후보입니다. 마지막 성공/실패 로그와 해시·설치 차단 증거를 보존한 뒤 해당 사본만 검토합니다. 전체 `build/` 삭제는 제안하지 않습니다. |
| `scratch/codex_review_prompt.md`, `scratch/codex_review_response.md` | 현재 두 파일 6,146바이트로 비어 있지 않습니다. 검토 근거이므로 먼저 읽고 필요한 내용을 문서에 반영·보존한 뒤 사용자와 삭제 여부를 정합니다. 현재는 보존합니다. |

`build/`, `dist/`, `tools/`, 빌드 가상환경은 통째로 삭제하지 않습니다. 특히 `build/release-input.json`과 해당 bundle은 서명 입력입니다. `tools/release-history/`, 공식 `release/`, 서명 기록·인증 자료·DB·UserSetting·사전 사용자 수정은 보존합니다.

10월 8일 Antigravity Gemini 3.8 Flash High/effort high 읽기 검토는 47.56초에 내용 있는 응답으로 완료했습니다. 이번 준비 변경의 최신 회귀 결과는 아래 검수 기록을 따르며 전날 검사 수치를 현재 소스의 결과로 재사용하지 않습니다.

[폴더 정리와 이름 제안 종합 검토](docs/CLEANUP_AND_RELEASE_REVIEW.md) — 실제 삭제는 사용자 검토 후 진행합니다.


## 2026-10-09 준비 검수 결과

- 최신 앱/설치 소스 전체 Python 회귀 536개 실행: 535개 통과, 선택 Excel COM 1개 제외. 이후 추가한 과거 `-Setup` 이름 검사까지 포함한 런처 8개도 통과했습니다. 로그는 `build/standardization/release-readiness-8c86a71799dd4828ab0acb4de927a232/`에 보존합니다.
- 단일 공식 세트·새 draft 원본 릴리스 노트 경로·기존 자산 덮어쓰기 거부를 포함한 PowerShell 실패 주입 39개와 백업 안전성 34개가 통과했습니다. 실제 개인키/GitHub를 사용하지 않는 검증입니다.
- 새 릴리스 노트는 `docs/release-notes/RELEASE_NOTES_v2.0.2.md`입니다. 신규 draft 게시 시 이 원본 경로를 사용하고 공식 세 파일 폴더에 노트를 섞지 않습니다.
- Ruff와 git diff --check 통과. 네이티브 업무, 실제 실행 중 업그레이드·복사 실패/취소·제거, 새 서명과 게시 검수는 남아 있습니다.
- 04·05 공통 계약의 agy Gemini 3.8 Flash High/effort high 검토는 65.60초에 내용 있는 SUCCESS로 완료했습니다. 전날 실행과 구분하며 구버전 updater 실제 호환과 잔여 DLL/복구 검수 지적을 확인했습니다. 내부 컴포넌트 검증과 공식 다운로드 자산을 혼동한 제안은 채택하지 않았습니다. 05의 ignored build/standardization/agy-release-contract-91334ce3ffe446b5aceeb1a51f5966fe/response.json에 응답을 보존하며 크레딧 차감은 미확인입니다.

- 추가 게시 보호: origin이 승인 저장소와 일치하지 않으면 push/tag 이전에 차단합니다. gh 저장소 조회·릴리스 목록·CI·생성·업로드·draft 해제는 모두 KwangBeomPark/04_DataRefinery를 명시합니다. 잘못된 origin 실패 주입 검수를 통과했습니다.
