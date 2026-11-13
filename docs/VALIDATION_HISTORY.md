# 실행·검증 이력

**기준일:** 2026-11-13(KST; 11월 11일 실패 자료 및 사용자 운영 확인 추가). 초기시각(init)은 UTC `YYYYMMDDHH`를 사용하며 웹 표출 시간은 KST다. 이 문서는 실행 스냅샷과 검증 계약의 일치 여부를 기록한다. 관측 대비 예측 정확도 검증은 이 자료에 포함되지 않는다.

## 1. 검증 범위·원천

- 2026-10-27 12UTC~10-29 00UTC 수정 **전**: `daio(20261029-120756).zip`
- 위 4개 초기시각 Step08~10 수정 **후**: `daio(20261029-160538).zip`
- 신규 `2026102912` 전체 실행: `AIFSHeavyRain_daio(6).zip`
- 운영 사이트 각 `/YYYYMMDDHH/` 페이지: 결과 텍스트·감시등급·시각·강수 수치 조회.
- GitHub P1 코드: `2f5a1a1` 및 병합 `4b66fc3`.

로컬 업로드 ZIP은 **Git에 첨부하지 않는다**. 자세한 SHA256은 [근거 등록부](EVIDENCE_REGISTER.md) 참고.

## 2. P1 수정 전후, 동일 초기시각 4회 비교

### 2.1 실행상태 요약

| init (UTC) | Step07 Checks | Step09 유효·표출 | 차단 | 수정 전 지속성 limited | 수정 후 limited | Step08 수정 후 재시도 |
|---|---:|---:|---:|---:|---:|---|
| `2026102712` | 23 | 3/3 | 0 | 1 | 2 | 의미 0 / 언어 0 / observable 0 |
| `2026102800` | 31 | 4/4 | 0 | 1 | 1 | 의미 1 / 언어 0 / observable 0 |
| `2026102812` | 29 | 4/4 | 0 | 0 | 1 | 의미 1 / 언어 0 / observable 0 |
| `2026102900` | 27 | 4/4 | 0 | 0 | 1 | 의미 1 / 언어 0 / observable 0 |
| **합계** | **110** | **15/15** | **0** | **2** | **5** | **의미 3** |

모든 재수행에서 Step08 `reused_candidates=0`. `run_status.json`의 `step01~07=not_started`와 `pipeline_status=partial_completed`는 **이번 작업에서 그 단계를 재실행하지 않았다는 뜻**이며, 기존 Step02~07 산출물은 두 ZIP 사이에서 동일하다. 이를 ‘원시 단계 실패’로 기록하지 않는다.

### 2.2 환경 지속성 판단 변화

| init | 후보 | 관련 Check | 수정 전 | 수정 후 | 판독 의미 |
|---|---|---|---|---|---|
| `2026102712` | CAND_002 | `CHK_CAND002_05` | 근거 부족 | 제한 | 후속 하층 수렴 축소 |
| `2026102812` | CAND_002 | `CHK_CAND002_06` | 근거 부족 | 제한 | 후속 925 hPa 수렴대 축소 |
| `2026102900` | CAND_001 | `CHK_CAND001_04` | 근거 부족 | 제한 | 후속 700 hPa 상승운동 소멸·하강 전환 |

나머지 12개 후보의 환경 지속성 상태는 변경되지 않았다. `confirmed`는 전후 0개다. 수정 전 13개 `insufficient_evidence` → 수정 후 10개.

### 2.3 감시등급 유지 여부

| init | `priority` 중점 | `elevated` 우선 | `general` 일반 | 전후 등급 변경 |
|---|---:|---:|---:|---|
| `2026102712` | 1 | 2 | 0 | 없음 |
| `2026102800` | 2 | 2 | 0 | 없음 |
| `2026102812` | 2 | 2 | 0 | 없음 |
| `2026102900` | 1 | 2 | 1 | 없음 |
| **합계** | **6** | **8** | **1** | **0건** |

### 2.4 P1 수정과 직접 무관한 재생성 변동

- `2026102900/CAND_002`: `environment_support_status`가 `partial` → `confirmed`, 이에 따른 `signal_class` 변화. Step07 근거 자체는 같음. 동일 입력 재생성에 따른 **LLM 종합판단 변동**이며 지속성 제한 근거 인정 결과와 혼동하지 않는다.
- `2026102900/CAND_004`: 최종 후보명 표현의 공간적 세부성이 이전보다 감소한 사례. 근거 정합성 판단과 후보명 문장 재현성을 구분할 필요가 있다.
- `2026102812/CAND_001`: 웹 `감시 판단 이유`에 **내부 표출 계약 설명에 가까운 표현**이 노출됨. 검증 상태는 `valid`였으나 예보관용 자연어 품질은 후속 보완 필요.
- 웹의 이전 분석과 비교 영역은 `2026102800`, `2026102812`, `2026102900`에서 확인; `2026102712`에서 확인되지 않은 이유는 현재 자료로 확정할 수 없음.

