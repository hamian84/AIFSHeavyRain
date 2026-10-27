#!/usr/bin/ksh
#######################################################################
# Main Shell of AIFSHeavyRain                                         #
#                                                                     #
# Step01 ~ Step10 operational pipeline / web publication / cleanup    #
#######################################################################
#
# USAGE
#   ksh run_pipeline.ksh
#   ksh run_pipeline.ksh YYYYMMDDHH
#
#   - 날짜를 생략하면 운영 시각에 따라 INIT을 자동 선택합니다.
#   - 날짜를 지정할 경우 HH는 00 또는 12만 허용합니다.
#   - 수행 기능은 아래 전역 스위치에서 설정합니다.
#
set -e
set -u

#--------------------------------------------------------------------
#####################################################################
### DEFINE JOB SWITCH
#
# True  : 수행
# False : 미수행
#
export STEP01=True
export STEP02=True
export STEP03=True
export STEP04=True
export STEP05=True
export STEP06=True
export STEP07=True
export STEP08=True
export STEP09=True
export STEP10=True

export WEB_PUBLISH=True
export CLEANUP=True

#--------------------------------------------------------------------
#####################################################################
### DEFINE DIRECTORY
#
SCRIPT_LOCATION=$(cd "$(dirname "$0")" && pwd)
PIPELINE_ENV=${AIFS_PIPELINE_ENV:-"${SCRIPT_LOCATION}/../envi/pipeline.env"}

if [ ! -f "${PIPELINE_ENV}" ]; then
    echo "ERROR: pipeline.env not found: ${PIPELINE_ENV}"
    exit 2
fi

. "${PIPELINE_ENV}"

# pipeline.env 값을 Python subprocess에도 전달합니다.
export AIFS_ROOT AIFS_CONDA_ROOT AIFS_CONDA_ENV AIFS_PYTHON
export AIFS_SCRIPT_DIR AIFS_FUNC_DIR
export AIFS_DAIN_DIR AIFS_DAIO_DIR AIFS_DAOU_DIR
export AIFS_DABA_DIR AIFS_PROMPTS_DIR AIFS_ENVI_DIR
export AIFS_LOG_DIR AIFS_TMP_DIR AIFS_API_ENV_FILE AIFS_WEB_ROOT

#--------------------------------------------------------------------
#####################################################################
### CHECK JOBTIME
#
usage()
{
    echo "Usage: $0 [YYYYMMDDHH]"
}

validate_switch()
{
    case "$2" in
        True|False) ;;
        *)
            echo "ERROR: $1 must be True or False: $2"
            exit 2
            ;;
    esac
}

select_auto_init()
{
    AUTO_DATE_UTC=$(/usr/bin/date -u '+%Y%m%d')
    AUTO_HOUR_UTC=$(/usr/bin/date -u '+%H')
    AUTO_HOUR_NUM="${AUTO_HOUR_UTC#0}"
    [ -n "${AUTO_HOUR_NUM}" ] || AUTO_HOUR_NUM=0

    # 운영 기준
    #   00:00~07:59 UTC -> 전일 12 UTC
    #   08:00~19:59 UTC -> 당일 00 UTC
    #   20:00~23:59 UTC -> 당일 12 UTC
    if [ "${AUTO_HOUR_NUM}" -ge 20 ]; then
        INIT="${AUTO_DATE_UTC}12"
    elif [ "${AUTO_HOUR_NUM}" -ge 8 ]; then
        INIT="${AUTO_DATE_UTC}00"
    else
        INIT="$(/usr/bin/date -u -d 'yesterday' '+%Y%m%d')12"
    fi

    INIT_MODE=auto
}

