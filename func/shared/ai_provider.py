"""Provider adapters for Codex CLI, OpenAI Responses, and compatible endpoints.

Only this module knows provider-specific HTTP and SDK contracts.  Callers receive a
sanitized :class:`ProviderResponse`; raw response bodies, authorization headers, and
full request payloads are deliberately never retained here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import shutil
import subprocess
import tempfile
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from func.shared.env_loader import ProjectEnvLoad


OFFICIAL_OPENAI_BASE_URL = "https://api.openai.com/v1"
RETRYABLE_HTTP_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
IMAGE_DETAILS = frozenset({"auto", "low", "high", "original"})
SUPPORTED_PROVIDERS = frozenset({"codex_cli", "openai", "openai_compatible"})
REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})
CODEX_DIAGNOSTIC_MAX_BYTES = 1024 * 1024


class ProviderError(RuntimeError):
    """Sanitized provider failure suitable for normal logs and failure summaries."""


class ProviderIncompleteError(ProviderError):
    """A completed HTTP call whose provider generation stopped before completion.

    ``diagnostics`` deliberately contains scalar metadata only.  It must never hold
    provider output, prompt text, request payloads, credentials, or headers.
    """

    def __init__(self, message: str, *, diagnostics: dict[str, Any]) -> None:
        super().__init__(message)
        self.diagnostics = copy.deepcopy(diagnostics)


class ProviderConfigurationError(ProviderError):
    """The requested provider/model combination is not an approved capability."""


def _redact_codex_diagnostic_text(text: str) -> str:
    """Redact common credential shapes before persisting Codex CLI stderr."""
    redacted = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)[^\s'\";,]+",
        r"\1[REDACTED]",
        text,
    )
    redacted = re.sub(
        r"(?i)\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}\b",
        "[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)\s*[=:]\s*)[^\s'\";,]+",
        r"\1[REDACTED]",
        redacted,
    )
    return redacted


def _persist_codex_failure_stderr(stderr: bytes, *, attempt: int) -> str | None:
    """Persist bounded, redacted stderr outside normal logs when explicitly enabled.

    The directory is supplied by the Step04 wrapper through
    ``AIFS_CODEX_DIAGNOSTIC_DIR``.  These files are operational diagnostics and
    are never copied into the forecaster-facing Step10 web product.
    """
    directory_raw = os.getenv("AIFS_CODEX_DIAGNOSTIC_DIR", "").strip()
    if not directory_raw:
        return None
    directory = Path(directory_raw)
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        bounded = stderr[:CODEX_DIAGNOSTIC_MAX_BYTES]
        text = bounded.decode("utf-8", errors="replace")
        if len(stderr) > len(bounded):
            text += (
                f"\n[AIFS_DIAGNOSTIC_TRUNCATED original_bytes={len(stderr)} "
                f"kept_bytes={len(bounded)}]\n"
            )
        text = _redact_codex_diagnostic_text(text)
        stamp = f"{time.time_ns()}_{os.getpid()}_attempt{int(attempt) + 1}"
        path = directory / f"codex_cli_failure_{stamp}.stderr.log"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path.name
    except OSError as exc:
        logging.warning(
            "provider diagnostic persistence failed provider=codex_cli error=%s",
            type(exc).__name__,
        )
        return None


class ProviderHTTPError(ProviderError):
    def __init__(
        self,
        status: int,
        body: str,
        *,
        message: str | None = None,
        body_bytes: int | None = None,
        body_sha256_prefix: str | None = None,
        diagnostic_fields: dict[str, str] | None = None,
    ) -> None:
        self.status = int(status)
        # Kept only in process for bounded provider error classification.  It is never
        # included in __str__, logs, checkpoints, or normal artifacts.
        self._body_for_classification = body
        encoded = body.encode("utf-8", errors="replace")
        self.body_bytes = len(encoded) if body_bytes is None else int(body_bytes)
        self.body_sha256_prefix = (
            hashlib.sha256(encoded).hexdigest()[:12]
            if body_sha256_prefix is None
            else str(body_sha256_prefix)
        )
        self.diagnostic_fields = dict(diagnostic_fields or {})
        diagnostics = "".join(
            f" provider_error_{name}={value}"
            for name, value in sorted(self.diagnostic_fields.items())
        )
        super().__init__(
            message
            or (
                f"provider HTTP status={self.status} body_bytes={self.body_bytes} "
                f"body_sha256_prefix={self.body_sha256_prefix}{diagnostics}"
            )
        )


@dataclass(frozen=True)
class ModelCapabilities:
    responses_api: bool
    image_input: bool
    structured_outputs: bool
    reasoning: bool
    reasoning_efforts: frozenset[str]
    image_details: frozenset[str]


@dataclass(frozen=True)
class ResolvedSetting:
    value: str
    source: str


@dataclass(frozen=True)
class ResolvedAISettings:
    provider: ResolvedSetting
    model: ResolvedSetting
    image_detail: ResolvedSetting
    reasoning_efforts: dict[str, ResolvedSetting]

    def sources(self) -> dict[str, str]:
        return {
            "provider": self.provider.source,
            "model": self.model.source,
            "image_detail": self.image_detail.source,
            **{
                f"reasoning_effort_{stage}": item.source
                for stage, item in self.reasoning_efforts.items()
            },
        }


# Model IDs are configured only in envi/api_keys.env.  Capability validation is
# therefore contract-based rather than an in-code model-ID allowlist: changing a
# production model must not require a Python edit.  The configured model still has
# to support the capabilities the pipeline actually uses; provider-side rejection
# remains a hard execution failure if it does not.
_PIPELINE_OPENAI_CAPABILITIES = ModelCapabilities(
    responses_api=True,
    image_input=True,
    structured_outputs=True,
    reasoning=True,
    reasoning_efforts=frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"}),
    image_details=IMAGE_DETAILS,
)


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    model: str
    retry_count: int
    elapsed_seconds: float
    usage: dict[str, Any] | None = None
    provider: str = "unknown"
    api_family: str = "unknown"
    response_id: str | None = None
    request_id: str | None = None
    status: str | None = None
    finish_reason: str | None = None
    incomplete_reason: str | None = None
    refusal: str | None = None
    structured_output_used: bool = False
    provider_extensions_used: bool = False
    structured_output_fallback_count: int = 0
    attempt_usage: tuple[dict[str, Any] | None, ...] = ()

    def compact_envelope(self) -> dict[str, Any]:
        """Return only non-sensitive response diagnostics used by legacy callers."""
        return {
            "id": self.response_id,
            "model": self.model,
            "usage": self.usage,
            "finish_reason": self.finish_reason,
            "status": self.status,
            "incomplete_reason": self.incomplete_reason,
        }


class AIProvider(Protocol):
    provider_name: str
    api_family: str
    endpoint_id: str

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        schema: dict[str, Any] | None = None,
        schema_name: str = "response",
        structured_output_mode: str = "auto",
        enable_thinking: bool = False,
        temperature: float = 0.0,
        top_p: float = 1.0,
        image_detail: str = "original",
        reasoning_effort: str | None = None,
    ) -> ProviderResponse: ...


def normalized_endpoint_id(base_url: str, *, official: bool = False) -> str:
    """Avoid persisting full endpoints, paths, queries, or credentials in checkpoints."""
    if official:
        return "openai_official"
    parsed = urllib.parse.urlsplit(base_url)
    normalized = urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))
    return "custom_sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def provider_defaults(provider: str) -> dict[str, str]:
    if provider == "codex_cli":
        return {
            "api_key_env": "",
            "model_env": "AIFS_MODEL_STEP04",
            "default_model": "",
            "base_url_env": "",
            "default_base_url": "codex_cli",
        }
    if provider == "openai":
        return {
            "api_key_env": "OPENAI_API_KEY",
            "model_env": "AIFS_MODEL_STEP04",
            "default_model": "",
            "base_url_env": "",
            "default_base_url": OFFICIAL_OPENAI_BASE_URL,
        }
    if provider == "openai_compatible":
        return {
            "api_key_env": "OPENAI_COMPATIBLE_API_KEY",
            "model_env": "AIFS_MODEL_STEP04",
            "default_model": "",
            "base_url_env": "OPENAI_COMPATIBLE_BASE_URL",
            "default_base_url": "",
        }
    raise ProviderConfigurationError(f"unsupported provider: {provider}")


def _resolve_setting(
    *,
    cli_value: str | None,
    env_name: str | None,
    env_names: tuple[str, ...] | None = None,
    default: str,
    env_load: ProjectEnvLoad | None,
) -> ResolvedSetting:
    if cli_value is not None:
        value = str(cli_value).strip()
        if value:
            return ResolvedSetting(value, "cli")
    names = env_names if env_names is not None else ((env_name,) if env_name else ())
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return ResolvedSetting(value, env_load.source_for(name) if env_load else "process_env")
    return ResolvedSetting(default, "default")


def resolve_ai_settings(
    *,
    cli_provider: str | None,
    cli_model: str | None,
    cli_image_detail: str | None,
    cli_reasoning_efforts: dict[str, str | None],
    reasoning_defaults: dict[str, str],
    env_load: ProjectEnvLoad | None,
    model_env_names: tuple[str, ...] | None = None,
) -> ResolvedAISettings:
    provider = _resolve_setting(
        cli_value=cli_provider,
        env_name="AIFS_AI_PROVIDER",
        default="openai",
        env_load=env_load,
    )
    if provider.value not in SUPPORTED_PROVIDERS:
        allowed = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ProviderConfigurationError(
            f"unsupported AI provider {provider.value!r}; allowed values: {allowed}"
        )
    defaults = provider_defaults(provider.value)
    resolved_model_env_names = model_env_names or (defaults["model_env"],)
    model = _resolve_setting(
        cli_value=cli_model,
        env_name=None,
        env_names=resolved_model_env_names,
        default=defaults["default_model"],
        env_load=env_load,
    )
    if not model.value:
        raise ProviderConfigurationError(
            f"{provider.value} requires model configuration in envi/api_keys.env ({', '.join(resolved_model_env_names)})"
        )
    image_detail = _resolve_setting(
        cli_value=cli_image_detail,
        env_name="OPENAI_IMAGE_DETAIL" if provider.value == "openai" else None,
        default="original",
        env_load=env_load,
    )
    if image_detail.value not in IMAGE_DETAILS:
        allowed = ", ".join(sorted(IMAGE_DETAILS))
        raise ProviderConfigurationError(
            f"unsupported image detail {image_detail.value!r}; allowed values: {allowed}"
        )
    reasoning_efforts: dict[str, ResolvedSetting] = {}
    for stage, default in reasoning_defaults.items():
        env_names: tuple[str, ...] = ()
        if provider.value == "openai":
            env_names = (f"OPENAI_REASONING_EFFORT_{stage.upper()}",)
            if stage in {"pass2_check", "pass2_synthesis"}:
                env_names += ("OPENAI_REASONING_EFFORT_PASS2",)
        elif provider.value == "codex_cli":
            env_names = (f"CODEX_REASONING_EFFORT_{stage.upper()}",)
            if stage in {"pass2_check", "pass2_synthesis"}:
                env_names += ("CODEX_REASONING_EFFORT_PASS2",)
        resolved = _resolve_setting(
            cli_value=cli_reasoning_efforts.get(stage),
            env_name=None,
            env_names=env_names,
            default=default,
            env_load=env_load,
        )
        if resolved.value not in REASONING_EFFORTS:
            allowed = ", ".join(sorted(REASONING_EFFORTS))
            raise ProviderConfigurationError(
                f"unsupported reasoning effort {resolved.value!r} for {stage}; allowed values: {allowed}"
            )
        reasoning_efforts[stage] = resolved
    return ResolvedAISettings(provider, model, image_detail, reasoning_efforts)


def resolve_model(*, provider: str, cli_model: str | None) -> str:
    defaults = provider_defaults(provider)
    model = (cli_model or os.getenv(defaults["model_env"]) or "").strip()
    if not model:
        raise ProviderConfigurationError(
            f"{provider} requires model configuration in envi/api_keys.env ({defaults['model_env']})"
        )
    return model


def validate_provider_api_key(*, provider: str, api_key_env: str, api_key: str | None) -> str:
    """Reject absent or template OpenAI credentials without revealing their value."""
    value = (api_key or "").strip()
    if not value:
        raise ProviderConfigurationError(f"required provider environment variable is not set: {api_key_env}")
    if provider != "openai":
        return value
    normalized = value.casefold()
    placeholder_markers = ("test", "placeholder", "your_api_key", "actual_api_key", "여기에", "입력")
    if normalized in {"test", "placeholder", "your_api_key"} or any(marker in normalized for marker in placeholder_markers[3:]):
        raise ProviderConfigurationError(
            "OPENAI_API_KEY is missing or a placeholder; configure a valid key before an API execution"
        )
    return value


def resolve_base_url(*, provider: str, cli_base_url: str | None, cli_openai_base_url: str | None = None) -> str:
    defaults = provider_defaults(provider)
    if provider == "codex_cli":
        return "codex_cli"
    if provider == "openai":
        # Do not inherit OPENAI_BASE_URL.  A non-official route must be an explicit CLI choice.
        return (cli_openai_base_url or OFFICIAL_OPENAI_BASE_URL).strip().rstrip("/")
    configured = cli_base_url or (os.getenv(defaults["base_url_env"]) if defaults["base_url_env"] else None) or defaults["default_base_url"]
    if not configured:
        raise ProviderConfigurationError(f"{provider} requires --base-url or {defaults['base_url_env']}")
    return str(configured).strip().rstrip("/")


def validate_openai_capabilities(
    *,
    model: str,
    require_images: bool,
    require_structured_outputs: bool,
    image_detail: str,
    reasoning_efforts: Iterable[str | None],
) -> ModelCapabilities:
    if not str(model).strip():
        raise ProviderConfigurationError("OpenAI model is empty; configure AIFS_MODEL_STEP04/AIFS_MODEL_STEP07/AIFS_MODEL_STEP08 in envi/api_keys.env")
    capability = _PIPELINE_OPENAI_CAPABILITIES
    if not capability.responses_api:
        raise ProviderConfigurationError(f"OpenAI model {model!r} does not support the Responses API")
    if require_images and not capability.image_input:
        raise ProviderConfigurationError(f"OpenAI model {model!r} does not support image input")
    if require_structured_outputs and not capability.structured_outputs:
        raise ProviderConfigurationError(f"OpenAI model {model!r} does not support strict Structured Outputs")
    if image_detail not in IMAGE_DETAILS:
        raise ProviderConfigurationError(f"unsupported image detail: {image_detail!r}")
    if require_images and image_detail not in capability.image_details:
        raise ProviderConfigurationError(f"OpenAI model {model!r} does not support image detail {image_detail!r}")
    for effort in reasoning_efforts:
        if effort is None:
            continue
        if not capability.reasoning:
            raise ProviderConfigurationError(f"OpenAI model {model!r} does not support reasoning settings")
        if effort not in capability.reasoning_efforts:
            raise ProviderConfigurationError(
                f"OpenAI model {model!r} does not support reasoning effort {effort!r}"
            )
    return capability


def build_chat_completions_http_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    response_schema: dict[str, Any] | None,
    schema_name: str,
    structured_output: bool,
    enable_thinking: bool,
    extensions: bool,
    max_tokens: int,
    temperature: float,
    top_p: float,
) -> dict[str, Any]:
    """Build the legacy Chat Completions request shape used for payload diagnostics."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "top_p": float(top_p),
        "stream": False,
    }
    if structured_output and response_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": response_schema},
        }
    if extensions:
        payload["chat_template_kwargs"] = {"enable_thinking": bool(enable_thinking)}
        payload["include_reasoning"] = False
    return payload


