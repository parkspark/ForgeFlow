# P0 Unity 카메라와 Bridge 호환성 개선

대상 요구사항: [후속 개선 요구사항](future-improvement-requirements-2026-09-20.md)의 P0-1, P0-2.

## 변경 내용

### 카메라 자동 맞춤

- Unity Bridge의 `unity_frame_character`가 캐릭터 하위 Renderer bounds 전체와 카메라 FOV·화면 비율을 사용한다. 기본 목표는 화면 높이 68%이며 폭과 깊이도 거리 계산에 포함한다.
- 정면 방향은 명시적인 `front_direction`, 양쪽 발→발끝 본 방향, 유효한 Humanoid 본 좌표 순으로 구한다. 본 이름·계층·양쪽 방향 일치도도 확인한다. 판단할 수 없으면 루트 축을 사용하되 `partial`, 정면 미확인으로 기록한다. 이는 본 좌표에 근거한 방향 추론이며 얼굴 이미지 인식은 아니다.
- 자동 맞춤은 Bridge가 이번 도메인에서 새로 만든 `Assets/ForgeFlow/<job>/` 씬에만 허용한다. 기존 씬을 열거나 다른 이름으로 저장하는 것으로는 이 권한을 얻지 않는다.
- 카메라·조명 변경 전 씬 백업을 남기고, 위치·주시점·거리·bounds·실측 화면 비율을 도구 결과와 실행 로그에 기록한다. 변경 후에는 씬을 별도로 저장한다.
- 화면 비율상 매우 넓은 캐릭터는 전신 표시와 높이 60%를 동시에 만족할 수 없다. 이 경우 폭을 우선하여 전신을 유지하고 폭 제약 및 실제 높이 비율을 보고한다.
- ForgeFlow에 `캐릭터 씬 구성 요청` 프리셋을 추가했다. 새 씬 생성, 자동 맞춤, 저장, Play 전후 캡처를 요청한다.

### 연결 단계의 Bridge 확인

- `unity_ping`에서 실행 중 Bridge 버전, 실제 지원 도구, 프로젝트 경로와 설치 소스 해시 정보를 전달한다. JSONL 연결 이벤트와 기존 `UnitySession.project_identity`에 보존한다.
- ForgeFlow는 Bridge `0.6.0` 이상과 프리셋별 필수 도구를 확인한다. Avatar, Clip, Controller 준비 상태를 표시하고, 지원하지 않는 프리셋과 명시적인 관련 도구 요청은 모델 실행 전에 차단한다.
- 기존 Bridge에서도 호환되는 일반 채팅은 유지한다. 버전/도구 정보가 없는 연결에서는 고급 프리셋 준비 상태를 추정하지 않는다.
- 연결 로그에 소스 버전·SHA-256, 설치 경로·SHA-256을 기록한다. 파일 해시는 진단 정보이며, 설치 파일이 같다는 이유만으로 실행 중 Editor의 지원 기능을 추정하지 않는다.
- 기존 Job 스키마와 receipt 형식을 유지한다. 카메라 백업 파일도 성공한 도구 결과에 따라 변경 산출물에 등록한다.

## 사용

Unity 프로젝트의 기존 `UnityMcpBridge.cs`를 `unity_mcp/UnityBridge/UnityMcpBridge.cs`로 갱신하고 Unity 컴파일이 끝난 뒤 ForgeFlow에서 다시 연결한다. 실제 설치 위치는 연결 패널에 표시된다. 프로젝트의 기존 Bridge는 연결 과정에서 자동으로 덮어쓰지 않는다.

FBX를 가져온 뒤 `캐릭터 씬 구성 요청`을 사용한다. 새 씬의 카메라 결과는 스크린샷으로 검토하고, 구도·조명·모션의 시각적 품질은 사람이 승인한다.

## 검증 기록

구현 검증 자료는 `logs/p0-improvements-20260920/`에 기록한다.

