# AIFSHeavyRain 날짜별 개발·수정 이력

**작성 기준일:** 2026-11-06(KST; 기존 기록 보존, 이번 실행 이력 추가)
**범위:** 현재 접근 가능한 과거 대화 맥락, 업로드된 운영 코드 스냅샷과 수행 ZIP, GitHub 이력. 전체 대화 원문/모든 과거 파일을 전수 확보한 것은 아니며, 복원 불가능한 세부사항을 임의로 보완하지 않았다.

## 0. 기록 방법

- **[코드반영]** 현재 GitHub 커밋과 파일 차이로 확인.
- **[실행검증]** 사용자 제공 로그·`run_status.json`·Step 결과 JSON으로 확인.
- **[대화기록]** 당시 사용자가 보고한 결과 또는 해당 시점 논의. 실제 현업 배포까지 보장하지 않음.
- **[제안/미확정]** 제안·패치 제공 또는 실패 분석만 확인. 실제 적용 증거 없음.
- **[시점 주의]** 오류가 발생한 날짜와 수정이 제시된 날짜, 서버 적용 날짜가 다를 수 있음.

검토 기준: `#strict_review`, `#verify_source`를 적용하고, 오류 발생일·코드 수정일·운영 적용일·검증일을 구분하였다. 기존 기능에 대한 영향, 회귀 위험 및 공개 저장소의 보안 위험도 점검하였다.

---

## 1. 2026년 6월 — 개념 검증과 단계적 파이프라인 구성

### 2026-06-08 — Phase 1A 기본 구조 수립 [대화기록]

- Phase 1A는 입력·출력 스키마와 `pending` 상태를 먼저 생성하는 골격 단계로 보고됨. 실제 AIFS 자료와 LLM 분석 결과는 아직 생성하지 않았음.
- `data_status=pending`, `llm_execution_status=not_started`; AIFS 자료 가용성 `false`; 스키마 JSON 11개 파싱 통과.
- +6/+12/+18/+24/+30/+36h 시간축을 다루며 AIFS lead time과 관측 `valid_time`/`time_range`를 분리하고 수치 metric과 진단 label을 구별하는 설계 원칙 확인.
- **결과:** Phase 1A 골격·계약 검토 단계. 실자료 운영 시작으로 기록하지 않음.

### 2026-06-18 — 초기 통합 입력 및 Round 기반 2-pass 검토 [대화기록]

- 대용량 예측자료/NPZ는 제외하고 샘플로 검수 수행.
- 당시 레거시 구조에서 weather signals 54개, Round1B 후보 3개 및 요청 9개, Round2 패키지·진단 QC 통과, blank PNG 0건으로 보고.
- 정리되지 않은 레거시 구조·manifest·후보 JSON의 설명 필드 등이 추가 개선사항으로 지적됨.
- **결과:** 초기 기능 일부 검수. 현재 Step01~10 코드와 1:1 대응되는 완성도·버전으로 해석하지 않음.

### 2026-06-22 — 수동 AI 응답 경로와 유효성 검사 [대화기록/제안]

- Round 4에서 요청 패키지 준비 → AI 응답 JSON 수동 저장 → 스키마 검사 → 화면 렌더링 절차를 도입. 해당 단계에는 자동 API 호출이 없음.
- 당시 후보 3개, 이미지·근거 참조 134개, base64 이미지 포함 0개로 보고.
- 별도 재검증에서 후보 수가 3→0, 오류가 1→5로 늘어난 사례와 UTF-8 BOM 문제 발생. 수정 JSON 작성이 제안되었으나 해당 정정 파일의 장기 적용 사실은 별도 미확인.

### 2026-06-23 — Phase 2.1과 수치 파생진단 개발 [대화기록]

- Phase 2.1까지 완료, Phase 2.2는 당시 미구현이라고 보고.
- Phase 2.6의 `low_level_convergence` 진단 개발 후 2026061400 예제에서 유효값 16,542,752개, 양의 수렴 분포 임계치 통계 및 단위/시나리오 검증 보고.
- **결과:** 개별 기상 파생진단 검증. 후속 버전의 수렴 부호/단위 변경 여부는 현행 코드·카탈로그를 우선해야 함.

### 2026-06-29 — Phase 2.10.4 계약 정리 [대화기록]

- real-init 금칙어 0건, 웹 quicklook 노출, `context_only` 제품 처리, `input_available` 수집 상태, 자체 완결형 Pass2 패키지, 스키마·회귀 검사 완료로 보고.
- 별도 검수에서 `source_region`/`impact_region`의 근거, 통계 scope 구분, 산출물 절대경로 제거가 후속 과제로 지적됨.

