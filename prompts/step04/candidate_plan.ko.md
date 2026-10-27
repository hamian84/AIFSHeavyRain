# AIFS Pass1 후보별 기상학적 검증계획

주어진 후보와 관련 group summary, 자산 색인만 사용하여 Step05 상세자료 생성과 Pass2 검증에 필요한 계획을 작성하십시오. 이 단계의 AI 역할은 **언제, 어디에서, 어떤 기상요소를, 왜 추가로 확인해야 하는지** 결정하는 것입니다. 내부 실행방법은 Python이 결정합니다.

후보 탐색 결과의 `candidate_area`, 시간창, 최대 강수 예상 시각 또는 선행환경 대표시각, 우선순위, 후보 분리·병합 판단은 변경하지 마십시오. `related_precursor_opportunities`는 기존 후보의 가설과 검증계획을 보완하는 참고자료이며 새로운 후보를 자동 생성하는 근거가 아닙니다.

## 1. 영역과 시간

- `candidate_area`는 위험 핵심영역이며 변경하지 마십시오.
- 후보환경을 보기 위해 확장영역이 실제로 필요할 때만 `context_domain`을 하나 지정하십시오. 후보영역 전체를 포함해야 하며 가용영역을 벗어나면 안 됩니다.
- 확장영역이 필요하지 않으면 `context_domain=null`, `context_domain_reason=null`로 반환하십시오.
- 각 `analysis_phases`에는 실제 `forecast_hours`를 입력의 `available_forecast_hours`에서 선택하십시오.
- 모든 `candidate_peak_forecast_hours`는 하나 이상의 `peak` 단계에 포함해야 합니다.
- `direct_precipitation` 또는 `mixed` 후보의 핵심 시각은 강수 최대 예상 시각입니다. `precursor_environment` 후보의 핵심 시각은 강수 최대가 아니라 선행환경의 시공간 중첩이 가장 뚜렷한 대표시각입니다.
- `context`를 제외한 분석단계는 시간축에서 중복·교차하지 않도록 구성하십시오. 같은 시각을 여러 단계에 반복 배치하지 마십시오.
- 설명에 특정 예측시간을 언급하면 그 시간이 해당 `phase_keys`가 가리키는 단계에 실제로 포함되어야 합니다.
- 특히 정량 비교의 `quantitative_scope.forecast_hours`는 설명 문자열이 아니라 **실행 계약**이므로, request와 atomic claim의 `phase_keys`가 그 모든 시각을 소유하도록 계획하십시오.
- 자연어에서 예측시간을 표기할 때는 반드시 `F000`, `F006`, `F024`, `F096`, `F120`처럼 `F` 뒤에 3자리 숫자를 사용하십시오. `F6`, `F24`, `F96` 같은 축약형은 사용하지 마십시오.

## 2. verification_requests: 무엇을 추가 확인할 것인가

각 요청에는 다음만 판단하십시오.

- `signal_components`: 확인할 기상요소
- `verification_mode`: 무엇을 확인할지에 대한 기상학적 목적
- `domain_scope`: 후보영역 또는 확장영역 중 어디를 볼지
- `spatial_relation_scope`: 이 요청이 후보영역 내부만으로 판정 가능한지, 후보 경계를 넘는 관계나 외부 주변환경이 필요한지를 구조적으로 표시
  - `candidate_interior`: 후보영역 내부 자료만으로 판정 가능
  - `candidate_boundary_crossing`: 후보영역 안↔밖 접근·이탈·경계 통과·후보 밖 위치를 판정해야 함
  - `candidate_external_context`: 후보영역 외부의 공급원·상류/인접 환경과의 연결을 판정해야 함
  - `not_applicable`: 수평 공간 관계가 핵심이 아닌 정량 통계·연직구조 등
- `phase_keys`: 언제 확인할지
- `representative_spatial_hours`: `temporal_change`의 정량 시계열을 공간장으로 함께 확인할 대표 비교시각. 다른 모드에서는 빈 배열
- `expected_signal`: 확인할 기상학적 특징
- `decision_relevance`: 이 결과가 후보 판단에 왜 필요한지
- `purpose`: 자료를 추가 확인하는 목적
- `atomic_claims`: `expected_signal`을 1~4개의 **원자적 검증명제**로 구조화한 계약. 일반 명제는 의미 일관성 감사에 사용하고, `specific` 정량 명제의 `quantitative_scope`는 Step07의 정량 범위·방향 검증에 사용

`spatial_relation_scope`와 `domain_scope`는 반드시 일치해야 합니다.

- `candidate_boundary_crossing` 또는 `candidate_external_context`이면 반드시 `domain_scope=context`를 사용하고, `context_domain`을 후보영역 전체와 필요한 외부영역을 포함하도록 지정하십시오.
- **`spatial_relation_scope`는 자료를 어디까지 그리는지가 아니라 `expected_signal`이 실제로 판정하려는 수평 공간관계를 뜻합니다.** `domain_scope=context`를 사용하거나 연직단면의 끝점이 후보영역 밖까지 확장된다는 이유만으로 `candidate_external_context` 또는 `candidate_boundary_crossing`을 선택하지 마십시오.
- `verification_mode=vertical_structure` 또는 `vertical_time_evolution`은 연직구조 판정이므로 **항상 `spatial_relation_scope=not_applicable`**로 작성하십시오. `vertical_structure`는 필요한 경우 `domain_scope=context`를 사용할 수 있지만, 이 경우에도 `spatial_relation_scope`는 `not_applicable`입니다.
- 연직구조와 후보 밖 공급원·상류환경의 위치/연결 같은 **수평 관계를 모두 확인해야 하면 하나의 요청에 섞지 말고**, 연직구조 요청(`vertical_structure`, `spatial_relation_scope=not_applicable`)과 수평 공간관계 요청(`spatial_distribution`/`spatial_evolution`/`multivariable_alignment`)으로 분리하십시오.
- 후보영역 **밖에 위치하는지**, 후보영역으로 **접근하는지**, 후보영역에서 **이탈하는지**, **경계를 통과하는지**를 묻는 요청을 `domain_scope=candidate`로 만들지 마십시오. 후보영역과 영상범위가 같으면 영상 밖 상태를 판독할 수 없습니다.
- 단순히 후보영역 내부에서 중심이 이동·분리·확대·축소되는지는 `candidate_interior`와 `domain_scope=candidate`로 충분할 수 있습니다.
- 남쪽 해상 수증기 공급원, 상류 유입경로 등 후보 밖 환경 자체를 봐야 하면 `candidate_external_context`를 사용하십시오.
- Python은 이 구조화된 공간관계를 검증하며, 자연어의 `밖`, `접근`, `이탈` 같은 단어를 임의 해석하여 영역을 바꾸지 않습니다.