- ForgeFlow 전체 테스트 **283개 통과**, Ruff와 diff 공백 검사 통과. 구버전·지원 정보 누락·도구 일부 누락·최신 Bridge의 버튼 상태, 전송 전 차단, 일반 채팅 유지, 연결 해시 기록을 포함한다.
- Unity Local Agent **575개 + 71 subtests 통과**. JSONL 메타데이터 전달과 카메라 도구의 정확한 Job 경로 제한·변경 후 저장 필요 상태를 확인했다.
- 1024×768, 853×533 UI를 렌더링하여 프리셋 줄바꿈과 버튼 접근성을 확인했다.
- 새 실행 파일과 소스의 격리 profile 시작 검증은 종료 코드 0이며 기존 사용자 설정 해시는 유지됐다.
- Unity MCP 전체 테스트 **86개 통과**. 설치된 C# 컴파일러로 실제 프레이밍 계산 코드를 실행하여 좁은·넓은·매우 큰·음수 좌표 모델을 포함한 **1,009개 구도 사례**, 정면 추론·입력 제한·씬 소유권을 확인했다. Unity 6000.5.2f1 라이브러리 대상 Bridge 컴파일도 오류 없이 통과했다(기존 참조·폐기 API 경고 2개).

### 실제 로컬 모델 E2E

격리 프로젝트에서 ForgeFlow 어댑터 → JSONL → 로컬 모델 `orcarouter/Qwen3.8-27B-Uncensored:q4_K_M` → Unity Bridge 경로로 실행했다. 모델이 새 씬·FBX 배치·자동 카메라 맞춤·씬 저장·Play 전후 캡처를 직접 수행했다.

- 최종 실행 **97.015초**, 호스트 검증 **requested 6 = measured 6**, failures 0, unmapped 0. 사람 검토 상태는 `pending`이다.
- 자동 맞춤 결과 `framingRatio=0.6799999475`, `fullyInViewport=true`, `frontBasis=paired_foot_toes`, `facingFront=true`.
- 호스트의 별도 관찰에서도 Edit/Play 모두 높이 비율 **0.679999974**, 좌우 범위 **0.3297751~0.670224845**이며 전신이 들어왔다. 이는 Renderer bounds의 화면 투영 비율이며 실제 캐릭터 픽셀 면적 측정은 아니다.
- 두 캡처에서 캐릭터 정면과 전신 구도를 직접 확인했다. 재질·조명의 시각적 품질은 승인하지 않았다.
- 새 씬 생성과 자동 맞춤이 동일한 Bridge generation에서 실행됐으며, 카메라·조명 JSON 및 씬 사본 백업이 남았다. 원본 FBX와 가져온 복사본의 SHA-256은 유지됐다.
- 연결 당시 실행된 Bridge 소스 해시, 설치 파일 해시, 저장소 소스 해시가 모두 `828593ee55c5eec24049217bc8cadf52e84ca0c5c800d66715d5acfc3fce1065`로 일치했다.
- Play 도메인 재로드 후 별도 보호 검사에서 기존 씬의 자동 맞춤 요청이 거부됐고 씬 해시는 변하지 않았다. 최종 상태는 Edit Mode, 씬 저장 완료, `runInBackground=false`, `mcpOwnsPlaySession=false`다.

원자료: `logs/p0-improvements-20260920/final-audit.json`, `model-evidence.json`, `ownership-guard.json`. 최종 Job은 `job-20260920-135303-e2e642ec`이며 receipt·모델 도구 기록·스크린샷은 해당 `jobs/` 디렉터리에 있다.

초기 실행에서는 루트 축만 쓰면 실제 FBX의 뒷면이 보이는 문제를 발견하여 발끝 본 기반 추론을 추가했다. 두 번째 실행은 Bridge 컴파일 완료 전에 시작해 도메인 전환으로 씬 소유권이 사라졌고, 모델이 수동 카메라 조정으로 대체했다. 이 실행은 자동 맞춤 성공에 포함하지 않았다. 최종 실행은 실행 소스/설치/저장소 해시 일치를 확인한 뒤 시작하여 자동 맞춤 도구 성공과 호스트의 Play 관찰을 모두 확인했다.

### 실행 파일

`dist/ForgeFlow.exe`를 갱신했다. 크기 **49,013,054 bytes**, SHA-256 `2e3b9524e12d7d230f372356d7cd81b913816154bad7ab9c1d6564cf13a75dcf`. 기존 실행 파일은 `logs/p0-improvements-20260920/ForgeFlow-before-p0.exe`에 보관했다. 기존 사용자 Unity 프로젝트의 Bridge를 자동으로 교체하지는 않았으므로, 사용할 프로젝트에서 위 설치 안내에 따라 갱신해야 한다.
