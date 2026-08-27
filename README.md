# ForgeFlow

> **An all-in-one orchestrator application to generate 3D models from images, modify assets, and automate rigging and animation in Unity—all from a single unified interface.**

ForgeFlow는 기존 Pixal3D를 이용한 이미지→3D 생성기와 승인 기반 Blender 자연어 에이전트, Unity 자동화 모듈을 하나의 한국어 PySide6 Windows 앱에서 연결하는 오케스트레이터입니다. CUDA/WSL/Pixal3D, Ollama, Blender MCP, Unity 의존성을 ForgeFlow 환경에 합치지 않고 각 프로젝트의 기존 실행 환경을 별도 프로세스로 사용합니다.

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

앱이 실행 중 강제 종료되면 `running` 단계는 다음 시작 시 `failed`로 복구되어 재시도할 수 있습니다. 완료된 `modeling/source.*`는 덮어쓰지 않습니다.

## 저장 형식

```text
jobs/<job-id>/
├─ job.json
├─ input/reference.png|jpg|jpeg
├─ modeling/source.glb, source.blend, source.fbx, preview.png
├─ blender/v001/<operation-id>/...
├─ logs/
└─ .runs/                 # 엔진의 격리된 시도별 원본 출력
```

`job.json`에는 스키마 버전, 작업/단계 상태, 타임스탬프, 생성 설정, 절대 산출물 경로, SHA-256, Blender 요청/승인 계획, 계획 해시, 버전 계보, 세션/프로세스 로그와 오류를 저장합니다. 임시 JSON을 flush/fsync한 뒤 `os.replace`로 교체합니다.

## 구조와 보안 경계

- `domain`: 작업, 단계, 산출물, 파이프라인 이벤트
- `services`: 원자적 저장, QProcess 실행, 작업 오케스트레이션, 환경 점검
- `adapters/modeling_adapter.py`: 기존 PowerShell 생성기 호출과 결과 정규화
- `adapters/blender_adapter.py`: 경로·버전·해시·세션 결과 검사
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
```

각 폴더의 `logs/*e2e-evidence.json`에 입력/결과 해시, 실제 대상 이름, dimensions, 승인 계획, 세션과 산출물 경로가 있습니다.

## 현재 제한

- 지원 범위는 장면 검사, 재질 목록/속성, transform, Bevel, Decimate, Smooth shading, GLB/BLEND/FBX 내보내기뿐입니다.
- 자유 메시 모델링, 임의 Blender Python, UV 편집, 리토폴로지, 리깅은 UI와 브리지에서 제공하지 않습니다.
- 자연어 계획 품질은 로컬 Ollama 모델에 의존하며 정확한 오브젝트/재질 이름이 요청에 없으면 에이전트가 실행 대신 후보 확인을 요구할 수 있습니다.
- Pixal3D 1024 생성은 이 PC의 실제 E2E에서 약 6분 이상 걸렸으며 UV 파라미터화 동안 GPU 사용률이 낮아도 CPU 작업이 계속될 수 있습니다.
- MCP의 고유 operation-id 하위 폴더는 비덮어쓰기 보장을 위해 유지합니다.
- `.runs`의 시도별 엔진 출력은 진단과 실패 증거를 위해 자동 삭제하지 않습니다.

## License

This project is licensed under the [Apache-2.0 License](LICENSE).