---

## 2. 2026년 7월 — 실제 AIFS 데이터 수집·개요·진단 확장

### 2026-07-02 — 개요자료와 구조 정비 [대화기록]

- 당시 Step03 개요 산출물 48장과 manifest 매칭, `not_created=0`, F000 적합성 검사, 계산 오류 0 보고.
- 다른 실행 기록에는 특성 19개, 영역 4개, field summary 474건, 처리행 1,588개가 표시됨.
- `components/`→`func/` 경로 이동, stale PNG 정리, pycache·로그 배포 제외, `raw_excluded` 메타데이터 등은 당시 **수정 권고**. 실제 커밋이 없으므로 일괄 완료로 기록하지 않음.

### 2026-07-11 — AIFS 자료·예측시간 축 검토 [대화기록]

- 당시 확인 자료는 F000~F120 6시간 간격 21개, GRIB 파일 567개, short name 15개, feature 19개, 영역 4개, compact summary 160건, broad map 48장 등의 구버전 지표.
- 설계/실행상태/향후 계획/완료 이력 문서 역할을 분리하기로 논의.
- **주의:** 위 수치는 10월 현재 자료 개수나 현재 운영권역 수를 의미하지 않는다.

### 2026-07-21 — Step01 수집 요청 최적화 [실행검증: 당시 사용자 로그]

- AIFS 수집 요청을 357→337건으로 조정하는 수정안을 검토. `z`의 초기시각 특례와 `sp` 예측시간 범위 등을 반영.
- 후속 사용자 실행에서 **337/337 파일 다운로드**, 실패·취소·재시도 0 보고. 단, GRIB 내부 메시지·레벨·시간축의 완전한 내용 검증은 별도 과제로 남음.

### 2026-07-23 — 테스트 함수 계약 불일치 [대화기록]

- `validate_final_response()` 테스트 호출에 `operational_region_catalog` 인자가 없어 targeted test 실패.
- 실제 구현이 아니라 테스트 코드의 호출 계약 문제로 판단하고 테스트 파일 수정본을 제공. 그 뒤 전체 테스트 통과 여부는 별도 근거가 없음.

### 2026-07-30~31 — Step05와 Step06 패키지 검증 [실행검증: 당시 보고]

- Step05 정량 진단 106/106 요청 충족, PNG 129개, 정량 JSON 106개, 전체 attachment 235개로 보고.
- Step06 strict dry-run에 후보 5개, provider 호출 0회, 조건부 준비 상태(`CONDITIONALLY_READY`) 보고.
- 잔여 과제: 당시 감사자료·stale 비교 JSON 및 provider 입력 한도 등은 완전 검증되지 않음.

---

## 3. 2026년 8월 — Linux/AWS 현업 운영 및 웹 배포

### 2026-08-13 — Linux 파이프라인 실행환경 검토 [대화기록]

- Linux 상의 ksh·Conda 기반 장기 운영(약 6개월 관찰)을 구상.
- 과거 버전 `run_pipeline.ksh --start-step NN` 수정과 테스트 일부 통과를 보고했으나 Windows에서는 실제 ksh end-to-end 검증이 제한되었음.
- **현행 주의:** 10월 운영본 `scripts/run_pipeline.ksh`는 상단 True/False 스위치 중심 구조이므로, 8월의 `--start-step` 지원을 **현재 사용법으로 단정하지 않는다**.

### 2026-08-17 — AWS Ubuntu와 웹서비스 운영 기반 [실행검증/대화기록]

- AWS Lightsail Ubuntu 24.04 x86_64, Python/Conda, SFTP 접속과 Nginx 제공 체계를 구성.
- 프로젝트 기준 디렉터리는 `/home/ubuntu/AIFSHeavyRain`; 실행 산출물 `daou/<init>/`와 웹 서버 제공 디렉터리를 구분.
- `2026081500` 예보관용 결과 파일 존재 및 브라우저 접속을 확인. 이는 당시 **웹 검증**이지 Step01~10 신규 실행 완료 증거는 아님.

### 2026-08-18 — Step07 API timeout 및 Step10 생성·웹 게시 [실행검증/대화기록]

