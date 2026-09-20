# E2E 개선 및 재검증 — 2026-09-19

[최초 점검](e2e-audit-2026-09-19.md)에서 발견한 실행 실패와 복구·판정·화면 크기 문제를 수정했다. ForgeFlow와 연결된 `unity_local_mcp`, `unity_mcp`에 변경이 있다. 기존 작업과 사용자 설정 대신 별도 Job 및 임시 Unity 프로젝트를 사용했다.

## 변경 내용

| 문제 | 개선 |
| --- | --- |
| 미리보기 실패 후 재생성·원본 충돌 | 등록된 GLB/BLEND/FBX의 경로·크기·SHA-256이 모두 유효하면 미리보기부터 재개한다. 불완전하거나 변경된 원본은 덮어쓰지 않는다. |
| 출력 대기 중 시간 제한 무효 | 출력 읽기를 분리하고 실행 시작부터 deadline을 적용한다. 초과 시 자신이 시작한 프로세스 트리를 종료하고 받은 로그를 남긴다. |
| Unity 종료 후 running 잔류 | 종료·오류·취소·전송 실패 시 Turn/단계를 종료 상태로 저장한다. 이전 세션에서 늦게 도착한 이벤트를 현재 세션에 적용하지 않는다. |
| 변경 자산 목록에 오류 문구 포함 | 성공한 변경 도구의 구조화된 응답 필드만 수집하고 경로를 검증한다. 입력 참조나 실패 메시지를 변경 자산으로 기록하지 않는다. |
| FBX 배치를 위해 잘못된 C# 생성 | `unity_instantiate_prefab`으로 편집 모드에 기존 FBX/prefab 인스턴스를 생성한다. 정확한 선택 경로·프로젝트 identity·저장 검사를 유지하고 renderer bounds를 반환한다. |
| 컴파일 오류가 콘솔에서 누락 | Bridge의 컴파일 완료 이벤트에서 오류를 수집한다. 호스트는 프로젝트 identity가 일치하는 Bridge 세션의 실제 Editor 로그 경로도 확인한다. |
| 배치 요청에서 자동 검증 미실행 | 생성 동사 없이 기존 자산을 배치하거나 실제 Play Mode 검사를 요청해도 구체적인 검사를 활성화한다. 카메라 프레이밍과 추종 요구를 구분한다. |
| 작은 화면에서 하단 조작부 잘림 | 탭 본문 스크롤과 상세 영역 높이 제한을 추가하고 Unity 옵션·버튼을 재배치했다. 진행·취소는 탭 본문 바깥에 유지한다. |
| E2E 실패 증거 누락·약한 성공 판정 | 단계별 evidence·로그를 저장하고 중단 상태를 정리한다. 저장된 설정과 `--jobs-root`를 사용하며 Unity 쓰기는 `verified` 영수증을 요구한다. |

## 실제 재검증

### 미리보기 복구

이전 실제 생성 산출물을 새 감사용 Job에 복사하고, 직전 미리보기 실패 상태만 fixture로 구성했다. 이후 앱의 `PipelineService.start_modeling`을 호출해 실제 Blender 5.2로 렌더링했다.

- **통과, 3.672초**. `failed → completed`, `modeling_preview`만 실행.
- Pixal3D 생성 0회, Ollama 모델 해제 0회.
- 원본 3종 및 이전 Job·입력 이미지 해시 불변.
- 640×640 PNG 생성·등록, 전신 렌더 육안 확인.
- 근거: `logs/e2e-improvements-20260919/preview-recovery-evidence.json`.

### Unity

같은 PC의 실제 Ollama 모델 `orcarouter/Qwen3.8-27B-Uncensored:q4_K_M`이 Unity 도구를 호출했다. 에이전트 대신 씬을 수동으로 만들어 성공 판정을 주입하지 않았다. Unity 6000.5.2f1의 그래픽 활성 batchmode 임시 프로젝트에서 실행했다.

강화 과정에서 숨겨진 실패도 보존했다. 첫 재실행은 배치 자체가 성공했지만 자동 검증 영수증이 없었다. 엄격한 판정을 적용한 다음 실행은 `unavailable` 때문에 실패했고, 실제 스크린샷도 카메라가 반대쪽을 향해 빈 배경이었다. 이 결과에 따라 검증 활성화와 bounds·카메라 방향 안내를 보완했다.

- 초기 재실행: `job-20260919-212937-3eab9c30`.
- 엄격 판정으로 실패한 실행: `job-20260919-213330-f52449ad`, `unity/unity-e2e-evidence-fbx.json`.
- 새 배치 도구는 잘못된 좌표 배열 4종을 객체 생성 전에 거부했다. 실제 Bridge 배관 검증 근거: `bridge-vector-evidence.json`.

**최종 재실행 통과: 66.922초, exit 0.** Job은 `job-20260919-214515-57f62f68`, Turn은 `turn-001-e3be7e`다.

