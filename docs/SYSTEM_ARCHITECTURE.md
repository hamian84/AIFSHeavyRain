# 운영 시스템 구조 및 입출력 계약

**적용 기준:** `main` 기준 커밋 `d37500b`와 작업 브랜치 코드 커밋 [`945320f`](https://github.com/hamian84/AIFSHeavyRain/commit/945320fed4003102e072197734dc033936df6dd3). 2026-11-03 UTC(`2026110300`) 수행자료와 현재 Step04/07~09 구현을 대조했다. 일부 코드 내부 상세 구현은 대표 모듈·회귀 테스트·저장 산출물로 확인한 수준이며 모든 런타임 분기를 전수 실행한 것은 아니다.

## 1. 주요 경로와 실행 방식

- 운영 프로젝트: `/home/ubuntu/AIFSHeavyRain`
- 정식 실행셸: `scripts/run_pipeline.ksh`.
- 각 단계 Python 엔트리포인트: `scripts/01_collect_aifs.py`~`scripts/10_run_forecaster_product.py`.
- `envi/pipeline.env`: 프로젝트 루트, Conda Python, `dain/daio/daou`, `logs/tmp`, 웹 루트 등의 운영 경로.
- `envi/api_keys.env`: API 제공자·모델·인증정보. 공개 Git에 등록하지 않음.
- `scripts/zrun_pipeline.ksh`, `scripts/zzrun_pipeline.ksh`, `scripts/his_run_pipeline.ksh`: 테스트 또는 과거 재수행용. 정식 운영 구조에서 제외.
- `func/shared/runtime_paths.py`: `AIFS_*_DIR` 환경변수와 논리적인 `dain/<init>/...` 참조의 물리 경로를 연결.

## 2. Step별 계약

| Step | 정식 실행 스크립트 | 주요 구현 모듈 | 대표 입력 | 대표 산출물/검증 |
|---|---|---|---|---|
| 01 AIFS 수집 | `01_collect_aifs.py` | shared AIFS 데이터 모듈 | ECMWF AIFS 원격 자료/수집 목록 | `dain/<init>/raw/aifs/` 및 ingest/run 상태·manifest; 요청 수·누락·자료 유효성 검사 |
| 02 개요·특성 | `02_build_pass1_overview.py` | `func/step02/*`, shared overview/map | Step01 GRIB, 데이터 카탈로그·운영권역 CSV | `daio/<init>/step02/overview_data.json`, `overview/*.png` |
| 03 1차 AI 요청 | `03_build_pass1_request.py` | `func/step03/pass1_request.py` | Step02 개요·요약/영상, 요청 프롬프트 | `daio/<init>/step03/pass1_request.json` |
| 04 후보·분석계획 | `04_run_ai_pass1.py` | `func/step04/pass1_engine.py`, `analysis_provenance.py` | Step03 요청, Step04 한국어 프롬프트·스키마 | `daio/<init>/step04/<provider>/pass1_result.json`, `step04_analysis_provenance.json`, 체크포인트 |
| 05 진단 산출 | `05_run_step05.py` | `func/step05/diagnostics.py`, `renderer.py`, `candidate_overview.py` | Step04 진단 요청·기상장·정량 정책 | `daio/<init>/step05/diagnostics/REQ_*.json` 및 PNG, `candidate_overview_data.json` |
| 06 Pass2 입력 구성 | `06_build_pass2_package.py` | `func/step06/package.py` | Step04 후보·계획/Step05 진단 | `daio/<init>/step06/pass2_input_package.json` |
| 07 독립 판독 | `07_run_pass2_checks.py` | `func/step07/checks.py`, shared majority | Step06 Check별 증거 이미지/정량값, Step07 프롬프트 | `daio/<init>/step07/pass2_check_results.json`, `llm_wiki_minority_report.json`, 진단기록 |
| 08 후보별 종합 | `08_run_pass2_synthesis.py` | `func/step08/synthesis.py`, `func/shared/signal_validation.py` | Step04 후보/가설, Step06 패키지, Step07 판독, Step08 프롬프트 | `daio/<init>/step08/pass2_result.json` 및 후보별 체크포인트 |
| 09 운영 게시 판단 | `09_finalize_pass2.py` | `func/step09/operational.py`, shared tier validation | Step08 후보별 신호·근거 + Step04~07 검증 자료 | `daio/<init>/step09/operational_result.json`; valid/blocked, display tier, should_surface |
| 10 예보관용 최종물 | `10_run_forecaster_product.py` | `func/step10/forecaster_product.py`, `analysis_history.py`, `llm_wiki.py` | Step04~09 결과, 지도/영상 자산, 템플릿 | `daou/<init>/index.html`, `forecaster_product.json`, `assets/`, 분석이력 관련 파일 |

위 출력은 **대표 경로**이며 부가 JSON·이미지·캐시는 초기시각별로 달라진다. Step01 경로·구성은 운영 환경 설정과 요청에 따라 변할 수 있다.

## 3. 주요 설정 파일 및 프롬프트

### 기상자료·진단 설정

- `daba/ai_pass1/product_catalog.yaml`: 개요 자료 제품 목록.
- `daba/ai_pass1/feature_definitions.yaml`, `field_summary_policy.yaml`: 특성량·요약 통계 처리.
- `daba/ai_pass1/discovery_execution_policy.yaml`, `discovery_operational_regions.yaml`, `discovery_spatial_grid.yaml`: 후보 탐색 실행·권역·격자 관련 설정.
- `daba/ai_pass1/broad_map_products.yaml`, `broad_map_render_styles.yaml`: 개요 영상/지도 표출 설정.
- `daba/analysis/diagnostic_catalog.yaml`, `step05_render_styles.yaml`: Step05 정량·영상 진단 목록 및 스타일.
- `daba/analysis/aifs_operational_region_grid_map.csv`: 운영권역-격자 매핑에 대한 권위 자료.
- `daba/collection/aifs_download_namelist.yaml`: Step01 다운로드 항목 정책.

### 프롬프트·스키마

| 단계 | 주요 파일 |
|---|---|
| Step04 | `prompts/step04/batch_observation.ko.md`, `group_summary.ko.md`, `candidate_discovery.ko.md`, `candidate_plan.ko.md`, `candidate_response.ko.md`, 관련 `*.schema.json`, `openai/*.transport.schema.json` |
| Step05 | `prompts/step05/diagnostic_result.schema.json` |
| Step06 | `prompts/step06/input_package.schema.json` |
| Step07 | `prompts/step07/check_assessment.ko.md`, `check_assessment_response.schema.json`, `check_results.schema.json`, `minority_report.schema.json` |
| Step08 | `prompts/step08/candidate_synthesis.ko.md`, `synthesis_response.schema.json`, `pass2_result.schema.json` |
| Step09 | `prompts/step09/operational_result.schema.json` |
| Step10 | `prompts/step10/forecaster_product.schema.json`, `analysis_history_snapshot.schema.json`, `wiki_explanation.ko.md`, `wiki_explanation_response.schema.json`, `wiki_result.schema.json` |

프롬프트는 LLM 역할과 표현·근거 제약을 정의하고, Python·JSON schema가 최종 수용가능성을 판정한다. 프롬프트의 문장만으로 결정론 검증을 대체하지 않는다.

## 4. 중요한 계약 경계

### 4.1 Step04 정량 범위와 복구 단위

- `majority + not_strengthen` 정량 계약에 필요한 Step05 실행 가능 운영권역이 고정된 후보영역과 겹치지 않으면 `CANDIDATE_QUANTITATIVE_SCOPE_UNAVAILABLE`로 판정한다. 이는 해당 명제의 실행 범위가 없는 후보 단위 제약이다.
- 후보영역을 넓히거나 옮기고, 정량 명제를 정성 문장으로 바꾸거나 극성을 뒤집어 통과시키지 않는다. Check 하나를 재생성해도 후보 범위 제약은 바뀌지 않으므로 원래 후보·가설 컨텍스트에서 계획 chunk 전체를 bounded budget으로 재생성한다. 같은 상태가 반복되면 `STEP04_CANDIDATE_CHUNK_REPAIR_NONPROGRESS`로 중단한다.
- 독립적인 국소 Check 계약 오류는 기존처럼 해당 Check 전체를 재생성한다. 수치 명제를 지원하는 운영권역과 입력이 있을 때의 기존 정량 검증은 유지한다.
- Step04 단계 체크포인트는 입력·프롬프트·스키마·provider 구성을 포함한 지문으로 재사용 여부를 확인한다. 이 수정은 해당 매칭·재개 절차를 바꾸지 않았다.

### 4.2 직접 강수 신호

- 후보를 위해 **Step04 SYSTEM 직접강수 존재 확인 anchor**가 있어야 하며, 현재 운영 정책상 육상·해상 운영권역의 **6시간 강수량 1.0 mm 이상** 여부를 독립 확인한다.
- 직접 강수 `confirmed`와 수분·수렴·상승 등의 환경 `confirmed`를 동일시하지 않는다.
- 후보 원시 위치, 대표 강수 객체, 실제 환경 결합 위치를 분리하여 공간을 과장하지 않는다.
- Step07은 핵심시각별 운영권역 중첩, 유효 강수값, scope 메타자료를 구분한다. 평가한 어느 핵심시각에서든 기준을 만족하면 직접강수 존재 신호가 성립할 수 있다. 기준 미충족을 강수 미관측으로 해석하려면 전체 핵심시각 평가가 완결되어야 한다. 미완결이면 `unavailable` 또는 `incomplete`로 유지한다.
- 필수 Step05 scope 요약이 누락되면 평가 불가 상태를 생성한다. 불완전 평가에는 결정론적 validator가 `insufficient_evidence / indeterminate`만 허용하며, 위반 시 bounded grounding retry를 수행한다. Anchor 지문에는 `scope-availability-v1` 계약 버전이 들어가 기존 Anchor 체크포인트를 다시 평가한다.

### 4.3 Check와 3회 판독

- Step07은 확인항목에 대해 3회 독립 판독 후 구조화 합의·소수 이견을 보존한다.
- 반대·제한 근거를 표시하고 특정 판독의 예측 시각·물리량·권역·의미를 고정한다.
- 응답에 값이 존재해도 미사용 상태(`available_but_unusable`)이면 Step08이 결론에 임의 반영해서는 안 된다.
- 정량 selector는 성공한 Step05 근거의 허용 namespace에 있는 request·feature·region·forecast hour·statistic 조합을 사용해야 한다. selector가 중복되거나 근거 namespace를 벗어나면 검증·복구 대상으로 처리한다.
- Check 입력 지문에는 요청 및 Step05 결과·이미지, prompt/schema, provider·ensemble 계약과 직접강수 Anchor 문맥이 포함된다. 관련 입력·범위 평가가 달라져 지문이 바뀌면 해당 체크포인트를 재사용하지 않고, 영향을 받지 않은 일치 체크는 재사용할 수 있다.

### 4.4 환경 지속성과 P1 수정

- Step04 내부 local `phase_keys`는 `chunk01:P02` 등 비의미적 식별자일 수 있음.
- 공식 `analysis_phases[].phase_type=post_peak`를 Check의 `phase_ids` 및 `intent_contract`, `atomic_claim_contract`와 대조해 후속 환경 Check를 식별한다.
- 단일 운영권역 정량값만으로 후보 전체 환경 지속성을 확정하지 않는다.
- `limited`는 **검증 가능한 제한 근거**가 있을 때, `insufficient_evidence`는 적격한 후속 환경 증거가 부족할 때 구분한다. 직접 강수 신호는 유지할 수 있다.

### 4.5 Step08 종합과 Step09 최종 게시 게이트

- Step08은 직접강수 SYSTEM Anchor를 별도 계약으로 읽는다. 완전하고 사용할 수 있는 Anchor의 `observed`는 `confirmed`, `partially_observed`는 `partial`, `not_observed`/`opposite_observed`는 `not_confirmed`로 매핑한다. Anchor 평가불능·무효·미분류 상태는 `insufficient_evidence`로 둔다.
- Step08은 usable한 Step07 Check의 증거 참조를 사용하고 지지·반대 근거와 uncertainty를 별도 구성한다. 직접강수, 선행 환경 지지, 환경 지속성은 서로 다른 상태로 유지하므로 직접강수 상태가 불충분해도 선행환경 신호의 적격성을 별도로 검토할 수 있다.
- Step09는 Step08 출력만으로 게시하지 않고 Check 참조·근거 일치·상태·강수·후속 설명을 다시 검증한다.
- `display_tier`: `priority` / `elevated` / `general` / 비표출(결과 구조·규칙에 따름).
- `validation_status=valid`는 계약 검증 통과를 뜻하며 표출을 보장하지 않는다. `should_surface` 및 표출 등급은 검증 상태와 별도로 운영 게이트를 거친다. `validation_status=blocked`가 있으면 정상 Step09 게시 완료로 처리하지 않는다.
- 감시등급은 전문 예보관의 특보·예보 판단과 별개임.

## 5. 예보관용 웹 배포

```text
Step10 → daou/<init>/ (영구 보존)
           ├── index.html
           ├── forecaster_product.json
           └── assets/...

WEB_PUBLISH → /var/www/html/.releases/<init>_<build-id>/
           → validate_web_release.py 검사
           → /var/www/html/<init> 심볼릭 링크 전환
           → /var/www/html/latest 심볼릭 링크 전환
```

- 새 release를 먼저 별도 위치에 복사·검증하고 최종 링크를 교체해 중간에 이미지만 깨지는 구간을 줄인다.
- 동일 init 재게시 뒤에는 페이지의 내용이 바뀔 수 있으므로 과거 실행결과 비교는 `analysis_history`와 당시 스냅샷을 사용한다.
- `CLEANUP`은 런타임 보존 정책에 영향을 줄 수 있으므로 과거 디버깅자료가 필요한 경우 실행 전에 주의한다.
- 페이지가 텍스트로 조회되는 것만으로 이미지를 실제로 불러오고 JS 버튼/그래프가 정상 동작한다는 증거가 되지 않는다.

## 6. 현재 코드에 대해 부정확하게 기술하면 안 되는 내용

- 범용 Agent 프레임워크(Hermes 등)를 반드시 사용했다는 주장 — **근거 없음**. 시스템의 Agentic 특성은 단계별 계획·증거 생성·검증·후속 행동의 분리된 workflow에 있음.
- Step07 다수결을 확률예측 또는 수치모델 ensemble이라고 표현하는 주장 — **부정확**.
- 후보별 6시간 최대강수량을 해당 권역 전역 또는 도시별 대표값으로 표현하는 주장 — **부정확**.
- 지속성 판단 제한을 기상현상 부재·강수 자체 부정으로 해석하는 주장 — **부정확**.
- 모든 프롬프트·스키마와 소스가 10월 30일에 수정됐다는 주장 — **부정확**. P1 실코드 변경은 4개 파일뿐.