validate_switch STEP01 "${STEP01}"
validate_switch STEP02 "${STEP02}"
validate_switch STEP03 "${STEP03}"
validate_switch STEP04 "${STEP04}"
validate_switch STEP05 "${STEP05}"
validate_switch STEP06 "${STEP06}"
validate_switch STEP07 "${STEP07}"
validate_switch STEP08 "${STEP08}"
validate_switch STEP09 "${STEP09}"
validate_switch STEP10 "${STEP10}"
validate_switch WEB_PUBLISH "${WEB_PUBLISH}"
validate_switch CLEANUP "${CLEANUP}"

case "$#" in
    0)
        select_auto_init
        ;;
    1)
        INIT="$1"
        INIT_MODE=explicit
        ;;
    *)
        usage
        exit 2
        ;;
esac

if ! echo "${INIT}" | grep -Eq '^[0-9]{8}(00|12)$'; then
    echo "ERROR: INIT must be YYYYMMDD00 or YYYYMMDD12: ${INIT}"
    exit 2
fi

INIT_DATE="${INIT%??}"
if [ "$(/usr/bin/date -u -d "${INIT_DATE}" '+%Y%m%d' 2>/dev/null || true)" != "${INIT_DATE}" ]; then
    echo "ERROR: invalid INIT date: ${INIT}"
    exit 2
fi

export INIT INIT_MODE

#--------------------------------------------------------------------
#####################################################################
### CHECK MINIMUM ENVIRONMENT
#
if [ ! -d "${AIFS_ROOT}" ]; then
    echo "ERROR: AIFS root not found: ${AIFS_ROOT}"
    exit 2
fi

if [ ! -x "${AIFS_PYTHON}" ]; then
    echo "ERROR: Python executable not found: ${AIFS_PYTHON}"
    exit 2
fi

if [ "${STEP04}" = "True" ] || [ "${STEP07}" = "True" ] || [ "${STEP08}" = "True" ]; then
    if [ ! -f "${AIFS_API_ENV_FILE}" ]; then
        echo "ERROR: api_keys.env not found: ${AIFS_API_ENV_FILE}"
        exit 2
    fi
fi

mkdir -p "${AIFS_LOG_DIR}/${INIT}" "${AIFS_TMP_DIR}"
PIPELINE_LOG="${AIFS_LOG_DIR}/${INIT}/pipeline.log"

#--------------------------------------------------------------------
#####################################################################
### DEFINE COMMON FUNCTION
#
log()
{
    MESSAGE="$(TZ=UTC date '+%Y-%m-%d %H:%M:%S UTC') $*"
    echo "${MESSAGE}"
    echo "${MESSAGE}" >> "${PIPELINE_LOG}"
}

run_step()
{
    STEP_NAME="$1"
    shift

    log "${STEP_NAME}_START init=${INIT}"

    if "$@"; then
        log "${STEP_NAME}_OK init=${INIT}"
    else
        RC=$?
        log "${STEP_NAME}_FAIL init=${INIT} exit_code=${RC}"
        log "PIPELINE_FAIL init=${INIT} failed_step=${STEP_NAME}"
        exit "${RC}"
    fi
}

run_enabled()
{
    ENABLED="$1"
    STEP_NAME="$2"
    shift 2

    [ "${ENABLED}" = "True" ] || return 0
    run_step "${STEP_NAME}" "$@"
}

web_exec()
{
    if [ "${WEB_COPY_MODE}" = "sudo" ]; then
        sudo -n "$@"
    else
        "$@"
    fi
}

