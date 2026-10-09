# Data Refinery 공통 정비 2단계 검수

검수일: 2026-10-07. 버전 2.0.1과 설치 AppId·기본 설치 위치 정책은 유지했습니다. LocalDataMart 기능은 변경하지 않았습니다.

## 변경 결과

- 기존 서명·스테이징·별칭·검증·게시 절차는 유지하고, 배포 실패 주입 검사 35개 assertion을 다시 통과했습니다.
- 설치의 `RestartApplications=no`를 명시했습니다. 설치 후 실행은 새 EXE를 가리키며 구버전 실행 상태를 자동 재시작하지 않습니다.
- `PrepareToInstall`에서 기존 uninstall AppId 등록과 설치 위치를 확인하고, 해당 폴더의 정규 `App04_DataRefinery_v<major.minor.patch>.exe` 중 엄격히 이전 버전만 해시와 함께 기록합니다. 등록·위치가 일치하지 않으면 정리를 하지 않습니다.
- 이 목록을 Restart Manager의 추가 확인 대상으로 등록합니다. 첫 페이로드 파일의 `BeforeInstall`은 정상 종료 요청 이후 실행되며, 기존 EXE가 계속 사용 중이거나 쓰기 접근이 거부되면 복사 전에 설치를 차단합니다. 강제 종료는 없습니다.
- 설치 성공 단계 `ssDone`에서 새 EXE가 컴파일 당시의 SHA-256과 같고 구형 EXE가 사전 기록과 같을 때만 구형 파일 하나씩 정리합니다. 현재·더 높은 버전, 다른 파일, 변경된 파일과 `UserSetting`은 정리하지 않습니다.
- 설치 실패·취소 전 구형 EXE를 미리 삭제하지 않습니다. 파일 여러 개의 설치 전체를 전원 장애까지 원자적으로 보장하는 변경은 아닙니다.

## 검증과 한계

- Inno Setup 6.7.3: 격리한 더미 페이로드로 설치 정의 컴파일 통과. 6/7 추가 리소스 API의 인자 분기를 확인했습니다.
- 독립 에이전트가 `PreviousAppId`의 전처리 결과를 기존 GUID와 비교하는 컴파일 assertion을 통과했습니다.
- `tests/test_installer_upgrade.py`는 실제 Pascal 콜백을 이용해 버전 판별, 파일 잠금, 소유권·새 파일 해시·구형 파일 해시 조건을 검증하는 격리 시험입니다. 이 PC에서 컴파일은 통과했지만 Windows Application Control이 미서명 실행을 차단했습니다. WinError 4551과 상관된 Code Integrity 3077 이벤트를 확인했으며 **실행 검사는 1개 skip**으로 기록했습니다. 정책을 우회하지 않았습니다.
- 새 테스트 Ruff 검사와 `git diff --check` 통과. 기존 `docs/project-structure.md`의 사용자 변경은 그대로이며 SHA-256은 `57BAF13077DAA77DC19D6A39D337E3C21E669F3D6B7398D18BE3EC8F24064643`입니다.
- Antigravity Gemini 3.8 Flash High/effort high의 설치 초안을 검토했습니다. 실제 이벤트 순서와 지원하지 않는 파일 API 제안은 공식 소스와 컴파일 결과를 기준으로 수정했습니다. Codex 담당 외 별도 에이전트가 설치 소유권·해시·정리 순서를 교차 검수했습니다.

실제 서명, 설치·업그레이드·제거, 활성 DB 작업 중 종료·거부, 실패·취소 후 구버전 실행과 데이터 보존은 실행하지 않았습니다. 기존 사용자 지정 설치가 기본 위치와 다르면 현재 고정 경로 정책을 유지하고 구형 파일을 임의 정리하지 않습니다. 기존 공식 파일·태그·게시 자산을 교체하지 않았습니다.

근거: [Inno 설치 이벤트](https://jrsoftware.org/ishelp/topic_scriptevents.htm), [정상 종료 요청](https://jrsoftware.org/ishelp/topic_setup_closeapplications.htm), [추가 리소스 등록](https://jrsoftware.org/ishelp/topic_isxfunc_registerextracloseapplicationsresource.htm), [실제 설치 순서](https://github.com/jrsoftware/issrc/blob/main/Projects/Src/Setup.Install.pas).
