# Data Refinery 공개 코드 안내

원본 CSV·Excel을 복구·정규화하고 집계한 뒤 데이터셋을 배포하는 앱입니다. 원본 파일, 사용자 설정과 기존 배포본을 보존하는 흐름을 유지합니다.

| 역할 | 진입점 |
| --- | --- |
| 앱 실행·호환 UI | `src/data_refinery.py`, 기본 화면 `src/qt/main_window.py` |
| CSV 복구·프로모션 정리·집계·배포 화면 | `src/qt/tabs/` |
| 집계와 데이터셋 | `src/data_aggregator.py`, `src/dataset_config.py`, `src/dataset_engine.py` |
| 설정 위치·검증 이관 | `src/app_paths.py` |
| 업데이트 설정·최근 작업·프리셋 | `src/update_checker.py`, `src/session_memory.py`, `src/preset_manager.py` |
| 완전한 파일 저장 후 교체 | `src/atomic_write.py` |
| 버전·번들 | `src/version.py`, `assets/` |
| 설치·패키징 설계 | `installer/setup.iss`, `installer/data_refinery.spec`, `installer/data_refinery_launcher.spec` |
| 빌드·서명·검증 | `scripts/build.ps1`, `scripts/sign.ps1` |
| 사용자 자료 백업·검증·새 폴더 복원 | `scripts/Manage-UserData.ps1`, `scripts/user-data-profile.json` |
| 회귀 검사 | `tests/`, `scripts/test_user_data_backup.ps1` |

`build/`는 임시 작업, `dist/`는 빌드·미서명 검증본, `release/`는 검증한 공식 서명 파일입니다. 사용자 자료는 `UserSetting/`에 있고 패키지·Git 추적 대상에서 제외합니다. 개인 작업 메모와 인증 자료를 공개 코드 안내에 넣지 않습니다.

[백업·복원 안내](USER_DATA.md)는 기본 저장소와 외부 원본·공유 배포 폴더의 차이를 설명합니다. [이번 검수](STANDARDIZATION_PHASE3_5_REVIEW.md)는 자동 검사와 남은 실제 환경 검수를 구분합니다.
