# 폴더 정리·단일 배포 이름·사용자 릴리즈 작업 재검토

최종 재확인: 2026-10-09 Europe/Warsaw. 범위: 01·02·04·05·06·07. LocalDataMart 및 다른 프로젝트의 구현은 제외합니다.

## 판정과 이번 작업

**삭제 후보 검토와 프로젝트별 체크리스트를 정리했습니다. 실제 삭제·이름 변경·서명·설치·게시를 실행하지 않았습니다.** 중단된 문서 작업을 이어갔으며 어젯밤 작업 이후 다른 담당의 소스 변경을 보존했습니다.

현재 단일 설치 파일 정책은 여러 프로젝트에 추가 반영됐지만 생성·서명·업데이트·검증·기존 공식 폴더와의 계약에 불일치가 남습니다. 지금 바로 서명하는 단계로 보지 않습니다. 아래 문제를 정리하고 현재 소스 기준 회귀 검사를 통과시킨 뒤 사용자 서명 세션으로 진행해야 합니다. 이전 1,088개 통과/2개 제외 등은 당시 소스의 근거이며, 이후 변경된 현재 소스의 통과로 재사용하지 않습니다.

## 삭제 후보 — 검토 후 선택

아래 경로는 프로젝트 루트 기준입니다. 용량은 읽을 수 있었던 파일의 논리 크기 합계이며 반올림했습니다. 실제 디스크 회수량은 압축/하드링크와 다를 수 있습니다. 재생성이 가능해도 같은 서명·같은 바이트의 재현을 보장하지 않습니다.

| 번호 | 프로젝트 / 후보 | 대략 용량 | 지우면 좋은 이유 | 삭제 전 조건 |
| --- | --- | ---: | --- | --- |
| A | 04·05·06·07 `.ruff_cache`, 06·07 `.pytest_cache`, 소스/검사의 `__pycache__` | 합계 약 6 MiB | 실행/검사 때 재생성되는 캐시, 소스 아님 | 앱·검사 종료, Git 추적이 없고 링크 경로가 아닌 실제 후보만 선택 |
| B01 | 01 `build`의 과거 더미 Inno/백업 fixture 하위 폴더 | build 전체는 약 13.7 MiB | 반복 검수용 사본 | 전체 build 삭제 금지. 로그·마지막 검수·Gemini/출처 자료를 남기고 생성 근거 확인된 사본만 |
| B02 | 02 `dist` | 약 7.1 MiB | 과거 미서명/개발 출력, 새 staging 빌드로 생성 가능 | 수동 사용본·개인 설정·서명 입력이 없는지 확인, 마지막 검수 요약 보존 |
| B04 | 04 `build/phase2-inno-da67db5fb2a746c7a182727fd4418f40`, `build/test-appdata`, `tools/qa-appdata` | 약 2.1 + 4.8 + 2.4 MiB | 더미 설치·격리 QA 사본 | 검수 로그 보존, 테스트용 DB/설정임을 확인. 실제 DB/사용자 설정 제외 |
| B05 | 05 `tools/_local/app05_build`, `launcher_build`, `main_build`, `main_build_final`, `main_dist`, `main_dist_final` | 약 1.01 GiB | 여러 차례 생성된 빌드 캐시·개발 출력 | 현재 빌드 입력/실행 중 파일인지 확인, 미서명·재생성 가능 자료만. 서명 도구/이전 배포 사본 제외 |
| C05 | 05 `build/standardization/canonical-package-d1d7b48afeb74a5c931a2e1b8a19a55b`의 큰 번들/캐시 | 폴더 약 308 MiB | 이전 canonical 전체 빌드 검수본 | canonical-package-result.json·로그·소스 출처를 보존하고 바이너리/캐시만 선택 |
| B06 | 06 `dist`, `build/stepwise` | 약 251 + 17.9 MiB | 이전 앱 번들·PyInstaller 캐시 | 현재 `build/unsigned` 경로와 구분. 수동 실행본·개인 자료 없음 확인 후 |
| B07 | 07 `dist`, `build/eml_viewer` | 약 550 + 9.0 MiB | 이전 앱 번들·작업 캐시 | 현재 staging·서명 입력·개인 설정과 구분 후 |
| C07 | 07 `build/standardization/canonical-package-66e5aca5a91944bf9f7cfe6818e6f375`의 큰 번들/캐시 | 폴더 약 604 MiB | 이전 canonical 전체 빌드 검수본 | 성공 JSON·로그·출처를 보존하고 큰 파일만 선택 |
| D07 | 07 `custom_macros`, `results` | 현재 빈 폴더 | EML 기능의 직접 참조 없음, 과거 다른 앱 검사와 관련된 흔적 | 외부 도구·사용자 용도가 없는지 확인 후 빈 폴더만. 생성 시각만으로 불필요하다고 단정하지 않음 |

