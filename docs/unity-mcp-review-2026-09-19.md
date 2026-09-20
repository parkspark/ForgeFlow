# Unity MCP 강화 검토 — 2026-09-19

현재 작업 트리의 `unity_mcp`, `unity_local_mcp`, ForgeFlow 연결부를 검토했다. 제품 소스 6개 해시를 전후 비교해 변경 없음도 확인했다. 이번에는 Unity를 새로 실행하거나 씬을 변경하지 않았다. 실제 Python 함수에 대한 격리 재현, 임시 localhost 서버, 앞선 실제 E2E 로그를 사용했다.

**우선순위는 쓰기 대상 보호 → 불확실한 쓰기 재전송 방지 → 저장·검증 성공 판정 강화다.** 아래는 이 시점의 검토 기록이며 구현 완료 목록이 아니다.

## 우선 개선할 항목

| 우선순위 | 발견 내용 | 근거 수준 |
| --- | --- | --- |
| P1 | 새 Job이 이전 Job의 씬 경로로 저장할 수 있다 | 이전 실제 E2E 기록 + 정책 함수 재현 |
| P1 | 최초 프로젝트 확인 결과를 재사용해 같은 포트의 다른 프로젝트에 쓸 수 있다 | 실제 client 함수 + transport stub |
| P1 | 적용 여부가 불명확한 timeout에도 쓰기를 재전송한다 | 실제 client 함수 + transport stub, 2회 전송 |
| P2 | `saved=false`가 성공 응답으로 전달되어 저장 대기를 해제한다 | Bridge 코드 + 실제 계약 함수 재현 |
| P2 | 선택 FBX·유효 이미지·대상 씬을 입증하지 못해도 검증 통과 가능 | 실제 검증 함수에 대한 3개 격리 재현 |
| P2 | batch 내부의 미지원 인자와 잘못된 좌표가 조용히 무시된다 | 실제 MCP schema/client 재현 + Bridge 코드 |
| P2 | 재질 적용 실패 전에 자산을 만들어 부분 결과를 남긴다 | Bridge 코드 검토 |
| P2 | transport timeout이 전체 시간을 제한하지 않고 불완전 응답을 반환한다 | 실제 server 함수 + 임시 localhost 서버 |

## 1. 다른 Job의 씬으로 저장되는 경로 차단

`task_contract.py:623`은 `preferred_output_root`를 `unity_create_scene`에만 적용한다. `:796–823`의 저장 경로 검사는 허용 디렉터리와 사용자가 명시한 `.unity` 파일을 확인하지만, 파일명 없이 새 씬을 요청한 경우 다른 Job의 기존 씬 경로도 허용한다. Bridge의 `UnityMcpBridge.cs:996`은 전달된 경로로 `SaveScene`을 수행한다.

**실제 기록:** 임시 프로젝트에서 Job `job-20260919-213330-f52449ad`가 이전 Job의 `Assets/ForgeFlow/job-20260919-212937-3eab9c30/HumanoidTest.unity`에 저장했다. 21:33:56.07의 응답은 `saved=true`, `isDirty=false`다. 기존 사용자 프로젝트에서 발생했다는 의미는 아니며, 이번 테스트의 이전 Job 씬이 대상이었다.

**개선:** 현재 씬 저장과 Save As를 구분한다. 현재 Job의 새 자산 범위, 명시적으로 선택·수정 승인된 씬, 허용된 Save As 경로를 계약에 보관한다. 기존 파일로의 Save As는 대상 허용과 덮어쓰기 의도를 확인한 뒤 실행한다. 사용자가 명시한 기존 씬 편집까지 일괄 차단해서는 안 된다.

근거: `logs/unity-mcp-review-20260919/cross-job-save-evidence.json`, `client-boundary-results.json`.

## 2. 프로젝트 identity를 실행 직전에 검증

`mcp_client.py:348–355`는 `_project_identity_verified=True`이면 ping을 생략한다. `:620–625`의 쓰기 전 검사도 이 캐시를 사용한다. 같은 포트에 다른 Editor 프로젝트가 응답하고 통신 오류가 없다면 recovery의 재확인을 거치지 않는다.

