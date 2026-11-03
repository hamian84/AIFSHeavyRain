# 개발·운영 이력 문서 관리 지침

## 1. 기록해야 하는 변경의 종류

1. **기능 변경:** 새로운 자료, 분석 단계, 감시 기준, 웹 표현.
2. **오류 수정:** 오류 코드/발생 조건/근본 원인/수정 파일/회귀 사례.
3. **운영 정책 변경:** 운영권역, 강수 최소기준, API 모델, 재시도, 보존기간.
4. **검증:** 수행 init, Step01~10 완료/실패, 근거·판정·표출 비교.
5. **문서 변경:** 코드 변경 없이 문서만 수정해도 Git에 별도 커밋 기록.

## 2. 신규 이력 항목 필수 템플릿

```markdown
### YYYY-MM-DD — [제목]

- **사건 일시(KST/UTC):**
- **변경 구분:** 기능 / 결함 / 정책 / 문서
- **상태:** 제안 / 코드작성 / 코드반영 / 실행검증 / 운영배포 / 보류
- **오류 코드·재현 조건:** `...` / `init=...`, 후보/Check
- **원인:** 확인된 사실과 가설 구분
- **수정 파일·프롬프트:** 구체 경로
- **예상 영향:** Step, 기존 판단/강수 최소기준/웹 이력
- **검증:** 구문/단위/회귀/실제 init/웹·화면
- **반례·잔여 위험:**
- **근거:** Git commit/PR, run_status·JSON·로그 요약, ZIP SHA256
```

## 3. 운영 코드에 변경 반영할 때

### 정식 실행과 부분 재개

- 정식 명령은 `ksh scripts/run_pipeline.ksh YYYYMMDDHH`이며 `HH`는 `00` 또는 `12`다. 날짜 생략 시 스크립트의 UTC 운영시간 규칙으로 init을 선택한다.
- 이 셸은 `--start-step`을 지원하지 않는다. 부분 재개는 `scripts/run_pipeline.ksh` 상단의 `STEP01`~`STEP10`을 `True`/`False`로 설정하고 같은 init을 전달한다. 재개할 단계의 입력 산출물과 설정이 남아 있는지 먼저 확인한다.
- 재개 범위에 맞춰 `WEB_PUBLISH`와 `CLEANUP`도 따로 설정한다. 산출물 보존·분석이력 확인이 목적이면 불필요한 웹 게시·정리를 끈다.
- 단계별 체크포인트는 입력 지문이 맞을 때 재사용된다. Step07 직접강수 Anchor 평가 범위·값 메타자료가 바뀌면 해당 Check 입력 지문이 달라져 재평가 대상이 된다.

Git 작업을 시작하기 전 현재 브랜치와 모든 미커밋 변경사항을 확인한다. 미커밋 변경이 있으면 `main`으로 전환하거나 초기화하지 말고, 해당 변경을 보존하는 작업 브랜치에서 계속한다. 새 브랜치가 필요하면 미커밋 변경을 포함한 현재 상태에서 분기한다.

```bash
cd /home/ubuntu/AIFSHeavyRain

git status --short
# 작업 트리가 깨끗한 경우에만 main을 동기화한다.
git switch main
git pull --ff-only origin main
git switch -c docs/history-YYYYMMDD

# README.md, CHANGELOG.md 및 docs/를 추가/수정한 후
git add README.md CHANGELOG.md docs/
git diff --cached --check
git diff --cached --stat
git diff --cached

git commit -m "Document development and validation history"
git push -u origin docs/history-YYYYMMDD
```

- 원격 `main`에 직접 push하지 않는다. Pull Request에서 날짜·확정 근거·대외 공개 정보·실제 변경 파일을 확인하고 별도로 병합한다.
- 현업 Ubuntu 서버의 `main`을 `git pull --ff-only origin main`으로 동기화.
- 파일을 덮어쓰기 전 사용자의 기존 README/CHANGELOG/docs 존재 여부를 확인. 중복·수동 작성 내용을 잃지 않도록 `git diff`로 검토.