**추천 순서는 A → B의 확인된 사본 → C입니다.** 캐시의 용량 이득은 작고 큰 이득은 개발/검수 바이너리에서 나옵니다. 아직 서명할 준비가 안 된 상태이므로 현재 릴리즈 검수 입력과 마지막 성공 기록을 먼저 보존합니다. 각 프로젝트 루트 RELEASE_CHECKLIST에 앱별 조건/경로를 추가했습니다.

다음 항목은 기본 삭제 후보에서 제외합니다.

- `release/`의 기존 공식 서명물·체크섬·매니페스트·게시 자산. 다음 정책 변경 때문에 과거 자산을 지우지 않습니다.
- 07 `build/release-history` 약 **1.88 GiB**: 과거 배포물 16개와 원래 경로/해시 장부입니다. 캐시가 아니라 보존 이동한 자료라 별도 검증 백업 없이는 삭제하지 않습니다.
- 04 `tools/release-history`, 05 previous-release/published-v1.4.2 보관본·서명 증빙·인증 도구.
- `.venv`, 빌드 환경·SignTool/Inno/외부 OCR 도구: 현재 검사/빌드가 참조합니다. 재설치 비용과 의존성을 검토한 별도 작업으로만 정리합니다.
- `UserSetting`, DB, 매크로, 이미지, 결과·이메일·첨부·업무 원본. 06 `macros/custom_macros/images/results`는 사용자 자료 가능성이 있어 보존합니다.
- 04 `scratch`: 재확인 시 파일 2개가 있어 빈 폴더가 아닙니다. 내용/용도 확인 전 보존합니다. 05 제한된 release 및 일부 dist는 권한을 완화해 조사/삭제하지 않습니다.
- `.git`, `.github`, 개발 지침·개인 AI 설정·tracked tests/검사 도구·호환 wrapper. 오래돼 보인다는 이유만으로 삭제하지 않습니다.

문서도 정리할 수 있지만 현재 8개 공통/단계 검수 문서의 앱별 합계는 약 54–60 KiB입니다. 용량 효과가 작으므로 삭제보다 나중에 `docs/reviews/`로 이력 보관하고 링크를 일괄 수정하는 것을 권장합니다. 현재 공개 CODE_MAP과 개인 AI_CODE_MAP은 공유 대상이 달라 단순 중복으로 지우지 않습니다.

## 배포 파일 이름 제안

추천 표기는 **`AppNN_<제품 식별명>_Setup_v<버전>.exe`**입니다. 현재 `App04*`, `App05*` 같은 외부 설치 탐색과 접두부 호환을 유지하기 쉽습니다. `APP_04_…`처럼 번호 앞에도 밑줄을 넣으면 그 탐색 패턴까지 변경해야 합니다. 사용자 표기 선택은 아직 없으므로 아래는 확정/적용된 이름이 아닌 제안입니다.

| 앱 | 추천 설치 파일 한 개 |
| --- | --- |
| 01 | `App01_ClipOCR-Pro_Setup_v<version>.exe` |
| 02 | `App02_SwiftDeck_Setup_v<version>.exe` |
| 04 | `App04_DataRefinery_Setup_v<version>.exe` |
| 05 | `App05_FileOps_Setup_v<version>.exe` |
| 06 | `App06_Stepwise_Setup_v<version>.exe` |
| 07 | `App07_EmlViewer_Setup_v<version>.exe` |

현재 다수의 단일 출력은 `-Setup`이고 07은 `_Setup`입니다. 01의 출력 식별명은 ClipOCR입니다. 이 차이를 확정한 규칙으로 한 번에 맞춰야 합니다. 설치 폴더·AppId·설치된 내부 EXE·단축키·UserSetting은 호환성을 유지합니다. 표준화 범위에 없는 03 등은 번호 규칙의 예시이며 이번에 소스를 변경하지 않았습니다.

**새 버전에서 설치 EXE는 한 개만 게시**하고 똑같은 EXE의 별칭/설치 EXE ZIP은 생성을 중단하는 방향을 권장합니다. 해시/출처 매니페스트는 유지합니다. 포터블/런처는 별도 기능·구버전 사용자가 요구하는 경우만 유지 여부를 결정합니다. 현재 여러 파이프라인은 이미 설치 전용으로 바뀌었으므로 포터블 제거를 다시 구현할 필요가 없습니다.