**격리 재현:** `ping(A) → delete(A) → delete(B)`. ping은 1회였으며 두 번째 쓰기는 B에 전달되고 성공으로 반환됐다. 실제 Unity 삭제가 아니라 transport stub에 기록된 호출이다.

**개선:** 요청에 expected project identity와 Bridge generation을 포함하고 Bridge가 실행 직전에 거부할 수 있게 한다. 클라이언트의 주기적인 ping만으로는 ping과 쓰기 사이의 대상 변경을 막지 못한다. 응답과 영수증에도 같은 identity를 남긴다.

## 3. timeout과 쓰기 재시도 분리

`server.py:23–46`은 연결 전 실패와 전송 후 수신 timeout을 모두 `Cannot reach the Unity bridge`로 반환한다. `mcp_client.py:635–644`는 이 문자열이면 쓰기 도구도 다시 실행한다. Bridge의 Request에는 ID·deadline이 없고(`UnityMcpBridge.cs:47–53`), 큐에서 꺼낸 요청은 클라이언트가 기다리는지와 무관하게 실행한다(`:263–281`).

**격리 재현:** 최초 요청은 적용됐지만 응답이 유실된 조건을 구성하자 `unity_add_component(AudioSource, allow_multiple=true)`가 같은 인자로 2회 전달되고 최종 성공했다.

**개선:** 전송 전 실패와 실행 여부 불명 상태를 다른 오류 코드로 반환한다. 읽기와 쓰기의 retry 정책을 구분하고, 불명확한 쓰기는 결과 조회·대조를 먼저 한다. request ID를 도입할 경우 Bridge 결과 캐시와 domain reload 이후 처리까지 설계해야 한다. 아직 실행하지 않은 만료 요청은 큐에서 버린다.

근거: `client-boundary-results.json`의 `mutation_replayed_after_ambiguous_timeout`, `cached_identity_does_not_detect_project_switch`.

## 4. 저장 실패를 성공으로 전달하지 않기

Bridge `UnityMcpBridge.cs:996–1002`는 `SaveScene=false`여도 `status=ok`를 반환한다. `task_contract.py:865–866`은 top-level 성공만 보고 저장 대기를 해제한다.

**격리 재현:** 오브젝트 생성 응답으로 `scene_save_pending=true`를 만든 뒤 `saved=false, isDirty=true` 응답을 관측시키자 `false`로 바뀌었다. 이후 독립 검증이 dirty 상태를 잡을 수 있더라도 저장 장벽 자체는 이미 잘못 해제된다.

**개선:** Bridge에서 저장 실패를 오류로 반환하고, 호스트도 `saved == true`, 저장 경로 일치, clean 상태를 확인한다. 근거: `save-result-evidence.json`.

## 5. 검증이 무엇을 입증했는지 강화

현재 `verified`는 선택한 FBX의 영구 인스턴스와 전신 가시성을 모두 입증하지 않는다. 실제 최신 E2E 화면에는 전신이 보였다. 아래는 그 실제 결과가 실패했다는 주장이 아니라 검증기가 놓치는 조건의 재현이다.

- **다른 빈 오브젝트:** 선택 FBX 경로는 `agent.py:490`에 저장되지만 `:501`의 검증 명세에 전달되지 않는다. `verification.py:1235–1243`, `:1856–1860`은 기본 카메라·광원 외 활성 루트 존재만 본다. Transform만 있는 `UnrelatedEmptyRoot`로도 통과했다.
- **빈 이미지:** `verification.py:2078–2084`는 PNG 경로의 존재만 검사한다. 0바이트 `.png`도 screenshot 측정 완료가 됐다.
- **다른 씬:** `verification.py:1123–1124`는 기대한 씬 계층이 없으면 첫 씬으로 대체한다. state는 A, hierarchy는 B인 응답열도 통과했다.

세 사례 모두 실제 `VerificationContract.failures()`가 빈 목록을 반환하고 requested/measured 체크가 일치했다.

**개선:** 선택 asset GUID/path와 생성 instance ID를 명세에 연결하고 저장된 씬 인스턴스의 prefab source를 확인한다. 계층·컴포넌트·스크린샷 증거를 같은 project/generation/scene에 묶는다. PNG 디코딩·해상도·캡처 식별자·새 파일 여부를 확인한다. 전신 표시 요청에는 renderer bounds의 카메라 투영·활성·culling 검사를 추가한다. 미적 품질의 인간 검토와는 별개다.