## 4. 로그·실행 데이터 보안 규칙

- `envi/api_keys.env`, 과거 자격증명 백업, PAT, 비밀번호, 서명 URL, 정확한 서버 내부 경로가 담긴 개인 식별 정보는 공개 문서에 기록하지 않는다.
- GRIB/NetCDF, `dain/`, `daio/`, `daou/`, 모델 입력 전체, 대용량 PNG·로그 원본은 버전관리 제외.
- 오류 재현에 필요한 최소 JSON key·비식별 로그 행·해시만 남긴다.
- 업로드 ZIP 안에 인증설정이 포함된 경우 **ZIP 자체를 공개 Git에 커밋하지 않는다**.

## 5. 재검증 체크리스트

- [ ] git diff 및 구문 검사 통과
- [ ] JSON Schema·프롬프트 계약 유지
- [ ] Step04 직접강수 SYSTEM anchor/1.0mm/6h 기준 유지
- [ ] Step04 운영권역 정량범위가 없는 후보는 Check 복구와 후보 chunk 복구를 구분하고 원래 가설을 보존
- [ ] Step07 독립 판독 및 소수의견 처리 유지
- [ ] Step07 운영권역 없음·유효 강수값 없음·범위 메타자료 없음과 완전 평가된 임계치 미달을 구분
- [ ] Step07 불완전 평가에서 `not_observed`/`opposite_observed`를 거부하고 제한된 Check 재생성을 확인
- [ ] Step07 입력 지문 변경 시 해당 체크포인트가 재사용되지 않고 나머지 일치 Check는 재개 가능
- [ ] Step08~09 직접강수 평가불능을 강수 부재로 변환하지 않고 선행환경 상태와 분리
- [ ] Step08 근거 참조와 환경 지속성 판정이 일치
- [ ] Step09 차단 0(정상 사례), 반례 차단 테스트도 통과
- [ ] Step10 배지/설명/수치/지도·PNG·JS 동작 확인
- [ ] 동일 init 재생성 시 분석이력 보존
- [ ] `run_status`와 실제 산출물 해석이 일치
- [ ] 모든 운영정책·모델 설정 변경을 문서에 기록

## 6. 다음 릴리스 전 권장 작업

- 코드 계약을 설명하는 문서는 해당 소스 수정·회귀 테스트와 같은 PR에서 함께 검토한다. 서로 무관한 정비만 문서 전용 PR로 분리한다.
- 이전 P1 PR에 `tests/test_p1_contract_regression.py`가 포함되지 않았다는 이력은 유지하되, 현재 파일이 존재한다고 가정하지 않는다. 신규 회귀 테스트는 실제 수정 계약별 파일을 기준으로 확인한다.
- 태그는 `main`의 현재 커밋에 맞게 버전 명명 규칙을 확정한 다음에만 생성(임의로 이전 날짜에 소급 태그하지 않음).
- 날짜별 이력에는 `기상학적 판단의 정확도`와 `소프트웨어 실행 성공률`을 다른 검증 축으로 기록.

## 7. 제공된 체크섬 파일 갱신

현재 저장소에는 `DOC_SHA256SUMS.txt`가 추적되어 있지 않다. 외부 배포에 체크섬 목록을 함께 제공하는 경우에만 다음 명령으로 생성하고, GitHub 문서를 수정한 뒤에는 오래된 체크섬을 포함하지 않는다.

```bash
cd /home/ubuntu/AIFSHeavyRain
sha256sum README.md CHANGELOG.md docs/*.md > DOC_SHA256SUMS.txt
sha256sum -c DOC_SHA256SUMS.txt
```

위 명령은 프로젝트 루트에 이 문서 외 Markdown이 있으면 그 파일도 포함할 수 있다. 정확한 최초 문서 8개만 기록하려면 대상 파일 목록을 명시해야 한다.