publish_web()
{
    WEB_SOURCE_DIR="${AIFS_DAOU_DIR}/${INIT}"
    WEB_RELEASE_ROOT="${AIFS_WEB_ROOT}/.releases"
    WEB_BUILD_ID="${INIT}_$(date -u +%Y%m%dT%H%M%SZ)_$$"
    WEB_RELEASE_DIR="${WEB_RELEASE_ROOT}/${WEB_BUILD_ID}"
    WEB_TARGET_LINK="${AIFS_WEB_ROOT}/${INIT}"
    WEB_TARGET_TMP="${AIFS_WEB_ROOT}/.run_${INIT}_$$"
    WEB_TARGET_BACKUP="${AIFS_WEB_ROOT}/.legacy_${INIT}_$$"
    WEB_LATEST_LINK="${AIFS_WEB_ROOT}/latest"
    WEB_LATEST_TMP="${AIFS_WEB_ROOT}/.latest_${INIT}_$$"
    WEB_ROOT_INDEX="${AIFS_WEB_ROOT}/index.html"
    WEB_ROOT_INDEX_TMP="${AIFS_TMP_DIR}/web_root_index_${INIT}_$$.html"

    # Step10 최종 산출물 최소 계약만 확인합니다.
    [ -f "${WEB_SOURCE_DIR}/index.html" ] || { echo "ERROR: index.html not found: ${WEB_SOURCE_DIR}"; return 2; }
    [ -f "${WEB_SOURCE_DIR}/forecaster_product.json" ] || { echo "ERROR: forecaster_product.json not found: ${WEB_SOURCE_DIR}"; return 2; }
    [ -d "${WEB_SOURCE_DIR}/assets" ] || { echo "ERROR: assets not found: ${WEB_SOURCE_DIR}"; return 2; }

    if [ -w "${AIFS_WEB_ROOT}" ]; then
        WEB_COPY_MODE=direct
    elif command -v sudo >/dev/null 2>&1 && sudo -n test -w "${AIFS_WEB_ROOT}" >/dev/null 2>&1; then
        WEB_COPY_MODE=sudo
    else
        echo "ERROR: web root is not writable: ${AIFS_WEB_ROOT}"
        return 2
    fi

    # 사용자가 보고 있는 live 디렉터리를 직접 덮어쓰지 않습니다.
    # 완성된 release를 별도 디렉터리에 만든 후 검증이 끝났을 때만 링크를 전환합니다.
    web_exec mkdir -p "${WEB_RELEASE_ROOT}" || return 2
    web_exec rm -rf "${WEB_RELEASE_DIR}" >/dev/null 2>&1 || true
    web_exec mkdir -p "${WEB_RELEASE_DIR}" || return 2
    if ! web_exec cp -a "${WEB_SOURCE_DIR}/." "${WEB_RELEASE_DIR}/"; then
        web_exec rm -rf "${WEB_RELEASE_DIR}" >/dev/null 2>&1 || true
        return 2
    fi

    if ! "${AIFS_PYTHON}" "${AIFS_SCRIPT_DIR}/validate_web_release.py" --release-dir "${WEB_RELEASE_DIR}"; then
        web_exec rm -rf "${WEB_RELEASE_DIR}" >/dev/null 2>&1 || true
        echo "ERROR: staged web release validation failed: ${WEB_RELEASE_DIR}"
        return 2
    fi

    cat > "${WEB_ROOT_INDEX_TMP}" <<EOF_WEB
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta http-equiv="cache-control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="pragma" content="no-cache">
<meta http-equiv="expires" content="0">
<meta http-equiv="refresh" content="0; url=/latest/">
<title>AIFSHeavyRain</title>
<script>location.replace("/latest/");</script>
</head>
<body>
<a href="/latest/">최신 AIFSHeavyRain 결과로 이동</a>
</body>
</html>
EOF_WEB
    if [ $? -ne 0 ]; then
        web_exec rm -rf "${WEB_RELEASE_DIR}" >/dev/null 2>&1 || true
        echo "ERROR: failed to create temporary web root index: ${WEB_ROOT_INDEX_TMP}"
        return 2
    fi
    if ! web_exec cp -f "${WEB_ROOT_INDEX_TMP}" "${WEB_ROOT_INDEX}"; then
        rm -f "${WEB_ROOT_INDEX_TMP}" >/dev/null 2>&1 || true
        web_exec rm -rf "${WEB_RELEASE_DIR}" >/dev/null 2>&1 || true
        return 2
    fi
    rm -f "${WEB_ROOT_INDEX_TMP}" >/dev/null 2>&1 || true

    # /latest는 검증 완료된 release를 직접 가리키게 하여 동일 init 재게시 중에도
    # 기존 날짜 디렉터리 덮어쓰기 때문에 이미지가 일시적으로 깨지는 구간을 없앱니다.
    web_exec rm -f "${WEB_LATEST_TMP}" >/dev/null 2>&1 || true
    web_exec ln -s "${WEB_RELEASE_DIR}" "${WEB_LATEST_TMP}" || return 2
    if ! web_exec mv -Tf "${WEB_LATEST_TMP}" "${WEB_LATEST_LINK}"; then
        web_exec rm -f "${WEB_LATEST_TMP}" >/dev/null 2>&1 || true
        return 2
    fi

    # 날짜별 주소도 같은 release를 가리킵니다. 과거 직접-copy 디렉터리는 한 번만
    # 안전하게 치운 뒤 symlink 구조로 마이그레이션합니다. latest는 이미 새 release를
    # 가리키므로 이 짧은 마이그레이션 구간에도 최신 페이지는 영향을 받지 않습니다.
    web_exec rm -f "${WEB_TARGET_TMP}" >/dev/null 2>&1 || true
    web_exec ln -s "${WEB_RELEASE_DIR}" "${WEB_TARGET_TMP}" || return 2
    if web_exec test -d "${WEB_TARGET_LINK}" && ! web_exec test -L "${WEB_TARGET_LINK}"; then
        web_exec rm -rf "${WEB_TARGET_BACKUP}" >/dev/null 2>&1 || true
        if ! web_exec mv "${WEB_TARGET_LINK}" "${WEB_TARGET_BACKUP}"; then
            web_exec rm -f "${WEB_TARGET_TMP}" >/dev/null 2>&1 || true
            return 2
        fi
        if ! web_exec mv -Tf "${WEB_TARGET_TMP}" "${WEB_TARGET_LINK}"; then
            web_exec mv "${WEB_TARGET_BACKUP}" "${WEB_TARGET_LINK}" >/dev/null 2>&1 || true
            return 2
        fi
        web_exec rm -rf "${WEB_TARGET_BACKUP}" >/dev/null 2>&1 || true
    else
        if ! web_exec mv -Tf "${WEB_TARGET_TMP}" "${WEB_TARGET_LINK}"; then
            web_exec rm -f "${WEB_TARGET_TMP}" >/dev/null 2>&1 || true
            return 2
        fi
    fi

    # 동일 init의 오래된 release는 최종 daou가 영구 보존되므로 웹 중복본으로 남길
    # 필요가 없습니다. 현재 활성 release 이외의 동일-init release만 정리합니다.
    web_exec find "${WEB_RELEASE_ROOT}" -mindepth 1 -maxdepth 1 -type d -name "${INIT}_*" ! -path "${WEB_RELEASE_DIR}" -exec rm -rf {} + >/dev/null 2>&1 || true

    log "WEB_RELEASE=${WEB_RELEASE_DIR}"
    log "WEB_PRODUCT=${WEB_TARGET_LINK}/index.html"
}