def normalized_chat_completions_request_shape(payload: dict[str, Any]) -> dict[str, Any]:
    """Stable regression-test representation without prompt text, base64, or credentials."""
    return {
        "model": payload.get("model"),
        "max_tokens": payload.get("max_tokens"),
        "temperature": payload.get("temperature"),
        "top_p": payload.get("top_p"),
        "stream": payload.get("stream"),
        "message_roles": [item.get("role") for item in payload.get("messages", []) if isinstance(item, dict)],
        "response_format_type": payload.get("response_format", {}).get("type") if isinstance(payload.get("response_format"), dict) else None,
        "response_schema_name": payload.get("response_format", {}).get("json_schema", {}).get("name") if isinstance(payload.get("response_format"), dict) else None,
        "thinking_enabled": payload.get("chat_template_kwargs", {}).get("enable_thinking") if isinstance(payload.get("chat_template_kwargs"), dict) else None,
        "include_reasoning": payload.get("include_reasoning"),
    }


def _retry_after_seconds(headers: Any) -> float | None:
    try:
        value = headers.get("Retry-After")
    except Exception:  # noqa: BLE001
        return None
    try:
        return max(0.0, float(value)) if value else None
    except (TypeError, ValueError):
        return None


def _error_mentions(body: str, terms: Iterable[str]) -> bool:
    lowered = body.lower()
    return any(term.lower() in lowered for term in terms)


