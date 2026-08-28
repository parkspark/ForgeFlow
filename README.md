# ForgeFlow

> **An all-in-one orchestrator application to generate 3D models from images, modify assets, and automate rigging and animation in Unity—all from a single unified interface.**

ForgeFlow는 기존 Pixal3D 이미지→3D 생성기, 승인 기반 Blender 자연어 에이전트, 로컬 UniRig/Blender Humanoid 후처리를 하나의 한국어 PySide6 Windows 앱에서 연결하는 오케스트레이터입니다. CUDA/WSL/Pixal3D, Ollama, Blender MCP, UniRig 의존성을 ForgeFlow 환경에 합치지 않고 각 프로젝트의 기존 실행 환경을 별도 프로세스로 사용합니다. Unity 프로젝트 임포트와 Play Mode 검증은 다음 단계의 범위입니다.

이전에 만들었던 모듈들을 합쳐, 하나의 앱에서 이미지를 넣어서 모델링 생성, 모델링 수정, Unity에서 리깅 및 애니메이션까지 한 곳에서 컨트롤할 수 있도록 통합화 중입니다.

- **Image-to-3D Generation**: https://github.com/parkspark/modeling_local_mcp
- **Blender Control & Refinement**: https://github.com/parkspark/blender-control-mcp
- **Unity Rigging & Animation**: https://github.com/parkspark/unity_local_mcp


## 제공 기능

- PNG/JPG/JPEG 입력과 작업별 원본 복사
- Pixal3D 실시간 stdout/stderr 로그, GLB/BLEND/FBX 및 PNG 미리보기 검증
- 생성 GLB를 Blender 입력으로 자동 연결
- Blender/MCP/Ollama/WSL/GPU 연결 상태 표시
- `scene.inspect`로 실제 오브젝트 이름과 dimensions 표시
- 자연어 계획 생성과 승인 전 쓰기 차단
- 승인된 계획의 SHA-256을 고정한 뒤 동일 계획만 MCP로 실행
- `blender/vNNN` 버전과 GLB/BLEND/FBX 계보·해시 기록
- 원본 해시 불변 검사, 누락/0바이트 산출물 실패 처리
- 최신 Blender GLB(없으면 모델링 원본)를 입력으로 사용하는 버전별 UniRig 자동 리깅
- Unity Humanoid 본 이름 후처리, FBX/BLEND 구조 검증, rest/pose 미리보기
- 리깅 결과의 `unity_input_path` 등록으로 다음 Unity 단계 입력 고정
- 원자적 `job.json` 저장과 재시작 복구
- 파일/결과 폴더 열기, 실패 재시도, 실행 취소

## 설치와 실행

앱의 `설정`에서 다음 실행부터 사용할 프로젝트 경로, Blender 실행 파일, 작업 루트, Ollama 주소/모델을 저장할 수 있습니다. ForgeFlow는 기존 시스템에서 확인한 `qwen3-coder:30b`와 설치된 Blender 5.2 경로를 기본값으로 사용하며 기존 프로젝트 설정 파일을 수정하지 않습니다.

## 사용자 흐름

1. `새 작업`에서 이미지와 작업 이름을 선택합니다.
2. `모델 생성`을 누르면 현재 설정의 Ollama 모델을 VRAM에서 내린 뒤 기존 `generate_model.ps1`을 실행합니다.
3. GLB/BLEND/FBX와 미리보기가 모두 존재하고 0바이트가 아닐 때만 모델링을 완료로 기록합니다.
4. Blender 탭에서 `장면 검사`로 실제 메시 이름을 확인합니다.
5. 정확한 이름을 포함한 한국어 요청을 입력하고 `실행 계획 만들기`를 누릅니다.
6. 계획과 모든 구조화 인자를 검토한 뒤 `승인하고 실행`을 누릅니다. 취소하면 `asset.*` 도구는 호출되지 않습니다.
7. 결과는 새 `blender/vNNN/<operation-id>` 폴더에 저장되고 다음 편집의 기본 입력이 됩니다.
8. `3. Humanoid 리깅` 탭에서 실제 절대 GLB 경로와 부모 버전을 확인합니다. 가장 최신의 성공한 Blender GLB가 기본 선택되며 Blender 결과가 없으면 `modeling/source.glb`가 선택됩니다. 콤보박스에서 작업에 등록된 다른 GLB 버전을 명시적으로 선택할 수 있습니다.
9. 정면 대칭 T-pose/A-pose의 이족보행 사람형 모델임을 확인하고 Seed(기본 `12345`)를 지정한 뒤 최종 확인창의 입력·출력 경로를 검토하여 실행합니다.
10. ForgeFlow는 Ollama VRAM을 해제한 뒤 신뢰된 `RiggingAdapter`가 `rig_humanoid.ps1`을 실행 파일/인자 배열로 직접 호출합니다. 골격 생성, 스키닝, 메시 병합, Humanoid 후처리와 구조 검증을 모두 통과해야 완료됩니다.
11. 완료 후 Humanoid FBX/BLEND, 결과 폴더, rest/pose 미리보기를 탭에서 열 수 있습니다. 미리보기만 실패하면 구조 리깅은 `completed`로 유지하되 별도 경고를 표시합니다.

