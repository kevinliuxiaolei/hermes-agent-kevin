"""
Unified Quota Registry for Hermes.

Reads /home/lighthouse/.ai-router/cache/quota.json and provides
per-family quota status.

Also separately queries cclimits for Codex Plus and Codex Business
using their respective CODEX_HOME paths.

Single source of truth for quota information used by:
  - /model display command
  - model_selector
  - fallback routing
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

QUOTA_CACHE_PATH = "/home/lighthouse/.ai-router/cache/quota.json"
AI_ROUTER_PATH = "/home/lighthouse/.ai-router/router.sh"

CODEX_HOME_PLUS = "/home/lighthouse/.codex-plus"
CODEX_HOME_BUSINESS = "/home/lighthouse/.codex-business"

# Quota cache TTL in seconds (refresh at most every 5 minutes)
_QUOTA_CACHE_TTL = 300

_cached_snapshot: dict | None = None
_cached_at: float = 0.0

# Separate cache for cclimits results
_codex_cache: dict | None = None
_codex_cached_at: float = 0.0

# Separate cache for Antigravity family status parsed from cclimits
_agy_cache: dict | None = None
_agy_cached_at: float = 0.0

NVIDIA_STATE_FILE = "/home/lighthouse/.hermes/nvidia_state.json"

def load_nvidia_state() -> dict:
    try:
        if os.path.exists(NVIDIA_STATE_FILE):
            with open(NVIDIA_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_nvidia_state(state: dict):
    try:
        os.makedirs(os.path.dirname(NVIDIA_STATE_FILE), exist_ok=True)
        with open(NVIDIA_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass

def run_nvidia_healthcheck() -> dict:
    import urllib.request
    import urllib.error
    
    api_key = os.getenv("NVIDIA_API_KEY")
    if not api_key:
        state = {
            "status": "auth_missing",
            "execute_enabled": False,
            "default_alias": "nv-fallback",
            "default_model": "deepseek-ai/deepseek-v4-flash",
            "last_error": "NVIDIA_API_KEY environment variable is missing",
            "last_healthcheck_at": time.time()
        }
        save_nvidia_state(state)
        return state

    base_url = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
    models = [
        ("nv-fallback", "deepseek-ai/deepseek-v4-flash"),
        ("nv-kimi", "moonshotai/kimi-k2.6"),
        ("nv-nemotron", "nvidia/llama-3.1-nemotron-nano-8b-v1")
    ]

    selected_alias = None
    selected_model = None
    status = "unavailable"
    execute_enabled = False
    last_error = None

    for alias, model_name in models:
        try:
            url = f"{base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            body = {
                "model": model_name,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 16,
                "temperature": 0
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status == 200:
                    selected_alias = alias
                    selected_model = model_name
                    status = "available" if alias == "nv-fallback" else "available_degraded"
                    execute_enabled = True
                    break
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                pass
            last_error = f"HTTP {e.code}: {err_body or e.reason}"
            if e.code in (401, 403):
                status = "auth_error"
                execute_enabled = False
                break
        except Exception as e:
            last_error = str(e)
            continue

    if not execute_enabled and status != "auth_error":
        status = "unavailable"

    state = {
        "status": status,
        "execute_enabled": execute_enabled,
        "default_alias": selected_alias or "nv-fallback",
        "default_model": selected_model or "deepseek-ai/deepseek-v4-flash",
        "last_error": last_error,
        "last_healthcheck_at": time.time()
    }
    old_state = load_nvidia_state()
    if old_state.get("cooldown_until") and old_state.get("cooldown_until") > time.time():
        state["cooldown_until"] = old_state["cooldown_until"]
        state["status"] = old_state.get("status", status)
    
    save_nvidia_state(state)
    return state



@dataclass
class QuotaFamilyStatus:
    quota_family: str
    provider_family: str
    available: bool
    quota_5h_percent: Optional[int] = None
    quota_7d_percent: Optional[int] = None
    reset_5h: Optional[str] = None
    reset_7d: Optional[str] = None
    reason: str = "no_data"
    raw: dict = field(default_factory=dict)
    # Backwards-compatible fields used by selector / older call sites.
    remaining_percent: Optional[int] = None
    reset_in: Optional[str] = None


QuotaStatus = QuotaFamilyStatus


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _parse_percent(value) -> Optional[int]:
    """Parse quota percent values.

    User policy: ACP/AGY explicit ``N/A``/``NA``/``未知`` means exhausted for
    routing, not unknown/unlimited.  Missing ``None`` still means the upstream
    omitted a numeric field and should not by itself mark a model exhausted.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        pct = float(value) * 100 if 0 <= float(value) <= 1 else float(value)
        return max(0, min(100, int(round(pct))))
    s = str(value).strip()
    if s in ("", "null"):
        return None
    if s.upper() in {"N/A", "NA"} or s in {"未知"}:
        return 0
    if s.endswith("%"):
        try:
            return max(0, min(100, int(float(s[:-1]))))
        except ValueError:
            return None
    try:
        pct = float(s)
        pct = pct * 100 if 0 <= pct <= 1 else pct
        return max(0, min(100, int(round(pct))))
    except ValueError:
        return None


def _first_present(mapping: dict, keys: tuple[str, ...]) -> Optional[str]:
    """Return the first non-empty string-like value from the provided keys."""
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"n/a", "null", "none"}:
            return text
    return None


