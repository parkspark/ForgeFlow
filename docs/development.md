# 개발 안내

모든 명령은 저장소 루트에서 실행합니다. Python 3.12 이상과 Windows를 기준으로 합니다.

## 개발 환경

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
forgeflow
```

PowerShell 활성화 스크립트를 사용할 수 없으면 `.\.venv\Scripts\python.exe`로 Python 명령을 직접 실행할 수 있습니다.

## 코드 위치

| 위치 | 역할 |
| --- | --- |
| `src/forgeflow/domain/` | Job, StageState, Artifact 등 저장 가능한 데이터 모델 |
| `src/forgeflow/services/` | 작업 저장, 외부 프로세스 수명 관리, 파이프라인, 환경 점검 |
| `src/forgeflow/adapters/` | Modeling, Blender, Rigging, Unity 외부 도구 연동 |
| `src/forgeflow/adapters/unity/` | 프로젝트 검증·FBX 가져오기, 프롬프트 생성, receipt 해석 |
| `src/forgeflow/ui/panels/` | 작업 화면과 재사용 패널 |
| `src/forgeflow/ui/dialogs/` | 설정 등 대화상자 |
| `src/forgeflow/ui/main_window.py` | 서비스 구성, 시그널 연결, 화면 간 이동 |
| `src/forgeflow/resources/` | 실행 파일에 포함하는 앱 리소스 |
| `tests/adapters/`, `tests/services/`, `tests/ui/` | 각 계층의 회귀 테스트 |
| `tests/integration/` | 실제 외부 엔진 없이 실행하는 모의 파이프라인 테스트 |
| `tests/test_version.py` | 패키지와 Windows EXE의 버전 일치 검사 |
| `scripts/e2e/` | 설치된 외부 엔진을 실제로 실행하는 수동 검증 스크립트 |
| `packaging/` | EXE spec, 런처, 버전 메타데이터 |
| `docs/images/screenshots/`, `docs/images/changelog/` | 소개 화면과 변경 이력 이미지 |

기능을 추가할 때는 해당 계층과 같은 테스트 폴더에 배치합니다. UI는 서비스와 어댑터를 호출하며, 저장 모델은 UI에 의존하지 않습니다. 외부 프로세스 시작·중단과 승인 상태 변경은 기존 서비스 및 어댑터 경계를 유지합니다.

`adapters/blender_bridge.py`는 Blender 에이전트의 별도 Python 환경에서 파일로 실행됩니다. ForgeFlow 패키지 내부에만 있는 공통 모듈에 의존시키지 않으며, EXE에도 원본 파일로 포함합니다.

## 형식과 테스트

Ruff 설정은 `pyproject.toml`에서 관리합니다. 들여쓰기, 줄바꿈, import 순서는 아래 명령으로 정리합니다.

```powershell
python -m ruff check . --fix
python -m ruff format .
```

변경 후에는 다음을 확인합니다.

```powershell
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
python -m forgeflow.main --smoke-test
```

pytest는 `src/`를 자동으로 import 경로에 추가합니다. UI와 Qt 프로세스 테스트는 `tests/conftest.py`의 세션 공통 `qapp` fixture를 사용하며, UI 테스트에는 자동 적용됩니다. 창을 띄우지 않는 `offscreen` 모드가 기본값입니다.

계층별로 확인하려면 `python -m pytest tests/ui -q`처럼 폴더를 지정합니다. 실제 Blender, Pixal3D, UniRig, Unity 환경을 사용하는 E2E 명령과 선행 조건은 [README](../README.md#테스트)를 참고하세요.

## Windows EXE

```powershell
python -m pip install -e ".[build]"
powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1
```

빌드 설정은 `packaging/ForgeFlow.spec`에 있습니다. 빌드 중간 결과는 `build/`, 실행 파일은 `dist/`에 생성됩니다. 실행 중인 EXE를 덮어쓸 수 없으면 `dist/ForgeFlow-update.exe`로 저장합니다.

## 생성 파일과 로컬 자료

`.venv/`, `build/`, `dist/`, `logs/`, `.tmp/`, `backup/`, 테스트·Ruff 캐시는 Git에서 제외합니다. 기존 로컬 자료인 `docs/architecture/`, `docs/presentation/`도 현재 제외 설정을 유지합니다. 코드 정리 과정에서 이 자료들을 자동 삭제하지 않습니다.

앱 설정과 실제 작업은 기본적으로 `%USERPROFILE%\Documents\ForgeFlow`에 저장합니다. `job.json` 스키마나 결과 경로를 변경할 때는 저장·복구 및 기존 작업 호환성 테스트를 함께 확인합니다.
