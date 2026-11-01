# 변경 이력 (CHANGELOG)

문서 기준일: **2026-10-30(KST)**. 이 문서는 실제 Git 변경을 우선 기록한다. 최초 Git 등록 이전 개발사는 [날짜별 개발 이력](docs/DEVELOPMENT_HISTORY.md)에 복원 기록으로 분리한다.

## [Unreleased] — 후속 개선 제안, **적용 완료 아님**

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