근거: `verification-proof.py`, `verification-proof-results.json`, `verification-findings.md`.

## 6. 입력 schema와 Bridge 검증 통일

`server.py:105`의 batch는 `objects: list[dict]`로 선언되어 내부의 `additionalProperties=true`다. `mcp_client.py:519–525`는 최상위 인자만 확인한다. 실제 schema 조회 결과 단일 생성의 `tag`는 거부하지만 `objects[].tag`는 통과했다. Bridge의 생성 함수는 tag를 읽지 않는다.

또한 `UnityMcpBridge.cs:1250–1267`의 공용 transform 처리는 길이 2인 배열을 무시하고, 길이 4 이상은 앞의 세 값을 사용한다. 앞서 강화한 prefab 도구와 일반 create/modify/batch 사이에 검증 차이가 있다.

**개선:** batch 항목을 엄격한 타입으로 정의하고 중첩 미지원 필드를 거부한다. 모든 벡터를 정확히 3개의 유한 수로 제한하며, 전체 인자를 검사한 뒤 변경을 시작한다. 태그가 필요하면 명시적으로 지원하거나 지원하지 않음을 오류로 알린다.

## 7. 변경 도구의 부분 적용 처리

`UnityMcpBridge.cs:857–867`은 재질을 생성·저장한 뒤 대상과 Renderer를 검사한다. 대상이 없거나 Renderer가 없으면 오류를 반환하지만 `.mat`은 이미 남는다. 재시도마다 고유 이름의 자산이 추가될 수 있고, 성공 결과만 수집하는 변경 자산 목록에도 빠진다. 이번 검토에서는 실제 Unity 재질 생성으로 재현하지 않았다.

**개선:** 대상·Renderer·입력 값을 먼저 검증한다. 변경 시작 후 실패하면 Undo/보상 처리를 적용하고, 보상이 불가능하면 구조화된 부분 변경 결과를 남긴다. reparent 시에도 최종 부모 아래 이름 중복을 검사해야 한다(`:718`, 현재는 `name` 인자가 있을 때만 검사).

## 8. transport 시간·크기·프레임 경계

실제 `server._send(timeout=0.2)`에 0.075초마다 응답 조각을 전달하자 **0.609초 후 `ok`**를 반환했다. timeout이 각 recv의 무응답 시간으로 적용되며 전체 deadline은 없다. 중간에서 끊긴 `{"status":"ok","result":`도 오류 포장 없이 반환됐다.

Bridge에는 무제한 `ReadLine`, 요청 큐, tick당 전체 큐 처리, main thread의 응답 쓰기가 있다(`UnityMcpBridge.cs:250`, `:276`, `:291`). 이 부하 조건으로 Editor가 멈추는지는 이번에 실측하지 않았다.

**개선:** monotonic deadline, 요청·응답 최대 바이트, 큐 길이와 tick 작업량, 응답 쓰기 제한 시간을 둔다. 완성된 JSON envelope와 필수 필드를 확인하고 오류 코드를 구조화한다. 근거: `transport-evidence.json`.

## 후속 검증 기준

먼저 다른 Job 저장·다른 프로젝트 전환·응답 유실 후 재시도를 회귀 테스트에 넣는다. 다음으로 저장 false, 빈/다른 씬, 누락된 FBX, 잘못된 PNG, batch 인자, 재질 실패 후 잔여 자산을 검사한다. Python 계약 테스트와 Unity EditMode의 실제 변경 전후 검사를 함께 둔다. 현재 MCP 테스트 7개 중 3개는 C# 소스 문자열 검사여서 이러한 상태 변화를 확인하지 못한다.

Bridge capability handshake도 권장한다. 현재 Python `list_tools()`는 서버가 제공하는 도구를 알리지만 설치된 C# Bridge의 실제 지원 도구를 확인하지 않는다. `bridgeVersion` 문자열 외에 protocol version·지원 명령·필수 패키지 상태를 전달하면 수동 Bridge 갱신 누락을 실행 전에 발견할 수 있다.

재현물 전체: `logs/unity-mcp-review-20260919/`. 본 검토의 제품 코드 수정·실제 Unity 씬 변경·커밋은 없다.