- `2026081712` Step07에서 `httpx.ReadTimeout`/`APITimeoutError`가 발생. SDK 자체 retry가 제한적이었고 timeout/network 계열에 대한 래퍼 재시도 범위 보강이 제안됨.
- 별도 `2026081800`에서는 Step10 재실행 후 성공, 후보 5개 중 3개 표출, 웹 릴리스·`latest` 제공 경로 정상으로 보고.
- **구분:** timeout 원인에 대한 패치 제안과 Step10 웹 게시 성공은 서로 다른 실행이며, 하나의 수정으로 모든 오류가 해결됐다고 단정할 수 없음.

### 2026-08-19 — Step04 check_scope 불일치·Step08 timeout [대화기록]

- Step04에서 CAND_001의 `temporal_evolution`과 `check_scope` 계약 불일치로 실패. 계약 2.9.0→2.9.1 수정본/모의 검증 보고가 있었으나 LLM 재실행은 미확인.
- 이어 Step07 31개 확인 후 Step08 API timeout 발생 사례 보고.

### 2026-08-20 — Step10 웹 UI 파일 버전 문제 [실행검증/대화기록]

- AWS에 배포된 실행 파일이 수정 전 버전이어서 `MINIMAP_COASTLINE` 속성 오류가 발생한 사례 확인.
- 위치도는 별도 카드 대신 후보 핵심판단 옆 `지도확인` 버튼 및 팝업으로 제공하는 방식을 요청·구체화.
- **상태:** 당시 패치 및 검증 맥락. 현재 Step10의 전체 지도 기능을 8월 패치와 동일시하지 않음.

### 2026-08-25 — Codex CLI 실행 경로 장애 [대화기록]

- 운영 서버의 Codex 실행파일이 사용자 경로에 설치되어 cron 유사 제한 PATH에서 `CODEX_NOT_FOUND` 발생.
- 실제 실행경로를 명시하도록 `CODEX_BIN` 설정을 조정하는 방안 제시. 설정이 어떤 사용자·프로세스에서 최종 우선 적용되는지는 환경변수 및 실행 래퍼 검사 필요.

---

## 4. 2026년 9월 — 직접 강수 anchor와 근거 연결·표출 고도화

### 2026-09-01~02 — SYSTEM 직접 강수 anchor 및 UI 결합 [대화기록/실행검증]

- Step04에서 직접 강수 존재 확인용 SYSTEM anchor를 이동·조직화 등 다른 진단과 분리하고 Step08 상태 구성에 전달하는 수정 방향을 검토.
- `2026083112` 재수행 웹페이지에서 일부 `850 hPa 수분수송` 영상 링크가 비연결되는 사례와 팝업 문구 키 불일치 가능성 제기.
- 9월 2일 후속 웹 검토에서 후보가 4→5개로 변경된 재현성 차이를 확인. 후속 동일-input 시험 필요성 제기.

### 2026-09-03 — 운영권역 겹침, 기존 출력물 충돌, LLM 영어 토큰 [대화기록]

- Step04 후보가 국내 운영권역과 중첩되지 않으면 `OPERATIONAL_REGION_TARGET_EMPTY`로 차단하도록 검증안 제시.
- Step04 37개 진단 요청 생성 후 Step05의 기존 `REQ_CAND001_001.json` 파일로 `OUTPUT_EXISTS` 발생.
- Codex 서비스가 503/504, `concurrency_limit`, `circuit_open`을 반환한 사례와, API fallback 테스트 성공 기록 존재.
- Step04 candidate_plan에서 한국어 전용 규칙에 맞지 않는 `mm/6h`, `confirmed` 토큰으로 실패. 한국어 표기로 교체하는 수정안 제공.
- **구분:** 위 사례들은 서로 다른 장애 원인이며 각 코드의 현행 반영 여부는 Git 이전 구간이라 부분 미확정.

### 2026-09-14 — Step07 판독·웹 화면 재검토 [실행검증/대화기록]

- 과거 `2026081600` 재수행 웹페이지에서 6종 기상장, 시계열·환경 지속성·위치 판정 관련 개편 표출 확인.
- Codex 멀티모달 실패 원인은 당시 원시 오류 출력 부재로 429/5xx/stream 문제 중 어느 것인지 확정하지 못함.

### 2026-09-17 — 예보지원 시스템의 역할과 구조 설명 정리 [대화기록]

- 예보기술발표회 관련 자료에서 시스템 정의를 **“예보를 대신하는 AI가 아니라, 분석계획과 자료 검증을 수행하는 Agentic AI Workflow 기반 예보지원 체계”**로 구체화.
- Step01~10 업무 절차, 후보 중심 가변 진단, 지지·제한 근거의 동시 확인, Python 결정론적 검증을 구분하여 설명.
- 일반-purpose agent 도구 사용 여부와 무관하게 단계를 분리한 검증 가능한 workflow 구조를 강조함. 발표문구 정리이지 코드 수정 증거는 아님.