### atomic_claims (구조화 검증 계약)

각 `verification_request`의 `expected_signal`을 가능한 한 1~4개의 독립적인 기상학적 명제로 분해하십시오. 한 명제는 Step07에서 참·부분참·미확인·반대 여부를 **하나의 기준으로 판정할 수 있어야** 합니다.

각 claim에는 다음을 작성하십시오.

- `claim_key`: 해당 요청 안에서 고유한 식별자. 예: `CLM01`
- `claim_statement`: 하나의 검증 가능한 기상학적 상태 또는 변화
- `claim_components`: 이 명제를 직접 구성하는 `signal_components`의 부분집합
- `phase_keys`: 이 명제가 적용되는 분석단계. 반드시 요청의 `phase_keys` 안에서 선택
- `expected_state`: `present`, `absent`, `increase`, `decrease`, `strengthen`, `not_strengthen`, `weaken`, `greater_than`, `less_than`, `overlap`, `not_overlap`, `move`, `persist`, `split`, `merge`, `expand`, `contract`, `reorganize`, `located_at`, `peak_at` 중 하나. `not_strengthen`은 현재 `convergence925_1e5_s`의 `scope_quantifier=majority`에서만 사용하십시오.
- `scope_quantifier`: 명제가 적용되는 범위의 양화. `all`, `majority`, `at_least_one`, `specific` 중 하나
- `scope_description`: `후보영역 전체`, `후보영역과 겹치는 운영권역 대부분`, `제주 주변`처럼 실제 범위를 짧고 구체적으로 작성
- `quantitative_scope`: 정량 명제의 **자료 범위와 비교 의미를 분리해 선언하는 계약**입니다. 지원 대상이 아니면 `null`입니다.
  - `quantitative_scope.forecast_hours`에 넣는 **모든 F시각은 해당 verification_request의 `phase_keys`와 그 atomic claim의 `phase_keys`가 가리키는 `analysis_phases[].forecast_hours`에 실제로 포함**되어야 합니다. 예를 들어 F072와 F078을 비교한다면 F072 phase와 F078 phase를 모두 선언하고 request와 claim이 두 phase를 모두 참조해야 합니다.
  - 정량 명제에 필요한 시각을 phase 계약에 맞추기 위해 임의로 삭제하지 마십시오. 해당 비교가 필요하면 phase 계획까지 일관되게 구성하고, 비교 자체가 불필요하면 원래 기상학적 목적에 맞게 정량 명제 전체를 다시 선택하십시오.
  - `feature_id`: Step05에서 실제 생산되는 정량 feature 식별자를 정확히 사용하십시오. 예: `tp_6h_increment_mm`, `convergence925_1e5_s`, `omega700_upward`, `moisture_flux850`. 다른 namespace의 product/diagnostic 이름을 넣지 마십시오.
  - `region_scope`: 단일 지정권역이면 `explicit_regions`, 후보영역과 겹치는 전체 운영권역의 다수결이면 `candidate_overlap`입니다.
  - `region_ids`: `explicit_regions`일 때 후보영역과 실제로 겹치는 `region_role=operational_land` 또는 `marine_macro` 중 정확히 1개의 실제 `region_id`만 넣으십시오. `source_corridor`나 `marine_detail`을 정량 판정권역으로 사용하지 마십시오. `candidate_overlap`일 때는 빈 배열 `[]`로 반환하십시오.
  - `forecast_hours`: claim 검증에 사용하는 모든 F시각을 **중복 없이, 시간 오름차순**으로 넣으십시오. 동일한 F시각을 두 번 이상 넣지 마십시오. 이는 자료 시간범위이며 좌항·우항 순서를 뜻하지 않습니다. 예: `[18,24]`는 허용되지만 `[24,18]`, `[18,24,18]`, `[18,18]`은 허용되지 않습니다.
  - `comparison_forecast_hours`: `greater_than`/`less_than`처럼 좌항·우항이 있는 비교에서는 `[좌항 F시각, 우항 F시각]` 순서로 정확히 2개를 넣으십시오. 시간변화 자체(`increase`, `decrease`, `strengthen`, `weaken`, `not_strengthen`) 또는 `peak_at`이면 `null`로 반환하십시오.
    - 특히 두 시각만 비교하더라도 `F018에서 F024로 감소한다`처럼 **시간 경과에 따른 변화**를 뜻하면 `expected_state=decrease`, `forecast_hours=[18,24]`, `comparison_forecast_hours=null`입니다. `F018 값이 F024 값보다 크다`처럼 **좌항·우항의 대소관계 자체**가 명제일 때만 `expected_state=greater_than`, `comparison_forecast_hours=[18,24]`를 사용하십시오.
    - **고정 수치 임계값과 시간 비교는 서로 다른 명제입니다.** `F096 수도권 6시간 강수량이 1 mm를 넘는다`는 `F096 값 > 1 mm`이지 `F096 값 > F090 값`이 아닙니다. 현재 `specific-v2` 정량 계약의 `greater_than`/`less_than`은 **두 F시각의 같은 지역·물리량·통계량 간 비교만** 실행합니다. 이 계약으로 mm·hPa·% 등의 고정 임계값을 다른 F시각으로 대체하지 마십시오. 1.0 mm **이상** 직접강수 존재 여부는 별도의 SYSTEM 강수 앵커 검증과 구분하고, `1 mm 초과`를 `1 mm 이상`으로 조용히 바꾸거나 확인되지 않은 임계값을 통과 처리하지 마십시오. 지원되지 않는 고정 임계값 판정이 반드시 필요하다면 원래 명제를 보존하고 현재 계획의 검증 실패를 수용하십시오.
  - `statistic`: `min`, `max`, `mean`, `p95` 중 실제 명제가 요구하는 통계 하나를 사용하십시오.

