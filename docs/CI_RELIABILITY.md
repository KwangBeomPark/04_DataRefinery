# GitHub Actions 실패 조사 및 재발 방지

2026-10-16에 이 저장소에서 조회 가능한 실행 26건(2026-09-26 이후)을 확인했습니다.
조사 시작 시 성공 23건·실패 3건이며, 최신 소스 `8a8f013`의 실행은 성공했습니다.
서로 다른 원인이며 동일한 앱 장애의 반복으로 판단하지 않습니다.

| 실패 | 실제 원인 | 기존 후속 조치 |
| --- | --- | --- |
| [2026-10-16 / 38859555007](https://github.com/KwangBeomPark/04_DataRefinery/actions/runs/38859555007) | 새 집계 보호 테스트가 C: TEMP에서 D: 체크아웃까지 `os.path.relpath`를 계산해 ValueError 발생 | `8a8f013`: fixture 폴더에서 상대 경로를 검사하고 cwd 복원. [후속 실행 성공](https://github.com/KwangBeomPark/04_DataRefinery/actions/runs/38859983181) |
| [2026-10-06 / 37509274335](https://github.com/KwangBeomPark/04_DataRefinery/actions/runs/37509274335) | 서명 실패 주입 검사는 성공했지만 마지막 외부 명령의 종료 코드 1이 CI로 누출됨 | `ea625f1`: 성공한 검사 스크립트 끝에서 명시적 `exit 0` |
| [2026-09-27 / 36314416949](https://github.com/KwangBeomPark/04_DataRefinery/actions/runs/36314416949) | 테스트의 변수 `l`이 Ruff E741 규칙 위반 | 후속 변경에서 명확한 변수명 사용 |

## 이번 예방 조치

- **검사 단일화:** `scripts/check.ps1`을 개발·일반 빌드·CI가 함께 사용합니다.
  기존에는 CI만 린트를 실행하고, 일반 빌드만 백업 검사를 실행하는 차이가 있었습니다.
  이제 의존성 일관성 → 린트 → 전체 Python 테스트 → 서명 실패 주입 → 백업 안전성을 공통 검사합니다.
- **푸시 전 차단:** `.githooks/pre-push`가 동일한 검사를 실행합니다. 각 체크아웃에서
  `git config --local core.hooksPath .githooks`가 필요합니다. 기존 사용자 훅은 먼저 확인합니다.
  훅은 서버 강제 정책이 아니므로 복제만으로 자동 활성화되지는 않습니다.
- **환경 격리:** AppData/TEMP/TMP를 고유한 `build/checks-<id>`에 지정하고 마지막에 복원합니다.
  개발 PC에 설치된 앱의 설정·DB는 검사 대상으로 사용하지 않습니다. fixture는 Git 제외 build에 남습니다.
- **환경 차이 재현:** `tests/test_windows_quality_gate.py`는 비어 있는 드라이브 문자에만
  일시적인 SUBST 매핑을 만들고, 자식 Python의 cwd와 TEMP가 실제로 다른 드라이브인지 확인한 뒤
  집계 보호 테스트를 실행합니다. 매핑은 finally에서 해제합니다. 기존 매핑을 덮어쓰지 않습니다.
- **실패 전달 검증:** Python 버전·pip·린트·앱 테스트 각각의 외부 명령이 실패할 때
  검사가 즉시 중단되고 성공 문구나 종료 코드 0을 반환하지 않는지 확인합니다.
  서명·백업 검사는 Windows PowerShell 자식 프로세스로 실행합니다.
- **개발 의존성 고정:** `requirements-dev.txt`가 앱의 고정 의존성과 Ruff 버전을 관리합니다.
  `-SkipTests` 미서명 CI 빌드는 기존과 같이 서명 가능한 테스트 완료 기록을 만들지 않습니다.
- **일반 검사와 Office 통합 검사 분리:** 새 푸시 검사를 실제 실행하던 중 일반 템플릿 테스트가
  설치된 Excel을 실행하고 `Call was rejected by callee`로 실패하는 것을 발견해 푸시를 차단했습니다.
  일반 검사는 COM 미설치 안내 파일 경로를 고정해 검사하고, COM 연결 실패가 그대로 전달되며
  작업한 통합 문서·Excel 인스턴스가 정리되는지 모의 COM으로 검증합니다. 실제 Excel 연결·모델·피벗·
  새로고침은 기존 `DATAREFINERY_EXCEL_TESTS=1` 통합 검사를 명시적으로 실행합니다.
  실제 Excel의 간헐적인 COM 거부 응답 자체를 이번 변경으로 해결했다고 주장하지 않습니다.

## 실행과 완료 기준

```powershell
python -m pip install -r requirements-dev.txt
git config --local core.hooksPath .githooks
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
```

main 푸시 후 **그 커밋**의 `Windows release check`가 끝날 때까지 결과를 확인합니다.
로컬 검사는 설치 파일 빌드를 대신하지 않습니다. CI는 공통 검사 외에 미서명 앱·런처·설치 파일
빌드와 설치·재설치·제거, UserSetting 보존을 확인합니다.

과거 실패 알림·기록은 그대로 남습니다. 알림을 끄거나 검사 생략·자동 재시도로 실패를 감추지 않습니다.
이 조치는 확인된 검사 누락·환경 차이를 줄이며, 향후 모든 GitHub 인프라·의존성 장애를 보장하지 않습니다.
실제 SimplySign 서명, 공유 폴더·Excel 새로고침 및 수동 GUI 검수는 별도 검증입니다.