## 3. 2026102912 신규 전체 수행 검증

| 항목 | 값 | 증거 |
|---|---:|---|
| Step01~10 | `completed` | `run_status.json` |
| Step07 Check | 25 | `step07/pass2_check_results.json` |
| Step08 후보 | 3 | `step08/pass2_result.json` |
| Step08 의미·언어·observable 재시도 | 각각 0 | Step08 run_status.summary |
| Step09 유효·표출 | 3/3 | `step09/operational_result.json` |
| Step09 차단 | 0 | Step09 summary |
| Step10 웹 생성시각 | 2026-10-30 06:46 KST | 게시 페이지 |
| 환경 지속성 `limited` | 0 | Step08/09 결과 |
| 환경 지속성 `insufficient_evidence` | 3 | Step08/09 결과 |

### 후보별 게시 판단

| 후보 | 최종 등급 | 환경 지지 | 환경 지속성 | 대표 6h 최대강수량 및 대표 운영권역 |
|---|---|---|---|---|
| CAND_001 대한해협 강수 | `general` | `partial` | `insufficient_evidence` | 동해남부 19.0 mm |
| CAND_002 서해남부 강수 | `priority` | `confirmed` | `insufficient_evidence` | 서해남부 19.8 mm |
| CAND_003 남해동부 강수 | `priority` | `confirmed` | `insufficient_evidence` | 남해동부 27.9 mm |

대표 운영권역 최대값과 후보의 전 영역 최대값은 같지 않을 수 있다. 위 수치는 감시시간축에서 표출된 수치다.

### Step07 다수결·수렴 부호 일관성

- 25 Check 중 23건 3/3, 2건 2/3 판독으로 당시 검토됨. 소수의견 보고서에는 **결과 상태 이견 2건 + 위치 관련 이견 1건**으로 총 3개 설명이 포함될 수 있음.
- CAND_001의 925 hPa 권역평균 2026-10-30 03→09 KST를 비교한 후속 강제력 Check에서 `수렴 강화 아님` 9/9와 실제 `수렴 약화`, `수렴→발산`, `발산 약화`를 구분. 음수의 절댓값이 작아지는 것을 `수렴 강화`로 오분류하지 않도록 검증 계약 유지.
- CAND_001의 모델 강수는 존재하지만 4개 환경요소의 한 격자 또는 운영권역 완전 중첩을 확정하지 않아 `partial` 및 `general`로 표시.

### 숫자·위치 검증 주의

- 6시간 강수량의 특정 격자 최대값을 예보 구역 전체의 강수량으로 일반화해서는 안 된다.
- `2026102912`의 남해동부 강수는 경남·전남 육상 격자에도 큰 강수가 포함되어 있으나, 첫 화면 이름이 해상 중심임. 육상 영향 관련 메모는 `KNOWN_ISSUES.md`에 기록.
- 같은 유효시각을 비교한 다른 init 예측은 일치도/변화를 볼 수 있으나 동일 강수계 이동·발달의 확정 근거는 아니다.

## 4. 2026102912 운영 완료의 의미와 미검증 범위

**확인된 범위:** 5개 초기시각의 실행 상태·JSON 계약, P1 4회 재실행의 후보별 판단 및 웹 텍스트 일부.

**확인되지 않은 범위:** 관측 대비 적중·오경보·POD/FAR/CSI, 모든 예측기간/계절/강수사례의 재현성, 모든 이미지의 HTTP 200과 실제 브라우저 렌더링, JS 상호작용 전수 테스트, API 장애/중단/재시작 주입 시험, 보안 침투 테스트.

운영사례에서 정상 완료한 사실과 연구 검증에서 예측 성능을 입증했다는 주장은 구분한다.

## 5. 2026110300 Step04 및 Step07~10 재실행

기존 로컬 실행 결과만 읽어 확인했으며 파이프라인을 다시 실행하거나 운영 산출물을 수정하지 않았다. 아래 운영 결과는 마지막 Step07 assessment guard를 추가하기 전 저장된 자료다. 주요 파일의 SHA256은 [근거 등록부 R-005](EVIDENCE_REGISTER.md)를 참조한다.

### 단계별 실행 결과