기존 앱의 자동 업데이트는 과거 이름의 EXE/INI 또는 Launcher를 찾는 경우가 있습니다. 최신 소스가 설치 EXE를 읽도록 바뀌었어도 이미 설치된 구버전 코드는 바뀌지 않습니다. 다음 버전까지 구형 payload를 한시 제공할지, 첫 전환을 수동 설치로 안내할지 결정하고 실제 구버전으로 확인해야 합니다. 과거 게시 버전/태그는 그대로 유지합니다.

## 현재 소스에서 남은 릴리즈 차단 항목

| 우선순위 | 근거 | 영향 및 최소 후속 작업 |
| --- | --- | --- |
| P1 | 02 `scripts/sign.ps1:103`는 SwiftDeck-Setup.v 파일을 서명하려 하지만 `installer/setup.iss`는 App02_SwiftDeck-Setup_v 파일을 생성. 새 JSON updater는 중첩 signature 객체를 읽지 못하는 실제 AHK 구조 재현도 실패 | 단일 정책 이행 중 서명/업데이트 실패. 생성 이름과 JSON 파서를 맞추고 실제 signed-shape 회귀 검사 |
| P1 | 04 `installer/setup.iss:43`, `scripts/sign.ps1:172`는 App04_DataRefinery-Setup_v, `src/data_refinery_launcher.py:22`는 App04_DataRefinery_Setup_v regex | 런처가 새 설치 파일을 선택하지 못함. 생성 규칙/탐색을 맞추고 이전 게시 런처도 검수 |
| P1 | 05 미커밋 `scripts/build_all.py:430`은 설치 파일만 체크섬 작성, 같은 파일 `:495,515`는 manifest 체크섬도 요구 | 자기 생성 결과가 검증/승격에서 실패. 작성·필수 목록·검증을 통일. 새 HEAD cc3bc72와 다른 사용자 미커밋 작업 보존 |
| P1 | 06 `scripts/release_helpers.ps1:66`은 generic 장부 사용, Publish-LocalRelease는 내용 다른 기존 장부를 거부. 기존 release에 generic 장부 존재 | 버전만 올려도 승격 충돌. 이전 장부를 지우지 않고 버전별 장부 또는 이전 공식 세트 보존/원자 교체 정책 적용 |
| P1/검증 게이트 | 04·05·06 배포 테스트가 이전 별칭·ZIP·장부 fixture를 사용, 01 optional full OCR은 삭제한 EXE를 다시 참조 | 변경 계약에 맞게 검사를 정비하고 현재 소스로 다시 수행. 사용한다면 01 full OCR 옵션의 삭제/포장 순서 검수. 실제 서명·설치 합격과 구분 |

02의 새 JSON updater는 현재 생성 함수가 넣는 중첩 signature 객체를 파싱하지 못합니다. 실제 AHK 파서로 기존 평평한 장부는 통과하고 같은 artifact에 signed-shape signature를 넣으면 실패함을 재현했습니다. 기록은 02 build/standardization/morning-parser-readonly-3397d311192a49a98b32256f093259c7/result.json에 있습니다. 실제 인증서 서명본 실행 시험은 아닙니다. 01 updater는 GitHub assets API를 읽으므로 이 중첩 장부 문제는 적용되지 않습니다.

이미 게시된 02 v1.4.1에는 SwiftDeck.update.ini가 없다는 것을 현재 GitHub API로 확인했습니다. 이전 앱에서 업데이트 실패 가능성은 구버전 전환 게이트이며, 최신 소스가 여전히 INI만 읽는다고 설명하지 않습니다.

