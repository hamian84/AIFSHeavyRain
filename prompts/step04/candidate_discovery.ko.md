# AIFS Pass1 고재현율 후보 탐색

검증된 그룹 요약, asset index, Step02 정량 강수 peak guidance만 근거로 강수 감시 후보를 탐색하십시오. Pass1은 위험을 확정하지 않으며 다음 두 경로를 독립적으로 검사한 뒤 중복을 조정합니다.

1. 모델 강수 예측 경로: 정량 강수 peak를 참고 anchor로 사용하면서 영상·공간구조·시간변화를 함께 판단하는 `direct_precipitation` 또는 `mixed` 후보
2. 선행환경 경로: 모델 강수가 약하거나 없지만 복수 선행환경이 중첩되는 `precursor_environment` 후보

## 출력 계약

- `candidates`, `candidate_evidence`, `precursor_opportunities`, `global_uncertainties`, `precursor_candidate_assessment`를 반환하십시오.
- 후보는 실제 신호에 따라 0~10건으로 생성하며 overview 시간그룹마다 의무적으로 만들지 마십시오.
- 후보와 evidence는 같은 순서로 정확히 하나씩 대응해야 합니다.
- `candidate_id`, `candidate_unit_type`, `discovery_order`, `package_id`, `analysis_coverage`, `run_summary`, `pass2_required`는 출력하지 마십시오. Python이 결정합니다. `discovery_order`는 위험도 순위가 아니라 후보가 확정된 순서입니다.
- evidence에는 원본 overview `asset_id`, `product_id`, `forecast_hours`만 정확하게 인용하십시오.
- 제공되지 않은 자료나 모델 결과를 사용하거나 추정했다고 주장하지 마십시오.

## 모델 강수 예측 후보와 peak 기준

- `precipitation_peak_guidance`는 Step02 운영권역별 6시간 강수량의 max, p95, mean을 결합해 Python이 계산한 정량 anchor입니다.
- `direct_precipitation`과 `mixed` 후보의 `peak_forecast_hours`는 정량 시계열을 중요한 참고근거로 사용하되, 영상의 공간구조·강수대 이동·재조직화·선행환경과 함께 AI가 최종 판단하십시오.
- AI가 선택한 peak가 `precipitation_peak_guidance`의 정량 국지 최대와 다르더라도 자동으로 정량값에 맞추지 마십시오. 차이가 기상학적으로 의미 있다고 판단하면 후보의 `uncertainties` 또는 근거에 간결히 남기십시오. Python은 이 차이를 후속 Pass2 재검토용 QC 참고정보로만 추가합니다.
- overview 그룹의 첫 시각, 그룹 중앙시각 또는 asset 시작시각을 자동으로 peak로 선택하지 마십시오.
- 강수 peak는 후보 구분의 핵심 anchor이지만 `peak 하나 = 후보 하나`는 아닙니다.
- 시간 간격 자체만으로 merge/split을 결정하지 마십시오. 같은 강수대의 이동·재강화인지, 영향지역이 달라 별도 감시가 필요한지, 독립적인 저기압·전선·수렴대인지 종합 판단하십시오.
- 여러 예측시각에 강수영역이 각각 존재한다는 사실만으로 `강수대가 이어진다`, `유지된다`, `지속된다`고 판단하지 마십시오. 공간 연결성과 시간적 동일성이 관찰근거에서 확인되지 않으면 `각 시각에 강수영역이 나타난다` 수준으로만 기술하십시오.
- 한 후보에 시간적으로 분리된 여러 peak를 포함할 수도 있고 별도 후보로 나눌 수도 있습니다. 어느 쪽이 예보 감시 관점에서 더 타당한지는 AI가 판단하십시오.
- 여러 peak를 하나의 후보에 유지한다면 피크 사이의 약화·재조직화·영향지역 변화가 후속 상세분석에서 검증될 수 있도록 evidence와 uncertainties를 충분히 남기십시오.
- 후보를 분리할 때 overview 그룹 경계 자체를 근거로 사용하지 마십시오.

## 후보 공간과 연속성