- 로컬 모델이 FBX를 `unity_instantiate_prefab`으로 배치했다. 배치용 C# 스크립트 생성 없이 완료했다.
- 호스트 receipt는 `verified`, `build_stage_success=true`. 요청된 `scene_objects`, `gameplay`, `screenshot`, `components:Main Camera` 4개를 모두 실제 측정했고 skipped/failures는 없었다.
- 저장된 씬은 `Assets/ForgeFlow/job-20260919-214515-57f62f68/E2E/Humanoid/HumanoidTest.unity`, `scene_clean=true`, 컴파일 오류와 런타임 오류 각각 0건.
- 원본 Humanoid FBX, 등록된 SHA-256, Unity 복사본 해시가 재검증 후에도 일치했다. 임시 프로젝트의 C# 파일은 Bridge 1개뿐이다. 근거: `unity-final-integrity.json`.
- 최종 Game 뷰 스크린샷을 직접 열어 중앙에 전신이 보이는 것을 확인했다. 캐릭터는 화면 높이의 약 1/3로 작고 밝은 재질이다. 안내에 넣은 60~75% 프레이밍을 자동 측정하거나 달성한 것으로 간주하지 않는다. 사람 검토 상태는 `pending`이다.
- 최종 evidence: `logs/e2e-improvements-20260919/jobs/job-20260919-214515-57f62f68/unity/unity-e2e-evidence-fbx.json`.
- receipt: 같은 Job의 `unity/sessions/session-20260919T124515-ff4c8041/receipts/2026/09/19/20260919_214622_049_2af0749b61.json`.
- 스크린샷: 같은 session의 `screenshots/turn-001-e3be7e-v19_20260919_214621.png`.

### UI

- Windows Segoe UI·맑은 고딕 폰트를 로드한 Qt offscreen **40개 조합 통과**.
- 일반 배율의 1280×800·1024×768 및 150% 배율의 853×533 논리 화면 등을 검사했다.
- 모든 조합에서 요청 창 크기가 유지됐고 스크롤 후 접근할 수 없는 버튼이 없었다.
- 상세 영역을 모두 펼친 작은 화면에서도 본문 172 논리 px를 확보했다.
- 근거: `logs/e2e-audit-20260919/ui-responsive-verification.md` 및 `ui-responsive-results-scale-*.json`.

### 회귀 검사 및 Windows 실행 파일

| 검사 | 최종 결과 |
| --- | --- |
| ForgeFlow 전체 pytest | **180 passed**, 6.92초 (최초 감사 114개) |
| ForgeFlow Ruff lint / format | 통과 / **75개 파일** 통과 |
| unity_local_mcp 전체 pytest | **413 passed, 71 subtests passed**, 3.10초 |
| unity_mcp pytest | **7 passed** |
| Windows EXE 빌드 | 성공, exit 0 |
| 새 EXE offscreen 시작·종료 | **4.085초, exit 0**, timeout 없음 |

새 실행 파일: `dist/ForgeFlow.exe`, 48,962,204바이트. SHA-256: `A96988770DD35EFD5196A29233160644EC7A553CDE1D596DFDC8943A7586F344`.

기존 실행 파일은 복사 백업했고 사용자 실행 중인 앱은 종료하지 않았다. EXE smoke는 별도 USERPROFILE·Job 경로에서 실행했다. 근거: `logs/e2e-improvements-20260919/build-exe-evidence.json`, `unity-local-verification-tests.log`, `unity-mcp-tests.log`. 컴파일 오류 복원 및 비정상 프로세스 종료 조건은 회귀 테스트로 검증했으며 실제 사용자 Unity 프로젝트에 결함을 주입하지 않았다.

검증 후에는 이번 작업에서 시작한 Unity PID 7232의 프로젝트 경로를 확인하고 종료했다. 테스트용 Ollama 모델도 내려 GPU 메모리를 반환했으며 결과·로그·EXE 백업은 보존했다. 근거: `logs/e2e-improvements-20260919/cleanup.json`.

## 적용 및 범위

ForgeFlow 실행 파일만으로 외부 Unity 엔진 변경이 배포되지는 않는다. 함께 수정된 `unity_local_mcp`, `unity_mcp`를 사용하고, 기존 Unity 프로젝트에 설치된 `Assets/Editor/McpBridge/UnityMcpBridge.cs`도 `unity_mcp/UnityBridge/UnityMcpBridge.cs`로 갱신해야 한다. 이번 검증에는 임시 프로젝트의 Bridge만 갱신했다.

최초 감사에서 실제 이미지 생성·Blender 편집·UniRig 리깅을 수행했다. 개선 검증은 원본을 재생성하지 않고 미리보기 복구와 실패했던 Unity 경로를 다시 실행했다. UI 검사는 실제 Windows 마우스·키보드 자동화가 아닌 Qt offscreen이다. 자동 검증과 스크린샷 확인은 애니메이션 변형·게임 조작감·최종 시각 품질의 인간 승인을 대신하지 않는다.

고폴리 메시의 자동 최적화와 이미지부터 Unity까지 한 명령으로 연결하는 통합 실행기는 이번 안정성 수정에 포함하지 않았다.