### 2026-09-21~24 — 재수행 연속 오류 검토 [대화기록, 결과 일부 미확정]

- `2026092112` 실행 및 9월 22~24일 여러 `daio`·로그·소스 ZIP에 대해 반복 오류와 웹 표출 검토가 요청됨.
- 해당 대화가 존재함은 확인되지만, 이번 자료에서 각 ZIP의 상세 내용을 모두 재열람할 수 없어 **개별 실패 원인·최종 해결 여부를 확정 기록하지 않는다**.
- Step07의 3회 판독 일치율은 AI 판단 안정성의 지표이며 기상 현상의 확률 자체가 아니라는 구분 및 Step09 결론-근거 일치 검증의 중요성을 논의.

### 2026-09-29 — 운영 URL 접근·robots 논의 [대화기록]

- 사용자는 운영 페이지가 브라우저에서 정상 접속되나 일부 외부 조회 도구는 실패하던 차이를 확인.
- robots.txt, 웹 크롤러 접근 정책과 실제 사용자 브라우저 서비스 가용성의 구분을 논의. 웹 접근 보안과 robots 정책은 별개.

---

## 5. 2026년 10월 — 운영 안정화, P1 검증, Git 관리

### 2026-10-02 — 불확정 공간 위치의 과잉 특정 [대화기록]

- `spatial_localization=indeterminate`인 Check에 대해 Step07의 문장에서 특정 방향·세부 해역을 단정하고 Step08에서 이를 재사용한 사례 분석.
- 후보명 생성 알고리즘 오류와 근거 없는 공간 수식어 사용을 구분하여, 공간적 정체성 계약 보강을 권고.

### 2026-10-05 — 후보 0개인 시각의 Step06 실패 [대화기록/당시 재검증]

- `2026100500`에서 후보 0개·진단 요청 0개인 실행의 Step05 이후 Step06 `STEP05_DIAGNOSTICS_MISSING` 실패.
- 후보가 없을 때 빈 산출물 디렉터리 생성, GRIB 의존 분리, 후보·요청 집합 일치 검사 등을 수정했다고 보고; Step05~10 및 웹 결과 `no_surface_candidates` 검증 기록 존재.
- **시점 주의:** 해당 대화에는 과거 임계치 5 mm 언급이 있었지만 **10월 30일 운영 정책은 1.0 mm/6h**다. 이력을 현재 운영 임계값으로 재사용하지 말 것.

### 2026-10-12 — 실제 운영 구조 재점검 [대화기록/운영 코드 스냅샷]

- 운영 ZIP을 기준으로 `run_pipeline.ksh`~Step01~10 흐름을 검토. `zrun_pipeline.ksh`, `zzrun_pipeline.ksh`는 **테스트용이라 공식 구조도 제외**.
- 프롬프트 이름, 단계별 Check·JSON·이미지·웹 출력 경로를 함께 문서화하도록 요구.
- Step07의 3회 판독·소수 의견 보존, Step09의 결정론적 게시 게이트, Step10의 웹 표시 계약이 주요 구조로 확인됨.

### 2026-10-13~14 — Step07 인자/판독 및 Step08 이동 단정 오류 [대화기록]

- Step07에서 필수 선택자 및 결과 구조의 일치 오류가 반복되었으며, 검증·재시도 수정안 제공 기록 존재.
- `2026101312` Step08의 `CAND_002`가 강수 이동 일치성이 검증되지 않았는데도 확정적 문장을 작성하여 `NARRATIVE_LANGUAGE_INVALID` 후 제한 횟수 재생성까지 실패(10/14 07:58 UTC).
- 당시 Step08 문구 검증 보강은 제안/패치 맥락이며, 그 시점에 전체 연계 시험 통과했다고 기록하지 않음.

### 2026-10-15~16 — Step09 정량 질의 검증 오탐 [대화기록/당시 재검증]

- Step09 후보 차단 보고. `2026101512`에서 단일시각 최대강수 질문에 시각 간 평균 비교 정량 조건을 요구하는 오탐을 식별.
- 실제 변화 질문에 대해서만 변화 검증을 적용하도록 수정했다고 보고. 이후 유효 후보 7개·차단 0, Step10 정상 및 117개 테스트 통과라는 당시 검증 기록 존재.

### 2026-10-17 — STEP04/STEP08 검증 실패 [대화기록]