표현 계약:

- `analysis_phases[].forecast_hours`, request/claim의 `phase_keys`, `signal_components`, `claim_components`, `region_ids`처럼 집합 의미를 갖는 배열에는 같은 값을 중복 작성하지 마십시오.
- 같은 request 안에서 기상학적 의미가 완전히 동일한 `atomic_claim`을 `claim_key`만 바꾸어 반복하지 마십시오.
- SYSTEM은 중복 제거·시간순 정렬·이미 선언된 phase와의 명백한 참조 연결처럼 **의미를 바꾸지 않는 표현 오류만 결정론적으로 정규화**합니다. 없는 시각·phase를 만들거나 권역을 바꾸거나 비교 방향을 뒤집지는 않습니다. 그런 의미 오류는 strict validation 후 verification request 전체 재생성 대상입니다.

중요한 규칙:

- `A 또는 B`, `A 혹은 B`처럼 서로 다른 조건을 하나의 claim에 넣지 마십시오. 필요하면 `CLM01`, `CLM02`로 분리하십시오.
- `강수 중심이 북동쪽으로 이동하고 띠의 연속성이 약화된다`처럼 **서로 다른 판정 기준**이 함께 있으면 이동과 연속성 약화를 별도 claim으로 분리하십시오.
- 모델 강수 예측 관련 요청에서도 서로 다른 판정 기준을 한 claim에 결합하지 마십시오. 예를 들어 `강수 영역이 이어지고 F012가 더 강하며 동쪽으로 이동한다`는 연속성, 강도, 이동을 각각 별도 claim으로 분리하십시오.
- `multivariable_alignment`에서 `overlap` 또는 `not_overlap` claim은 실제 비교하는 요소를 모두 `claim_components`에 포함하십시오.
- **핵심시각의 긍정적 강수-환경 결합과 후속 약화·분리·지속성 제한을 같은 verification_request에 섞지 마십시오.** `direct_precipitation`과 하나 이상의 환경요소가 함께 있는 `evidence_role=supporting`의 긍정적 `overlap` Check는 `atomic_claims`를 긍정적 `overlap` 명제로만 구성하십시오. `decrease`, `weaken`, `not_overlap`, `split`, `contract`, `reorganize` 등 후속 제한은 별도 verification_request로 분리하십시오.
- 긍정적 강수-환경 `overlap`에서 후보 안의 운영상 의미 있는 결합영역 존재를 확인하려면 `scope_quantifier=at_least_one`을 사용하십시오. 특정 하위영역을 명시적으로 검증할 때는 `specific`을 사용할 수 있습니다. **후보 bbox 전체의 동시 중첩을 요구하는 `scope_quantifier=all`은 사용하지 마십시오.**
- 같은 `signal_components`, 같은 예측시각, 같은 `domain_scope`/`spatial_relation_scope`의 긍정적 `overlap` 명제를 둘 이상의 verification_request에 중복 작성하지 마십시오. 핵심시각 결합은 하나의 pure `multivariable_alignment` Check에만 두고, 이후 지속성·약화·이동은 별도 Check로 검증하십시오.
- `move`, `split`, `merge`, `expand`, `contract`, `reorganize`는 `spatial_evolution` 요청에서만 사용하십시오.
- `expected_state=move`는 **동일하게 식별 가능한 주강수영역 또는 강수 중심이 중간 시각을 포함해 연속적으로 추적되며 위치가 변하는 경우**를 뜻합니다. 시작·종료 위치가 단순히 다르거나, 약화·분산·소멸 후 다른 위치에서 다시 형성되는 경우, 분리·병합·구조 재편은 `move`로 대신 표현하지 말고 각각의 실제 상태로 작성하십시오.
- 직접강수의 `move`를 사용자 후보명 이동근거로 검증하려는 Check는 atomic claim을 **`direct_precipitation + move` 1개로만** 구성하고 `scope_quantifier=specific`으로 두어 식별 가능한 **주강수영역 또는 주강수 중심** 하나의 연속 이동을 검증하십시오. `at_least_one`처럼 여러 중심 중 일부의 이동만으로 후보 전체를 이동형으로 명명하지 마십시오. 이동과 `reorganize`, `split`, `merge`, `expand`, `contract` 등을 함께 확인해야 하면 별도의 spatial_evolution verification_request로 분리하십시오.
- claim은 현재 요청의 `evidence_role`을 그대로 상속합니다. claim 안에서 후보 지지/억제 방향을 새로 판단하거나 다른 `evidence_role`을 만들지 마십시오.
- `expected_signal` 자연어는 계속 작성하십시오. `atomic_claims`는 이를 대체하는 문장이 아니라 **기계 검증을 위한 구조화 분해**입니다.
- `scope_quantifier=specific`이고 `expected_state`가 `greater_than`, `less_than`, `increase`, `decrease`, `strengthen`, `weaken`, `peak_at` 중 하나이며 운영권역 정량값으로 검증하는 claim이면 `quantitative_scope`를 반드시 작성하십시오. 이때 `region_scope=explicit_regions`, `region_ids`는 정확히 1개여야 합니다.
- 위 specific 정량계약 또는 `majority + not_strengthen` 정량계약을 사용하는 verification_request는 해당 정량명제가 Step07에서 독립적으로 검증될 수 있도록 **atomic_claims를 그 명제 1개로만 구성**하십시오. 다른 판정 기준은 별도 verification_request로 분리하십시오.
- `scope_quantifier=majority`에서 **925 hPa 수렴이 대부분 권역에서 강화되지 않는지**를 정량 검증하려면 `expected_state=not_strengthen`, `feature_id=convergence925_1e5_s`, `region_scope=candidate_overlap`, `region_ids=[]`, 비교할 2개 시각을 `forecast_hours`에 시간순으로, `comparison_forecast_hours=null`, `statistic=mean`으로 작성하십시오. 현재 다른 feature의 majority 물리상태는 이 계약으로 일반화하지 마십시오.
- `majority`는 후보영역과 겹치며 해당 두 시각의 정량값이 모두 존재하는 운영권역 중 **절반 초과(>50%)**가 명제를 만족할 때 성립합니다. 정확히 절반은 majority가 아닙니다.
- specific 정량 claim에서 서로 다른 지역을 보조근거로 추가해 명제의 성립 범위를 바꾸지 마십시오. 예를 들어 `남해동부 F066 > F084`라는 명제라면 `region_ids`에는 남해동부의 실제 region_id만 넣고 동해남부 등 다른 권역을 포함하지 마십시오.
- `convergence925_1e5_s`는 양수=수렴, 음수=발산입니다. 음수에서 덜 음수로 변하는 것은 **발산 약화**이지 수렴 강화가 아닙니다. `not_strengthen` 다수결에서도 이러한 권역을 수렴 강화로 세지 마십시오.

