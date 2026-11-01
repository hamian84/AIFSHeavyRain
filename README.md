# AIFSHeavyRain — AIFS 기반 위험강수 감시 우선순위 예보지원 시스템

> 문서 기준일: **2026-10-30(KST)**
> 코드 기준: [`main` / `82f42b1`](https://github.com/hamian84/AIFSHeavyRain/commit/82f42b193663bb65f8ccf4b9af276ca7524d05f0)
> 문서 성격: 2026년 6~10월 개발·운영 이력의 **사후 복원본**. 일부 초기 사실은 대화 기록에 근거하며 Git 커밋으로 개별 확인되지 않는다.

## 1. 프로젝트 목적

ECMWF AIFS 예측자료를 기반으로 한반도 육상·주변 해상에서 예보관이 우선 확인해야 할 강수 신호를 찾아, **분석계획 작성 → 결정론적 기상자료 검증 → 독립 판독 → 근거 기반 운영 판단 → 예보관용 웹페이지**로 연결한다.

- **AI는 예보·특보를 자동 확정하지 않는다.** 감시 후보와 확인 항목을 조직하고, 정량 계산 결과 및 기상장 판독을 근거로 해석을 지원한다.
- 직접 모델 강수의 존재와 수분 공급·하층 수렴·중층 상승·열역학·바람 구조 등 선행/동반 환경을 구분한다.
- 지지 근거뿐 아니라 상반·제한·불확실성 근거를 함께 보여준다.
- Step07의 3회 판독 다수결은 **LLM 판독의 재현성 보조수단**이며, 수치예보 앙상블 불확실성과 동일시하지 않는다.
- 최종 후보 등급은 `priority`(중점 감시), `elevated`(우선 확인), `general`(일반 감시), 비표출 등 운영 규칙과 검증 게이트를 거친다.

## 2. 전체 처리 구조

```text
scripts/run_pipeline.ksh
   │
   ├─ Step01 AIFS 수집                  scripts/01_collect_aifs.py
   ├─ Step02 1차 개요·특성 산출        scripts/02_build_pass1_overview.py
   ├─ Step03 AI 1차 요청 패키지        scripts/03_build_pass1_request.py
   ├─ Step04 후보·가설·분석계획        scripts/04_run_ai_pass1.py
   ├─ Step05 정량 진단/영상 생성        scripts/05_run_step05.py
   ├─ Step06 2차 판독 입력 패키지       scripts/06_build_pass2_package.py
   ├─ Step07 확인항목별 3회 AI 판독     scripts/07_run_pass2_checks.py
   ├─ Step08 후보별 종합 분석          scripts/08_run_pass2_synthesis.py
   ├─ Step09 운영 검증·표출 결정        scripts/09_finalize_pass2.py
   ├─ Step10 예보관용 제품 생성        scripts/10_run_forecaster_product.py
   ├─ WEB_PUBLISH 원자적 웹 릴리스
   └─ CLEANUP 보존기한 경과 임시자료 정리
```

자세한 실제 파일명·입출력·검증 경계는 [시스템 구조](docs/SYSTEM_ARCHITECTURE.md)를 참고한다.

## 3. 운영 원칙과 제약

- 강수 신호는 운영권역의 육상·해상을 포함하여 검증하며, **현재 적용되는 직접 강수 최소기준은 6시간 강수량 1.0 mm 이상**이다. 과거 단계의 실험 기준과 혼동하지 않는다.
- 제주 육상·해상을 고려하고, 현재 운영정책상 울릉·독도 및 서해5도는 제외한다. 권역 카탈로그·격자 CSV가 최종 권위 자료다.
- 부정확한 숫자, 확인되지 않은 기상현상, 입증되지 않은 강수계 이동·조직화를 단정하지 않는다.
- 모델·API 설정은 저장소에 포함하지 않는 `envi/api_keys.env`에서 관리하고, 런타임 경로는 `envi/pipeline.env`에 정의한다.
- 테스트용 `zrun_pipeline.ksh`, `zzrun_pipeline.ksh`, 과거 재수행용 `his_run_pipeline.ksh`는 정식 운영 엔트리포인트가 아니다.
- 기존 Step10 제품을 다시 만들 때에는 산출물 보존·이력 유지 및 `--overwrite` 조건을 확인한다.

## 4. 현재 확인된 기준 상태

2026-10-30 기준, 2026-10-27 12UTC~10-29 00UTC의 4개 초기시각에 대해 Step08~10 P1 재수행이 완료되었다. 합계 15개 후보 모두 Step09 유효·표출이며, 환경 지속성에 대한 제한 근거 누락 사례 3개가 개선되었다. 신규 초기시각 `2026102912`는 Step01~10 전체가 완료되었고 Step09 유효·표출 후보 3개가 확인되었다. 이는 **해당 입력에 대한 실행 검증**이지 모든 시각·모델·장애 조건에서 성공이 보장된다는 의미는 아니다.

- [운영 검증 이력](docs/VALIDATION_HISTORY.md)
- [알려진 문제·후속 과제](docs/KNOWN_ISSUES.md)

## 5. 문서 목차

| 문서 | 용도 |
|---|---|
| [CHANGELOG.md](CHANGELOG.md) | 공식 버전·코드 변경 요약 |
| [개발 이력](docs/DEVELOPMENT_HISTORY.md) | 6~10월 날짜별 설계·오류·대응·검증의 복원 |
| [운영 검증 이력](docs/VALIDATION_HISTORY.md) | 실행 결과, 초기시각, 재수행 전후 비교 |
| [시스템 구조](docs/SYSTEM_ARCHITECTURE.md) | Step01~10, 입력·출력, 설정·운영 흐름 |
| [알려진 문제](docs/KNOWN_ISSUES.md) | 미해결·추가 검증·계획을 우선순위별 관리 |
| [근거 등록부](docs/EVIDENCE_REGISTER.md) | Git 커밋, 업로드 자료, 대화 기록, 확실성 수준 |
| [문서 관리 지침](docs/MAINTENANCE_PLAYBOOK.md) | 신규 변경 기록·검증·Git 반영 방법 |

## 6. 저장소와 보안

- [GitHub 저장소](https://github.com/hamian84/AIFSHeavyRain)
- [운영 웹사이트](https://aifsheavyrain.duckdns.org/)
- `dain/`, `daio/`, `daou/`, `logs/`, `tmp/`, API 인증정보 및 원본 기상자료를 문서화 목적으로 커밋하지 않는다.
- 본 문서는 운영 구조·날짜·기술적 판단을 공개 저장소에 기록할 수 있도록 작성했다. 내부 시스템 식별정보, API 토큰, 전체 로그와 개인 식별정보는 제외했다.

> **증거 해석 원칙:** Git 기록에서 확인되는 내용은 `코드반영`, 실행 JSON에서 확인되는 내용은 `실행검증`, 과거 대화만 확인되는 사항은 `대화기록`, 권고·제안만 있는 사항은 `계획/미확정`으로 구분한다. 최초 Git 등록일 이전의 이력을 사후 생성한 커밋 날짜로 표현하지 않는다.