앱이 실행 중 강제 종료되면 `running` 단계는 다음 시작 시 `failed`로 복구되어 재시도할 수 있습니다. 완료된 `modeling/source.*`는 덮어쓰지 않습니다.

## 저장 형식

```text
jobs/<job-id>/
├─ job.json
├─ input/reference.png|jpg|jpeg
├─ modeling/source.glb, source.blend, source.fbx, preview.png
├─ blender/v001/<operation-id>/...
├─ rigging/v001/
│  ├─ rig_<ascii-job-id>_v001_skeleton.fbx
│  ├─ rig_<ascii-job-id>_v001_skin.fbx
│  ├─ rig_<ascii-job-id>_v001_rigged.glb
│  ├─ rig_<ascii-job-id>_v001_unity.blend, .fbx
│  ├─ rig_<ascii-job-id>_v001_humanoid.blend, .fbx
│  ├─ rig_report.json
│  ├─ rest_preview.png, pose_preview.png
│  └─ rigging.log
├─ logs/
└─ .runs/                 # 엔진의 격리된 시도별 원본 출력
```

`job.json` 스키마 버전은 2입니다. schema v1 작업은 리깅 단계와 새 필드의 기본값을 보존적으로 추가해 v2로 마이그레이션하며, 지원하지 않는 미래 버전은 거부합니다. 작업/단계 상태, 리깅 요청과 Seed, 절대 산출물 경로, SHA-256, `parent_path`, Blender 승인 계획, 버전 계보, 로그와 오류를 저장합니다. `unity_input_path`는 검증에 성공한 최종 Humanoid FBX만 가리킵니다.

## UniRig 환경과 성공 판정

기본 환경은 `Ubuntu-24.04`의 사용자 `park`, `/home/park/local-modeling/UniRig`, Conda 환경 `/home/park/miniforge3/envs/unirig`, 공식 체크포인트 캐시 `/home/park/.cache/huggingface/hub/models--VAST-AI--UniRig`입니다. 앱의 비동기 환경 표시줄은 PowerShell 진입점, WSL 배포판, UniRig Python/저장소/`src/model/sdpa_mha.py`/체크포인트, Blender를 점검합니다. 확인 기준 commit은 `6793c6640ff01c8fb389f3993434124bb43d2933`이며 다른 commit은 실제 값을 경고로 표시하되 그것만으로 실행을 차단하지 않습니다.

종료 코드 0만으로 성공하지 않습니다. 8개 핵심 파일의 존재/크기, 입력 GLB의 실행 전후 SHA-256, 파싱 가능한 `rig_report.json`, `status == "PASS"`, 양수인 본/정점 수, 빈 누락 본 배열, 전체 정점 웨이트, 1~4개의 최대 영향 본, report의 최종 FBX/BLEND 경로 일치를 모두 확인합니다. PASS는 파일·본·웨이트 구조 검사 통과일 뿐 애니메이션이나 변형의 육안 품질 보장이 아닙니다.

UniRig 업스트림은 WSL 경로의 공백을 처리하지 못합니다. 입력 또는 작업 출력 경로에 공백이 있으면 `%LOCALAPPDATA%\ForgeFlow\rigging-staging\<job-id>\vNNN` 아래의 공백 없는 경로로 입력을 복사해 실행하고, 검증된 결과만 실제 `rigging/vNNN`으로 옮깁니다. staging 루트 자체에 공백이 있으면 실행 전에 명확히 실패합니다. 실패/취소 중간 파일은 Job 산출물이나 `unity_input_path`로 등록하지 않으며 `rigging.log`는 진단용으로 남깁니다.

## 구조와 보안 경계