예시:

`expected_signal`: `F060 이후 강수 중심이 북동쪽으로 이동하고 강수대 연속성이 약화된다.`

- `CLM01`: `F060 이후 강수 중심이 북동쪽으로 이동한다.`, `expected_state=move`
- `CLM02`: `F060 이후 강수대 연속성이 약화된다.`, `expected_state=weaken`

### signal_components

사용 가능한 값은 다음과 같습니다.

- `direct_precipitation`: 모델 강수 예측
- `moisture_supply`: 수분 공급 관련 환경. 수평 독립 Check에서는 850 hPa 수증기 플럭스 크기와 실제 850 hPa 바람, 연직 Check에서는 비습·고수분층의 연직구조를 판독
- `low_level_forcing`: 하층 수렴 등 저층 강제력
- `mid_upper_forcing`: 중층 상승운동 등 중상층 강제력
- `organization_persistence`: 바람의 연직구조 관련 환경. 수평 독립 Check에서는 850–500 hPa 연직 바람 시어를, 연직 Check에서는 풍속·단면평행 바람의 연직구조를 판독하며 서로 같은 물리량으로 치환하지 않음

현재 독립적인 열역학 검증자료가 없으므로 새로운 열역학 요소를 만들어 내지 마십시오.
사용자 표시용 한글 자연어에서는 표준 변수 약어 `TCW`를 `대기 전층 수분량(TCW)`처럼 병기할 수 있습니다. 내부 식별자나 다른 영문 구현용 용어는 자연어에 노출하지 마십시오.

### verification_mode

- `spatial_distribution`: 한 기상요소의 한 시각 또는 선택 시각 공간분포, 강도, 위치를 확인. 시간에 따른 중심 이동·재배치는 이 모드에서 결론내리지 않음
- `regional_intensity`: 한 기상요소의 **후보영역과 겹치는 운영권역별** 강도와 선택 시각 최대위치를 정량 비교. 후보영역 전체를 하나로 합친 평균값은 제공되지 않으며 시간에 따른 중심 이동은 다루지 않음
- `candidate_operational_region_overlap`은 현재 immutable candidate_area와 실제로 겹치는 실행 가능한 운영권역을 Python이 미리 계산한 권위 있는 입력입니다. `regional_verification_available=false`이면 `regional_intensity`, `temporal_change`, `precipitation_amount`를 선택하지 말고, `quantitative_scope.region_ids`가 필요한 운영권역 정량 명제도 만들지 마십시오. 이때 `available_verification_modes`에 남아 있는 `spatial_distribution`, `spatial_evolution`, `multivariable_alignment` 또는 적절한 연직 모드로 실제 후보/맥락 공간장을 검증하십시오.
- `regional_intensity`, `temporal_change`, `precipitation_amount`처럼 운영권역별 정량자료가 필요한 모드는 `candidate_operational_region_overlap.regional_verification_available=true`일 때만 사용하십시오. `selectable_regions`에 없는 region_id를 만들거나, 실행 가능하게 만들기 위해 candidate_area를 임의로 확대·이동하지 마십시오.
- `temporal_change`: 한 기상요소의 **후보영역과 겹치는 운영권역별 평균값**이 여러 시각에 어떻게 강화·약화되는지 확인. 후보영역 전체를 하나로 합친 평균값은 제공되지 않으며 공간 중심의 이동·재배치는 결론내리지 않음. 전체 시계열은 정량자료로 제공되고, `representative_spatial_hours`에 지정한 대표 비교시각은 같은 물리량의 공간장으로 추가 제공됨
- `convergence925_1e5_s`는 **양수=수렴, 음수=발산**입니다. 음수값이 커져 0에 가까워지는 변화는 `발산 약화`이지 `수렴 강화`가 아닙니다. 음수에서 양수로 바뀔 때만 `발산에서 수렴으로 전환`, 양수 상태에서 값이 증가할 때 `수렴 강화`로 표현하십시오.
- `spatial_evolution`: 여러 시각의 공간장을 직접 비교하여 중심 이동, 공간구조의 지속(`persist`), 분리, 병합, 확대·축소, 재조직화 등을 확인. 뚜렷한 이동 근거가 없으면 이동을 만들어내지 말고 동일 위치·형태의 지속 여부를 검증
- `multivariable_alignment`: 강수, 수분, 하층 수렴, 중층 상승 중 둘 이상의 공간적 중첩을 확인
- `vertical_structure`: 수분, 중층 상승, 바람구조의 지정 단면 연직구조를 확인. `spatial_relation_scope=not_applicable`을 사용하며, 단면선 확보를 위해 `domain_scope=context`를 사용하더라도 후보 밖 수평관계를 이 요청의 결론으로 만들지 않음
- `vertical_time_evolution`: 후보영역 평균 연직구조가 시간에 따라 어떻게 변하는지 확인. `domain_scope=candidate`, `spatial_relation_scope=not_applicable`을 사용
- `precipitation_amount`: 선택 시각의 6시간 강수량과 후보지역 강도·최대위치를 확인
- 강수 용어는 반드시 구분하십시오. `tp_6h_increment_mm`는 **6시간 강수량**, `tp_accum_mm`는 **초기시각부터 해당 예측시각까지의 누적강수량**입니다. `6시간 누적 강수량`이라는 표현은 두 물리량을 혼동하므로 expected_signal, decision_relevance, purpose에 사용하지 마십시오.

