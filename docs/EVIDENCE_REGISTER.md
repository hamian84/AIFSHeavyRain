# 근거 등록부 및 복원 감사

**작성 기준:** 2026-11-13(KST; 11월 6일까지의 기존 증거 기록은 당시 시점 기준으로 보존)
**목적:** 이 문서 묶음의 사실이 실제 커밋·수행파일·과거 대화·추정 중 어디에서 나왔는지 구분하여 재검증할 수 있도록 한다.

## 1. 증거 등급

| 기호 | 등급 | 설명 | 허용하는 주장 |
|---|---|---|---|
| G1 | Git 검증 | GitHub가 현재 제공하는 특정 커밋/파일 변경 | 어떤 파일을 어떤 커밋에서 변경했는지, main 병합 여부 |
| R1 | 산출물 검증 | 동일 초기시각의 원본/재수행 JSON·run_status 등 | 실측된 실행상태, Step09 상태, Step08/09 상태 차이 |
| W1 | 웹 검증 | 특정 init의 운영 게시 페이지 본문 | 조회 시점의 표시 상태·문장·생성시각 |
| C1 | 과거 대화 기록 | 사용자 실측 로그 인용 또는 당시 상세 상담 요약 | 당시 발견한 문제·작성된 패치·사용자 보고 수치 |
| P0 | 제안·추정 | 당시 제안·위험 분석·후속 연구 필요성 | 미적용 계획 또는 조사 과제만 |

**주의:** G1의 날짜는 **버전 관리에 반영된 날짜**이지 최초 아이디어·실제 문제 발생 시점이 아니다. C1의 ‘수정파일 제공’은 시스템에 배포·검증됐다는 의미가 아니다.

## 2. GitHub 증거 — 외부에서 확인 가능