- 입력의 `candidate_operational_scope`가 Candidate 공간정책의 권위 있는 기준입니다.
- `direct_precipitation` 또는 `mixed` 후보의 `candidate_area`는 `candidate_operational_scope.eligible_forecast_regions`의 `operational_land` 또는 `marine_macro` 중 하나 이상과 **실제로 겹쳐야 합니다.**
- `candidate_operational_scope.context_only_source_corridors`에만 존재하는 강수는 독립 `direct_precipitation`/`mixed` 후보로 만들지 마십시오. 이러한 강수는 주변 맥락, 접근 경로 또는 선행환경 판단의 근거로만 유지하십시오.
- 운영권역과 겹치게 만들기 위해 실제 강수 핵심영역의 bbox를 국내 또는 해상 운영권역 쪽으로 임의 확대·이동하지 마십시오. 실제 영향 핵심영역이 운영권역과 겹치지 않으면 해당 direct/mixed 후보를 생성하지 않는 것이 맞습니다.
- 후보별 `candidate_area`는 실제 강수 영향 또는 선행환경 중첩이 예상되는 위험 핵심영역의 최소 단일 bbox로 제시하십시오. **overview 전체 범위나 서로 떨어진 여러 강수군의 외곽을 한 번에 감싸는 큰 bbox를 후보영역으로 사용하지 마십시오.**
- 수증기 공급원, 상류 이동경로, 중국 동부·동중국해 등 종관 배경을 `candidate_area`에 함께 넣지 마십시오. 주변영역은 candidate plan의 `context_domain`에서 지정합니다.
- 강수영역이 서로 떨어져 있고 그 사이의 공간적 연속성 또는 동일 기상계의 시간적 연결이 관찰근거에서 확인되지 않으면, 넓은 무강수 공간까지 포함하여 하나의 `candidate_area`로 병합하지 마십시오. 각각 독립적인 감시대상으로 의미가 있으면 별도 후보를 검토하고, 국내 위험 핵심영역과 직접 연결되지 않는 원거리 강수군은 주변 맥락으로 남기십시오.
- **`candidate_area`와 `context_domain`의 역할을 구분**하십시오. 위험 핵심영역은 candidate_area에, 수증기 공급원·상류 경로·원거리 강수군·종관 배경은 context_domain에 두며, 맥락 자료를 포함하기 위해 candidate_area를 확대하지 마십시오.
- 한 후보의 영역에 대부분 포함되고 시간대가 겹치는 경우에도 자동 병합하지 마십시오. 동일 위험현상의 중복 후보인지, 이동하는 강수대에 따라 감시대상이 달라지는 별도 후보인지 AI가 판단하십시오.
- 후보명은 **실제 관찰근거가 직접 지지하는 강수 핵심 위치 또는 위험 핵심영역**을 기준으로 짧게 작성하십시오. 후보 bbox가 특정 운영권역과 겹친다는 이유만으로 그 운영권역명을 후보의 대표 강수 위치로 사용하지 마십시오.
- `한반도 남쪽 해상`을 자동으로 `남해`로, `한반도 서쪽·남서쪽 해상`을 자동으로 `서해남부`로 바꾸지 마십시오. 입력 그룹요약이 특정 해역명을 명확히 지지하지 않으면 더 구체적인 지명을 새로 만들지 말고 상대위치 중심으로 후보명을 작성하십시오.
- 특정 운영권역의 정량 강수 최대·평균 또는 일부 격자 강수가 확인되었다는 사실을 후보 전체의 대표 위치, 주요 강수 중심 또는 후보 전체 최대강수 시각으로 일반화하지 마십시오.
- 후보 배열의 `separation_reason`은 기본적으로 `null`입니다.
- 공간·시간 중첩이 큰 후보를 별도로 유지한다면 가능하면 `separation_reason`에 강수대 이동, 영향지역 변화, 재조직화, 강제력 변화 또는 독립 기상계 등 판단 근거를 구체적으로 남기십시오. 다만 Python은 이 설명의 유무로 후보를 강제 merge/split하지 않습니다.
- “별도 피크군으로 감시”, “재강화”, “시간이 늦음” 같은 선언만으로 충분하다고 보지 말고, 실제 기상학적·감시상 이유를 설명하십시오.

## SYSTEM 정량 직접강수 coverage 감사