### 조합 규칙

- `spatial_distribution`, `regional_intensity`, `temporal_change`, `spatial_evolution`, `precipitation_amount`는 `signal_components`를 하나만 사용하십시오.
- `multivariable_alignment`는 `direct_precipitation`, `moisture_supply`, `low_level_forcing`, `mid_upper_forcing` 중 실제 비교할 요소를 2개 이상 선택하십시오.
- `multivariable_alignment`에서 `moisture_supply`를 포함할 때 다변수 합성자료의 수분장은 **대기 전층 수분량(TCW) 음영**이며 화살표는 실제 **850 hPa 바람**입니다. 이 Check에서는 `수증기 플럭스가 큰 영역`, `수증기 플럭스`, `수분 유입축`의 중첩을 요구하지 마십시오. 수분 요소는 `대기 전층 수분량(TCW)이 상대적으로 큰 영역` 또는 `고수분 영역`처럼 작성하고, 850 hPa 바람은 보조적인 흐름 맥락으로만 다루십시오. 예: `F060 전북 부근에서 강수영역, 대기 전층 수분량(TCW)이 상대적으로 큰 영역, 925 hPa 수렴, 700 hPa 상승운동이 공간적으로 겹친다.`
- **독립 `moisture_supply` 수평 Check**는 다변수 합성자료와 다릅니다. `spatial_distribution`, `spatial_evolution`, `regional_intensity`, `temporal_change`는 **850 hPa 수증기 플럭스 크기**를 기본 수분장으로 사용하며 공간장 화살표는 실제 **850 hPa 바람**입니다. 이 독립 Check에서 `대기 전층 수분량(TCW)`, `TCW`, `대기 전층 수분량(TCW)이 상대적으로 큰 영역`을 요구하지 마십시오. `spatial_distribution`/`spatial_evolution`에서는 `수증기 플럭스가 큰 영역과 850 hPa 바람이 후보 방향으로 이어진다`처럼 두 의미를 구분하고, `regional_intensity`/`temporal_change`에서는 수증기 플럭스 크기 통계만 사용하십시오.
- `vertical_structure`, `vertical_time_evolution`은 `moisture_supply`, `mid_upper_forcing`, `organization_persistence`만 사용할 수 있습니다. 이 연직자료에서 `moisture_supply`는 **비습과 고수분층의 연직구조**, `mid_upper_forcing`은 **상승·하강운동의 연직구조**, `organization_persistence`는 **풍속 및 단면평행 바람의 연직구조**를 확인합니다. 연직단면/시간-고도 자료 자체를 `850–500 hPa 연직바람시어` 자료로 해석하지 마십시오. 전용 시어 판단이 필요하면 `organization_persistence`의 `spatial_distribution` 또는 `spatial_evolution`을 별도 요청으로 만드십시오.
- **연직 Check에서는 `direct_precipitation`을 검증대상으로 사용하지 마십시오.** 연직단면/시간-고도 증거에는 강수장이 직접 제공되지 않으므로 `강수대와 고수분층이 겹친다`, `강수 중심과 상승운동이 일치한다`처럼 강수 자체가 중첩·일치 관계의 한 요소가 되는 결론은 요구할 수 없습니다. 다만 `강수대 중심을 가로지르는 단면에서`, `강수 최대 시각 이후`처럼 강수대 또는 강수 피크를 **단면 위치나 비교 시각의 종속적인 기준점**으로 언급하는 것은 허용됩니다. `vertical_structure`에서는 가능하면 `cross_section_strategy`, `reference_band_axis`, `cross_section_line.selection_reason`에 그 기준을 기록하십시오. 강수와 환경장의 실제 공간적 중첩이 필요하면 별도의 `multivariable_alignment` 요청으로 분리하십시오.
- `regional_intensity`는 `organization_persistence`에 사용하지 마십시오.
- `temporal_change`는 `organization_persistence`에 사용하지 마십시오. 여러 시각의 바람구조가 필요하면 `spatial_evolution`을 사용하십시오.
- `organization_persistence`의 `spatial_distribution` 또는 `spatial_evolution`은 850–500 hPa **연직바람시어의 크기·방향·공간구조**를 확인하는 경우에만 사용하십시오. 연직바람시어 벡터를 실제 하층 바람, 수분 유입축, 강수대 이동방향으로 해석하지 마십시오.
- 실제 목적이 **하층 수분 유입축, 850 hPa 흐름 방향, 수증기 플럭스 크기의 지속성**이라면 `organization_persistence`가 아니라 `moisture_supply`를 사용하십시오. 수평 수증기 수송 자료의 **음영은 850 hPa 수증기 플럭스 크기이고 화살표는 실제 850 hPa 바람**입니다. `수증기 플럭스 벡터`, `수증기 플럭스 화살표`, `수증기 플럭스 흐름`이라고 표현하지 말고, 방향성이 필요하면 `수증기 플럭스가 큰 영역과 850 hPa 바람이 후보 방향으로 이어진다`처럼 두 의미를 구분하십시오.
- `precipitation_amount`는 `direct_precipitation`에만 사용하십시오. 한 시각 또는 선택 시각의 6시간 강수량과 지역 강도·최대위치 확인에 사용하되, 시간에 따른 강수 중심 이동·재배치는 별도 `spatial_evolution` 요청으로 분리하십시오.
- `temporal_change`, `spatial_evolution`, `vertical_time_evolution`은 서로 다른 예측시각 2개 이상을 포함해야 합니다.
- `temporal_change`에서는 `representative_spatial_hours`에 **1~3개의 대표 비교시각**을 반드시 지정하십시오. 값은 해당 요청의 `phase_keys`가 포함하는 실제 예측시각 중에서만 고르고, 정량 변화의 전후·최대/최소·전환점을 공간적으로 확인하는 데 가장 유용한 시각을 선택하십시오. Python이 대표시각을 임의로 고르지 않습니다. 전체 시계열은 별도 정량자료로 계속 제공되므로 모든 시각을 반복해서 넣지 마십시오.
- `temporal_change`가 아닌 요청은 `representative_spatial_hours=[]`로 반환하십시오. 대표 공간장은 정량 시계열의 공간적 맥락을 확인하기 위한 보조근거이며, `expected_signal`에 없던 이동·재배치·바람방향 결론을 새로 만드는 근거로 사용하지 마십시오.
- 하나의 `expected_signal`에 **정량 강도 변화와 공간 이동·재배치**를 함께 넣지 마십시오. 예를 들어 `후보영역 평균 수렴이 약화하며 동쪽으로 이동한다`처럼 작성하지 마십시오. 평균·최대값 등 정량 변화는 `temporal_change` 또는 `regional_intensity`/`precipitation_amount`, 공간 중심의 이동·분리·재조직은 별도 `spatial_evolution`으로 나누십시오.
- 수평 정량자료에서 `후보영역 평균`, `후보지역 평균`이라고 쓰지 마십시오. 현재 정량자료는 후보영역과 겹치는 **운영권역별 통계**로 제공됩니다. 따라서 `충남권 평균`, `전남권 최대`, 또는 `후보영역과 겹치는 운영권역별 평균`처럼 실제 제공 단위와 맞춰 작성하십시오.
- `temporal_change`, `regional_intensity`, `precipitation_amount`에서 평균·최대값·강도와 함께 기상장 자체의 `면적`, `폭`, `영역 확대`, `영역 축소` 같은 공간범위 변화를 한 Check에 요구하지 마십시오. 공간범위 자체는 `spatial_distribution`, 시간에 따른 면적·폭·확대·축소는 별도 `spatial_evolution`으로 분리하십시오. 다만 운영권역별 정량값을 근거로 여러 지역의 강도 경향을 요약하는 것은 허용되며, 이 경우 `F042에도 여러 운영권역에서 평균 상승운동의 뚜렷한 강화가 없다`처럼 실제 제공되는 운영권역별 평균 통계로 판정 가능한 표현을 사용하십시오. `넓은 강화`처럼 공간범위와 지역별 강도 경향이 혼동되는 표현은 피하십시오.
- `moisture_supply`의 `temporal_change`는 운영권역별 **수증기 플럭스 크기**의 시계열을 확인하는 용도입니다. 이 모드에서 850 hPa 바람 방향이나 유입 방향을 결론내리지 마십시오. 방향을 확인해야 하면 `spatial_distribution` 또는 `spatial_evolution`을 사용하십시오.
- **모든 후보의 모델 강수 예측 존재 여부는 시스템이 후보의 기존 `peak_forecast_hours`와 `candidate_area`를 사용해 별도 필수 SYSTEM presence Check로 추가**합니다. `precursor_environment` 후보에서도 핵심시각은 강수 피크로 재해석하지 않고 기존 선행환경 대표시각을 그대로 사용합니다. 따라서 존재 여부만 확인하기 위한 중복 `verification_request`를 만들지 마십시오.
- SYSTEM presence Check는 오직 `6시간 강수량 1 mm 이상의 식별 가능한 모델 강수영역이 후보 핵심시각 중 하나 이상에서 존재하는가`만 판독합니다. 후보의 `detection_basis`를 변경하거나 이동, 연속성, 조직화, 피크, 강도 비교를 판단하지 않습니다.
- AI 검증계획에서는 강수대 이동, 연속성, 조직화, 피크 시각, 강도 변화, 확대·축소처럼 **존재 여부와 구별되는 추가 기상학적 특성**을 필요에 따라 별도 요청으로 작성하십시오. 이러한 Check에서 `expected_state=absent`가 `observed`이더라도 이는 해당 부재 가설이 확인됐다는 뜻일 뿐, 모델 강수 예측 존재의 긍정 근거가 아닙니다.
- 모델 강수 예측 관련 추가 요청을 만들 때에도 이동·연속성·강도·피크 등 서로 다른 판정 기준을 한 `expected_signal` 또는 하나의 `atomic_claim`에 결합하지 마십시오.
- `precursor_environment` 후보에서 `강수가 약하다`, `연속 강수대가 없다`, `조직화되지 않는다` 등을 추가 확인할 수는 있지만, 순수한 강수 존재/부재 판정은 SYSTEM presence Check에 맡기십시오.
- 단일 기상요소 요청의 `expected_signal`에서 다른 기상요소와의 중첩·일치·동반을 결론으로 요구하지 마십시오. 여러 요소의 공간적 관계가 실제 검증 목적이면 `multivariable_alignment`로 분리하십시오.
- 다른 기상요소는 시간 또는 상황의 기준점으로 언급할 수 있습니다. 예를 들어 `강수 최대 시각 이전의 하층 수렴 강화`는 `low_level_forcing` 단일요소 요청으로 허용됩니다. 이 경우 강수와 수렴의 공간적 일치까지 같은 요청에서 요구하지 마십시오.
- 후보당 같은 기상요소, 검증목적, 영역, 시간대의 요청을 중복하지 마십시오.

