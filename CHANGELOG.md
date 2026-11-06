# 변경 이력 (CHANGELOG)

문서 기준일: **2026-11-06(KST)**. 2026-11-04 작성 당시 기준 `main`: [`d37500b`](https://github.com/hamian84/AIFSHeavyRain/commit/d37500b6ef7cae11028545b52acbb4b1e69a1c6f). 2026-11-06 운영 검토의 공개 기준 커밋은 `ffa5f78`이며 실제 로컬 코드 Diff 확인 전이다. 이 문서는 실제 Git 변경을 우선 기록한다. 최초 Git 등록 이전 개발사는 [날짜별 개발 이력](docs/DEVELOPMENT_HISTORY.md)에 복원 기록으로 분리한다.

## [Unreleased] — Step04 정량 범위·Step07 직접강수 평가 상태

### Fixed

- `func/step04/pass1_engine.py`: 후보영역과 겹치는 Step05 실행 가능 운영권역이 없을 때 `majority + not_strengthen` 정량 명제를 명시적인 후보 단위 범위 오류로 판정한다. Check 하나를 반복 수정해도 해결되지 않는 오류는 기존 후보·가설 컨텍스트에서 현재 계획 chunk 전체를 제한된 횟수로 재생성한다. 후보영역·가설 의미·정량 조건을 바꾸어 오류를 우회하지 않는다.
- [`945320f`](https://github.com/hamian84/AIFSHeavyRain/commit/945320fed4003102e072197734dc033936df6dd3) — 위 Step04 수정, Step07 상태 구분 및 회귀 테스트.
- [`4d797cc`](https://github.com/hamian84/AIFSHeavyRain/commit/4d797cc4eefc3985a11b5447026575c4052c6e81) — Step07 평가 불가 상태의 오판 방지, 누락된 운영권역 메타자료 처리 및 관련 회귀 테스트.

### Changed

- `func/step07/checks.py`: 직접강수 1.0 mm/6h 임계값은 유지하면서 운영권역 중첩·유효 강수값·범위 메타자료 상태를 구분한다. 핵심시각 평가가 불완전하면 Step07 검증기가 `insufficient_evidence / indeterminate`를 강제해 결측을 강수 부재로 판정하지 않는다. 누락된 Step05 scope 메타자료는 중단 오류 대신 평가 불가 상태로 전달한다. 이 계약 버전은 Anchor 체크 지문에 포함된다.
- Step08은 직접강수 Anchor 평가가 불충분하면 `insufficient_evidence`로 유지하고, Step09는 이를 강수 미확인으로 바꾸지 않는다. 환경 선행신호 후보는 별도 환경 근거와 표출 게이트에 따라 평가한다.

### Tests

- `/home/ubuntu/miniconda3/envs/aifsheavyrain/bin/python -B -m unittest discover -s tests -v`: 23/23 회귀 테스트 통과(프로젝트 Conda Python 3.13.13).
- `/home/ubuntu/miniconda3/envs/aifsheavyrain/bin/python -B -m compileall -q func/step04 func/step07 tests` 및 `git diff --check` 통과.

### Notes

- `2026110300`: Step01~10 완료, Step07 17개 Check(11 unanimous, 5 majority, 1 unresolved; 각 판독 3회), Step08 후보 3개, Step09 valid 3·blocked 0·surfaced 1, Step10 제품 생성. 이는 해당 실행 산출물의 확인이지 기상학적 예측성능 검증이 아니다.
- 같은 실행의 Step04 직접신호 감사는 11개 중 5개만 후보 근거와 연결되어 6개가 미연결이다. CAND_003의 직접강수 상태·문장 범위는 검토 대상으로 남기며, 현재 증거만으로 결함이라고 확정하지 않는다.
- 저장 산출물은 현재 수정 코드로 재생성하지 않았다. 특히 위의 결정론적 Step07 assessment guard는 운영 재실행으로 확인하지 않았으며, 회귀 테스트에서만 확인했다.
- 운영 웹페이지는 이 작업에서 브라우저 렌더링까지 독립 확인하지 않았다. 상세 운영 수치와 제한은 [VALIDATION_HISTORY.md](docs/VALIDATION_HISTORY.md)를 참조한다.

### 2026-11-06 — Step08 환경 지속성 검증 오류 재수행 [실행검증 / 로컬 변경 미확정]

- `init=2026110600`: 최초 Step08 `CAND_002`에서 `ENVIRONMENT_PERSISTENCE_INVALID` 발생으로 중단.
- 같은 초기시각 후속 Step08~10 완료. Step09 후보 3/3 유효·차단 0, 표출 2, 비표출 1.
- 추가 Step08·09 재생성(`daio(3).zip`) 후에도 후보 상태와 표출 결정 동일. Step04~07 입력/근거 파일은 이전 ZIP과 같음.
- 관련 점검 파일: `func/step08/synthesis.py`, `prompts/step08/candidate_synthesis.ko.md`, `func/shared/signal_validation.py`. **실제 서버 로컬 Diff는 미확인**이므로 특정 함수/프롬프트 수정이 완료됐다고 기록하지 않음.
- 검증: DAIO 후보별 계약·표출 상태 비교 및 제공 로그 확인. 코드 단위 회귀 테스트, 22:12 KST 최신 재생성 로그, 최신 Step10 웹 게시 및 관측 대비 성능 검증은 미수행.

### 후속 개선 제안 — **적용 완료 아님**

- Step04 후보별 후속 환경의 공간적 지속성을 확인할 분석계획·Check 보강.
- Step08 동일 근거의 판단 재현성 검증과 내부 표출 검증 용어의 예보관용 문장 노출 억제.
- Step10 육상·해상 영향을 첫 화면에 분리 제시하는 개선.
- Step04 포괄성 감사의 그룹 개요 강수 신호와 운영 임계치 충족 신호를 분리 표시.
- 부분 재수행 `run_status.json`에 이전 완료 단계와 미수행 단계를 명확히 구분.
- 배포 이미지·JS 동작에 대한 브라우저 자동 테스트 추가.

## [2026-10-30] — P1 환경 지속성 근거 및 설명 일치 수정

### Fixed

- `func/shared/signal_validation.py`: `POST` 문자열만으로 후속 시각을 인식하던 로직을 개선하여 Step04의 정식 `analysis_phases[].phase_type=post_peak`와 Check 단계 매핑을 사용하도록 함. 근거 적용 범위가 모호하거나 단일권역 정량 주장인 경우 기존 제한 유지.
- `func/step08/synthesis.py`: Step08 환경 지속성 검증 호출에 분석단계 정보 전달.
- `func/step09/operational.py`: Step09 검증에도 정식 분석단계 정보 전달.
- `func/shared/tier_narrative.py`: 감시등급과 충돌하는 ‘일반 감시후보로 제시할 가치가 있다’ 계열 서술을 검출하도록 개선.

### Verified

- `2026102712`, `2026102800`, `2026102812`, `2026102900`: Step08~10 재수행. Step09 유효·표출 15/15, 차단 0.
- 지속성 `limited` 후보 2→5, `insufficient_evidence` 13→10; 기존 15개 최종 등급 유지.
- `2026102912`: Step01~10 전체 완료, Step07 25개 확인항목, Step09 유효/표출 3/3.
- 신규 소스에 대한 Python 구문·공백 검사 및 제한된 회귀 테스트 진행. **회귀 테스트 파일은 해당 PR의 Git 커밋에 포함되지 않았다.**

### Git

- [`2f5a1a1`](https://github.com/hamian84/AIFSHeavyRain/commit/2f5a1a166f4d46ab37c0db60e99628f6cd99eae5) — 실제 수정 파일 4개 반영.
- [`4b66fc3`](https://github.com/hamian84/AIFSHeavyRain/commit/4b66fc32dd012ec95e0ee73ff7136973f07c781d) — Pull Request #1을 `main`에 병합.
- [`82f42b1`](https://github.com/hamian84/AIFSHeavyRain/commit/82f42b193663bb65f8ccf4b9af276ca7524d05f0) — 로컬 과거 재수행 스크립트 `/scripts/his_run_pipeline.ksh` 추적 제외.

## [2026-10-27] — 최초 Git 운영 기준선

- [`24ff2e2`](https://github.com/hamian84/AIFSHeavyRain/commit/24ff2e291468384fd790303bd451e2e6c59e2434) — `AIFSHeavyRain operational baseline` 등록.
- `func/shared`, `func/step02`~`func/step10`, `scripts/`, `prompts/`, `daba/` 등 당시 운용 코드와 설정 데이터의 스냅샷을 등록.
- 이는 해당 날짜에 모든 기능을 처음 개발했다는 의미가 아니라 **그 시점의 코드 전체를 최초로 형상관리한 것**이다.
- 운영 원시자료, 로그, 출력물, 개인·인증 설정 등은 커밋 제외 대상으로 관리.

## 2026-06~10-26 — Git 이전 개발 기록 (사후 복원)

이 기간의 세부 작업은 Git에 개별 커밋으로 남아 있지 않으므로, 날짜별 정리와 근거 출처를 [DEVELOPMENT_HISTORY.md](docs/DEVELOPMENT_HISTORY.md)와 [EVIDENCE_REGISTER.md](docs/EVIDENCE_REGISTER.md)에 기록했다. **대화에서 코드 수정안을 제공한 사실과 AWS 현업 코드에 실제로 적용된 사실을 동일하게 취급하지 않는다.**

### 주요 진행 단계

- **6월:** 초기 Phase 1A/1B, 수동 AI 응답, 기상자료 진단 및 2-pass 구조의 단계적 개발.
- **7월:** AIFS 수집 항목 최적화 및 Step01의 337개 파일 수집 검증; 개요자료·Step05 진단 패키지 검증.
- **8월:** AWS Ubuntu/Lightsail/Nginx 운영 배포, Step01~10 실행·웹 게시, API timeout/경로·화면 오류 분석.
- **9월:** SYSTEM 직접강수 anchor, Step07~09 근거 계약, 3회 판독, 후보 위치·시간축·웹 표현 개선.
- **10월 초~중:** 후보가 없는 시각의 파이프라인 실패, Step04·08 검증 오류, 정량 주장·언어 계약·시공간 정합성 문제 대응.

위 요약은 확인된 대화/기록 중심의 개발사이며, **현재 코드의 반영 시점을 입증하는 릴리스 이력이 아니다.**