| ID | 기준 | 내용 |
|---|---|---|
| G-001 | [`24ff2e2` 초기 운영본](https://github.com/hamian84/AIFSHeavyRain/commit/24ff2e291468384fd790303bd451e2e6c59e2434) | 최초 Git 업로드, Step01~10 및 프롬프트·설정 스냅샷 |
| G-002 | [`2f5a1a1` P1 수정](https://github.com/hamian84/AIFSHeavyRain/commit/2f5a1a166f4d46ab37c0db60e99628f6cd99eae5) | 4개 Python 파일, 총 +49/-7줄 수정 |
| G-003 | [`4b66fc3` PR #1 병합](https://github.com/hamian84/AIFSHeavyRain/commit/4b66fc32dd012ec95e0ee73ff7136973f07c781d) | P1 코드 `main` 반영 |
| G-004 | [`82f42b1` .gitignore](https://github.com/hamian84/AIFSHeavyRain/commit/82f42b193663bb65f8ccf4b9af276ca7524d05f0) | `/scripts/his_run_pipeline.ksh` 제외 |
| G-005 | [`945320f` Step04/Step07 수정·회귀 테스트](https://github.com/hamian84/AIFSHeavyRain/commit/945320fed4003102e072197734dc033936df6dd3) | `func/step04/pass1_engine.py`, `func/step07/checks.py`, 관련 테스트 2개; unittest 17건 통과 |
| G-006 | [`4d797cc` Step07 평가 불가 상태 결정론 검증](https://github.com/hamian84/AIFSHeavyRain/commit/4d797cc4eefc3985a11b5447026575c4052c6e81) | 필수 scope 메타자료 누락 처리, 불완전 평가의 `insufficient_evidence / indeterminate` 강제, Anchor 지문 계약 버전 및 Step07 회귀 테스트 13건 |
| G-007 | [`15b0e07` / PR #5 병합](https://github.com/hamian84/AIFSHeavyRain/commit/15b0e076f897a77cdd7f5fd6d6ea8ee966997b60) | 2026-11-08 00:26 KST, `main`에 Step04·07·08 모듈 역할 분리, Cleanup 등 31개 파일 반영 |

2026-11-04 당시 검토 기준: `main`/`d37500b`. 당시 PR의 코드 커밋은 G-005·G-006이며, 이 서술은 병합 이전 시점의 기록이다. 2026-11-06 갱신 당시 최신 공개 `main`은 `ffa5f78`이다. 운영 산출물 R-005는 G-006의 최종 validator를 사용해 재생성한 결과가 아니다.

## 3. 업로드 ZIP 증거 — 로컬/대화 자료, Git 업로드 금지

아래 SHA256은 이 문서 생성 환경의 **원본 ZIP 바이트**를 대상으로 계산했다. 원시자료·이미지·API 인증파일이 포함될 수 있으므로 ZIP 자체를 공개 저장소에 올리지 않는다.

| ID | 파일명 | SHA256 | 내용 |
|---|---|---|---|
| R-001 | `oper_AIFSHeavyRain.zip` | `e7586bec6e14d4b921ae1077d5497051e96758f31e75681a2d9302b096b74cbe` | 이전 운영 코드 스냅샷, scripts/func/prompts/daba/envi |
| R-002 | `daio(20261029-120756).zip` | `2c592c36b0db9141847b1a4265971c03aacf339486b29469d7e2a4d41e5dc03f` | 4회 초기시각 수정 전 Step 결과 |
| R-003 | `daio(20261029-160538).zip` | `65a1e5ff0c834594798cd1df087bf5696ace0edd2cf3e147b3d62af5e3cd7ef6` | 같은 4회 초기시각 Step08~10 수정 후 |
| R-004 | `AIFSHeavyRain_daio(6).zip` | `e15b1cc227c98c1fb8a6c9971be66a5c597f06a405c3c4b6e6a1b123665e99ae` | 신규 `2026102912` 전체 실행 |

### R-005 — 로컬 `2026110300` 실행자료 (원본 Git 제외)

필요한 JSON만 읽어 SHA256을 기록했다. 실행 JSON·웹 제품 원본과 이미지 자산은 저장소에 추가하지 않았다.

| 경로(저장소 밖 실행 산출물) | SHA256 |
|---|---|
| `daio/2026110300/run_status.json` | `e1659bb37d3c9397c18735a83a3632d72d5d428e562568e6edac5fe2b7031929` |
| `daio/2026110300/step04/codex_cli/pass1_result.json` | `35db4e9eba7faa5aa432ba1e5664b61759a330eec48d82722f7887cb90ef42e4` |
| `daio/2026110300/step04/codex_cli/step04_analysis_provenance.json` | `880fb2b792f7bee62527b54b92401db13a405c3854fcbfca2c6280b456c919c8` |
| `daio/2026110300/step07/pass2_check_results.json` | `c76e60d4479e71b396f9f1aa7b5938feef15ab13e367c52dadff80cd3d9d96a4` |
| `daio/2026110300/step08/pass2_result.json` | `d948d1b1eec495c01f4fd1734656e14d53e5bfa68a2f4cf98e8ee5e925b8a7b9` |
| `daio/2026110300/step09/operational_result.json` | `91f06e612d9252a75abb5272c2e56565083c7de5b85f9faf4f14f35186a9219d` |
| `daou/2026110300/forecaster_product.json` | `80971afb886396d2ad1795c4d5973b3b3004662b112408daefdd320ebee59a3b` |

**중요 보안 주의:** `oper_AIFSHeavyRain.zip`에는 `envi/api_keys.env`와 과거 백업형 환경파일이 포함되어 있다. 이 파일들을 GitHub, Git Issue, PR, 공개 문서에 첨부하지 않는다. 실제 키/토큰 값은 이 문서에서 읽거나 전재하지 않았다. 과거 배포 여부와 노출 가능성은 별도 보안 점검 대상이다.

## 4. 실행 자료에서 직접 확인한 경로

| 확인사항 | 검증 근거 |
|---|---|
| 4회 Step07 확인항목 23/31/29/27 | R-002/R-003: `<init>/step07/pass2_check_results.json` |
| Step08 지속성 2→5 및 해당 Check | R-002/R-003: `<init>/step08/pass2_result.json`, `<init>/step09/operational_result.json` |
| Step09 15/15 valid·surface | R-003: `<init>/step09/operational_result.json` 및 run_status |
| `run_status` partial_completed | R-003: `<init>/run_status.json` |
| 신규 2026102912 Step01~10 completed | R-004: `2026102912/run_status.json` |
| 신규 Step07 Check 25, Step08 3, Step09 3 | R-004: `step07/`, `step08/`, `step09/` JSON |
| `2026110300` Step01~10 완료와 단계 요약 | R-005: `run_status.json` 및 Step04~10 결과 JSON |
| `2026110300` Step04 direct-signal audit 11 required / 5 covered / 6 missing | R-005: `step04_analysis_provenance.json` |
| `2026110300` CAND_003 direct precipitation `insufficient_evidence`, Step09 hidden | R-005: Step07/08/09 결과와 로컬 제품 JSON |

## 5. 실제 운영 사이트 — 조회 시점의 변동성 주의

- [`2026102712`](https://aifsheavyrain.duckdns.org/2026102712/)
- [`2026102800`](https://aifsheavyrain.duckdns.org/2026102800/)
- [`2026102812`](https://aifsheavyrain.duckdns.org/2026102812/)
- [`2026102900`](https://aifsheavyrain.duckdns.org/2026102900/)
- [`2026102912`](https://aifsheavyrain.duckdns.org/2026102912/)
- [`2026110300`](https://aifsheavyrain.duckdns.org/2026110300/)

`2026110300` 링크는 사용자가 제공한 운영 URL이다. 이번 검토에서 live 페이지를 도구로 불러오지 못했으므로 해당 URL의 현재 응답을 검증 근거로 쓰지 않고 R-005 로컬 제품 JSON을 사용했다. 현재 `/latest/` 또는 동일 init의 페이지는 재발행할 수 있다. 최초 웹 조회 내용과 나중 결과가 달라질 수 있으므로 불변 검증에는 R-002~R-005와 `daou` 이력 스냅샷을 사용한다.

## 6. 과거 대화 기록 기반 사건군 (C1)

| 사건군 | 근거 시점 | 검증 강도·주의 |
|---|---|---|
| 초기 Phase 1A/2-pass/수동 AI | 6/08, 6/18, 6/22, 6/23, 6/29 | 과거 사용자 수행 보고·검토. 현재 Git 당시 커밋 없음 |
| 데이터 수집·진단 | 7/02, 7/11, 7/21, 7/23, 7/30~31 | 당시 실행 수치 일부 사용자 제공. 현재 파이프라인과 버전 차이 존재 |
| AWS 배포·Step07/08 장애 | 8/13, 8/17~20, 8/25 | 터미널 로그·대화 맥락. 각 해결책의 실제 반영 여부는 부분 미확정 |
| 직접 강수 anchor·표출·Step07 3회 | 9/01~03, 9/14, 9/17, 9/21, 9/29 | 개선 제안과 재실행 확인 혼재: 본문에서 분리 |
| 연속 검증 오류 및 대응 | 10/02, 10/05, 10/12~19, 10/21~22, 10/25 | 오류 발생은 확인되나 최종 패치 적용 증거가 불완전한 항목 존재 |
| P1 원인·재검증·Git | 10/27~30 | Git/두 개 daio ZIP/웹페이지를 함께 확인 가능 |
| Step04 후보 범위 복구·Step07 평가 가능성 상태 구분 | 11/03 UTC | G-005 코드·테스트 커밋, R-005 운영 JSON | 구현·회귀검증 및 저장 실행 결과 확인; 웹 브라우저 표시·기상학적 정확도는 미검증 |

## 7. 엄격 검토에서 배제한 주장

1. ‘이 프로젝트의 전체 대화와 메모리의 모든 문장을 전수 읽었다’ — 현재 도구는 일부 과거 맥락만 반환하므로 **주장하지 않음**.
2. ‘2026-10-27 이전 모든 수정은 GitHub에서 추적할 수 있다’ — 최초 커밋 이전이라 **불가**.
3. ‘당시 제안한 모든 패치는 AWS 서버에 적용됐다’ — 별도 실행/코드 확인 없이는 **미확정**.
4. ‘이번 5회 재실행으로 현업 예보성능이 검증됐다’ — 검증된 것은 **파이프라인·근거 계약과 일부 결과 정합성**.
5. ‘2026102912 환경 지속성 판단 제한은 반드시 코드 오류다’ — 적격 Check 부족 가능성이 있으므로 **미확정**.
6. ‘웹페이지 텍스트 추출 성공은 모든 PNG·지도·JavaScript 정상이다’ — **아님**.
7. ‘10월 5일 구버전의 5mm 논의가 현재 1.0mm/6h 정책보다 우선한다’ — **아님**.


## 8. 기록 신뢰도 향상을 위한 다음 수집 항목

추후에는 Git PR 본문에 `init`, `candidate_id`, `check_id`, 단계별 입력 해시, 오류 코드, 수정 커밋, 재실행 결과, 웹 검증 URL, 적격성 반례 테스트를 함께 기입한다. 실행 전체 ZIP은 저장소 밖에 보관하고 SHA256만 문서에 넣는다.

## 9. 2026-11-06 실행근거 추가 (R1 / 코드 Diff 미확인)

| 자료 | SHA-256 | 확인 범위 |
|---|---|---|
| `logs.zip` | `96fcaa4579218e86f5c4a094ef750c99e9113352a22526ce0077ec78b69e458d` | 최초 Step08 실패와 12:17 UTC 재수행 성공 로그 |
| `daio(2).zip` | `6b628206997296e2c6a328a6e6376700e18681d2f5696f1464699a9946932ba6` | 21:17 KST 실행 Step08·09 최종 상태 |
| `daio(3).zip` | `9c4a751f503fcf858e561bf6267035616c06fef513d8bb2eb12f2a54744d8e45` | 22:12 KST 새 Step08·09 JSON, Step04~07 동일 |

- GitHub 공개 기준 `main`: [`ffa5f78`](https://github.com/hamian84/AIFSHeavyRain/commit/ffa5f7869204610b1693060c6844c960a14566c1). 이 커밋은 `.gitignore`의 `/tests/` 제외를 반영한 것이며, 11월 6일 미커밋 Step08 코드 수정 커밋은 아니다.
- **증거 수준:** 위 ZIP은 R1 실행 산출물 증거다. 서버 로컬 코드 Diff를 아직 받지 못했으므로 실제 수정 파일·함수에 대한 G1 변경 검증은 불가능하다. Git에 ZIP 원본을 올리지 않는다.
- Step08/09 최종 수치와 출처, 미확인 재발 위험은 `VALIDATION_HISTORY.md`, `KNOWN_ISSUES.md`의 11월 6일 항목 참고.

## 10. 2026-11-13 현행화 추가 근거 및 미병합 수정본

- **G1 확인:** [GitHub `main` `15b0e07`](https://github.com/hamian84/AIFSHeavyRain/commit/15b0e076f897a77cdd7f5fd6d6ea8ee966997b60), PR #5 병합과 31개 파일 변경. 현재 Step04/07/08 운영 모듈명은 이 커밋의 저장소 파일을 기준으로 함.
- **R-006 원본 실패 DAIO:** `AIFSHeavyRain_daio.zip`, SHA256 `b8d72f2227417e6d7a7fc012e68941fde816d2e2e60bbc99d690fc0e7cec7b84` (사용자 제공 원본 ZIP 바이트). `2026111112/step07/diagnostics/grounding_failures.json`, `step06/pass2_input_package.json`, `step05/diagnostics/REQ_CAND002_005.json`, `step04/codex_cli/pass1_result.json`로 원인 조사. **공개 저장소에 ZIP 원본을 올리지 않음.**
- **C1 수정 후 운영 확인:** 2026-11-13 사용자 메시지에서 "수정한 내용이 문제없이 수행되는 걸 확인했어"라고 보고. 정확한 재수행 init·로그·Step08~10 산출물은 제공되지 않았으며, 사용자 보고를 R1 직접 실행 검증으로 승격하지 않음.
- **수정본 제공물:** `AIFSHR_STEP07_MAJORITY_GROUNDING_FIX_20261112.zip` (이전 응답에서 제공; 수정 코드 6개 + `MODIFICATION_REPORT.md`). 당시 산출물 SHA256 `3f9dbc7ce455059b5c77463e169e87815ab430b65d6795e7454a59ff636cb9e9`. 이 ZIP 제공이 곧 GitHub `main` 반영을 의미하지는 않음.
- **현행 저장소 경계:** 검토 시점 `main=15b0e07`, 최근 수정본은 사용자 운영 적용 확인 단계이지만 Git 저장소에는 아직 미커밋/미병합 상태. 문서 커밋에서도 이 구분을 유지해야 함.