## 3. 연직단면 위치 지정

`verification_mode=vertical_structure`는 AI가 **어디를 자를지** 판단해야 하므로 다음 위치정보를 작성하십시오. 이때 단면선이 `context_domain`까지 확장되더라도 `spatial_relation_scope=not_applicable`을 유지하십시오. `candidate_external_context`는 단면 범위가 넓다는 뜻이 아니라 후보 밖 수평환경 자체의 위치·연결을 판정하는 별도 공간 Check에만 사용합니다. 강수대·강수중심을 기준으로 단면을 배치해야 하는 경우 그 관계는 아래 위치정보에만 기록하고, `expected_signal`에는 강수와의 중첩·일치 명제를 넣지 마십시오.

- `cross_section_line`: 시작·종료 위도/경도와 선택 이유
- `cross_section_strategy`: `across_precipitation_band`, `along_precipitation_axis`, `along_moisture_inflow` 중 하나
- `reference_band_axis`: 강수대 또는 유입축의 기준 방향

단면선의 양 끝점은 선택한 `domain_scope` 안에 있고 선분은 `candidate_area`를 지나야 합니다. `across_precipitation_band`이면 강수대 기준축과 대체로 직교하고, `along_precipitation_axis`이면 대체로 평행해야 합니다. 불필요하게 넓은 장거리 단면을 만들지 마십시오.