- Step04: `SYSTEM_DIRECT_PRECIPITATION_COVERAGE_REQUIRED` incremental reconciliation 반복 소진 사례. 실제 필수 강수 anchor의 범위 포괄성 문제를 지적.
- Step08: `OPERATIONAL_GUIDANCE_INVALID`(필수 `current_action` 누락)와 `PHYSICAL_DECISION_INVALID` 오류 사례.
- **상태:** 오류 발생 자체는 확인. 각 버그를 해당 일자에 최종 해결했다는 증거는 없음.

### 2026-10-18~19 — 후보 분석계획, Step08 출력 오류 [대화기록]

- 10/18 Step04 `candidate_plan`에서 직접 강수 진단 요구의 계약 위반 사례.
- 10/19 Step08 `OPERATIONAL_GUIDANCE_INVALID`가 다른 후보에서 재발.
- 지속적 재수행만으로 해결하기보다 실패 코드를 분류하고 입력 계약·검증기의 원인을 분리하자는 방향 검토.

### 2026-10-21~22 — 작은 영역 PNG, 정량 주장, 중복 출력 [대화기록]

- 10/21 Step05 `PNG_RENDER_FAILED`: 입력 격자 shape `(1,1)`인 너무 작은 요청 영역 사례. 유효한 진단 영역 최소조건 필요.
- 10/22 Step04 `QUANTITATIVE_ATOMICITY_INVALID`: 하나의 정량 atomic claim 계약 위반.
- 10/22 Step10 `FORECASTER_PRODUCT_EXISTS`: 동일 init 기존 결과가 존재할 때 `--overwrite` 등 명시적 교체 절차가 필요.

### 2026-10-25 — Step08 시공간 정합 서술 [대화기록]

- `ALIGNMENT_NARRATIVE_INVALID`(수분 공급과 정합 서술의 불일치) 반복 사례 기록.
- 환경 지지를 강화해 표현하는 대신, 사용 가능한 Step07 근거·구조화된 정합 상태에 맞는 서술만 허용해야 한다는 운영 원칙 유지.

### 2026-10-27 — 운영 기준 코드 최초 Git 등록 [코드반영]