- 입력의 `system_direct_precipitation_coverage_guidance`는 Step02 운영권역별 6시간 강수량을 Python이 독립 계산한 SYSTEM 감사 기준입니다. 그룹요약의 `direct_precipitation_audit`와 별개이며 모델이 `absent`로 판독해도 사라지지 않습니다.
- `required_reference_signals`의 각 시각·운영권역은 candidate reference threshold 이상인 정량 신호입니다. 적어도 하나의 direct/mixed 후보가 해당 시각과 공간을 포함하고, 일치하는 `QL_PRECIP_6H` overview asset/hour를 evidence로 인용해야 합니다.
- 같은 기상계로 판단되는 여러 SYSTEM 신호는 하나의 후보가 포괄할 수 있습니다. 단순히 신호 수만큼 후보를 만들지 말고 영상의 공간 연결성, 이동·재조직, 환경장을 함께 고려하십시오.
- 이미 존재하는 후보를 확장하려면 동일한 강수계라는 기상학적 근거가 있어야 합니다. 서로 떨어진 강수영역을 SYSTEM coverage를 맞추기 위해 큰 bbox 하나로 억지로 합치지 마십시오.
- `minimum_signal_hour_summary`에는 1.0 mm 이상 직접강수 신호가 존재하는 시각이 요약됩니다. candidate reference threshold 미만의 미커버 신호는 감사 기록에 남지만 그 값만으로 신규 후보 생성이 강제되지는 않습니다.
- SYSTEM 정량값을 삭제·축소하거나 임계값을 낮추는 방식으로 coverage 오류를 회피하지 마십시오.

## 모델 강수 예측 관찰과 운영 coverage의 구분

- group summary의 `direct_precipitation_audit`은 원본 강수 overview에서 관찰된 신호를 시간그룹 단위로 보존하기 위한 **관찰 감사정보**입니다. 이것만으로 Candidate 생성 의무가 생기지 않습니다.
- 특히 `direct_precipitation_audit.status=present`이더라도 해당 강수가 `candidate_operational_scope.context_only_source_corridors`에만 있으면 독립 direct/mixed Candidate로 만들지 마십시오.
- Candidate 생성이 반드시 필요한 정량 직접강수 coverage는 `system_direct_precipitation_coverage_guidance.required_reference_signals`만 권위 있게 사용하십시오. 이 신호는 Step02의 `eligible_for_risk_rank` 운영권역에서 계산됩니다.
- group summary의 강수 asset/hour는 이미 존재하는 운영 Candidate와 실제로 같은 강수계·시간창·공간영역을 지지할 때 evidence로 인용할 수 있습니다. 원거리 source corridor 강수를 Candidate coverage를 맞추기 위해 억지로 포함하지 마십시오.
- 같은 강수계에 속하는 여러 유의 시각은 후보를 불필요하게 나누지 말고, 실제로 같은 Candidate를 지지하는 경우 동일 강수 evidence item의 `forecast_hours`에 함께 기록할 수 있습니다.
- 후보 time window와 강수 asset 참조가 이미 운영 신호를 포괄하는데 evidence 배열에서 그 시각만 누락된 경우, Python은 같은 evidence item에 해당 시각을 결정론적으로 보완합니다. 후보 시간창을 새로 만들거나 임의로 확장하지는 않습니다.
- `direct_precipitation_audit.status=weak|absent|uncertain|not_applicable`인 그룹을 의무적으로 후보화하지 마십시오.

## 선행환경 기반 후보

- 전체 기간과 전체 영역에서 모델 강수 예측 후보와 별도로 선행환경 기반 후보 가능성을 반드시 한 번 검사하십시오.
- 유효하고 독립적인 선행환경 신호가 하나 이상 있으면 `precursor_environment` 후보를 최소 1개 유지하십시오.
- 유효한 독립 선행환경 신호가 없으면 `precursor_environment` 후보를 만들지 마십시오.
- 출력 할당량을 맞추기 위해 근거 없는 선행환경 후보를 생성하지 마십시오.
- `precursor_environment` 후보의 `precipitation_signal_status`는 `weak`, `absent`, `uncertain` 중 하나여야 합니다.
- 운영상 핵심 강수 발달 환경 범주인 `moisture_supply`, `low_level_forcing`, `mid_upper_forcing` 중 서로 다른 범주가 둘 이상이어야 하며, 그중 `low_level_forcing` 또는 `mid_upper_forcing`이 하나 이상 포함되어야 합니다. `thermodynamic_support`와 `organization_persistence`는 보조 근거로 기술할 수 있지만 현재 독립 감시후보 성립 범주 수에는 포함하지 마십시오.
- 수증기 관련 변수 여러 개만으로는 서로 다른 범주 둘로 계산하지 마십시오.
- 선행환경 신호는 같은 시간과 후보 핵심영역에서 중첩되어야 하며 `precursor_spatiotemporal_overlap=true`여야 합니다.
- `direct_precipitation` 또는 `mixed` 후보와 같은 시간·영역·메커니즘이면 별도 precursor 후보로 중복 생성하지 말고 기존 후보를 `mixed`로 분류하십시오.
- `precursor_environment` 후보의 `peak_forecast_hours`는 강수 peak가 아니라 선행환경 신호의 시공간 일치가 가장 뚜렷한 대표시각입니다.