| 단계 | 저장 결과 | 해석·제한 |
|---|---|---|
| Step01~03 | 완료 | 입력·개요·Step04 요청 패키지가 저장됨 |
| Step04 | 완료; 후보 3, 진단 요청 15, provenance 사용 가능 | `mandatory_direct_signal_audit`: required 11, covered 5, missing 6, `all_required_anchors_covered=false`. 후보 근거 포괄률 미완료를 운영 1.0 mm/6h 임계치 실패와 동일시하지 않음 |
| Step05~06 | 진단 요청 15 성공; Step06 후보 3, Check 17 | 진단 영상·수치 산출과 패키지 입력이 존재함 |
| Step07 | Check 17; Check별 독립 판독 3회; unanimous 11, majority 5, unresolved 1 | minority report `member_count=3`, `entries=7`; grounding retry 2, language retry 0, quantitative relation selector 자동수정·자동교정·실패 각각 0 |
| Step08 | 후보 3; 재사용 0; semantic retry 1 | Step07 직접강수 Anchor 상태를 후보별 종합에 전달함 |
| Step09 | valid 3, blocked 0, surfaced 1 | 계약 검증 통과가 표출을 보장하지 않으며 후보 2개는 hidden |
| Step10 | 제품 `published`; 후보 3 중 1개 표출; 자산 16; 분석 이력 생성 | 로컬 `daou/2026110300`의 `index.html`과 `forecaster_product.json` 존재를 확인함 |

### Step08~09 후보별 전달

| 후보 | 직접강수 | 환경 지지 | 지속성 | 물리 일관성 | Step09 |
|---|---|---|---|---|---|
| CAND_001 | `confirmed` | `confirmed` | `insufficient_evidence` | `conditionally_coherent` | `priority`, 표출 |
| CAND_002 | `confirmed` | `not_confirmed` | `not_applicable` | `conditionally_coherent` | `hidden` |
| CAND_003 | `insufficient_evidence` | `partial` | `limited` | `incoherent` | `hidden` |

CAND_003의 `CHK_CAND003_07`은 `uncertain / indeterminate`이며, uncertainty는 후보영역과 겹치는 운영권역 강수 기준의 완전 평가가 불가능했다고 설명한다. 제품 `current_action`도 강수 존재를 확정하기 어렵다고 기술한다. 한편 `final_assessment`에는 고정 분석영역 안에서 강수영역이 확인되지 않았다는 문장이 있다. 공간 범위가 서로 다를 수 있으므로 오류로 확정하지 않고, 예보관 화면에서 기준 평가범위와 후보영역 설명이 구별되는지 추가 확인 대상으로 기록한다.

### 재현·검증 범위

- 회귀 테스트 `tests/test_step04_unavailable_majority_scope.py` 10건과 `tests/test_step07_direct_precipitation_scope_availability.py` 13건, 총 23건 통과. 프로젝트 Conda Python 3.13.13으로 전체 `unittest` discovery 실행.
- `compileall -q func/step04 func/step07 tests`와 `git diff --check` 통과.
- 실제 `2026110300`의 JSON 결과와 로컬 웹 제품을 대조했다. 운영 URL은 도구에서 불러오지 못했고 별도 브라우저 자동화도 수행하지 않아 웹 렌더링·이미지 HTTP 응답·JavaScript 상호작용은 확인하지 않았다.
- 실행 완료와 계약 검증은 해당 실행의 산출 상태만 보증한다. 마지막 Step07 assessment guard의 운영 적용 여부는 확인하지 않았다. Step04 미연결 anchor 6건, CAND_003 표현 범위, 관측 대비 적중률·오경보율 및 전체 기상학적 타당성은 별도 검증 대상이다.

## 6. 2026110600 Step08 오류 및 재수행 대조

### 실제 실행 로그(`logs.zip`)

| 구분 | 기록(UTC) | 결과 |
|---|---|---|
| 최초 Step08 | 2026-11-06 09:02:54 | `CAND_002 / ENVIRONMENT_PERSISTENCE_INVALID`로 실패, 전체 파이프라인 중단 |
| 후속 Step08~10 | 2026-11-06 12:15:09~12:17:35 | Step08·09·10 완료, `PIPELINE_OK` |
| 후속 Step08 | 2026-11-06 12:17:27 | 후보 3, 체크포인트 재사용 1·신규 2, 재시도 0 |
| 후속 Step09 | 2026-11-06 12:17:29 | valid 3 / blocked 0 / surfaced 2 |

### `daio(2).zip` → `daio(3).zip` 비교