`vertical_structure`가 아닌 모든 요청에서는 `cross_section_line`, `cross_section_strategy`, `reference_band_axis`를 `null`로 반환하십시오.

## 4. 가설과 예보관 질문

- 가설은 최대 4개, 검증 요청은 한 chunk에서 최대 10개, 예보관 질문은 최대 6개입니다.
- 모든 가설은 `supporting_check_keys`에 최소 1개의 실제 검증 요청을 포함해야 합니다.
- 같은 check를 하나의 가설에서 지지와 반대 근거에 동시에 넣지 마십시오.
- `supporting_check_keys`와 `opposing_check_keys`는 **각 가설 문장에 상대적인 역할**로 연결하십시오. 예를 들어 `환경이 후속 시각까지 유지된다`는 지속 가설에서 `약화`, `감소`, `not_strengthen`, `not_overlap`을 기대하는 Check는 supporting이 아니라 opposing에 연결하십시오. 반대로 제한 가설이라면 그 역할이 뒤집힐 수 있으므로 Check의 전역 `evidence_role`만 기계적으로 복사하지 마십시오.
- 생성한 검증 요청은 최소 하나의 가설과 연결하십시오.
- 여러 피크 또는 재강화가 중요하면 피크 사이 약화, 이동, 강제력 변화 등을 확인할 질문을 추가하십시오.
- 장문의 해설이나 내부 실행절차 설명은 작성하지 마십시오.

## 5. 내부 실행계약은 작성하지 않음

다음 값은 AI가 결정하거나 출력하지 않습니다.

- `diagnostic_id`
- `variable_role`
- `check_scope`
- `required_signal_components`
- `analysis_methods`
- `product_id`, `feature_id`, `request_id`, `output_spec`
- 단, `atomic_claims[].quantitative_scope`의 `feature_id`, `region_scope`, `region_ids`, `forecast_hours`, `comparison_forecast_hours`, `statistic`은 위 구조화 정량계약에 따라 작성합니다. `feature_id`와 `region_id`는 입력에 제공된 실제 식별자를 그대로 사용하며 새 식별자를 만들지 않습니다.

Python이 `signal_components + verification_mode + domain_scope + spatial_relation_scope + phase_keys`를 이용해 위 내부 계약과 Step05 실행요청으로 결정론적으로 변환합니다.

## 6. Chunk와 continuation

- 첫 번째 chunk에는 모든 후보 핵심 시각을 포함하는 `peak` 단계, 검증 요청 3개 이상, 예보관 질문 1개 이상을 포함하십시오.
- `is_complete=true`인 최종 계획에서는 선언한 모든 `analysis_phases`가 이전 chunk와 현재 chunk를 합쳐 최소 하나의 `verification_requests[].phase_keys`에서 실제로 참조되어야 합니다. 검증 요청에 사용하지 않을 단계는 만들지 마십시오. 반대로 필요한 단계라면 그 단계를 사용하는 의미 있는 검증 요청을 함께 작성하십시오.
- 한 번에 필요한 계획을 모두 작성할 수 있으면 `is_complete=true`, `remaining_roles=[]`로 반환하십시오.
- 후속 chunk에서는 `continuation_context`의 기존 phase와 검증 요청을 재작성하지 말고, 새 검증이 필요한 경우에만 추가하십시오. 기존 phase의 실제 시각은 `continuation_context.existing_phases[].forecast_hours`를 확인하고, 재사용할 때는 해당 `phase_key`를 그대로 참조하십시오.
- 후속 chunk의 `continuation_context.remaining_role_guidance`가 있으면 해당 지침을 우선 적용하십시오. 특히 `temporal_evolution`은 단일시각 요청에 태그만 붙이지 말고, 둘 이상의 실제 예측시각을 비교하는 `temporal_change`, `spatial_evolution`, 또는 `vertical_time_evolution` 요청으로 충족하십시오. `spatial_evolution`은 여러 시각의 공간장 이동·분리·재조직 또는 동일 공간구조의 지속(`persist`)을 직접 비교해야 합니다.
- `temporal_evolution`과 `spatial_evolution`이 동시에 남아 있으면, 둘 이상의 실제 예측시각을 사용하는 하나의 의미 있는 `spatial_evolution` 요청으로 두 역할을 함께 충족할 수 있습니다. 역할별로 중복 요청을 만들 필요는 없습니다.
- `coverage_roles`는 계획 의도를 설명하는 태그이지만, 시간·공간 진화 역할은 실제 `verification_mode`와 예측시각 구조가 일치해야 합니다. Python이 구조에서 결정론적으로 재검증하므로 태그만으로 빈 역할을 채우려 하지 마십시오.
- 후속 가설이나 질문이 기존 요청을 사용하면 기존 `check_key`를 그대로 참조하십시오.
- `continuation_context.remaining_roles`가 하나 이상이면 최소 하나의 남은 역할을 실제로 감소시키는 `verification_request`를 반드시 추가하십시오. 이 경우 `analysis_phases=[]`, `verification_requests=[]`인 빈 continuation을 반환하지 마십시오.
- `continuation_context.remaining_roles=[]`이고 신규 검증이 실제로 불필요한 경우에만 `analysis_phases=[]`, `verification_requests=[]`로 반환할 수 있습니다.
- 첫 chunk에서 정한 `context_domain`을 후속 chunk에서 변경하지 마십시오.