## precursor opportunity와 Candidate 생성의 분리

- 직접강수 후보를 먼저 탐색한 뒤, 전체 기간·영역의 선행환경 opportunity를 별도로 기록하십시오. opportunity는 Candidate와 별개의 Pass1 provenance이며 영구 식별자가 아닙니다.
- 각 `precursor_opportunities` 항목은 `qualification`, `relation_to_direct`, `candidate_creation`의 세 축을 모두 기록합니다. `detection_basis=mixed`는 관계 분류가 아니며 `relation_to_direct`에 쓰지 마십시오.
- `qualification=not_qualified`이면 `relation_to_direct=not_applicable`, `candidate_creation=not_created`, `created_candidate_id=null`이어야 합니다.
- `qualification=qualified`이면 직접/mixed 후보와 시간·공간·메커니즘 관계를 각각 판단해 `independent`, `absorbed_into_direct`, `boundary_ambiguous` 중 하나를 고르십시오. 숫자 임계값이나 단순 bbox 겹침만으로 판정하지 마십시오.
- `absorbed_into_direct`와 `boundary_ambiguous`는 `related_direct_candidate_ids`에 관련 direct/mixed 후보를 넣고, 새 precursor Candidate를 만들지 마십시오. `boundary_ambiguous`는 후보 생성 또는 향후 surface의 근거가 아닙니다.
- 새 Candidate는 `qualification=qualified`와 `relation_to_direct=independent`일 때만 `candidate_creation=created`로 만드십시오. 이 경우에만 `created_candidate_id`에 해당 후보 ID를 넣으십시오. 후보 ID는 candidates 배열의 1번째부터 `CAND_001`, `CAND_002` 순서로 Python이 부여하므로 정확한 배열 위치를 기준으로 참조하십시오.
- `relation_basis`에는 시간관계, 공간관계, 메커니즘관계와 짧은 근거를 모두 작성하십시오. `support_asset_ids`와 `representative_forecast_hours`는 실제 overview 근거만 인용하십시오.
- Candidate 내부의 `precursor_spatiotemporal_overlap`은 해당 Candidate의 선행환경 support 판단용 필드로 유지하십시오. opportunity의 direct 관계를 대신하는 필드가 아닙니다.

## precursor_candidate_assessment

- `precursor_opportunities`가 신규 source of truth입니다. 이 legacy 호환 요약은 Python이 opportunity에서 다시 계산하므로, opportunity와 충돌하는 독립 판단을 추가하지 마십시오.

- `assessment_status`는 다음 중 하나입니다.
  - `qualified_independent_signal`: 기존 direct/mixed 후보로 흡수되지 않는 유효 선행환경 신호가 있음
  - `no_qualified_independent_signal`: 자격조건을 만족하는 독립 선행환경 신호가 없음
- `qualified_independent_signal`이면 `qualified_opportunity_count`를 1 이상으로 쓰고 `precursor_environment` 후보를 최소 1개 생성하십시오.
- `no_qualified_independent_signal`이면 `qualified_opportunity_count=0`으로 쓰고 precursor 후보를 생성하지 마십시오.
- `source_asset_ids`에는 판단에 사용한 비강수 overview 자산을 넣고, `forecast_hours`에는 대표시각을 넣으십시오.
- `rationale`에는 후보를 유지하거나 생성하지 않은 이유를 한두 문장으로 작성하십시오.

## 시그널 분류