def _load_quota_file() -> dict:
    """Load quota.json from disk. Returns {} on error."""
    try:
        with open(QUOTA_CACHE_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.debug("quota_registry: could not read quota.json: %s", e)
        return {}


def _refresh_quota_file() -> dict:
    """Call ai-router to refresh quota then reload file."""
    env = os.environ.copy()
    env.update({
        "HOME": "/home/lighthouse",
        "USER": "lighthouse",
        "LOGNAME": "lighthouse",
        "AI_ROUTER_HOME": "/home/lighthouse/.ai-router",
    })
    # Ensure lighthouse's local bin is in PATH
    local_bin = "/home/lighthouse/.local/bin"
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = local_bin + ":" + env.get("PATH", "")

    try:
        if os.path.isfile(AI_ROUTER_PATH):
            result = subprocess.run(
                [AI_ROUTER_PATH, "--refresh-quota", "--show-quota", "--real"],
                env=env,
                timeout=30,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode == 0 and result.stdout.strip():
                try:
                    data = json.loads(result.stdout)
                    with open(QUOTA_CACHE_PATH, "w") as f:
                        json.dump(data, f, indent=2)
                    return data
                except Exception:
                    logger.debug("quota_registry: router quota stdout was not JSON")
    except Exception as e:
        logger.debug("quota_registry: refresh subprocess failed: %s", e)

    return _load_quota_file()


def _run_cclimits(codex_home: str) -> Optional[str]:
    """Run cclimits with the given CODEX_HOME. Returns stdout text or None on failure."""
    env = os.environ.copy()
    env.update({
        "HOME": "/home/lighthouse",
        "USER": "lighthouse",
        "LOGNAME": "lighthouse",
        "CODEX_HOME": codex_home,
    })
    local_bin = "/home/lighthouse/.local/bin"
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = local_bin + ":" + env.get("PATH", "")

    try:
        result = subprocess.run(
            ["cclimits"],
            env=env,
            timeout=20,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        return result.stdout or ""
    except Exception as e:
        logger.debug("quota_registry: cclimits failed for %s: %s", codex_home, e)
        return None


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def _run_antigravity_usage(*, all_accounts: bool = False, json_output: bool = False) -> Optional[str]:
    """Run antigravity-usage and return stdout text or None on failure."""
    env = os.environ.copy()
    env.update({
        "HOME": "/home/lighthouse",
        "USER": "lighthouse",
        "LOGNAME": "lighthouse",
        "AI_ROUTER_HOME": "/home/lighthouse/.ai-router",
    })
    local_bin = "/home/lighthouse/.local/bin"
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = local_bin + ":" + env.get("PATH", "")

    cmd = ["antigravity-usage"]
    if all_accounts:
        cmd.extend(["quota", "--all"])
    cmd.append("--all-models")
    if json_output:
        cmd.append("--json")

    try:
        result = subprocess.run(
            cmd,
            env=env,
            timeout=20,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        return result.stdout or ""
    except Exception as e:
        logger.debug("quota_registry: antigravity-usage failed: %s", e)
        return None


def _parse_cclimits_output(text: str) -> dict:
    """
    Parse cclimits output for the OpenAI Codex section.

    Returns a dict:
      {
        auth_connected: bool,
        plan: str | None,
        quota_5h_percent: int | None,
        quota_7d_percent: int | None,
        reset_5h: str | None,
        reset_7d: str | None,
        rate_limit_reached: bool,
        available: bool,
        reason: str,
      }
    """
    result = {
        "auth_connected": False,
        "plan": None,
        "quota_5h_percent": None,
        "quota_7d_percent": None,
        "reset_5h": None,
        "reset_7d": None,
        "rate_limit_reached": False,
        "available": False,
        "reason": "no_data",
    }

    if not text:
        result["reason"] = "command_failed"
        return result

    # Find the OpenAI Codex section
    lines = text.splitlines()
    in_codex_section = False
    in_5h = False
    in_7d = False
    section_headings = {
        "Claude Code",
        "Gemini CLI",
        "Z.AI (5h shared - GLM-4.x)",
        "Google Antigravity",
        "Done!",
    }

    for line in lines:
        stripped = line.strip()

        # Detect section headers
        if "OpenAI Codex" in stripped:
            in_codex_section = True
            in_5h = False
            in_7d = False
            continue

        if not in_codex_section:
            continue

        # Skip visual separators inside the Codex section.
        if stripped and set(stripped) == {"="}:
            continue

        # Once we reach the next real section heading, stop parsing Codex.
        if stripped in section_headings:
            break

        if not stripped:
            continue

        # Auth connected
        if "Connected" in stripped and "✅" in stripped:
            result["auth_connected"] = True
        elif "No credentials" in stripped or "Not authenticated" in stripped:
            result["auth_connected"] = False
            result["reason"] = "no_credentials"

        # Plan
        m = re.search(r"Plan:\s*(.+)", stripped)
        if m:
            result["plan"] = m.group(1).strip()

        # Rate limit
        if "Rate limit reached" in stripped or "rate limit reached" in stripped:
            result["rate_limit_reached"] = True

        # 5h window section
        if "5h Window" in stripped:
            in_5h = True
            in_7d = False
            continue
        if "7d Window" in stripped:
            in_7d = True
            in_5h = False
            continue

        # Remaining %
        m = re.search(r"Remaining:\s*(\d+)%", stripped)
        if m:
            pct = int(m.group(1))
            if in_5h:
                result["quota_5h_percent"] = pct
            elif in_7d:
                result["quota_7d_percent"] = pct

        # Resets in
        m = re.search(r"Resets in:\s*(.+)", stripped)
        if m:
            reset_val = m.group(1).strip()
            if in_5h:
                result["reset_5h"] = reset_val
            elif in_7d:
                result["reset_7d"] = reset_val

    # Determine availability
    w5h = result["quota_5h_percent"]
    w7d = result["quota_7d_percent"]
    rate_limited = result["rate_limit_reached"]

    if not result["auth_connected"]:
        result["available"] = False
        result["reason"] = result["reason"] or "no_credentials"
    elif rate_limited:
        result["available"] = False
        result["reason"] = "rate_limited"
    elif w5h is not None and w5h == 0:
        result["available"] = False
        result["reason"] = "5h_exhausted"
    elif w7d is not None and w7d == 0:
        result["available"] = False
        result["reason"] = "7d_exhausted"
    else:
        result["available"] = True
        result["reason"] = "available"

    return result


def _clean_remaining_token(token: Any) -> Optional[str]:
    """Normalize remaining quota tokens like '🔴 20%', 0.6 or 'N/A'."""
    if token is None:
        return None
    if isinstance(token, bool):
        return "100%" if token else "0%"
    if isinstance(token, (int, float)):
        value = float(token)
        pct = value * 100 if 0 <= value <= 1 else value
        return f"{max(0, min(100, int(round(pct))))}%"
    s = _strip_ansi(str(token)).strip()
    if not s:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)%", s)
    if m:
        return f"{int(float(m.group(1)))}%"
    if re.search(r"\bN/?A\b", s, re.IGNORECASE) or s in {"未知"}:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        value = float(s)
        pct = value * 100 if 0 <= value <= 1 else value
        return f"{max(0, min(100, int(round(pct))))}%"
    return s


def _clean_text_remaining_token(token: Any) -> Optional[str]:
    """Normalize human table quota text; explicit N/A means exhausted."""
    cleaned = _clean_remaining_token(token)
    if cleaned is None and token is not None:
        s = _strip_ansi(str(token)).strip()
        if re.search(r"\bN/?A\b", s, re.IGNORECASE) or s in {"未知"}:
            return "0%"
    return cleaned


def _parse_antigravity_output(text: str) -> dict:
    """
    Parse the Google Antigravity section from cclimits or antigravity-usage.

    Returns:
      {
        provider_connected: bool,
        available: bool,
        reason: str,
        project: str | None,
        tier: str | None,
        models: {model_name: {"remaining": str | None, "reset": str | None}},
      }
    """
    result = {
        "provider_connected": False,
        "available": False,
        "reason": "no_data",
        "project": None,
        "tier": None,
        "models": {},
    }

    if not text:
        result["reason"] = "command_failed"
        return result

    lines = _strip_ansi(text).splitlines()
    in_section = False

    def _finish_reason() -> None:
        models = result["models"]
        if not models:
            result["available"] = False
            result["reason"] = "all_models_na"
            return
        numeric = []
        has_zero = False
        for item in models.values():
            pct = _parse_percent(item.get("remaining"))
            if pct is None:
                continue
            numeric.append(pct)
            if pct == 0:
                has_zero = True
        if numeric and max(numeric) > 0:
            result["available"] = True
            result["reason"] = "available"
        elif numeric and has_zero:
            result["available"] = False
            result["reason"] = "quota_exhausted"
        else:
            result["available"] = False
            result["reason"] = "all_models_na"

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if "Google Antigravity" in stripped or "Antigravity Quota Status" in stripped:
            in_section = True
            continue

        if not in_section:
            continue

        if stripped == "Done!":
            break

        if stripped.startswith("Retrieved:"):
            continue
        if stripped.startswith("👤"):
            continue
        if stripped.startswith("📦 Project:"):
            result["project"] = stripped.split(":", 1)[1].strip()
            continue
        if stripped.startswith("📊 Tier:"):
            result["tier"] = stripped.split(":", 1)[1].strip()
            continue
        if "Model Quotas" in stripped or stripped in {"Models:", "Tightest:", "Average:"}:
            continue

        if "│" in stripped or "|" in stripped:
            parts = [p.strip() for p in re.split(r"[│|]", stripped) if p.strip()]
            if len(parts) >= 3:
                model, remaining, reset = parts[0], parts[1], parts[2]
                if model.lower() in {"model", "remaining", "resets in"}:
                    continue
                if model.startswith("---") or model.startswith("─"):
                    continue
                if "more models hidden" in model.lower():
                    continue
                result["models"][model] = {
                    "remaining": _clean_text_remaining_token(remaining),
                    "reset": _strip_ansi(reset).strip() or None,
                }
                continue

        if stripped.startswith("-") or set(stripped) == {"-"}:
            continue
        if stripped.lower().startswith("model ") and "remaining" in stripped.lower():
            continue

        m = re.match(
            r"^(?P<model>.+?)\s+(?P<remaining>(?:N/?A|\d+%|0))\s+(?P<reset>\S.+)$",
            stripped,
        )
        if m:
            model = m.group("model").strip()
            remaining = _clean_text_remaining_token(m.group("remaining"))
            reset = m.group("reset").strip()
            if model and model.lower() != "model":
                result["models"][model] = {
                    "remaining": remaining,
                    "reset": reset,
                }

    _finish_reason()
    return result


def _build_codex_family_status(codex_home: str, quota_family: str) -> QuotaStatus:
    """Build QuotaStatus for one Codex account by running cclimits."""
    text = _run_cclimits(codex_home)
    parsed = _parse_cclimits_output(text or "")

    w5h = parsed["quota_5h_percent"]
    w7d = parsed["quota_7d_percent"]
    available = parsed["available"]
    reason = parsed["reason"]

    # remaining_percent = minimum of 5h and 7d (both must have headroom)
    parts = [p for p in [w5h, w7d] if p is not None]
    remaining = min(parts) if parts else None

    # reset_in: if unavailable, show whichever window resets first
    reset_in = None
    if not available:
        if parsed["reset_5h"]:
            reset_in = f"5h resets in {parsed['reset_5h']}"
        elif parsed["reset_7d"]:
            reset_in = f"7d resets in {parsed['reset_7d']}"

    return QuotaStatus(
        quota_family=quota_family,
        provider_family="codex",
        available=available,
        quota_5h_percent=w5h,
        quota_7d_percent=w7d,
        reset_5h=parsed["reset_5h"],
        reset_7d=parsed["reset_7d"],
        reason=reason,
        raw=parsed,
        remaining_percent=remaining,
        reset_in=reset_in,
    )


def _get_codex_statuses(refresh: bool = False) -> dict[str, QuotaStatus]:
    """Return codex_plus and codex_business QuotaStatus, with caching."""
    global _codex_cache, _codex_cached_at

    now = time.time()
    if not refresh and _codex_cache is not None and (now - _codex_cached_at) < _QUOTA_CACHE_TTL:
        return _codex_cache

    logger.info("quota_registry: querying cclimits for codex_plus and codex_business")
    plus_status = _build_codex_family_status(CODEX_HOME_PLUS, "codex_plus")
    business_status = _build_codex_family_status(CODEX_HOME_BUSINESS, "codex_business")

    _codex_cache = {
        "codex_plus": plus_status,
        "codex_business": business_status,
    }
    _codex_cached_at = now
    return _codex_cache


def _agy_family_match(model_name: str, quota_family: str) -> bool:
    """Return True if a raw antigravity model id/name belongs to a quota family."""
    s = (model_name or "").strip().lower()
    if quota_family == "agy_gemini":
        return s.startswith("gemini-") or s.startswith("gemini ")
    if quota_family == "agy_claude":
        return s.startswith("claude-") or s.startswith("claude ")
    if quota_family == "agy_gpt":
        return s.startswith("gpt-oss-") or s.startswith("gpt-oss ")
    return False


def _agy_candidate_sort_value(candidate: dict, missing_value: int = 101) -> int:
    value = candidate.get("quota_5h")
    if value is None:
        return missing_value
    try:
        return int(value)
    except Exception:
        return missing_value


def _agy_family_candidate(quota_family: str, models: dict, *, email: str | None = None, is_active: bool = False) -> dict:
    family_models = {
        name: data
        for name, data in (models or {}).items()
        if _agy_family_match(name, quota_family)
    }
    pct_values: list[int] = []
    exhausted_seen = False
    reset_5h = None
    for item in family_models.values():
        if not isinstance(item, dict):
            continue
        pct = _parse_percent(item.get("remaining"))
        if pct is not None:
            pct_values.append(pct)
        exhausted_seen = exhausted_seen or bool(item.get("is_exhausted"))
        if not reset_5h:
            reset_5h = item.get("reset")

    quota_5h = min(pct_values) if pct_values else None
    if quota_5h is None:
        available = bool(family_models and not exhausted_seen)
        reason = "available" if available else ("quota_exhausted" if exhausted_seen else "no_data")
        if exhausted_seen:
            quota_5h = 0
    elif quota_5h > 0:
        available = True
        reason = "available"
    else:
        available = False
        reason = "quota_exhausted"

    return {
        "email": email,
        "is_active": is_active,
        "models": family_models,
        "quota_5h": quota_5h,
        "available": available,
        "reason": reason,
        "reset_5h": reset_5h,
    }


def _build_agy_family_status(
    quota_family: str,
    parsed: dict,
) -> QuotaStatus:
    """Build QuotaStatus for a grouped Antigravity family.

    Multi-account AGY data must be evaluated per account first, then the best
    usable account selected.  Flattening all accounts and taking the minimum can
    incorrectly mark the active dirisephan account unavailable because another
    account has omitted/N/A percentages.
    """
    account_candidates: list[dict] = []
    accounts = parsed.get("accounts") if isinstance(parsed, dict) else None
    if isinstance(accounts, dict) and accounts:
        for email, account in accounts.items():
            if not isinstance(account, dict):
                continue
            account_candidates.append(
                _agy_family_candidate(
                    quota_family,
                    account.get("models") or {},
                    email=str(email),
                    is_active=bool(account.get("isActive") or account.get("is_active")),
                )
            )
    else:
        account_candidates.append(
            _agy_family_candidate(quota_family, parsed.get("models", {}) or {})
        )

    usable = [c for c in account_candidates if c.get("available")]
    if usable:
        active_usable = [c for c in usable if c.get("is_active")]
        pool = active_usable or usable
        chosen = max(pool, key=_agy_candidate_sort_value)
    else:
        active_candidates = [c for c in account_candidates if c.get("is_active")]
        pool = active_candidates or account_candidates
        chosen = min(
            pool,
            key=_agy_candidate_sort_value,
            default={"quota_5h": None, "available": False, "reason": parsed.get("reason", "no_data"), "reset_5h": None, "models": {}},
        )

    quota_5h = chosen.get("quota_5h")
    available = bool(chosen.get("available"))
    reason = chosen.get("reason") or ("available" if available else parsed.get("reason", "no_data"))
    reset_5h = chosen.get("reset_5h")

    try:
        from agent.agy_slot_state import load_agy_slot_state
        execution_state = load_agy_slot_state()
    except Exception:
        execution_state = {}
    execution_slots = execution_state.get("slots", {}) if isinstance(execution_state, dict) else {}
    authenticated_slots = [
        slot for slot in execution_slots.values()
        if isinstance(slot, dict) and slot.get("authenticated")
    ]
    ready_slots = []
    for slot in authenticated_slots:
        try:
            cooldown_until = float(slot.get("cooldown_until") or 0)
        except (TypeError, ValueError):
            cooldown_until = 0
        if cooldown_until <= time.time():
            ready_slots.append(slot)
    if authenticated_slots and not ready_slots:
        available = False
        reason = "cooldown"

    return QuotaStatus(
        quota_family=quota_family,
        provider_family="antigravity",
        available=available,
        quota_5h_percent=quota_5h,
        quota_7d_percent=None,
        reset_5h=reset_5h,
        reset_7d=None,
        reason=reason,
        raw={
            "models": chosen.get("models") or {},
            "accounts": account_candidates,
            "account_count": len(account_candidates),
            "active_account": next((c.get("email") for c in account_candidates if c.get("is_active")), None),
            "chosen_account": chosen.get("email"),
            "execution_slots": execution_slots,
            "ready_slot_count": len(ready_slots),
            "last_executed_slot": execution_state.get("last_executed_slot"),
            "source": parsed,
        },
        remaining_percent=quota_5h,
        reset_in=reset_5h,
    )


def _normalize_antigravity_model_snapshot(models_raw: Any) -> dict[str, dict]:
    models: dict[str, dict] = {}
    if not isinstance(models_raw, list):
        return models
    for idx, model in enumerate(models_raw):
        if not isinstance(model, dict):
            continue
        key = str(model.get("modelId") or model.get("label") or f"model-{idx}")
        # Keep duplicate labels/modelIds by suffixing with index instead of
        # overwriting; AGY reports multiple Gemini Flash Lite variants.
        if key in models:
            key = f"{key}#{idx}"
        remaining = model.get("remainingPercentage")
        reset = model.get("resetTime") or model.get("timeUntilResetMs")
        # Antigravity reports exhausted/unavailable buckets as null/N/A in the
        # JSON source.  Keep /model aligned with the hourly quota watchdog: null
        # means 0%, not unknown/unlimited.
        cleaned_remaining = "0%" if remaining is None else _clean_remaining_token(remaining)
        models[key] = {
            "label": model.get("label"),
            "modelId": model.get("modelId"),
            "remaining": cleaned_remaining,
            "reset": str(reset).strip() if reset else None,
            "is_exhausted": bool(model.get("isExhausted")),
            "is_autocomplete_only": bool(model.get("isAutocompleteOnly")),
        }
    return models


def _parse_antigravity_json_all_accounts(text: str) -> dict:
    try:
        data = json.loads(text or "")
    except Exception:
        return {"reason": "parse_failed", "models": {}, "accounts": {}}

    accounts: dict[str, dict] = {}
    if isinstance(data, dict):
        email = str(data.get("email") or "active")
        accounts[email] = {
            "isActive": True,
            "models": _normalize_antigravity_model_snapshot(data.get("models")),
        }
    elif isinstance(data, list):
        for idx, account in enumerate(data):
            if not isinstance(account, dict) or account.get("status") == "error":
                continue
            snapshot = account.get("snapshot") if isinstance(account.get("snapshot"), dict) else {}
            email = str(account.get("email") or snapshot.get("email") or f"account-{idx}")
            accounts[email] = {
                "isActive": bool(account.get("isActive")),
                "models": _normalize_antigravity_model_snapshot(snapshot.get("models")),
                "status": account.get("status"),
            }

    flattened: dict[str, dict] = {}
    for email, account in accounts.items():
        account_models = account.get("models") if isinstance(account, dict) else {}
        for name, value in (account_models or {}).items():
            flattened[f"{email}:{name}"] = value
    return {
        "provider_connected": bool(accounts),
        "available": bool(accounts),
        "reason": "available" if accounts else "no_data",
        "models": flattened,
        "accounts": accounts,
    }


def _parse_agy_from_quota_snapshot(snapshot: dict) -> dict:
    """Convert ai-router quota.json antigravity data to parser-compatible shape."""
    providers = snapshot.get("providers") if isinstance(snapshot, dict) else None
    ag = providers.get("antigravity") if isinstance(providers, dict) else None
    if not isinstance(ag, dict):
        return {"reason": "no_data", "models": {}}

    models_raw = ag.get("models") or {}
    models: dict[str, dict] = {}
    if isinstance(models_raw, dict):
        for name, value in models_raw.items():
            if isinstance(value, dict):
                remaining = value.get("remaining")
                reset = value.get("reset") or value.get("reset_5h") or value.get("resets_in")
            else:
                remaining = value
                reset = None
            models[str(name)] = {
                "remaining": _clean_remaining_token(remaining),
                "reset": str(reset).strip() if reset else None,
                "is_exhausted": bool(value.get("isExhausted") or value.get("is_exhausted")) if isinstance(value, dict) else False,
            }

    return {
        "provider_connected": bool(ag.get("available")),
        "available": bool(ag.get("available")),
        "reason": ag.get("reason") or "no_data",
        "models": models,
        "raw": ag,
    }


def _load_antigravity_cached_accounts() -> dict:
    accounts_dir = Path("/home/lighthouse/.config/antigravity-usage/accounts")
    config_path = Path("/home/lighthouse/.config/antigravity-usage/config.json")
    active_account = None
    try:
        config = json.loads(config_path.read_text())
        active_account = config.get("activeAccount")
    except Exception:
        pass

    accounts: dict[str, dict] = {}
    if not accounts_dir.exists():
        return {"reason": "no_data", "models": {}, "accounts": {}}
    for cache_path in accounts_dir.glob("*/cache.json"):
        try:
            payload = json.loads(cache_path.read_text())
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                continue
            email = str(data.get("email") or cache_path.parent.name)
            accounts[email] = {
                "isActive": email == active_account,
                "models": _normalize_antigravity_model_snapshot(data.get("models")),
                "status": "cached_file",
            }
        except Exception:
            continue
    flattened: dict[str, dict] = {}
    for email, account in accounts.items():
        for name, value in (account.get("models") or {}).items():
            flattened[f"{email}:{name}"] = value
    return {
        "provider_connected": bool(accounts),
        "available": bool(accounts),
        "reason": "available" if accounts else "no_data",
        "models": flattened,
        "accounts": accounts,
    }


def _get_agy_statuses(refresh: bool = False) -> dict[str, QuotaStatus]:
    """Return agy_gemini, agy_claude and agy_gpt QuotaStatus, with caching."""
    global _agy_cache, _agy_cached_at

    now = time.time()
    if not refresh and _agy_cache is not None and (now - _agy_cached_at) < _QUOTA_CACHE_TTL:
        return _agy_cache

    logger.info("quota_registry: querying ai-router/antigravity-usage for AGY family quotas")
    parsed = _load_antigravity_cached_accounts()

    if refresh or not parsed.get("accounts"):
        fallback = _run_antigravity_usage(all_accounts=True, json_output=True)
        if fallback:
            parsed = _parse_antigravity_json_all_accounts(fallback)

    if not parsed.get("accounts"):
        snapshot = get_quota_snapshot(refresh=refresh)
        parsed = _parse_agy_from_quota_snapshot(snapshot)

    if not parsed.get("models"):
        fallback = _run_antigravity_usage()
        if fallback:
            parsed = _parse_antigravity_output(fallback)

    if parsed is None or not isinstance(parsed, dict):
        parsed = {"reason": "no_data", "models": {}}

    result = {
        "agy_gemini": _build_agy_family_status("agy_gemini", parsed),
        "agy_claude": _build_agy_family_status("agy_claude", parsed),
        "agy_gpt": _build_agy_family_status("agy_gpt", parsed),
    }

    _agy_cache = result
    _agy_cached_at = now
    return result


def _build_agy_model_status(
    display_name: str,
    quota_family: str,
    models_dict: dict,
    provider_available: bool,
    provider_reason: str,
) -> QuotaStatus:
    """Build QuotaStatus for a specific antigravity model."""
    if not provider_available:
        return QuotaStatus(
            quota_family=quota_family,
            provider_family="antigravity",
            available=False,
            reason=provider_reason,
            raw={},
            remaining_percent=0,
            reset_in=None,
        )

    raw_pct = models_dict.get(display_name)
    pct = _parse_percent(raw_pct)

    available = (pct is None or pct > 0)
    reason = "available" if available else "quota_exhausted"

    return QuotaStatus(
        quota_family=quota_family,
        provider_family="antigravity",
        available=available,
        quota_5h_percent=pct,
        quota_7d_percent=None,
        reset_5h=_first_present(models_dict, ("reset_5h", "window_5h_reset", "window_5h_resets_in", "reset_in_5h", "resets_in_5h")),
        reset_7d=_first_present(models_dict, ("reset_7d", "window_7d_reset", "window_7d_resets_in", "reset_in_7d", "resets_in_7d")),
        reason=reason,
        raw={"display_name": display_name, "raw_pct": raw_pct},
        remaining_percent=pct,
        reset_in=_first_present(models_dict, ("reset_5h", "window_5h_reset", "window_5h_resets_in", "reset_in_5h", "resets_in_5h")),
    )


def _agy_entry_model_status(entry, family_status: QuotaStatus) -> Optional[QuotaStatus]:
    """Return trusted per-model AGY status when the chosen account exposes it."""
    models = family_status.raw.get("models") if isinstance(family_status.raw, dict) else None
    if not isinstance(models, dict) or not models:
        return None

    wanted = {
        str(getattr(entry, "model", "") or "").strip().lower(),
        str(getattr(entry, "display_name", "") or "").strip().lower(),
    }
    wanted.discard("")
    for name, item in models.items():
        if not isinstance(item, dict):
            continue
        identities = {
            str(name or "").split("#", 1)[0].strip().lower(),
            str(item.get("label") or "").strip().lower(),
            str(item.get("modelId") or item.get("model_id") or "").strip().lower(),
        }
        identities.discard("")
        if not wanted.intersection(identities):
            continue
        pct = _parse_percent(item.get("remaining"))
        exhausted = bool(item.get("is_exhausted") or item.get("isExhausted"))
        available = not exhausted and (pct is None or pct > 0)
        reset = item.get("reset")
        return QuotaStatus(
            quota_family=entry.quota_family,
            provider_family="antigravity",
            available=available,
            quota_5h_percent=pct,
            reset_5h=str(reset).strip() if reset else None,
            reason="available" if available else "quota_exhausted",
            raw={"model": name, "details": item, "family": family_status.raw},
            remaining_percent=pct,
            reset_in=str(reset).strip() if reset else None,
        )
    return None


# ─── Public API ───────────────────────────────────────────────────────────────

def get_quota_snapshot(refresh: bool = False) -> dict:
    """
    Return raw quota.json data (as dict), using cache if fresh enough.
    
    Returns {} if file not found.
    """
    global _cached_snapshot, _cached_at

    now = time.time()
    if not refresh and _cached_snapshot is not None and (now - _cached_at) < _QUOTA_CACHE_TTL:
        return _cached_snapshot

    if refresh:
        data = _refresh_quota_file()
    else:
        data = _load_quota_file()

    _cached_snapshot = data
    _cached_at = now
    return data


def get_nvidia_nim_status() -> QuotaStatus:
    state = load_nvidia_state()
    now = time.time()
    last_hc = state.get("last_healthcheck_at", 0)
    if not state or (now - last_hc > 300):
        state = run_nvidia_healthcheck()
    
    status_str = state.get("status", "unavailable")
    cooldown_until = state.get("cooldown_until")
    
    available = state.get("execute_enabled", False)
    reason = status_str
    
    if cooldown_until and now < cooldown_until:
        available = False
        reason = "cooldown"
        
    return QuotaStatus(
        quota_family="nvidia_nim",
        provider_family="nvidia_nim",
        available=available,
        quota_5h_percent=None,
        quota_7d_percent=None,
        reset_5h=None,
        reset_7d=None,
        reason=reason,
        raw=state,
        remaining_percent=None,
        reset_in=None,
    )


def get_nvidia_nim_status_fast() -> QuotaStatus:
    """Return cached NVIDIA NIM state without running a network healthcheck."""
    state = load_nvidia_state()
    status_str = state.get("status", "not_refreshed") if state else "not_refreshed"
    cooldown_until = state.get("cooldown_until") if state else None
    now = time.time()
    available = bool(state.get("execute_enabled", False)) if state else False
    reason = status_str
    if cooldown_until and now < cooldown_until:
        available = False
        reason = "cooldown"
    return QuotaStatus(
        quota_family="nvidia_nim",
        provider_family="nvidia_nim",
        available=available,
        quota_5h_percent=None,
        quota_7d_percent=None,
        reset_5h=None,
        reset_7d=None,
        reason=reason,
        raw=state or {},
        remaining_percent=None,
        reset_in=None,
    )


def get_google_ai_studio_status() -> QuotaStatus:
    try:
        auth_file = "/home/lighthouse/.hermes/auth.json"
        if os.path.exists(auth_file):
            with open(auth_file, "r") as f:
                auth = json.load(f)
            pool = auth.get("credential_pool", {}).get("gemini", [])
            active_keys = [k for k in pool if k.get("last_status") not in ("exhausted", "dead")]
            if active_keys:
                return QuotaStatus(
                    quota_family="gemini",
                    provider_family="gemini",
                    available=True,
                    quota_5h_percent=None,
                    quota_7d_percent=None,
                    reset_5h=None,
                    reset_7d=None,
                    reason="available",
                    raw={"active_keys": len(active_keys), "total_keys": len(pool)},
                    remaining_percent=100,
                    reset_in=None,
                )
    except Exception:
        pass

    if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
        return QuotaStatus(
            quota_family="gemini",
            provider_family="gemini",
            available=True,
            quota_5h_percent=None,
            quota_7d_percent=None,
            reset_5h=None,
            reset_7d=None,
            reason="available",
            raw={},
            remaining_percent=100,
            reset_in=None,
        )

    return QuotaStatus(
        quota_family="gemini",
        provider_family="gemini",
        available=False,
        quota_5h_percent=None,
        quota_7d_percent=None,
        reset_5h=None,
        reset_7d=None,
        reason="no_credentials",
        raw={},
        remaining_percent=0,
        reset_in=None,
    )


def _check_volcengine_cooldown(provider_name: str) -> Optional[datetime]:
    """Check errors.log for recent 429 errors for provider_name and return future reset datetime if found."""
    from datetime import datetime
    log_path = "/home/lighthouse/.hermes/logs/errors.log"
    if not os.path.exists(log_path):
        return None

    try:
        with open(log_path, "rb") as f:
            try:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                seek_size = min(size, 262144)  # 256KB
                f.seek(size - seek_size)
            except Exception:
                pass
            lines = f.read().decode("utf-8", errors="ignore").splitlines()
    except Exception:
        return None

    latest_reset = None
    reset_pat = re.compile(r"reset\s+at\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})")
    provider_pattern = provider_name.lower()
    
    for line in reversed(lines):
        is_match = False
        if provider_pattern in line.lower():
            is_match = True
        elif provider_name == "volcengine-coding-plan" and "api/coding/v3" in line:
            is_match = True
        elif provider_name == "volcengine-agent-plan" and "api/plan/v3" in line:
            is_match = True
            
        if not is_match:
            continue

        if "429" in line or "quota_exceeded" in line.lower() or "accountquotaexceeded" in line.lower() or "exceeded the" in line.lower():
            match = reset_pat.search(line)
            if match:
                dt_str = match.group(1)
                try:
                    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                    if dt > datetime.now():
                        if latest_reset is None or dt > latest_reset:
                            latest_reset = dt
                except Exception:
                    pass
    return latest_reset


def _get_volcengine_provider_status(provider_name: str, quota_family: str) -> QuotaStatus:
    """Report whether a configured Volcengine/Ark plan provider is callable."""
    api_key = None
    base_url = None
    model = None

    # Backward-compatible env defaults for the primary Agent Plan.
    if provider_name == "volcengine-agent-plan":
        api_key = os.getenv("VOLCENGINE_AGENT_API_KEY") or os.getenv("VOLCENGINE_API_KEY") or os.getenv("ARK_API_KEY")
        base_url = os.getenv("VOLCENGINE_AGENT_BASE_URL") or os.getenv("VOLCENGINE_BASE_URL") or os.getenv("ARK_BASE_URL")
    elif provider_name == "volcengine-coding-plan":
        api_key = os.getenv("VOLCENGINE_CODING_API_KEY") or os.getenv("VOLCENGINE_API_KEY") or os.getenv("ARK_API_KEY")
        base_url = os.getenv("VOLCENGINE_CODING_BASE_URL") or os.getenv("VOLCENGINE_BASE_URL") or os.getenv("ARK_BASE_URL")

    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        providers = cfg.get("providers") or {}
        provider_cfg = providers.get(provider_name) or {}
        if isinstance(provider_cfg, dict):
            api_key = api_key or provider_cfg.get("api_key")
            base_url = base_url or provider_cfg.get("base_url")
            model = provider_cfg.get("model")
    except Exception:
        pass

    available = bool(str(api_key or "").strip() and str(base_url or "").strip())
    
    reset_dt = None
    if available:
        reset_dt = _check_volcengine_cooldown(provider_name)

    quota_5h_percent = None
    remaining_percent = 100 if available else 0
    reset_5h = None
    reason = "available" if available else "no_credentials"

    if reset_dt:
        available = False
        quota_5h_percent = 0
        remaining_percent = 0
        reset_5h = reset_dt.isoformat()
        reason = "quota_exhausted"

    return QuotaStatus(
        quota_family=quota_family,
        provider_family="volcengine",
        available=available,
        quota_5h_percent=quota_5h_percent,
        quota_7d_percent=None,
        reset_5h=reset_5h,
        reset_7d=None,
        reason=reason,
        raw={
            "provider": provider_name,
            "model": model,
            "base_url_configured": bool(base_url),
            "api_key_configured": bool(api_key),
            "reset_time": reset_5h,
        },
        remaining_percent=remaining_percent,
        reset_in=reset_5h,
    )


def get_volcengine_status() -> QuotaStatus:
    """Aggregate status for the logical Volcengine family."""
    agent = _get_volcengine_provider_status("volcengine-agent-plan", "volcengine_agent_plan")
    coding = _get_volcengine_provider_status("volcengine-coding-plan", "volcengine_coding_plan")
    available = agent.available or coding.available
    
    pct_agent = agent.remaining_percent if agent.remaining_percent is not None else (agent.quota_5h_percent or 0 if not agent.available else 100)
    pct_coding = coding.remaining_percent if coding.remaining_percent is not None else (coding.quota_5h_percent or 0 if not coding.available else 100)
    
    if not available:
        reason = "quota_exhausted" if (agent.reason == "quota_exhausted" or coding.reason == "quota_exhausted") else "no_credentials"
        remaining_percent = 0
    else:
        reason = "available"
        remaining_percent = max(pct_agent, pct_coding)
        
    reset_5h = None
    if not available:
        resets = [r for r in [agent.reset_5h, coding.reset_5h] if r]
        if resets:
            reset_5h = min(resets)

    return QuotaStatus(
        quota_family="volcengine",
        provider_family="volcengine",
        available=available,
        quota_5h_percent=None if available else 0,
        quota_7d_percent=None,
        reset_5h=reset_5h,
        reset_7d=None,
        reason=reason,
        raw={"agent_plan": agent.raw, "coding_plan": coding.raw},
        remaining_percent=remaining_percent,
        reset_in=reset_5h,
    )

def get_all_quota_statuses(refresh: bool = False) -> dict[str, QuotaStatus]:
    """
    Return a mapping of quota_family -> QuotaStatus for all known families.

    quota_families: codex_plus, codex_business, agy_gemini, agy_claude, agy_gpt, nvidia_nim, gemini
    """
    result: dict[str, QuotaStatus] = {}

    # ── Codex Plus and Codex Business (via cclimits) ─────────────────────────
    codex_statuses = _get_codex_statuses(refresh=refresh)
    result["codex_plus"] = codex_statuses["codex_plus"]
    result["codex_business"] = codex_statuses["codex_business"]

    # ── Antigravity ──────────────────────────────────────────────────────────
    agy_statuses = _get_agy_statuses(refresh=refresh)
    result["agy_gemini"] = agy_statuses["agy_gemini"]
    result["agy_claude"] = agy_statuses["agy_claude"]
    result["agy_gpt"] = agy_statuses["agy_gpt"]

    # ── NVIDIA NIM Free ──────────────────────────────────────────────────────
    result["nvidia_nim"] = get_nvidia_nim_status()

    # ── Google AI Studio ─────────────────────────────────────────────────────
    result["gemini"] = get_google_ai_studio_status()

    # ── Volcengine / Ark Plans ───────────────────────────────────────────────
    result["volcengine_agent_plan"] = _get_volcengine_provider_status(
        "volcengine-agent-plan", "volcengine_agent_plan"
    )
    result["volcengine_coding_plan"] = _get_volcengine_provider_status(
        "volcengine-coding-plan", "volcengine_coding_plan"
    )
    result["volcengine"] = get_volcengine_status()
    return result


def _not_refreshed_status(quota_family: str, provider_family: str) -> QuotaStatus:
    return QuotaStatus(
        quota_family=quota_family,
        provider_family=provider_family,
        available=False,
        quota_5h_percent=None,
        quota_7d_percent=None,
        reset_5h=None,
        reset_7d=None,
        reason="not_refreshed",
        raw={},
        remaining_percent=None,
        reset_in=None,
    )


def get_all_quota_statuses_fast() -> dict[str, QuotaStatus]:
    """Return quota statuses without spawning external quota commands.

    Latency-sensitive UI surfaces such as Telegram `/model` use this path.
    It reuses in-memory caches when available and otherwise reads only local
    files/config. Explicit refresh paths keep using get_all_quota_statuses().
    """
    result: dict[str, QuotaStatus] = {}

    if _codex_cache is not None:
        result["codex_plus"] = _codex_cache["codex_plus"]
        result["codex_business"] = _codex_cache["codex_business"]
    else:
        result["codex_plus"] = _not_refreshed_status("codex_plus", "codex")
        result["codex_business"] = _not_refreshed_status("codex_business", "codex")

    if _agy_cache is not None:
        result["agy_gemini"] = _agy_cache["agy_gemini"]
        result["agy_claude"] = _agy_cache["agy_claude"]
        result["agy_gpt"] = _agy_cache["agy_gpt"]
    else:
        parsed = _load_antigravity_cached_accounts()
        if not parsed.get("accounts"):
            snapshot = _load_quota_file()
            parsed = _parse_agy_from_quota_snapshot(snapshot)
        result["agy_gemini"] = _build_agy_family_status("agy_gemini", parsed)
        result["agy_claude"] = _build_agy_family_status("agy_claude", parsed)
        result["agy_gpt"] = _build_agy_family_status("agy_gpt", parsed)

    result["nvidia_nim"] = get_nvidia_nim_status_fast()
    result["gemini"] = get_google_ai_studio_status()
    result["volcengine_agent_plan"] = _get_volcengine_provider_status(
        "volcengine-agent-plan", "volcengine_agent_plan"
    )
    result["volcengine_coding_plan"] = _get_volcengine_provider_status(
        "volcengine-coding-plan", "volcengine_coding_plan"
    )
    result["volcengine"] = get_volcengine_status()

    return result


def get_model_quota(entry, snapshot_statuses: dict[str, QuotaStatus] | None = None) -> QuotaStatus:
    """
    Return QuotaStatus for a specific ModelEntry.

    If snapshot_statuses is not provided, loads fresh quota.
    For agy_gemini models, uses per-model quota if available.
    """
    if snapshot_statuses is None:
        snapshot_statuses = get_all_quota_statuses()

    # Fall back to family-level status
    family_status = snapshot_statuses.get(entry.quota_family)
    if family_status:
        if entry.quota_family.startswith("agy_"):
            model_status = _agy_entry_model_status(entry, family_status)
            if model_status is not None:
                return model_status
        return family_status

    return QuotaStatus(
        quota_family=entry.quota_family,
        provider_family="nvidia_nim" if entry.quota_family == "nvidia_nim" else ("antigravity" if entry.quota_family.startswith("agy_") else "codex"),
        available=False,
        remaining_percent=None,
        reset_in=None,
        reason="no_data",
        raw={},
    )


def invalidate_cache() -> None:
    """Force next call to reload from disk and re-query cclimits."""
    global _cached_snapshot, _cached_at, _codex_cache, _codex_cached_at, _agy_cache, _agy_cached_at
    _cached_snapshot = None
    _cached_at = 0.0
    _codex_cache = None
    _codex_cached_at = 0.0
    _agy_cache = None
    _agy_cached_at = 0.0
