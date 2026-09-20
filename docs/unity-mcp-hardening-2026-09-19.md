# Unity MCP 강화 — 2026-09-19

[검토 기록](unity-mcp-review-2026-09-19.md)에서 확인한 쓰기 대상·재시도·저장·검증 경계를
`unity_mcp`와 `unity_local_mcp`에 구현했다. 기존 작업 트리의 변경을 유지했으며 커밋은 하지 않았다.

## 변경 내용

| 영역 | 적용한 동작 |
| --- | --- |
| 프로젝트 보호 | 변경 도구와 호스트 파일 도구 실행 직전에 프로젝트를 확인한다. TCP 요청에도 프로젝트·Bridge generation·request ID·기한을 넣고 Bridge가 실행 직전에 검사한다. |
| 저장 범위 | 새 씬 생성과 Save As 모두 Job 출력 범위를 적용한다. 사용자가 명시하거나 선택한 기존 씬은 편집할 수 있고, 원문에 명시한 출력 폴더에는 새 씬을 만들 수 있다. 폴더 지정만으로 기존 외부 씬을 덮어쓸 수 없으며, 모델의 계획·repair 증거가 저장 권한을 늘릴 수 없다. |
| 재시도 | 연결 전에 실패한 쓰기만 재시도할 수 있다. 전송 후 응답 유실·시간 초과는 실행 여부 불명으로 반환하며 자동 재전송하지 않는다. 같은 generation 안에서는 request ID와 본문을 비교해 완료 결과를 재사용한다. |
| 저장 결과 | Bridge 저장 실패를 오류로 반환한다. 호스트는 `saved=true`, 예상 경로, `isDirty=false`를 확인해야 저장 대기를 해제한다. |
| 선택 자산 | 새 읽기 도구 `unity_get_asset_instances`로 실제 배치, 출처 path/GUID, 활성 mesh/renderer, 씬·프로젝트와 카메라 bounds를 측정한다. FBX는 원본 `sharedMesh`의 출처까지 확인하므로 다른 자식 Cube로 대체할 수 없다. Edit Mode·캡처 직전 Play Mode·종료 후 Edit Mode를 확인한다. |
| 이미지 증거 | 캡처마다 UUID 경로를 사용하고 기존 파일을 거부한다. Pillow로 PNG 전체 디코딩·해상도·캡처 후 변경 여부를 확인한다. |
| 검증 실패 | 기대한 씬이 계층 응답에 없으면 다른 씬으로 대체하지 않는다. 최종 관측 전에 이전 성공 기록을 무효화하며, 최종 조회 오류도 실패로 남긴다. |
| 입력 | batch 내부 미지원 필드, 잘못된 primitive, 길이가 3이 아닌 벡터, 비유한 수를 변경 전에 거부한다. 실제 MCP 스키마와 클라이언트의 중첩 검증을 맞췄다. |
| 부분 적용 | 재질 적용 전 대상·renderer를 검사하고 실패 시 보상한다. 복원 실패는 부분 변경으로 보고한다. 부모 변경도 최종 이름 중복을 검사한다. |
| 자원 제한 | Python 전송에 총 deadline·완성 JSON frame·요청 1MiB·응답 8MiB 제한을 적용했다. Bridge는 연결/큐 128건, tick 8건/8ms, 읽기 5초·쓰기 2초를 제한하고 응답 쓰기를 main thread 밖에서 수행한다. |

## 설치와 호환성

MCP 패키지와 Bridge 버전은 **0.4.0**, wire protocol은 **1**이다. 함께 갱신해야 한다.
기존 프로젝트의 `Assets/Editor/McpBridge/UnityMcpBridge.cs`를 새 소스로 교체한다.
구형 Bridge는 `BRIDGE_UPGRADE_REQUIRED`로 변경 실행 전에 차단한다.
`unity_local_mcp`에서 `uv sync`를 실행해 추가된 Pillow 의존성을 설치한다.