def _normalise_usage(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    value = usage if isinstance(usage, dict) else {
        name: getattr(usage, name, None)
        for name in ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens", "input_tokens_details", "output_tokens_details")
    }
    input_details = value.get("input_tokens_details") if isinstance(value, dict) else None
    output_details = value.get("output_tokens_details") if isinstance(value, dict) else None
    if input_details is not None and not isinstance(input_details, dict):
        input_details = {
            "cached_tokens": getattr(input_details, "cached_tokens", None),
            "cache_write_tokens": getattr(input_details, "cache_write_tokens", None),
            "cache_creation_tokens": getattr(input_details, "cache_creation_tokens", None),
        }
    if output_details is not None and not isinstance(output_details, dict):
        output_details = {"reasoning_tokens": getattr(output_details, "reasoning_tokens", None)}

    def number(*names: str) -> int | None:
        for name in names:
            candidate = value.get(name) if isinstance(value, dict) else None
            if isinstance(candidate, int):
                return candidate
        return None

    def detail_number(details: Any, *names: str) -> int | None:
        if not isinstance(details, dict):
            return None
        for name in names:
            candidate = details.get(name)
            if isinstance(candidate, int):
                return candidate
        return None

    return {
        "input_tokens": number("input_tokens", "prompt_tokens"),
        "cached_input_tokens": detail_number(input_details, "cached_tokens"),
        "cache_write_tokens": detail_number(
            input_details, "cache_write_tokens", "cache_creation_tokens"
        ),
        "output_tokens": number("output_tokens", "completion_tokens"),
        "reasoning_tokens": detail_number(output_details, "reasoning_tokens"),
        "total_tokens": number("total_tokens"),
    }


def _safe_partial_output_metadata(response: Any, partial_content: str) -> dict[str, Any]:
    """Measure a partial response without retaining any response text."""
    encoded = partial_content.encode("utf-8", errors="replace")
    output = getattr(response, "output", None)
    rows = output if isinstance(output, (list, tuple)) else []
    item_types = [
        value
        for item in rows
        if isinstance((value := _safe_provider_error_field(getattr(item, "type", None))), str)
    ]
    return {
        "partial_output_present": bool(partial_content.strip()),
        "partial_output_utf8_bytes": len(encoded),
        "partial_output_characters": len(partial_content),
        "partial_output_sha256_prefix": hashlib.sha256(encoded).hexdigest()[:12],
        "output_item_count": len(rows),
        "output_item_types": item_types,
    }


def classify_incomplete_diagnostics(diagnostics: dict[str, Any]) -> str:
    """Return an operational estimate, never a definitive provider root cause."""
    output_tokens = diagnostics.get("output_tokens")
    reasoning_tokens = diagnostics.get("reasoning_tokens")
    partial_present = diagnostics.get("partial_output_present") is True
    partial_bytes = diagnostics.get("partial_output_utf8_bytes")
    if isinstance(output_tokens, int) and output_tokens > 0 and isinstance(reasoning_tokens, int):
        if reasoning_tokens * 4 >= output_tokens * 3 and not partial_present:
            return "reasoning_budget_exhausted"
        if reasoning_tokens * 10 <= output_tokens and partial_present and isinstance(partial_bytes, int) and partial_bytes > 0:
            return "visible_structured_output_expansion"
        if reasoning_tokens > 0 and partial_present:
            return "mixed_generation_budget_exhaustion"
    return "unknown"


def _normalise_message_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        texts: list[str] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
            elif isinstance(item, str):
                texts.append(item)
        return "".join(texts)
    return "" if value is None else str(value)


# Canonical-schema lowering removes constructs that need client-side validation.
# The live OpenAI transport validator is less restrictive: OpenAI Structured
# Outputs accepts nested ``anyOf`` and valid local references, but not every JSON
# Schema keyword. Keep the two sets separate so manually maintained transport
# schemas are checked accurately before an HTTP request is sent.
_TRANSPORT_LOWERING_KEYWORDS = frozenset({
    "$schema", "$id", "$ref", "$defs", "allOf", "anyOf", "oneOf",
    "if", "then", "else", "not", "dependentSchemas",
    "dependentRequired", "uniqueItems",
})
_TRANSPORT_API_UNSUPPORTED_KEYWORDS = frozenset({
    "$schema", "$id", "allOf", "oneOf", "if", "then", "else",
    "not", "dependentSchemas", "dependentRequired", "uniqueItems",
})


def _resolve_local_ref(value: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    reference = value.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return value
    target: Any = root
    for part in reference.removeprefix("#/").split("/"):
        if not isinstance(target, dict) or part not in target:
            raise ProviderConfigurationError(f"unresolvable local schema reference: {reference}")
        target = target[part]
    if not isinstance(target, dict):
        raise ProviderConfigurationError(f"schema reference is not an object: {reference}")
    merged = copy.deepcopy(target)
    merged.update({key: item for key, item in value.items() if key != "$ref"})
    return merged


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(schema)
    if "const" in value:
        value["enum"] = [value.pop("const"), None]
    elif isinstance(value.get("enum"), list):
        if None not in value["enum"]:
            value["enum"].append(None)
    schema_type = value.get("type")
    if isinstance(schema_type, str):
        value["type"] = [schema_type, "null"]
    elif isinstance(schema_type, list):
        if "null" not in schema_type:
            value["type"] = [*schema_type, "null"]
    else:
        value["type"] = ["string", "null"]
    return value


def _merge_any_of(options: list[dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for option in options:
        if isinstance(option.get("properties"), dict):
            properties.update(option["properties"])
    if properties:
        return {"type": "object", "properties": {key: _nullable(value) for key, value in properties.items()}, "required": sorted(properties), "additionalProperties": False}
    return {"type": "string"}


def _lower_transport_schema(value: Any, root: dict[str, Any]) -> Any:
    if isinstance(value, list):
        return [_lower_transport_schema(item, root) for item in value]
    if not isinstance(value, dict):
        return value
    source = _resolve_local_ref(value, root)
    if "oneOf" in source:
        options = [item for item in source["oneOf"] if isinstance(item, dict)]
        non_null = [item for item in options if item.get("type") != "null"]
        if len(non_null) == 1 and len(non_null) != len(options):
            return _nullable(_lower_transport_schema(non_null[0], root))
        source = {key: item for key, item in source.items() if key != "oneOf"}
        source.update(_merge_any_of([_lower_transport_schema(item, root) for item in options]))
    if "anyOf" in source:
        options = [item for item in source["anyOf"] if isinstance(item, dict)]
        source = {key: item for key, item in source.items() if key != "anyOf"}
        source.update(_merge_any_of([_lower_transport_schema(item, root) for item in options]))
    source = {key: item for key, item in source.items() if key not in _TRANSPORT_LOWERING_KEYWORDS}
    result: dict[str, Any] = {}
    for key, item in source.items():
        if key == "properties" and isinstance(item, dict):
            original_required = set(source.get("required", [])) if isinstance(source.get("required"), list) else set()
            properties = {name: _lower_transport_schema(child, root) for name, child in item.items()}
            result["properties"] = {
                name: child if name in original_required else _nullable(child)
                for name, child in properties.items()
            }
            result["required"] = sorted(properties)
            continue
        if key == "required":
            continue
        if key == "items":
            result[key] = _lower_transport_schema(item, root)
            continue
        # Keep simple constraints.  Conditional business rules stay in the canonical validator.
        result[key] = _lower_transport_schema(item, root)
    if result.get("type") == "object" or "properties" in result:
        result["type"] = result.get("type", "object")
        result["properties"] = result.get("properties", {})
        result["required"] = sorted(result["properties"])
        result["additionalProperties"] = False
    return result


def build_openai_transport_schema(canonical_schema: dict[str, Any]) -> dict[str, Any]:
    """Build an OpenAI strict-schema subset without changing the business schema."""
    transport = _lower_transport_schema(copy.deepcopy(canonical_schema), canonical_schema)
    if not isinstance(transport, dict):
        raise ProviderConfigurationError("transport schema root must be an object")
    validate_openai_transport_schema(transport)
    return transport


def validate_openai_transport_schema(schema: dict[str, Any]) -> None:
    """Validate the locally stored OpenAI strict-output transport contract.

    This catches unsupported keywords, dangling local references, and incomplete
    object contracts before the first provider HTTP request. Canonical business
    constraints remain enforced by the separate canonical JSON Schema validator.
    """
    if schema.get("type") != "object":
        raise ProviderConfigurationError("transport schema root must have type=object")

    def resolve_reference(reference: str) -> Any:
        if not reference.startswith("#/"):
            raise ProviderConfigurationError(
                f"transport schema only supports local references: {reference}"
            )
        target: Any = schema
        for raw_part in reference.removeprefix("#/").split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                raise ProviderConfigurationError(
                    f"transport schema has unresolved local reference: {reference}"
                )
            target = target[part]
        return target

    try:
        import jsonschema  # type: ignore

        jsonschema.Draft202012Validator.check_schema(schema)
    except ModuleNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        raise ProviderConfigurationError(f"transport schema is invalid JSON Schema: {type(exc).__name__}") from exc

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            forbidden = sorted(set(value) & _TRANSPORT_API_UNSUPPORTED_KEYWORDS)
            if forbidden:
                raise ProviderConfigurationError(f"transport schema has unsupported keyword at {path}: {forbidden}")
            reference = value.get("$ref")
            if reference is not None:
                if not isinstance(reference, str):
                    raise ProviderConfigurationError(
                        f"transport schema reference must be a string at {path}"
                    )
                resolve_reference(reference)
            declared_type = value.get("type")
            declares_object = declared_type == "object" or (
                isinstance(declared_type, list) and "object" in declared_type
            )
            if declares_object or "properties" in value:
                properties = value.get("properties")
                required = value.get("required")
                if not isinstance(properties, dict) or value.get("additionalProperties") is not False:
                    raise ProviderConfigurationError(f"transport object must declare properties and additionalProperties=false at {path}")
                if not isinstance(required, list) or set(required) != set(properties):
                    raise ProviderConfigurationError(f"transport object must require every property at {path}")
            enum = value.get("enum")
            if isinstance(enum, list) and declared_type is not None:
                declared_types = {declared_type} if isinstance(declared_type, str) else set(declared_type)
                for enum_value in enum:
                    actual_type = (
                        "null" if enum_value is None else
                        "boolean" if isinstance(enum_value, bool) else
                        "integer" if isinstance(enum_value, int) else
                        "number" if isinstance(enum_value, float) else
                        "string" if isinstance(enum_value, str) else
                        "object" if isinstance(enum_value, dict) else
                        "array" if isinstance(enum_value, list) else
                        "unknown"
                    )
                    if actual_type not in declared_types and not (
                        actual_type == "integer" and "number" in declared_types
                    ):
                        raise ProviderConfigurationError(
                            f"transport enum value type {actual_type} is incompatible with declared type at {path}"
                        )
            for key, item in value.items():
                visit(item, f"{path}/{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}/{index}")

    visit(schema, "$")


def _canonicalise_transport_value(value: Any, canonical: Any, root: dict[str, Any]) -> Any:
    if not isinstance(value, (dict, list)) or not isinstance(canonical, dict):
        return value
    source = _resolve_local_ref(canonical, root)
    if isinstance(value, list):
        items = source.get("items")
        return [_canonicalise_transport_value(item, items, root) for item in value]
    properties = source.get("properties")
    if not isinstance(properties, dict):
        return value
    required = set(source.get("required", [])) if isinstance(source.get("required"), list) else set()
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key not in properties:
            normalized[key] = item
            continue
        if item is None and key not in required:
            continue
        normalized[key] = _canonicalise_transport_value(item, properties[key], root)
    return normalized


def normalize_openai_transport_json(content: str, canonical_schema: dict[str, Any]) -> str:
    """Remove transport-only nullable optional values before canonical validation."""
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return content
    normalized = _canonicalise_transport_value(value, canonical_schema, canonical_schema)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


class _RetryingProvider:
    provider_name: str
    api_family: str
    endpoint_id: str

    def __init__(self, *, timeout_seconds: float, max_retries: int, retry_backoff_seconds: float) -> None:
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = int(max_retries)
        self.retry_backoff_seconds = float(retry_backoff_seconds)

    def _retry_delay(self, attempt: int, headers: Any = None) -> float:
        return _retry_after_seconds(headers) or self.retry_backoff_seconds * (2**attempt) + random.uniform(0.0, min(1.0, self.retry_backoff_seconds))


class CodexCLIProvider(_RetryingProvider):
    """Run the existing provider contract through non-interactive Codex CLI.

    The upstream Step04/07/08 logic remains unchanged.  Messages and strict
    transport schemas are adapted inside this provider, and Codex is executed in
    an isolated temporary read-only workspace so it cannot modify the project.
    """

    provider_name = "codex_cli"
    # Keep the existing Responses-compatible transport/schema path so Step04
    # compact planning, transport normalization, checkpointing, and retries do
    # not change when only the execution provider changes.
    api_family = "responses"
    endpoint_id = "codex_cli"

    def __init__(self, *, timeout_seconds: float, max_retries: int, retry_backoff_seconds: float) -> None:
        super().__init__(timeout_seconds=timeout_seconds, max_retries=max_retries, retry_backoff_seconds=retry_backoff_seconds)
        configured = os.getenv("CODEX_BIN", "codex").strip() or "codex"
        resolved = shutil.which(configured)
        if resolved is None and Path(configured).is_file():
            resolved = str(Path(configured).resolve())
        if resolved is None:
            raise ProviderConfigurationError(f"Codex CLI executable not found: {configured}")
        self.codex_bin = resolved
        self.sandbox = (os.getenv("CODEX_SANDBOX", "read-only").strip() or "read-only").lower()
        if self.sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
            raise ProviderConfigurationError(
                "CODEX_SANDBOX must be one of: read-only, workspace-write, danger-full-access"
            )
        ephemeral_raw = (os.getenv("CODEX_EPHEMERAL", "true").strip() or "true").lower()
        if ephemeral_raw not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
            raise ProviderConfigurationError("CODEX_EPHEMERAL must be true or false")
        self.ephemeral = ephemeral_raw in {"1", "true", "yes", "on"}
        self._verify_authentication()

    def _verify_authentication(self) -> None:
        try:
            result = subprocess.run(
                [self.codex_bin, "login", "status"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                timeout=min(max(self.timeout_seconds, 1.0), 30.0),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProviderConfigurationError(
                f"Codex CLI authentication preflight failed: {type(exc).__name__}"
            ) from exc
        if result.returncode != 0:
            raise ProviderConfigurationError(
                f"Codex CLI is not authenticated or login status failed: exit_code={result.returncode}"
            )

    @staticmethod
    def _decode_data_image(value: str, *, directory: Path, index: int) -> Path:
        match = re.fullmatch(r"data:(image/[A-Za-z0-9.+-]+);base64,(.+)", value, flags=re.DOTALL)
        if match is None:
            raise ProviderConfigurationError("Codex CLI image input requires a base64 data image URL")
        mime = match.group(1).lower()
        suffix_by_mime = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }
        suffix = suffix_by_mime.get(mime)
        if suffix is None:
            raise ProviderConfigurationError(f"unsupported Codex CLI image MIME type: {mime}")
        import base64
        import binascii

        try:
            raw = base64.b64decode(match.group(2), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ProviderConfigurationError("invalid base64 image supplied to Codex CLI") from exc
        path = directory / f"input_image_{index:03d}{suffix}"
        path.write_bytes(raw)
        return path

    @classmethod
    def _prompt_and_images(
        cls,
        messages: list[dict[str, Any]],
        *,
        directory: Path,
    ) -> tuple[str, str | None, list[Path]]:
        """Adapt the existing role-based request to Codex exec without flattening roles.

        Codex exec accepts one initial user prompt.  System/developer text is
        therefore supplied separately through the ``developer_instructions``
        config override, while user content remains the initial prompt.  This
        preserves the strongest available role boundary instead of embedding
        ``## SYSTEM`` text inside a user message.
        """
        developer_rows: list[str] = []
        prompt_rows: list[str] = []
        images: list[Path] = []
        image_index = 0

        def append_text(role: str, text: str) -> None:
            if role in {"system", "developer"}:
                developer_rows.append(text)
            elif role == "user":
                prompt_rows.append(text)
            else:
                # The current AIFSHeavyRain request path does not emit assistant
                # messages.  Preserve compatibility if one is introduced later
                # without weakening system/developer instructions into user text.
                prompt_rows.append(f"[이전 assistant 응답]\n{text}")

        for message in messages:
            if not isinstance(message, dict):
                raise ProviderConfigurationError("Codex CLI message must be an object")
            role = message.get("role")
            if role not in {"system", "developer", "user", "assistant"}:
                raise ProviderConfigurationError(f"unsupported Codex CLI message role: {role!r}")
            content = message.get("content")
            if isinstance(content, str):
                append_text(str(role), content)
                continue
            if not isinstance(content, list):
                raise ProviderConfigurationError("Codex CLI message content must be text or a content list")
            for item in content:
                if not isinstance(item, dict):
                    raise ProviderConfigurationError("Codex CLI content item must be an object")
                item_type = item.get("type")
                if item_type in {"text", "input_text"} and isinstance(item.get("text"), str):
                    append_text(str(role), item["text"])
                    continue
                if item_type in {"image_url", "input_image"}:
                    if role not in {"user", "assistant"}:
                        raise ProviderConfigurationError(
                            "Codex CLI image input is only supported in user/assistant message content"
                        )
                    image_value = item.get("image_url")
                    image_url = image_value.get("url") if isinstance(image_value, dict) else image_value
                    if not isinstance(image_url, str) or not image_url:
                        raise ProviderConfigurationError("Codex CLI image input requires image_url data")
                    image_index += 1
                    path = cls._decode_data_image(image_url, directory=directory, index=image_index)
                    images.append(path)
                    prompt_rows.append(f"[첨부 이미지 {image_index}]")
                    continue
                raise ProviderConfigurationError(f"unsupported Codex CLI content type: {item_type!r}")

        if images:
            prompt_rows.insert(
                0,
                "첨부 이미지는 본문의 [첨부 이미지 N] 표시와 Codex --image 전달 순서가 동일합니다.",
            )
        prompt = "\n\n".join(row.strip() for row in prompt_rows if row.strip()).strip()
        if not prompt:
            # codex exec requires an initial user prompt even when all original
            # content is system/developer instructions.
            prompt = "위 개발자 지시를 수행하고 요구된 구조화 JSON만 반환하십시오."
        developer_instructions = "\n\n".join(
            row.strip() for row in developer_rows if row.strip()
        ).strip() or None
        return prompt, developer_instructions, images

    def complete(self, *, messages: list[dict[str, Any]], model: str, max_tokens: int, schema: dict[str, Any] | None = None, schema_name: str = "response", structured_output_mode: str = "auto", enable_thinking: bool = False, temperature: float = 0.0, top_p: float = 1.0, image_detail: str = "original", reasoning_effort: str | None = None) -> ProviderResponse:
        del max_tokens, schema_name, enable_thinking, temperature, top_p, image_detail
        if structured_output_mode == "off":
            raise ProviderConfigurationError("Codex CLI provider requires structured output")
        if schema is None:
            raise ProviderConfigurationError("Codex CLI provider requires a JSON schema")
        started = time.monotonic()
        retry_count = 0
        last_failure: str | None = None
        for attempt in range(self.max_retries + 1):
            with tempfile.TemporaryDirectory(prefix="aifs_codex_") as temp_name:
                temp_dir = Path(temp_name)
                prompt, developer_instructions, images = self._prompt_and_images(
                    messages,
                    directory=temp_dir,
                )
                schema_path = temp_dir / "output.schema.json"
                output_path = temp_dir / "output.json"
                schema_path.write_text(
                    json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                command = [
                    self.codex_bin,
                    "exec",
                    "--ignore-user-config",
                    "--skip-git-repo-check",
                    "--sandbox",
                    self.sandbox,
                    "--model",
                    model,
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(output_path),
                    "--cd",
                    str(temp_dir),
                ]
                if self.ephemeral:
                    command.append("--ephemeral")
                if reasoning_effort is not None:
                    command.extend(["--config", f'model_reasoning_effort="{reasoning_effort}"'])
                if developer_instructions is not None:
                    command.extend(
                        [
                            "--config",
                            "developer_instructions="
                            + json.dumps(developer_instructions, ensure_ascii=False),
                        ]
                    )
                for image in images:
                    command.extend(["--image", str(image)])
                command.append("-")
                try:
                    result = subprocess.run(
                        command,
                        input=prompt.encode("utf-8"),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=self.timeout_seconds,
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    last_failure = "timeout"
                    if attempt >= self.max_retries:
                        raise ProviderError("Codex CLI request timed out after retries") from exc
                    delay = self._retry_delay(attempt)
                    logging.warning(
                        "provider retry provider=codex_cli reason=timeout attempt=%s/%s wait=%.1fs",
                        attempt + 1,
                        self.max_retries,
                        delay,
                    )
                    time.sleep(delay)
                    retry_count += 1
                    continue
                except OSError as exc:
                    raise ProviderError(f"Codex CLI execution failed: {type(exc).__name__}") from exc
                if result.returncode != 0:
                    stderr = result.stderr or b""
                    diagnostic_file = _persist_codex_failure_stderr(stderr, attempt=attempt)
                    last_failure = (
                        f"exit_code={result.returncode} stderr_bytes={len(stderr)} "
                        f"stderr_sha256_prefix={hashlib.sha256(stderr).hexdigest()[:12]}"
                    )
                    if diagnostic_file:
                        last_failure += f" diagnostic_file={diagnostic_file}"
                    if attempt < self.max_retries:
                        delay = self._retry_delay(attempt)
                        logging.warning(
                            "provider retry provider=codex_cli %s attempt=%s/%s wait=%.1fs",
                            last_failure,
                            attempt + 1,
                            self.max_retries,
                            delay,
                        )
                        time.sleep(delay)
                        retry_count += 1
                        continue
                    raise ProviderError(f"Codex CLI request failed after retries: {last_failure}")
                if not output_path.is_file():
                    raise ProviderError("Codex CLI completed without an output file")
                content = output_path.read_text(encoding="utf-8").strip()
                if not content:
                    raise ProviderError("Codex CLI returned empty output")
                return ProviderResponse(
                    content=content,
                    model=model,
                    retry_count=retry_count,
                    elapsed_seconds=round(time.monotonic() - started, 3),
                    usage=None,
                    provider=self.provider_name,
                    api_family=self.api_family,
                    status="completed",
                    structured_output_used=True,
                    attempt_usage=(None,) * (retry_count + 1),
                )
        raise ProviderError(f"Codex CLI request failed after retry loop: {last_failure or 'unknown'}")


class OpenAICompatibleProvider(_RetryingProvider):
    provider_name = "openai_compatible"
    api_family = "chat_completions"

    def __init__(self, *, base_url: str, api_key: str, timeout_seconds: float = 600.0, max_retries: int = 1, retry_backoff_seconds: float = 5.0) -> None:
        super().__init__(timeout_seconds=timeout_seconds, max_retries=max_retries, retry_backoff_seconds=retry_backoff_seconds)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.endpoint_id = normalized_endpoint_id(self.base_url)

    def complete(self, *, messages: list[dict[str, Any]], model: str, max_tokens: int, schema: dict[str, Any] | None = None, schema_name: str = "response", structured_output_mode: str = "auto", enable_thinking: bool = False, temperature: float = 0.0, top_p: float = 1.0, image_detail: str = "original", reasoning_effort: str | None = None) -> ProviderResponse:
        del enable_thinking, image_detail, reasoning_effort
        payload: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": int(max_tokens), "temperature": float(temperature), "top_p": float(top_p)}
        if schema is not None and structured_output_mode != "off":
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema}}
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        started = time.monotonic()
        retry_count = 0
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(self.base_url + "/chat/completions", data=encoded, headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    envelope = json.loads(response.read().decode("utf-8"))
                choice = dict(envelope.get("choices", [{}])[0])
                message = dict(choice.get("message", {}))
                content = _normalise_message_content(message.get("content"))
                if not content.strip():
                    raise ProviderError("provider returned empty assistant content")
                usage = _normalise_usage(envelope.get("usage"))
                return ProviderResponse(content=content, model=str(envelope.get("model") or model), retry_count=retry_count, elapsed_seconds=round(time.monotonic() - started, 3), usage=usage, provider=self.provider_name, api_family=self.api_family, response_id=str(envelope.get("id")) if envelope.get("id") is not None else None, status="completed", finish_reason=str(choice.get("finish_reason")) if choice.get("finish_reason") is not None else None, structured_output_used=schema is not None and structured_output_mode != "off", attempt_usage=(None,) * retry_count + (usage,))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code not in RETRYABLE_HTTP_STATUS or attempt >= self.max_retries:
                    raise ProviderHTTPError(exc.code, body) from exc
                time.sleep(self._retry_delay(attempt, exc.headers))
                retry_count += 1
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
                if attempt >= self.max_retries:
                    raise ProviderError(f"provider request failed after retries: {type(exc).__name__}") from exc
                time.sleep(self._retry_delay(attempt))
                retry_count += 1
        raise ProviderError("provider request failed after retry loop")


def _responses_input(messages: list[dict[str, Any]], *, image_detail: str) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role not in {"system", "developer", "user", "assistant"}:
            raise ProviderConfigurationError(f"unsupported Responses API message role: {role!r}")
        content = message.get("content")
        if isinstance(content, str):
            converted.append({"role": role, "content": [{"type": "input_text", "text": content}]})
            continue
        if not isinstance(content, list):
            raise ProviderConfigurationError("Responses API message content must be text or a content list")
        rows: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                raise ProviderConfigurationError("Responses API content item must be an object")
            item_type = item.get("type")
            if item_type in {"text", "input_text"} and isinstance(item.get("text"), str):
                rows.append({"type": "input_text", "text": item["text"]})
                continue
            if item_type in {"image_url", "input_image"}:
                image_value = item.get("image_url")
                image_url = image_value.get("url") if isinstance(image_value, dict) else image_value
                if isinstance(image_url, str) and image_url:
                    rows.append({"type": "input_image", "image_url": image_url, "detail": image_detail})
                    continue
                if isinstance(item.get("file_id"), str) and item["file_id"]:
                    rows.append({"type": "input_image", "file_id": item["file_id"], "detail": image_detail})
                    continue
                raise ProviderConfigurationError("Responses API image input requires image_url or file_id")
            raise ProviderConfigurationError(f"unsupported Responses API content type: {item_type!r}")
        converted.append({"role": role, "content": rows})
    return converted


def _response_content(response: Any) -> tuple[str, str | None]:
    direct = getattr(response, "output_text", None)
    if isinstance(direct, str) and direct.strip():
        return direct, None
    refusal: str | None = None
    for output in getattr(response, "output", []) or []:
        if getattr(output, "type", None) != "message":
            continue
        for item in getattr(output, "content", []) or []:
            if getattr(item, "type", None) == "refusal":
                refusal = str(getattr(item, "refusal", "") or "refusal")
            if getattr(item, "type", None) == "output_text" and isinstance(getattr(item, "text", None), str):
                return str(item.text), refusal
    return "", refusal


def _safe_provider_error_field(value: Any) -> str | None:
    """Keep only bounded machine-readable API error identifiers, never messages."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", candidate):
        return None
    return candidate


def _openai_sdk_error_summary(exc: Exception) -> tuple[int, str, dict[str, str]]:
    """Extract non-secret OpenAI error diagnostics without retaining its body."""
    response = getattr(exc, "response", None)
    raw = b""
    try:
        content = getattr(response, "content", b"")
        if isinstance(content, bytes):
            raw = content
        elif isinstance(content, bytearray):
            raw = bytes(content)
        elif isinstance(content, str):
            raw = content.encode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - diagnostics must not mask the original SDK error
        raw = b""
    error_body = getattr(exc, "body", None)
    if isinstance(error_body, dict) and isinstance(error_body.get("error"), dict):
        error_body = error_body["error"]
    fields: dict[str, str] = {}
    if isinstance(error_body, dict):
        for name in ("type", "code", "param"):
            value = _safe_provider_error_field(error_body.get(name))
            if value is not None:
                fields[name] = value
    return len(raw), hashlib.sha256(raw).hexdigest()[:12], fields


class OpenAIResponsesProvider(_RetryingProvider):
    provider_name = "openai"
    api_family = "responses"

    def __init__(self, *, api_key: str, timeout_seconds: float, max_retries: int, retry_backoff_seconds: float, base_url: str = OFFICIAL_OPENAI_BASE_URL) -> None:
        super().__init__(timeout_seconds=timeout_seconds, max_retries=max_retries, retry_backoff_seconds=retry_backoff_seconds)
        try:
            import openai as openai_sdk  # type: ignore
            from openai import OpenAI  # type: ignore
        except ModuleNotFoundError as exc:
            raise ProviderConfigurationError("OpenAI provider requires the openai package; install requirements.txt") from exc
        # SDK patch releases may wrap httpx/httpcore transport failures in
        # APITimeoutError or APIConnectionError.  Keep the tuple dynamic so the
        # provider remains compatible with SDK versions where one class is not
        # exported, while still retrying transient transport failures.
        self._retryable_sdk_errors = tuple(
            error_type
            for error_type in (
                getattr(openai_sdk, "APITimeoutError", None),
                getattr(openai_sdk, "APIConnectionError", None),
            )
            if isinstance(error_type, type)
        )
        self.base_url = base_url.rstrip("/")
        self.endpoint_id = normalized_endpoint_id(self.base_url, official=self.base_url == OFFICIAL_OPENAI_BASE_URL)
        # Explicit base_url prevents OPENAI_BASE_URL from changing the official provider route.
        self.client = OpenAI(api_key=api_key, base_url=self.base_url, timeout=self.timeout_seconds, max_retries=0)

    def complete(self, *, messages: list[dict[str, Any]], model: str, max_tokens: int, schema: dict[str, Any] | None = None, schema_name: str = "response", structured_output_mode: str = "auto", enable_thinking: bool = False, temperature: float = 0.0, top_p: float = 1.0, image_detail: str = "original", reasoning_effort: str | None = None) -> ProviderResponse:
        # The pipeline uses the Responses API reasoning path and does not send sampling
        # parameters for the model selected in envi/api_keys.env.
        del enable_thinking, temperature, top_p
        if structured_output_mode == "off":
            raise ProviderConfigurationError("OpenAI Responses provider requires strict Structured Outputs; --structured-output=off is unsupported")
        if schema is None:
            raise ProviderConfigurationError("OpenAI Responses provider requires a transport schema")
        require_images = any(
            isinstance(part, dict) and part.get("type") in {"image_url", "input_image"}
            for message in messages
            if isinstance(message, dict) and isinstance(message.get("content"), list)
            for part in message["content"]
        )
        validate_openai_capabilities(
            model=model,
            require_images=require_images,
            require_structured_outputs=True,
            image_detail=image_detail,
            reasoning_efforts=[reasoning_effort],
        )
        payload: dict[str, Any] = {
            "model": model,
            "input": _responses_input(messages, image_detail=image_detail),
            "max_output_tokens": int(max_tokens),
            "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
        }
        if reasoning_effort is not None:
            payload["reasoning"] = {"effort": reasoning_effort}
        started = time.monotonic()
        retry_count = 0
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.responses.create(**payload)
            except Exception as exc:  # SDK exception classes vary across supported SDK patch releases.
                status = getattr(exc, "status_code", None)
                headers = getattr(getattr(exc, "response", None), "headers", None)
                retryable_transport_error = bool(self._retryable_sdk_errors) and isinstance(exc, self._retryable_sdk_errors)
                retryable_http_error = isinstance(status, int) and status in RETRYABLE_HTTP_STATUS
                if (retryable_transport_error or retryable_http_error) and attempt < self.max_retries:
                    delay = self._retry_delay(attempt, headers)
                    logging.warning(
                        "provider retry provider=openai status=%s reason=%s attempt=%s/%s wait=%.1fs",
                        status if isinstance(status, int) else "-",
                        type(exc).__name__,
                        attempt + 1,
                        self.max_retries,
                        delay,
                    )
                    time.sleep(delay)
                    retry_count += 1
                    continue
                if not retryable_http_error or attempt >= self.max_retries:
                    if isinstance(status, int):
                        body_bytes, body_sha256_prefix, diagnostic_fields = _openai_sdk_error_summary(exc)
                        raise ProviderHTTPError(
                            status,
                            "",
                            body_bytes=body_bytes,
                            body_sha256_prefix=body_sha256_prefix,
                            diagnostic_fields=diagnostic_fields,
                        ) from exc
                    raise ProviderError(f"OpenAI Responses request failed: {type(exc).__name__}") from exc
            status = str(getattr(response, "status", "") or "")
            incomplete = getattr(response, "incomplete_details", None)
            incomplete_reason = getattr(incomplete, "reason", None) if incomplete is not None else None
            content, refusal = _response_content(response)
            usage = _normalise_usage(getattr(response, "usage", None))
            request_id = getattr(response, "_request_id", None)
            if status == "incomplete":
                message = f"OpenAI Responses returned status={status} incomplete_reason={incomplete_reason or '-'}"
                if status == "incomplete" and incomplete_reason == "max_output_tokens":
                    message += f" configured_max_output_tokens={int(max_tokens)}"
                usage = usage or {}
                diagnostics = {
                    "provider": self.provider_name,
                    "api_family": self.api_family,
                    "model": str(getattr(response, "model", None) or model),
                    "status": status,
                    "incomplete_reason": str(incomplete_reason) if incomplete_reason else None,
                    "configured_max_output_tokens": int(max_tokens),
                    "input_tokens": usage.get("input_tokens"),
                    "cached_input_tokens": usage.get("cached_input_tokens"),
                    "cache_write_tokens": usage.get("cache_write_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "reasoning_tokens": usage.get("reasoning_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                    "response_id": str(getattr(response, "id", "") or "") or None,
                    "request_id": str(request_id) if request_id else None,
                    "retry_count": retry_count,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    **_safe_partial_output_metadata(response, content),
                }
                output_tokens = diagnostics["output_tokens"]
                reasoning_tokens = diagnostics["reasoning_tokens"]
                diagnostics["visible_output_token_estimate"] = (
                    max(output_tokens - reasoning_tokens, 0)
                    if isinstance(output_tokens, int) and isinstance(reasoning_tokens, int)
                    else None
                )
                diagnostics["incomplete_diagnosis"] = classify_incomplete_diagnostics(
                    diagnostics
                )
                raise ProviderIncompleteError(message, diagnostics=diagnostics)
            if status == "failed":
                raise ProviderError(
                    f"OpenAI Responses returned status={status} incomplete_reason={incomplete_reason or '-'}"
                )
            if refusal:
                raise ProviderError("OpenAI Responses returned a refusal")
            if status != "completed":
                raise ProviderError(f"OpenAI Responses returned unexpected status={status or '-'}")
            if not content.strip():
                raise ProviderError("OpenAI Responses returned empty output")
            return ProviderResponse(content=content, model=str(getattr(response, "model", None) or model), retry_count=retry_count, elapsed_seconds=round(time.monotonic() - started, 3), usage=usage, provider=self.provider_name, api_family=self.api_family, response_id=str(getattr(response, "id", "") or "") or None, request_id=str(request_id) if request_id else None, status=status, finish_reason=str(getattr(response, "finish_reason", "") or "") or None, incomplete_reason=str(incomplete_reason) if incomplete_reason else None, refusal=refusal, structured_output_used=True, attempt_usage=(None,) * retry_count + (usage,))
        raise ProviderError("OpenAI Responses request failed after retry loop")


def build_provider(*, provider: str, base_url: str | None, api_key_env: str | None, timeout_seconds: float, max_retries: int, retry_backoff_seconds: float = 5.0, openai_base_url: str | None = None) -> AIProvider:
    defaults = provider_defaults(provider)
    if provider == "codex_cli":
        return CodexCLIProvider(
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )
    env_name = api_key_env or defaults["api_key_env"]
    api_key = validate_provider_api_key(
        provider=provider,
        api_key_env=env_name,
        api_key=os.getenv(env_name),
    )
    if provider == "openai":
        return OpenAIResponsesProvider(api_key=api_key, timeout_seconds=timeout_seconds, max_retries=max_retries, retry_backoff_seconds=retry_backoff_seconds, base_url=resolve_base_url(provider=provider, cli_base_url=base_url, cli_openai_base_url=openai_base_url))
    if provider == "openai_compatible":
        return OpenAICompatibleProvider(base_url=resolve_base_url(provider=provider, cli_base_url=base_url), api_key=api_key, timeout_seconds=timeout_seconds, max_retries=max_retries, retry_backoff_seconds=retry_backoff_seconds)
    raise ProviderConfigurationError(f"unsupported provider: {provider}")
