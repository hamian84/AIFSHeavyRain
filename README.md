## 2026-11-26 Step04 검증 계약 개선 [운영 재수행 확인 / Git 반영 작업 중]

- 기준 GitHub `main`: [`c619c10`](https://github.com/hamian84/AIFSHeavyRain/commit/c619c10cf9c96a048f1abbe6cfdedf60f7cdcaa6). 작업 브랜치: `fix/step04-verification-contract-20261126`.
- `2026112512`의 Step04 예측시간 및 정량 범위 계약 불일치를 개선했다. 후보별 제품 실행 가능 시각과 정량 검증 옵션을 계획 입력에 제공하고, 정량 계약 오류에 대한 기존 계획 복구 경로를 보완했다. 기존 Step07 검증, 감시 기준 및 종관분석 정책은 유지한다.
- 사용자 재수행 완료 보고와 2026-11-26 운영 웹페이지(생성 11:42 KST)에서 동해중부 후보 1건 및 상세 검증 6건 표출 확인. 동일 실패 후보의 재현 검증, 전체 내부 로그 확인 및 관측 기반 예측성능 검증은 미수행이다.
- 세부 변경·검증은 [변경 이력](CHANGELOG.md), [운영 검증 이력](docs/VALIDATION_HISTORY.md) 참고. 아래의 이전 날짜와 커밋 상태는 당시 기록이며, 현재 Git 상태는 이 항목과 실제 Git 이력을 기준으로 확인한다.

## 2026-11-19 종관 심층분석 시험서버 검증

시험 코드는 GitHub `main`의 `9c93f0bf525878fe35509d149e5383cf3174b195`를 기반으로 합니다.
사용자 승인 전 GitHub main 또는 AWS 운영 서버에 반영하지 마십시오.
기존 10단계는 유지하며, SYNOPTIC_CONTEXT_CORE 설명 전용 종관진단은 AIFSHR_ENABLE_SYNOPTIC_CONTEXT=0을 기본값으로 사용합니다.
독립 시험공간에서 1로 활성화하면 Step03 실행가능 카탈로그 → Step04 설명용 Check → Step05 지상 해면기압·10m 바람, 500hPa 기온·지위고도, 300hPa 제트·지위고도 PNG/정량요약 → Step06 SHA 증거 → Step07 판독 → Step08 종합판단 → Step09 운영 판단 보호 → Step10 원래 종합판단 내부 일기도·통계 표가 연결됩니다.
Step04 최대 가설 6, chunk당 검증 요청 12, 질문 8, phase 10. 단순 후보에는 필요한 분석만 선택합니다.
기존 직접강수 확인 앵커, 선행환경만으로 후보가 생성될 수 있는 정책, 3회 판독, 감시등급 기준은 변경하지 않습니다.
`2026111812` 시험에서 기존 AIFS GRIB와 Step02 개요를 사용해 Step10까지 연결을 확인했습니다. 서해중부 후보의 종관 Check는 독립 판독 3회가 일치했고, 로컬 종합판단에 일기도 6개와 두 예측시각의 정량표가 생성됐습니다. 단위시험 7건이 통과했습니다.
실행 방법과 시험 근거는 [종관분석 통합 가이드](docs/SYNOPTIC_INTEGRATION.md)와 [검증 이력](docs/VALIDATION_HISTORY.md)에 있습니다.
이하의 2026-11-15 등 과거 문구는 작성 당시 기준의 이력이며 현행 main SHA를 의미하지 않습니다.

# AIFSHeavyRain — AIFS 기반 위험강수 감시 우선순위 예보지원 시스템

> 문서 갱신일: **2026-11-15(KST)**. 현재 확인된 공개 GitHub `main`: [`8afd3c3`](https://github.com/hamian84/AIFSHeavyRain/commit/8afd3c329d4fa1358adeb9e6d2035bb371e8732e). Step04·Step08 수정 코드는 **사용자 시험 완료 보고/공개 Git 미반영** 상태이며, 변경사항과 검증 수준은 기존 [CHANGELOG.md](CHANGELOG.md)의 2026-11-14~15 항목에서 관리한다.
> 아래 초기 기준일·커밋 및 10~11월 초 실행 결과는 **과거 문서화 당시 기준의 이력**으로 보존한다.
> 기존 문서 기준일: **2026-11-04(KST; 2026-11-03 UTC)**
> 기준 브랜치·커밋: [`main` / `d37500b`](https://github.com/hamian84/AIFSHeavyRain/commit/d37500b6ef7cae11028545b52acbb4b1e69a1c6f)
> 이번 변경 코드: [`945320f`](https://github.com/hamian84/AIFSHeavyRain/commit/945320fed4003102e072197734dc033936df6dd3), [`4d797cc`](https://github.com/hamian84/AIFSHeavyRain/commit/4d797cc4eefc3985a11b5447026575c4052c6e81) (검토 중인 작업 브랜치)
> 문서 성격: 2026년 6~11월 개발·운영 이력의 **사후 복원본**. 일부 초기 사실은 대화 기록에 근거하며 Git 커밋으로 개별 확인되지 않는다.

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

정식 실행은 `ksh scripts/run_pipeline.ksh YYYYMMDDHH`이며 `HH`는 `00` 또는 `12`여야 한다. 단계별 재개는 셸 스크립트의 `STEP01`~`STEP10` 스위치와 기존 체크포인트를 사용한다. `--start-step` 인자는 지원하지 않는다. 자세한 재개·게시·정리 주의사항은 [문서 관리 지침](docs/MAINTENANCE_PLAYBOOK.md)을 참고한다.

## 3. 운영 원칙과 제약

- 강수 신호는 운영권역의 육상·해상을 포함하여 검증하며, **현재 적용되는 직접 강수 최소기준은 6시간 강수량 1.0 mm 이상**이다. 과거 단계의 실험 기준과 혼동하지 않는다.
- 제주 육상·해상을 고려하고, 현재 운영정책상 울릉·독도 및 서해5도는 제외한다. 권역 카탈로그·격자 CSV가 최종 권위 자료다.
- 부정확한 숫자, 확인되지 않은 기상현상, 입증되지 않은 강수계 이동·조직화를 단정하지 않는다.
- 모델·API 설정은 저장소에 포함하지 않는 `envi/api_keys.env`에서 관리하고, 런타임 경로는 `envi/pipeline.env`에 정의한다.
- 테스트용 `zrun_pipeline.ksh`, `zzrun_pipeline.ksh`, 과거 재수행용 `his_run_pipeline.ksh`는 정식 운영 엔트리포인트가 아니다.
- 기존 Step10 제품을 다시 만들 때에는 산출물 보존·이력 유지 및 `--overwrite` 조건을 확인한다.
- 부분 재실행 시에는 필요한 단계만 `True`로 두고 입력 산출물이 있는지 확인한다. `WEB_PUBLISH`와 `CLEANUP`도 의도적으로 설정해 불필요한 게시나 정리를 피한다.

## 4. 현재 확인된 기준 상태

2026-10-30 기준, 2026-10-27 12UTC~10-29 00UTC의 4개 초기시각에 대해 Step08~10 P1 재수행이 완료되었다. 합계 15개 후보 모두 Step09 유효·표출이며, 환경 지속성에 대한 제한 근거 누락 사례 3개가 개선되었다. 신규 초기시각 `2026102912`는 Step01~10 전체가 완료되었고 Step09 유효·표출 후보 3개가 확인되었다. 이는 **해당 입력에 대한 실행 검증**이지 모든 시각·모델·장애 조건에서 성공이 보장된다는 의미는 아니다.

2026-11-03 UTC(`2026110300`) 저장 산출물은 Step01~10 완료, 후보 3개 중 Step09 유효 3개·차단 0개, Step10 표출 1개로 기록되었다. Step04 직접신호 감사는 11개 중 5개만 후보 근거와 연결되어 6개가 미연결 상태다. 이 감사 공백은 운영 임계치 충족 또는 기상학적 오류를 단독으로 판정하지 않으며, 실행 완료 역시 예측 정확도 검증을 의미하지 않는다. 본 PR의 마지막 Step07 assessment guard는 저장 산출물 생성 이후 추가했으며 운영 파이프라인은 재실행하지 않았다. 세부 결과와 남은 검토사항은 [운영 검증 이력](docs/VALIDATION_HISTORY.md)을 참고한다.

- [운영 검증 이력](docs/VALIDATION_HISTORY.md)
- [알려진 문제·후속 과제](docs/KNOWN_ISSUES.md)

## 5. 문서 목차

| 문서 | 용도 |
|---|---|
| [CHANGELOG.md](CHANGELOG.md) | 공식 버전·코드 변경 요약 |
| [개발 이력](docs/DEVELOPMENT_HISTORY.md) | 6~11월 날짜별 설계·오류·대응·검증의 복원 |
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