- `domain`: 작업, 단계, 산출물, 파이프라인 이벤트
- `services`: 원자적 저장, QProcess 실행, 작업 오케스트레이션, 환경 점검
- `adapters/modeling_adapter.py`: 기존 PowerShell 생성기 호출과 결과 정규화
- `adapters/blender_adapter.py`: 경로·버전·해시·세션 결과 검사
- `adapters/rigging_adapter.py`: GLB 입력/버전/staging/UniRig 명령/report/산출물/계보 검증
- `adapters/blender_bridge.py`: blender-prompt-agent 가상환경에서 기존 에이전트/MCP 클래스만 호출하는 JSON Lines 브리지
- `ui`: 프로젝트, 모델링, Blender 계획/승인, 로그 패널

외부 프로세스는 실행 파일과 인자 배열로 시작하며 셸 문자열을 만들지 않습니다. Blender 브리지는 임의 Python/셸을 실행하지 않고 `blender-control-mcp`의 allow-list 도구만 사용합니다. 제안 단계는 읽기 도구만 자동 실행하고 항상 승인을 거부한 세션을 남깁니다. 승인 단계는 제안 JSON, 계획 SHA-256, 입력 경로, 출력 버전 폴더를 다시 검증한 뒤 정확히 그 계획만 실행합니다.

## 테스트

ForgeFlow:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m pytest -q
python -u -m forgeflow.main --smoke-test
```

기존 프로젝트 회귀 테스트:

```powershell
python -m pytest -q                                      # modeling_local_mcp
.\.venv\Scripts\python.exe -m pytest -q                # blender-prompt-agent
.\.venv\Scripts\python.exe -m pytest -q                # blender-control-mcp
```

실제 E2E 재실행:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -u .\scripts\run_blender_e2e.py --asset C:\absolute\asset.glb --image C:\absolute\reference.png
python -u .\scripts\run_full_e2e.py --image C:\absolute\reference.png
python -u .\scripts\run_rigging_e2e.py --asset "C:\absolute\humanoid.glb" --seed 12345
```

리깅 E2E는 반드시 ForgeFlow `RiggingAdapter`와 동일 검증 로직을 사용하며 `logs/rigging-e2e-evidence.json`에 입력 전후 해시, Seed, UniRig commit, 실행 시간/종료 코드, 모든 산출물 절대 경로·해시, report 핵심 값, 미리보기와 최종 판정을 기록합니다.

## 현재 제한

- Blender 자연어 탭의 지원 범위는 장면 검사, 재질 목록/속성, transform, Bevel, Decimate, Smooth shading, GLB/BLEND/FBX 내보내기입니다. 리깅은 LLM/MCP allow-list가 아니라 별도 신뢰 경계의 `RiggingAdapter`에서만 실행됩니다.
- 사람형 여부를 자동 판정하지 않습니다. 이족보행 Humanoid와 정면 대칭 T-pose/A-pose를 권장하며 얼굴 리그는 포함하지 않습니다.
- 자동 리토폴로지를 수행하지 않습니다. 고폴리 모델은 게임 투입 전 최적화가 필요하고 갑옷·치마·장식 메시의 웨이트는 수동 보정이 필요할 수 있습니다.
- 이번 완료 범위는 Humanoid FBX/BLEND 생성과 구조/미리보기 검증까지입니다. Unity 프로젝트 임포트, Avatar 생성, 애니메이션 재생, Play Mode는 아직 검증하지 않습니다.
- 자연어 계획 품질은 로컬 Ollama 모델에 의존하며 정확한 오브젝트/재질 이름이 요청에 없으면 에이전트가 실행 대신 후보 확인을 요구할 수 있습니다.
- Ollama가 구조화된 `tool_calls` 대신 설명문만 반환하면 ForgeFlow가 계획 형식을 한 번 자동 재요청합니다. 재요청도 실패하면 쓰기 작업 없이 실패로 표시합니다.
- Pixal3D 1024 생성은 이 PC의 실제 E2E에서 약 6분 이상 걸렸으며 UV 파라미터화 동안 GPU 사용률이 낮아도 CPU 작업이 계속될 수 있습니다.
- MCP의 고유 operation-id 하위 폴더는 비덮어쓰기 보장을 위해 유지합니다.
- `.runs`의 시도별 엔진 출력은 진단과 실패 증거를 위해 자동 삭제하지 않습니다.

## License

This project is licensed under the [Apache-2.0 License](LICENSE).