ForgeFlow는 선택한 `UNITY_PROJECT_DIR`를 MCP 서버에 전달한다. 독립 실행에서 환경변수가
없으면 첫 non-ping 요청의 프로젝트를 프로세스 수명 동안 고정한다.
request ID 결과 캐시는 현재 Bridge generation의 메모리에 최대 256건·총 8MiB를 보관하며,
1MiB를 넘는 응답 본문은 재전송하지 않는다. 캐시에서 사라진 요청이나 domain reload
이후 불명확한 쓰기를 안전하게 재실행할 수 있다는 의미는 아니다.

## 검증

증거 디렉터리: `logs/unity-mcp-hardening-20260919/`.
이 폴더의 `baseline/`에는 이번 수정 직전 제품 파일을 보존했다.

- ForgeFlow 회귀 테스트: **180 passed**, 6.07초.
- MCP 테스트: **30 passed**, 1.28초. 실제 localhost 전송의 총 기한·불완전 프레임·크기 제한을 포함한다.
- Unity 에이전트 전체 테스트: **511 passed + 71 subtests**, 3.74초. 최종 결과는 `agent-tests-final.log`, `agent-tests-final.xml`에 보존했다.
- C# 전체 컴파일: 오류 **0개**. 실제 생산 메서드를 추출한 엔진 미사용 회귀 **35개 통과**. 이 숫자는 실제 Unity E2E와 구분한다.
- MCP 서버와 신규 전송 테스트의 Ruff 검사 통과. 에이전트에는 수정 전부터 있던 unused import/local 경고 2개가 남아 있어 전체 lint 통과라고 보고하지 않는다.
- 실제 FastMCP 도구 스키마 통합: 잘못된 batch 내부 인자는 Unity에 전달되지 않으며 정상·nullable 인자는 통과했다.
- 실제 Unity의 부정 경로: 프로젝트·generation·deadline·필수 문맥, 벡터·미지원 필드·재질 대상 검사와 request ID 충돌 **8개 통과**. 거부된 생성 요청이 오브젝트·재질을 남기지 않은 것도 확인했다.

### 최초 로컬 모델 E2E에서 발견한 통합 문제

Job `job-20260919-224651-371865ba`에서 씬 조회는 성공, 기본 씬 생성·저장·Play Mode는
`verified`였다. FBX는 Edit Mode에서 원본 인스턴스가 확인됐지만 Play Mode에서 Unity의
prefab 연결 조회가 빈 결과를 반환해 `capture_framing_not_observed`로 거절됐다.
실제 캡처에는 캐릭터 전신이 있었다. 모델의 자동 수정으로 해결할 수 없는 Bridge 조회
문제였고, 이 시도는 360초 제한으로 종료됐다. 실패 로그와 이미지를 그대로 보존했다.

Bridge는 매번 Play 진입 직전에 출처·씬·인스턴스/renderer ID·mesh GUID/local file ID를
SessionState에 보관하도록 수정했다. 이 기록은 domain reload를 넘겨 유지되며,
재생 중 현재 객체와 원본 메시의 식별자가 일치해야만 검증 증거를 반환한다.
Edit Mode로 돌아오면 지운다. 계층 이름만 같은 새 오브젝트로 대체하는 추론은 사용하지 않는다.

같은 실측에서 사용자 명시 출력 폴더까지 Job 기본 경로로 강제하던 거부를 발견해,
원문 폴더의 새 씬 작성만 예외로 허용했다. 기존 외부 씬 저장 보호는 유지한다.

### 최종 FBX 재실측 — 통과

Unity **6000.5.2f1**, 로컬 모델 **orcarouter/Qwen3.8-27B-Uncensored:q4_K_M**로
Job `job-20260919-225630-32f42485`를 실행했다. 실제 씬 생성·FBX 배치·카메라 조정은
로컬 모델이 수행했고, 호스트 검증이 Play Mode와 캡처 증거를 측정했다.