run_cleanup()
{
    log "RUNTIME_CLEANUP_START init=${INIT} retention_days=7"

    if "${AIFS_PYTHON}" "${AIFS_SCRIPT_DIR}/cleanup_runtime_data.py" \
        --root "${AIFS_ROOT}" \
        --web-root "${AIFS_WEB_ROOT}" \
        --retention-days 7 \
        --current-init "${INIT}"; then
        log "RUNTIME_CLEANUP_OK init=${INIT} retention_days=7"
    else
        RC=$?
        log "RUNTIME_CLEANUP_WARNING init=${INIT} exit_code=${RC} retention_days=7"
    fi
}

#--------------------------------------------------------------------
#####################################################################
### MAIN JOB PROCESS
#
log "PIPELINE_START init=${INIT} init_mode=${INIT_MODE}"
log "SWITCH STEP01=${STEP01} STEP02=${STEP02} STEP03=${STEP03} STEP04=${STEP04} STEP05=${STEP05} STEP06=${STEP06} STEP07=${STEP07} STEP08=${STEP08} STEP09=${STEP09} STEP10=${STEP10} WEB_PUBLISH=${WEB_PUBLISH} CLEANUP=${CLEANUP}"

cd "${AIFS_ROOT}"

# Step01: AIFS 원자료 수집
run_enabled "${STEP01}" "STEP01" \
    "${AIFS_PYTHON}" "${AIFS_SCRIPT_DIR}/01_collect_aifs.py" \
    --init "${INIT}" \
    --root "${AIFS_ROOT}" \
    --namelist "${AIFS_DABA_DIR}/collection/aifs_download_namelist.yaml"