## 사용자 표시용 자연어

JSON 키, 열거값, 식별자, 예측시간, 물리단위는 계약 그대로 유지하되 사람이 읽는 문자열은 모두 한국어 기상용어로 작성하십시오. 현재 체계는 기상특보 기준을 독립적으로 판정하지 않으므로 자연어에 `호우`, `집중호우`를 단정적으로 사용하지 마십시오.

`expected_signal`에는 실제로 확인할 위치, 시각, 기상요소와 변화 형태만 구체적으로 기술하십시오. **분석 행위가 아니라 상세자료에서 참·부분참·미확인·반대 여부를 판정할 수 있는 기상학적 상태 또는 변화를 명제로 작성하십시오.** `확인한다`, `비교한다`, `살펴본다`, `판단한다`, `검토한다`, `분석한다`, `평가한다`처럼 무엇을 할 것인지 설명하는 절차형 문장으로 끝내지 마십시오. 예를 들어 단일요소 요청에서는 `925 hPa 수렴 분포를 확인한다`가 아니라 `F060 전북 부근에 925 hPa 수렴 중심이 나타난다`처럼 작성하십시오.

또한 **어느 쪽으로 달라질지 밝히지 않는 문장**은 사용하지 마십시오. `서로 다르게 나타난다`, `정량적으로 구분된다`, `차이가 나타난다`, 방향 없이 `변화한다`와 같은 표현은 거의 항상 참이 될 수 있어 검증 명제로 부적합합니다. `F120의 최대 6시간 강수량이 F102보다 크다`, `F120에는 강수 중심과 상승운동 중심의 중첩이 F102보다 약하다`처럼 어느 시각·영역이 더 강한지/약한지, 중심이 어디에 위치하는지, 중첩이 강화되는지/약화되는지를 구체적으로 작성하십시오. `강화 또는 약화`, `이동 또는 유지`처럼 서로 반대되는 결과를 한 문장에 `또는/혹은`으로 함께 적지 마십시오.

수평 정량 시계열에서 `후보영역 평균 수렴`, `후보영역 평균 상승운동`처럼 **후보영역 전체를 하나의 평균값으로 가정하지 마십시오.** 실제 자료는 후보영역과 겹치는 운영권역별 평균·최대값으로 제공됩니다. 또한 `평균 상승운동이 가장 넓다`, `평균 수렴의 면적이 축소된다`처럼 통계값과 공간범위를 혼합하지 마십시오.

수증기 수송 수평자료에서는 **수증기 플럭스 크기와 850 hPa 바람을 구분**하십시오. `수증기 플럭스 벡터가 북동향이다`가 아니라 `수증기 플럭스가 큰 영역에서 850 hPa 바람이 북동향이다`처럼 작성하십시오. 독립 수증기 수송 수평 Check에서는 대기 전층 수분량(TCW)을 expected_signal로 요구하지 마십시오. 대기 전층 수분량(TCW)은 다변수 중첩 Check에서만 사용합니다.

연직단면과 시간-고도 자료에서는 **비습/고수분층, 상승·하강운동, 풍속/단면평행 바람**을 직접 판독하십시오. 이 자료에 850–500 hPa 연직바람시어가 직접 제공된다고 가정하지 마십시오. 시어가 필요하면 별도의 전용 연직바람시어 Check로 분리하십시오.

여러 요소의 중첩을 명제로 작성하려면 해당 요소를 모두 `signal_components`에 선언하고 `multivariable_alignment`를 사용하십시오. `evidence`, `check`, `peak`, `precursor`, `hazard`, `forcing`, `episode`, `Step04`~`Step09` 같은 내부 용어를 사용자 설명에 반복하지 마십시오.

JSON 객체만 반환하십시오.

### `expected_signal`과 `verification_mode`의 책임 경계
- `verification_mode`가 실제로 생산할 증거 형태를 결정합니다. `expected_signal`은 그 증거에서 확인하려는 기상학적 특징을 자연어로 설명합니다.
- `expected_signal`에 `강수 최대 시각 이전`, `Fxxx 전후`, `강화`, `약화`, `변화` 같은 시간 기준 또는 서술 표현이 포함되어도 그 표현만으로 시간변화 진단을 선택할 필요는 없습니다.
- 실제 목적이 **여러 시각의 값 변화 자체를 비교**하는 것이면 `temporal_change`, **여러 시각의 공간장 이동·재조직을 비교**하는 것이면 `spatial_evolution`을 선택하십시오.
- 다른 변수는 시간 기준이나 배경 설명으로 언급할 수 있습니다. 다만 두 변수의 공간적 일치·중첩·정합성을 직접 검증하려는 경우에만 모든 비교 변수를 `signal_components`에 선언하고 `multivariable_alignment`를 사용하십시오.