| 측정 | 결과 |
| --- | --- |
| 전체 실행 | **42.407초**, `succeeded` / `verified`, 최초 검증 1회, repair 0회 |
| 요청/측정 검사 | **6/6**: 씬 내용, 런타임, 스크린샷, Main Camera, 선택 자산 배치, 전신 프레이밍 |
| 저장 씬 | 사용자 지정 폴더의 `Assets/ForgeFlow/E2E/Humanoid/HumanoidE2EScene.unity`, 저장 후 clean |
| 원본 자산 | `Assets/ForgeFlow/job-20260919-225630-32f42485/Models/v001/Character_humanoid.fbx` |
| Edit/Play 출처 | `edit_prefab` / `play_snapshot`, 동일 GUID `fcbba238ec581b94e836e380051b803d` |
| 실제 메시 | Edit·Play 모두 원본 mesh renderer **1개**, 활성 **1개**, `fullyInViewport=true` |
| 원본 FBX SHA256 | `33fd02987f9841d1215ff1e9b3e3705e581e7b02841525b533f67f9f81313621` 유지 |
| 스크린샷 | 새 UUID 경로, **1280×720 PNG**, 전체 디코딩 통과, 207,901바이트 |
| 오류·종료 | 컴파일 오류 **0**, 런타임 오류 **0**, Play Mode 종료 확인 |
| 인간 검토 | `pending` 유지. 이미지에서도 전신이 잘리지 않음을 확인했으며 조명·재질 품질 승인을 대신하지 않음 |

최종 영수증: `jobs/job-20260919-225630-32f42485/unity/sessions/session-20260919T135630-e32c23d4/receipts/2026/09/19/20260919_225712_910_5b363db045.json`.
E2E 결과: `jobs/job-20260919-225630-32f42485/unity/unity-e2e-evidence-fbx.json`.
위 두 경로는 증거 디렉터리 기준이다.

독립 로그 감사 `live-e2e-fbx-audit.json`에서도 동일 결과와 FBX 해시 보존을 확인했다.
모델이 이전 Job의 FBX를 선택하려 한 1건은 정책이 차단했고, 이후 정확한 선택 경로로
수정됐다. 이는 실제 모델 호출에서 확인한 보호 동작이다.

테스트용 Unity PID 25836을 해당 프로젝트의 프로세스인지 확인해 종료했고, 테스트에
사용한 Ollama 모델을 unload했다. `cleanup.json`에 기록했다.

## 검증의 의미

카메라 검사는 renderer bounds의 화면 포함·near/far·culling을 측정한다. 다른 물체에 의한
가림, 조명·재질의 품질, 최종 픽셀의 인식 가능성을 보장하지 않는다. 자동 검증 `verified`와
인간 검토 `pending`을 분리한다. 화면 높이의 임의 비율을 필수 기준으로 추가하지 않았다.

batch는 모든 항목의 입력 검사를 먼저 하지만, 실행 중 실패까지 전체 원자성을 보장하지 않는다.
8ms tick 예산은 요청 사이에서 검사하며 단일 Unity 작업을 중단하지 않는다.
재질 rollback 실패, 응답 쓰기 지연, 대규모 큐 부하의 모든 경우를 실제 Editor에서 실측한 것은 아니다.

## 변경 파일

- `unity_mcp`: `server.py`, `UnityBridge/UnityMcpBridge.cs`, `tests/test_transport_guards.py`, `pyproject.toml`, `uv.lock`, `README.md`.
- `unity_local_mcp`: `mcp_client.py`, `task_contract.py`, `verification.py`, `agent.py`, 관련 테스트, `pyproject.toml`, `uv.lock`, `README.md`, `docs/STATUS.md`.
- `ForgeFlow`: 이 보고서와 README의 검증 문서 링크. 이번 강화에서는 ForgeFlow 실행 파일을 다시 빌드하지 않았다.