- `2026102700` 수행에서 Step07 32개 Check(30건 3/3, 2건 2/3), Step08 4개 결과, PNG 104개, 직접강수 필수 anchor 13/13이라는 당시 검토 기록 존재. 해당 수치는 별도 입력의 실행기록임.
- 운영본을 GitHub Public 저장소에 **최초 등록**: [`24ff2e2`](https://github.com/hamian84/AIFSHeavyRain/commit/24ff2e291468384fd790303bd451e2e6c59e2434).
- Git에 처음 등록되었다고 이전 수개월의 개발이 이때 발생한 것은 아님. `envi/`, 원자료, 실행 중간산출물, 로그·임시 파일은 추적 제외.
- Model 라우팅 실험에서 Terra/Sol 변형을 사용한 대화 기록 존재. 모델 ID는 운영 설정이 변할 수 있으므로 **현재 실행의 `run_status.json`에 기록된 값**을 우선한다.

### 2026-10-28~29 — 4개 시각에서 환경 지속성 검증 누락 발견 [실행검증]

- `2026102712`~`2026102900` 4회 초기시각의 완료된 Step01~10 원본 출력 수집. 총 **15개 후보**, Step07 확인항목 **110건**(23+31+29+27), Step09 차단 0.
- 수정 전 환경 지속성은 `limited` 2개, `insufficient_evidence` 13개.
- 원인: Step08 공통 함수에서 `"POST" in phase_key.upper()`에 의존했지만 실제 Step04 Check의 local key는 `chunk01:P02`, `chunk01:P03`, 공식 분석단계는 `phase_type=post_peak`로 따로 관리됨.
- 수정 설계: 공식 분석단계·source intent·atomic claim·Check 시각 범위를 교차 검증하고 범위가 모호하면 fail-closed. 감시등급 계산은 변경하지 않음.

### 2026-10-29~30 — P1 실제 코드 적용 및 4회 재수행 [코드반영/실행검증]

- 실제 변경된 파일 4개: `func/shared/signal_validation.py`, `func/shared/tier_narrative.py`, `func/step08/synthesis.py`, `func/step09/operational.py`.
- 수정 후 4회 동일 init Step08~10 재수행: `limited` **2→5**, `insufficient_evidence` **13→10**, 3개 후보에서 제한 근거 신규 인식.
- 해당 세 Check는 `2026102712/CAND_002/CHK_CAND002_05`, `2026102812/CAND_002/CHK_CAND002_06`, `2026102900/CAND_001/CHK_CAND001_04`.
- Step09 15/15 유효·표출, 차단 0. 등급 구성은 중점 6/우선 확인 8/일반 1로 유지됨.
- Step02~07 입력 파일은 수정 전후 두 ZIP에서 동일하며, 일부 Step08 서술·환경 지지 수준은 LLM 재생성으로 달라짐(`2026102900/CAND_002`: partial→confirmed). **환경 지속성 수정의 직접 효과와 동일 입력 LLM 변동을 분리해야 함.**
- 부분 재수행 `run_status.json`은 Step01~07을 `not_started`로 기록하면서 `pipeline_status=partial_completed`가 됨. 파일이 삭제된 것은 아님.

### 2026-10-30 — 신규 2026102912 전체 실행 및 PR 병합 [실행검증/코드반영]

- 신규 `2026102912` Step01~10 완료, Step07 확인항목 25개, Step09 유효·표출 3/3, Step08 재시도 0. 웹 생성시각 **10월 30일 06:46 KST**.
- 환경 지속성 세 후보 모두 `insufficient_evidence`; 후속 공간 지속성의 적격 근거 부족일 수 있으므로 자동으로 오류로 단정하지 않음.
- 10/30 운영 사이트의 후보는 대한해협(일반), 서해남부(중점), 남해동부(중점)로 표시됨.
- GitHub 수정 커밋 [`2f5a1a1`](https://github.com/hamian84/AIFSHeavyRain/commit/2f5a1a166f4d46ab37c0db60e99628f6cd99eae5), 병합 [`4b66fc3`](https://github.com/hamian84/AIFSHeavyRain/commit/4b66fc32dd012ec95e0ee73ff7136973f07c781d).
- `.gitignore`에 `his_run_pipeline.ksh`만 추가 제외하여 [`82f42b1`](https://github.com/hamian84/AIFSHeavyRain/commit/82f42b193663bb65f8ccf4b9af276ca7524d05f0)로 원격 `main` 반영. 로컬/원격 clean 동기화는 사용자 터미널 출력으로 확인됨.
- GitHub 계정 비밀번호 직접 Git HTTPS 인증은 지원되지 않으므로 PAT/SSH/credential manager를 사용하며, 토큰 값은 문서화하지 않음.

### 2026-11-03 UTC (2026-11-04 KST) — 후보 단위 정량 범위 복구와 직접강수 평가 가능성 [코드반영/회귀검증/실행검증]

- **발생 증상:** 초기시각 `2026110300`의 Step04에서 `CANDIDATE_QUANTITATIVE_SCOPE_UNAVAILABLE` 및 `STEP04_ATOMIC_REPAIR_NONPROGRESS`가 발생했다. CAND_003은 선행 환경신호 후보였고, 고정된 `candidate_area`와 겹치는 실행 가능한 육상/해상 운영권역이 없었다. 그럼에도 `majority + not_strengthen` 명제는 Step05/Step07 운영권역 925 hPa 정량 판정이 필요한 조건으로 해석됐다.
- **직접·구조적 원인:** `_validate_compact_quantitative_atomic_claims()`가 운영권역 범위의 실행 불가능성과 개별 Check의 정량 계약 누락을 구분하지 않았다. `call_and_validate_json()`의 Check 단위 재생성은 후보영역 제약을 바꿀 수 없어 같은 위반을 반복할 수 있었다.
- **Step04 변경:** `CANDIDATE_QUANTITATIVE_SCOPE_UNAVAILABLE`를 후보 계획 chunk 수준 계약으로 분류한다. `_compact_candidate_chunk_repair_required()`가 이 오류에는 Check 복구를 적용하지 않게 하고, `call_and_validate_json()`은 원래 후보·가설 컨텍스트에서 현재 chunk 전체를 bounded budget으로 재생성한다. `_repair_rules_for_validation_errors()`는 후보 영역 이동·확대, 수량 명제의 정성 전환·극성 변경을 금지하고, 원래 가설 목적을 유지하되 확인할 수 없는 명제는 미검증으로 남기도록 안내한다. 반복 무진전은 `STEP04_CANDIDATE_CHUNK_REPAIR_NONPROGRESS`로 중지한다. 다른 국소 Check 오류의 기존 전체 Check 복구는 유지한다.
- **Step07~09 변경:** `_system_direct_precipitation_anchor_context()`가 운영권역 중첩, 유효 강수값, scope 메타자료를 분리하고 시간별 평가 상태와 임계치 평가 완결성을 계산한다. 유효 강수값이 0인 완전 평가와, 운영권역 중첩·값·메타자료가 없어 평가할 수 없는 경우를 구별하며, 필요한 Step05 메타자료가 없으면 파이프라인을 중단하지 않고 평가 불가 상태를 만든다. `_bundle_messages()`는 평가가 불완전할 때 이를 강수 미발생으로 판정하지 않도록 한다. 독립 검토에서 prompt 지시만으로는 LLM의 `not_observed`/`opposite_observed` 출력을 막지 못하는 경계가 확인되어, `_validate_system_direct_precipitation_assessment()`가 불완전 평가에는 `insufficient_evidence / indeterminate`만 허용하도록 보완했다. 이 위반은 `DIRECT_PRECIPITATION_EVALUATION_UNAVAILABLE_ASSESSMENT_INVALID`로 분류해 제한된 grounding 재시도에 연결했다. Step08 `_direct_precipitation_status_contract()`는 평가불능 또는 사용할 수 없는 Anchor를 `insufficient_evidence`로 전달하고, Step09 표출 Gate는 이를 강수 부재로 축약하지 않는다.
- **체크포인트:** `_check_fingerprint()`의 Anchor 문맥에 `scope-availability-v1` 계약 버전을 추가했다. 기존 범위·값 메타자료와 함께 버전도 지문에 반영되므로 이전 규칙으로 만든 Anchor 체크포인트는 재사용되지 않고 재평가된다. 다른 입력 지문이 일치하는 Check의 재개 경로는 유지한다.
- **회귀 검증:** `tests/test_step04_unavailable_majority_scope.py` 10건과 `tests/test_step07_direct_precipitation_scope_availability.py` 13건, 총 23건 통과. 프로젝트 Conda Python 3.13.13으로 `/home/ubuntu/miniconda3/envs/aifsheavyrain/bin/python -B -m unittest discover -s tests -v`, `compileall -q func/step04 func/step07 tests`, `git diff --check`를 실행했다. 빈 운영권역에서의 chunk 복구, 기존 국소 Check 복구, 유효 0 강수의 완전한 임계치 미달, 필수 scope 메타자료 누락, 평가 불가 상태의 결정론적 강제와 bounded retry 분류, Step08 상태 전달, Step09 선행환경 후보 경로를 확인했다.
- **기존 운영 결과 확인:** 저장된 `2026110300` 산출물에서 Step01~10 완료, Step04 후보 3개·진단 요청 15개·provenance 사용 가능, Step07 17개 Check·3회 판독·`unanimous=11 / majority=5 / unresolved=1`, Step08 후보 3개·재사용 0·semantic retry 1, Step09 valid 3·blocked 0·표출 1, Step10 제품과 16개 자산 및 분석 이력 생성을 확인했다. 이 자료에는 Step04 복구 시도 횟수가 기록되지 않아 해당 운영 실행에서 chunk 복구가 실제 호출됐는지는 단정하지 않는다. 마지막 Step07 assessment guard는 이 산출물 이후 추가한 코드이며 해당 init을 재실행하지 않았다.
- **남은 검증 범위:** Step04 `mandatory_direct_signal_audit`는 11개 중 5개만 후보 근거로 연결되고 6개가 미연결(`all_required_anchors_covered=false`)이다. 이 지표를 운영 임계치 실패 또는 강수 부재로 해석하지 않으며 후속 확인 대상으로 유지한다. CAND_003은 Step07 직접강수 Check가 `uncertain / indeterminate`, Step08/09 상태가 `insufficient_evidence`이다. 제품의 현재 대응 문장은 강수 존재를 확정하기 어렵다고 쓰지만, 종합 문장은 고정 후보영역 안에서 강수영역이 확인되지 않았다고 기술한다. 범위 차이일 수 있어 결함으로 확정하지 않고 화면 문구의 평가범위 명시를 후속 검토한다. 현재 저장된 `run_status`에는 Step07 체크포인트 재사용 수가 없어 해당 실행의 재사용 여부는 확인하지 않았다.
- **최종물 확인 한계:** 로컬 `daou/2026110300` 제품 파일은 확인했다. 제공된 운영 URL은 이번 도구에서 열리지 않아 실제 브라우저 렌더링, 이미지·JavaScript 상호작용은 검증하지 않았다. 정상 실행은 기상학적 정확도 검증과 구분한다.
- **Git:** 코드·회귀 테스트 [`945320f`](https://github.com/hamian84/AIFSHeavyRain/commit/945320fed4003102e072197734dc033936df6dd3), [`4d797cc`](https://github.com/hamian84/AIFSHeavyRain/commit/4d797cc4eefc3985a11b5447026575c4052c6e81). 문서는 코드 변경과 분리한 같은 PR의 후속 커밋으로 관리한다.

### 2026-11-06 — Step08 환경 지속성 상태 불일치와 재수행 [실행검증/로컬코드변경 미확정]

- **초기시각:** `2026110600`(2026-11-06 00UTC). 최초 Step08는 2026-11-06 09:02:54 UTC에 `CAND_002`의 `ENVIRONMENT_PERSISTENCE_INVALID`로 중단되었다. 원문: `environment persistence must be not_applicable when environment support is not confirmed/partial`.
- **원인(확인):** Step08 AI가 생성한 환경 존재 상태(`environment_support_status`)와 환경 지속성 상태(`environment_persistence_status`)의 조합이 현행 검증 계약과 불일치했다. 실패한 AI 원본 응답은 확보하지 못해 잘못 생성된 정확한 enum 값은 미확정이다.
- **검증 계약(기준코드 확인):** 환경 `not_confirmed` 또는 `insufficient_evidence`에서는 지속성 `not_applicable`과 빈 `environment_persistence_evidence_check_ids`가 필수이다. 환경 `confirmed`/`partial`에서는 시간변화 근거에 따라 `confirmed`/`limited`/`insufficient_evidence` 중 하나를 사용한다. 해당 계약이 11월 6일 새로 구현되었다는 증거는 없다.
- **관련 소스:** `func/step08/synthesis.py`(상태·근거 검증 및 재생성), `prompts/step08/candidate_synthesis.ko.md`(AI 지시), `func/shared/signal_validation.py`(결정론적 계약), `scripts/08_run_pass2_synthesis.py`(`--no-resume`, 체크포인트 재사용). 이전 서버 `git status`에는 앞의 두 수정파일과 `.gitignore` 변경이 표시됐으나 **서버의 실제 `git diff`를 확보하지 못해 함수·문장 단위 코드 변경과 변경 원인은 확정하지 않는다.**
- **재실행:** 같은 초기시각의 Step08~10을 2026-11-06 12:15:09~12:17:35 UTC에 재수행하여 완료했다. Step08 후보 3건(재사용 1건/신규 2건), Step09 유효 3건·차단 0건·표출 2건·비표출 1건.
- **추가 재생성 산출물:** `daio(3).zip`의 Step08 및 Step09 JSON은 각각 22:12:36, 22:12:38 KST로 갱신되었다. 이전 `daio(2).zip`에 비해 Step04~07은 변경되지 않고 Step08·09 JSON만 변경되었다. 후보별 상태 및 표출 여부는 유지되었다. 해당 재생성의 **별도 로그는 제공되지 않았고**, 동봉 `run_status.json`은 이전 실행과 동일하므로 최신 실행 내부 재시도 횟수는 확인되지 않는다.
- **판단:** 재수행으로 정상 산출물이 생성된 것은 확인했다. 동일 오류의 근본적인 재발 방지 또는 원본 관측 대비 예측 성능 향상은 입증되지 않았다. 코드의 실제 변경사항은 커밋 전 `git diff`를 기준으로 추가 기록해야 한다.

---

## 6. 개발사 복원의 한계

1. 2026-10-27 이전 개별 소스 수정은 Git 커밋이 없으므로 **날짜별 변경 diff를 전수 재현할 수 없다**.
2. 대화에서 어시스턴트가 제공한 패치와 서버에 실제 반영된 패치는 별개의 사실이다.
3. 초기의 `round*`, `components/` 경로 및 옛 스키마 버전은 현행 `func/stepNN`의 직접 실행 매뉴얼이 아니다.
4. 일부 9월 오류 ZIP 전문은 이번에 접근할 수 없어 코드 수정·해결 확정을 유보했다.
5. 운영 웹사이트는 과거 날짜 페이지에 최신 동일-init 재수행 결과가 덮어 게시될 수 있다. 사이트를 과거 분석의 불변 스냅샷으로 간주하지 않는다.
6. 새로운 날짜의 수정은 `CHANGELOG.md`와 해당 상세 이력을 동시에 갱신하되, 완료/제안 상태를 명시한다.

**근거 및 재현 대상:** [EVIDENCE_REGISTER.md](EVIDENCE_REGISTER.md), [VALIDATION_HISTORY.md](VALIDATION_HISTORY.md).