# Step02: Pass1 개요자료/이미지 생성
run_enabled "${STEP02}" "STEP02" \
    "${AIFS_PYTHON}" "${AIFS_SCRIPT_DIR}/02_build_pass1_overview.py" \
    --init "${INIT}" \
    --root "${AIFS_ROOT}" \
    --raw-dir "${AIFS_DAIN_DIR}/${INIT}/raw/aifs"

# Step03: Pass1 요청 패키지 생성
run_enabled "${STEP03}" "STEP03" \
    "${AIFS_PYTHON}" "${AIFS_SCRIPT_DIR}/03_build_pass1_request.py" \
    --init "${INIT}" \
    --root "${AIFS_ROOT}"

# Step04: 1차 AI 분석 및 후보/후속요청 생성
run_enabled "${STEP04}" "STEP04" \
    "${AIFS_PYTHON}" "${AIFS_SCRIPT_DIR}/04_run_ai_pass1.py" \
    --init "${INIT}" \
    --root "${AIFS_ROOT}" \
    --env-file "${AIFS_API_ENV_FILE}"

# Step05: 요청된 상세 진단자료/이미지 생성
run_enabled "${STEP05}" "STEP05" \
    "${AIFS_PYTHON}" "${AIFS_SCRIPT_DIR}/05_run_step05.py" \
    --init "${INIT}" \
    --root "${AIFS_ROOT}" \
    --fail-fast

# Step06: 2차 분석 입력/evidence 패키지 생성
run_enabled "${STEP06}" "STEP06" \
    "${AIFS_PYTHON}" "${AIFS_SCRIPT_DIR}/06_build_pass2_package.py" \
    --init "${INIT}"

# Step07: VLM Check 판독
run_enabled "${STEP07}" "STEP07" \
    "${AIFS_PYTHON}" "${AIFS_SCRIPT_DIR}/07_run_pass2_checks.py" \
    --init "${INIT}" \
    --env-file "${AIFS_API_ENV_FILE}"

# Step08: Candidate 종합 분석
run_enabled "${STEP08}" "STEP08" \
    "${AIFS_PYTHON}" "${AIFS_SCRIPT_DIR}/08_run_pass2_synthesis.py" \
    --init "${INIT}" \
    --env-file "${AIFS_API_ENV_FILE}"

# Step09: 운영 검증 및 표출 여부 결정
run_enabled "${STEP09}" "STEP09" \
    "${AIFS_PYTHON}" "${AIFS_SCRIPT_DIR}/09_finalize_pass2.py" \
    --init "${INIT}"

# Step10: 예보관용 최종 산출물 생성
run_enabled "${STEP10}" "STEP10" \
    "${AIFS_PYTHON}" "${AIFS_SCRIPT_DIR}/10_run_forecaster_product.py" \
    --init "${INIT}"

if [ "${WEB_PUBLISH}" = "True" ]; then
    run_step "WEB_PUBLISH" publish_web
fi

if [ "${CLEANUP}" = "True" ]; then
    run_cleanup
fi

log "PIPELINE_OK init=${INIT}"

#####################################################################
## END
exit 0