- `detection_basis`는 `direct_precipitation`, `precursor_environment`, `mixed` 중 하나입니다.
- `precipitation_signal_status`는 `present`, `weak`, `absent`, `uncertain` 중 하나입니다.
- `precursor_signal_categories`는 `moisture_supply`, `low_level_forcing`, `mid_upper_forcing`, `thermodynamic_support`, `organization_persistence`, `direct_precipitation`만 사용합니다.
- `detection_basis`는 후보가 처음 발견된 경로이고, `precursor_signal_categories`는 실제로 확인된 환경 근거입니다. 모델 강수 예측 경로로 발견된 후보라도 관련 evidence에 수분 공급·하층 수렴·중층 상승 등이 있으면 해당 범주를 누락하지 마십시오.
- 모델 강수 예측 경로의 후보에서 선행환경이 후보 핵심 시간·영역과 실제로 겹치면 `precursor_spatiotemporal_overlap=true`로 기록하십시오. `detection_basis=direct_precipitation`이라는 이유만으로 선행환경 범주를 `direct_precipitation` 하나로 축소하거나 overlap을 false로 고정하지 마십시오.
- `mixed`는 선행환경 중첩이 후보 선정 자체에 실질적으로 기여한 경우에만 사용합니다. 모델 강수 예측 경로로 발견된 후보에 선행환경이 함께 존재한다는 이유만으로 반드시 `mixed`로 바꿀 필요는 없습니다.
- `precursor_environment` 또는 `mixed` 후보는 서로 다른 선행환경 범주가 둘 이상이고 그중 하나가 역학적 강제력 범주이며 `precursor_spatiotemporal_overlap=true`일 때만 만들 수 있습니다.
- `hypothesis.statement`에는 검증 가능한 핵심 가설만 한 문장으로 작성하십시오.

## 간결성 제한

- 후보명은 짧은 지명·현상 중심으로 작성하십시오.
- 후보별 지지 근거는 최대 4개, 반증 근거는 최대 3개, AI가 작성하는 불확실성은 최대 2개입니다. 세 번째 `uncertainties` 슬롯은 Python 정량 QC 참고정보가 필요한 경우를 위해 비워 두십시오.
- 후보별 evidence는 최대 6개, 관련 overview 자산은 최대 8개입니다.
- 전역 불확실성은 최대 6개입니다.
- `analysis_envelope_request.temporal_padding_hours`는 0~12시간 범위에서 선택하십시오.
- 기상학 일반론, 교과서식 설명, 같은 내용의 반복, 상세 진단 절차를 작성하지 마십시오.

단계 응답 스키마를 만족하는 JSON 객체만 반환하십시오. Markdown이나 설명문을 추가하지 마십시오.

## 사용자 표시용 자연어 작성 원칙

JSON 키, 열거값, 식별자, 예측시간, 물리단위는 내부 계약 그대로 유지합니다. 그 밖의 후보명·가설·근거·목적·불확실성·관계 설명 등 사람이 읽는 모든 문자열은 **한글 기상용어**로 작성하십시오.

현재 체계는 기상특보 수준의 강수량 기준을 독립적으로 판정하지 않습니다. 자연어 문장에서는 `호우`, `집중호우`를 단정적으로 사용하지 말고, 자료에서 확인되는 강수량·강도·분포와 강수 발달 환경을 구체적으로 기술하십시오.

자연어 문장에는 내부 단계명이나 영문 분류명을 쓰지 마십시오. 특히 `precursor`, `event`, `peak`, `candidate`, `evidence`, `alignment`, `hazard`, `forcing`, `episode`, `organization_persistence`, `direct_precipitation`, `moisture_supply`, `low_level_forcing`, `mid_upper_forcing`, `thermodynamic_support`, `Step04` 등의 표현을 넣지 마십시오. 이러한 값이 JSON 키·열거값으로 필요한 경우에만 내부 구조 안에서 사용합니다.

표현은 실제 기상요소를 우선합니다.

- 선행환경: 수분 공급, 하층 수렴, 중층 상승운동 등 강수 발달 환경
- `direct_precipitation`: 모델 강수 예측
- 시공간 관계: 시공간 일치 또는 위치·시각의 일치 정도
- 조직화·지속성: 강수계의 조직화 및 지속 가능성
- `peak_forecast_hours`: 모델 강수 예측 계열에서는 최대 강수 예상 시각, 선행환경 계열에서는 강수 발달 환경의 대표 시각

예: `precursor와 forcing이 정렬된다`라고 쓰지 말고 `수분 공급 관련 신호와 하층 수렴이 같은 시각과 영역에서 함께 나타난다`라고 작성하십시오.

AIFS, AI, UTC, KST, F084 형식의 예측시간과 hPa·mm·m/s·Pa/s·g/kg 등 물리단위는 필요한 경우 사용할 수 있습니다.