- 동일: Step04~07 산출물, `run_status.json` SHA-256 `864d5b9f9750b66d91b111a7d4893484644056370aa5a43ef858ab3143eda6a2`.
- 변경: `step08/pass2_result.json` SHA-256 `21cdf6fc98b1db358372f2f638044b55ea899b2846c6b52a8cf954f805b6da5e` → `42c3ba2b54497a3182dcc51f21b966d9df8e578a632208fa2adc759564fbc154`.
- 변경: `step09/operational_result.json` SHA-256 `ceb9631dbe6d21851a94f1e5f5aa680cbbf67db96017f809157c5f4d3dc4c07f` → `e287889a1d99dde4915d2682509936073061b8f87cf9aa78587cc04368d0d7e4`.
- 최신 Step08/09 파일의 ZIP 시각은 2026-11-06 22:12:36/22:12:38 KST다. 이 시점의 로그는 `logs.zip`에 포함되지 않았다.

| 후보 | 직접 강수 | 환경 존재 | 환경 지속성 | Step09 | 표출 상태 |
|---|---|---|---|---|---|
| `CAND_001` 동해중부 | `confirmed` | `confirmed` | `limited` | `valid` | `elevated`(우선 확인) |
| `CAND_002` 서해남부 서부 | `confirmed` | `not_confirmed` | `not_applicable` | `valid` | `hidden`(비표출) |
| `CAND_003` 동해남부 | `confirmed` | `partial` | `insufficient_evidence` | `valid` | `general`(일반 감시) |

### 검증의 한계

- `CAND_002`의 환경 미확인/지속성 미적용 조합은 현행 검증 계약과 일치한다. Step07 Check 22건 중 합의 미성립 1건은 설계된 제외 처리다.
- **Step08·09 JSON 검증 통과를 동일 오류의 재발 방지 입증이나 기상학적 정확도 검증으로 해석하지 않는다.**
- 운영 서버에서 미커밋 변경된 실제 코드 Diff, 최신(22:12 KST) 실행의 로그, 최신 Step10 제품 재생성·웹 게시, 브라우저 내 PNG/JS 동작은 확인 대상이다.

## 7. 2026111112 Step07 정량 선택자 오류 수정 검증 (2026-11-11~13)

### 실패 원본과 수치 확인

- **원본:** 사용자 업로드 `AIFSHeavyRain_daio.zip` (SHA256 및 근거 파일 경로는 [EVIDENCE_REGISTER.md](EVIDENCE_REGISTER.md)의 R-006 참조).
- **오류:** `CAND_002 / CHK_CAND002_05 / BND_642E5798D6AC520A`; 첫 번째 판독은 복구, 두 번째 판독은 성공, 세 번째 판독에서 `QUANTITATIVE_RELATION_MISMATCH` 후 `QUANTITATIVE_CLAIM_INVALID`이 재발하며 Step07 실패(2026-11-11 21:58:54 UTC).
- **Step05 유효 자료:** `REQ_CAND002_005`, `convergence925_1e5_s`, F054·F060·F066·F072. F054→F072의 공통 운영권역 18개 중 물리적 수렴 강화 8개, 비강화 10개(부호 의미 고려). 이 집계는 전체 기간 중 모든 시각의 기상학적 지속성을 확정하는 결과는 아님.

### 수정본의 정적·연결 회귀검증

| 검사 | 결과 | 증거 범위 |
|---|---|---|
| 실패 진단과 동일한 Check의 데이터 선택자 연결 | 통과 | Step05 기반 정량 선택자 36개·비교 관계 18개 |
| 잘못된 `feature_id` 및 수치 비교 관계를 원자료 기준으로 교정 | 통과 | 저장된 오류 진단 4건의 구조를 이용한 소스 연결 검증 |
| 실제 8/18 강화인데 `observed`로 주장한 판정 | 거부 확인 | `QUANTITATIVE_EXPECTED_STATE_MISMATCH` |
| 같은 자료에 맞는 `opposite_observed` 판정 | 정량계약 통과 | 실제 AI 응답의 전체 문장 검증을 의미하지 않음 |
| 명시적 강화 계약·기존 비강화 계약·누락/중복 자료 | 통과 | 범위 검증 및 기존 기능 회귀검증 |
| Python 구문 및 `git diff --check` | 통과 | 수정본 제공 시점의 파일 검사 |

### 실제 운영 재수행 상태와 한계

- **사용자 확인:** 2026-11-13, 수정 내용을 적용한 후 "문제없이 수행되는 걸 확인"했다고 보고.
- **독립 확인 여부:** 수정 후 운영 `run_status.json`, 단계별 로그, 전체 Step07 앙상블·Step08~10 산출물 및 운영 웹 결과는 이번 문서 작업에서 추가로 확보하지 않았다. 따라서 완료 시각·후보 수·Step09 표출 수 등 세부 수치는 기재하지 않음.
- **Git 상태:** 문서화 시점의 공개 `main`은 `15b0e07`이며 이번 수렴 강화 정량연결 수정은 Git 반영 예정. 사용자 운영 성공 보고를 Git 병합 완료나 예측성능 검증과 동일시하지 않음.