07은 현재 소스 **0.1.17**, 공개 최신은 0.1.16입니다. 기존 설정 이관의 hard-link 미지원 문제에는 Windows MoveFileW fallback이 추가되어 이전 “fallback 없음” 설명을 정정했습니다. FAT32/exFAT는 hard link를 지원하지 않습니다([Microsoft 비교표](https://learn.microsoft.com/en-us/windows/win32/fileio/filesystem-functionality-comparison)). 현재 fallback 코드·mock 검사와 실제 USB/대상 경합/권한 오류 검수는 구분합니다. 공식 배포 승격은 아직 hard link를 사용하므로 배포 작업 폴더의 지원도 확인합니다.

04 설치 정의의 `_internal` 사전 정리와 실패/취소 후 이전 런타임 보존, 여섯 앱의 실행 중 업그레이드·제거·실제 업무 화면은 실제 Windows에서 확인해야 합니다. 소스 검사로 설치 성공을 표시하지 않습니다.

## 프로젝트별 사용자 작업 파일

| 앱 | 프로젝트 루트 파일 |
| --- | --- |
| 01 | [RELEASE_CHECKLIST.md](../../01_ClipOCR-Pro/RELEASE_CHECKLIST.md) |
| 02 | [RELEASE_CHECKLIST.md](../../02_SwiftDeck/RELEASE_CHECKLIST.md) |
| 04 | [RELEASE_CHECKLIST.md](../RELEASE_CHECKLIST.md) |
| 05 | [RELEASE_CHECKLIST.md](../../05_FileOperation/RELEASE_CHECKLIST.md) |
| 06 | [RELEASE_CHECKLIST.md](../../06_Stepwise/RELEASE_CHECKLIST.md) |
| 07 | [RELEASE_CHECKLIST.md](../../07_eml-viewer/RELEASE_CHECKLIST.md) |

순서는 이름/배포 형태·삭제 범위 결정 → 위 정합성 수정 → 현재 소스 회귀/빌드 → 검수 커밋/버전 확정 → 전체 자료 백업 → 사용자 SimplySign 세션에서 앱/설치 서명 → 해시/서명/출처 검증 → 격리 Windows 설치/실행 중 업그레이드/제거 검수 → draft 다운로드 대조 → 공개입니다. 정확한 앱별 명령과 아직 실행하면 안 되는 게이트는 각 파일에 있습니다.

## 검수 근거와 경계

최신 Git 상태·폴더 목록/용량·추적/참조·현재 함수/CLI 인자·현재 GitHub 최신 자산 목록을 읽고 담당별 감사 및 독립 교차 검수를 수행했습니다. 05는 그 사이 커밋과 빌드 파일이 변경되어 이전 검사 숫자를 최신 성공으로 표시하지 않습니다. source/실제 사용자 설정을 고쳐 얻은 검수 결과가 아닙니다. 삭제 명령과 서명/게시 명령은 실행하지 않았습니다.

10월 8일 추가 감사에서 Gemini High의 내용 있는 완료 응답 2회(01/02 공동 약49.42초, 04/05 공동47.56초)를 사용했고 06/07 공동45초 빈 응답 timeout은 완료로 계산하지 않았습니다. 10월 9일 문서 마무리는 현재 소스의 독립 읽기 검수입니다. 계정 청구/크레딧 감소는 확인하지 않았습니다.

최종 파일명/삭제 후보는 사용자 검토 대기입니다. 깨끗한 검수 커밋·새 버전·사용자 서명 세션·실제 Windows 검증이 없어 새 릴리즈 완료로 표시하지 않습니다. 이전 [표준화 검수 기록](STANDARDIZATION_FINAL_REVIEW.md)은 당시 증거로 보존합니다.

최종 문서 상대 링크는 모두 실제 파일을 확인했습니다. git diff --check는 01·02·04·05·06 통과, 07은 다른 작업의 tests/test_main_window.py:451에 EOF 빈 줄 1건이 남아 있습니다. 이번 읽기 검토에서 그 소스를 임의 수정하지 않았습니다. 07은 그 사이 트레이·시작 시 실행·단일 인스턴스·윈도우 관리 변경도 추가돼 현재 소스의 새 회귀/실환경 검수가 필요합니다.


## 2026-10-09 서명 준비 후속 정정

본 문서의 앞부분 차단 결함은 발견 당시 상태입니다. 최신 요청에 따라 이름·검증·게시 정합성 결함을 수정하고 각 RELEASE_CHECKLIST에 최신 검수 결과를 반영했습니다. 01/02/04/06은 새 후보 버전으로 준비하며, 05 1.4.3·07 0.1.17·08 1.0.0은 미게시 후보를 유지합니다. 현재 실행 명령은 [SIGNING_RELEASE_HANDOFF.md](SIGNING_RELEASE_HANDOFF.md)를 사용합니다.

삭제 후보 검토는 여전히 사용자와 별도로 합의해야 합니다. 이번 준비에서 해당 후보 폴더를 삭제하거나 기존 서명/공개본을 덮어쓰지 않았습니다. 08의 사전 삭제 상태인 Release 작업은 커밋 원본 42개를 별도 보존하여 근거를 남겼습니다. 실제 KSP 서명·Windows 설치/업그레이드/제거·새 GitHub 공개는 준비 검사와 구분하여 진행합니다.
