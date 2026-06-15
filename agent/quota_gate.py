import os
import time
import json
import subprocess
import logging

CACHE_PATH = "/home/lighthouse/.ai-router/cache/quota.json"
ROUTER_PATH = "/home/lighthouse/.ai-router/router.sh"
AGY_SMOKE_ENV = "HERMES_AGY_QUOTA_SMOKE"

def get_quota_gate(task: str = "light") -> dict:
    logger = logging.getLogger("hermes.quota_gate")
    
    cache_valid = False
    data = None
    
    if os.path.exists(CACHE_PATH):
        try:
            mtime = os.path.getmtime(CACHE_PATH)
            age = time.time() - mtime
            if age <= 60:
                with open(CACHE_PATH, 'r') as f:
                    data = json.load(f)
                if isinstance(data, dict) and "providers" in data:
                    cache_valid = True
                    logger.info(f"Quota gate: Loaded from cache (age={age:.1f}s)")
                else:
                    logger.warning("Quota gate: Cache JSON missing 'providers' key")
            else:
                logger.info(f"Quota gate: Cache too old (age={age:.1f}s > 60s)")
        except Exception as e:
            logger.warning(f"Quota gate: Failed to read/parse cache: {e}")
            
    if not cache_valid:
        if os.path.exists(ROUTER_PATH):
            logger.info("Quota gate: Refreshing quota cache...")
            cmd = [ROUTER_PATH, "--dry-run", "--refresh-quota", "--real", "--task", task, "quota gate"]
            try:
                run_env = os.environ.copy()
                run_env["HOME"] = "/home/lighthouse"
                subprocess.run(cmd, capture_output=True, text=True, timeout=25, check=True, env=run_env)
                if os.path.exists(CACHE_PATH):
                    with open(CACHE_PATH, 'r') as f:
                        data = json.load(f)
                    if isinstance(data, dict) and "providers" in data:
                        cache_valid = True
                        logger.info("Quota gate: Cache refreshed successfully")
                    else:
                        logger.warning("Quota gate: Refreshed cache JSON is invalid")
            except subprocess.TimeoutExpired:
                logger.error("Quota gate: Refresh command timed out after 25s")
            except Exception as e:
                logger.error(f"Quota gate: Failed to run refresh command: {e}")
        else:
            logger.error(f"Quota gate: router.sh not found at {ROUTER_PATH}")
            
    res = {
        "codex": {
            "available": False,
            "reason": "quota_unknown",
            "disable_provider": True
        },
        "antigravity": {
            "available": False,
            "reason": "quota_unknown",
            "disable_provider": True
        },
        "gemini_cli": {
            "available": False,
            "reason": "quota_unknown",
            "disable_provider": True
        },
        "claude_code": {
            "available": False,
            "reason": "quota_unknown",
            "disable_provider": True
        }
    }
    
    if data and "providers" in data:
        providers = data["providers"]
        
        # Codex rules
        codex_info = providers.get("codex", {})
        codex_avail = codex_info.get("available", False)
        codex_reason = str(codex_info.get("reason", "unknown"))
        codex_7d_rem = codex_info.get("window_7d_remaining", 0)
        codex_rl_reached = codex_info.get("rate_limit_reached", False)
        
        codex_disabled = (
            not codex_avail or
            "rate_limited" in codex_reason or
            "quota" in codex_reason or
            codex_7d_rem == 0 or
            codex_rl_reached or
            codex_reason == "unavailable"
        )
        res["codex"] = {
            "available": codex_avail,
            "reason": codex_reason,
            "disable_provider": codex_disabled
        }
        
        # Antigravity rules
        agy_info = providers.get("antigravity", {})
        agy_avail = agy_info.get("available", False)
        agy_reason = str(agy_info.get("reason", "unknown"))
        
        if agy_avail and _agy_smoke_enabled():
            if not check_and_update_agy_health():
                agy_avail = False
                agy_reason = "health_uncallable"
        elif agy_avail and not agy_health_allows_calls():
            agy_avail = False
            agy_reason = "health_cooldown"
        
        res["antigravity"] = {
            "available": agy_avail,
            "reason": agy_reason,
            "disable_provider": not agy_avail
        }
        
        # Gemini & Claude rules
        for key, res_key in [("gemini_cli", "gemini_cli"), ("claude_code", "claude_code")]:
            info = providers.get(key, {})
            avail = info.get("available", False)
            reason = str(info.get("reason", "unknown"))
            res[res_key] = {
                "available": avail,
                "reason": reason,
                "disable_provider": not avail
            }
            
    return res

def is_provider_disabled(provider: str, gate: dict) -> tuple[bool, str]:
    mapping = {
        "openai-codex": "codex",
        "antigravity-acp": "antigravity",
        "gemini-acp": "antigravity",
        "gemini": "gemini_cli",
        "claude": "claude_code"
    }
    
    mapped_provider = mapping.get(provider, provider)
    if mapped_provider not in gate:
        return True, "unknown_provider"
        
    info = gate[mapped_provider]
    return info.get("disable_provider", True), info.get("reason", "unknown")

def build_user_facing_model_error(gate: dict, attempts: int, last_errors: list) -> str:
    import os
    codex_disabled = gate.get("codex", {}).get("disable_provider", True)
    agy_info = gate.get("antigravity", {})
    agy_reason = agy_info.get("reason", "")
    
    base_msg = ""
    if agy_reason == "health_uncallable":
        base_msg = "当前 AGY 执行通道异常，已暂停调用 10 分钟。请稍后再试。"
    else:
        # Check if any error is a timeout from antigravity or gemini
        agy_timeout = False
        any_agy_error = False
        for err in last_errors:
            err_str = str(err).lower()
            is_agy = "antigravity" in err_str or "gemini" in err_str or "agy" in err_str or "direct" in err_str
            is_timeout = "timeout" in err_str or "deadline" in err_str or "unresponsive" in err_str or "no response" in err_str
            if is_agy:
                any_agy_error = True
                if is_timeout:
                    agy_timeout = True
                    
        if codex_disabled and (agy_timeout or any_agy_error):
            base_msg = "当前 AGY 执行通道异常，已停止重试。请稍后再试。"
        elif codex_disabled:
            base_msg = "当前 Codex 额度已用尽，已停止重试。请稍后再试。"
        elif agy_timeout:
            base_msg = "当前 AGY 调用超时，已停止重试。请稍后再试。"
        else:
            base_msg = "当前模型额度或响应异常，已停止重试。请稍后再试。"

    if os.getenv("HERMES_DEBUG_EXCEPTIONS", "").lower() in ("true", "1", "yes"):
        if last_errors:
            err_details = "\n\n⚠️ [DEBUG ERROR DETAILS]:\n" + "\n".join(str(e) for e in last_errors)
            return base_msg + err_details
            
    return base_msg

HEALTH_CACHE_PATH = "/home/lighthouse/.hermes/cache/agy_health.json"

def load_agy_health() -> dict:
    import os
    import json
    if os.path.exists(HEALTH_CACHE_PATH):
        try:
            with open(HEALTH_CACHE_PATH, 'r') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {
        "provider": "antigravity-acp",
        "model": "models/gemini-flash-lite-latest",
        "smoke_callable": True,
        "production_callable": True,
        "last_smoke_success": "",
        "last_production_success": "",
        "last_production_failure": "",
        "consecutive_production_failures": 0,
        "cooldown_until": None
    }

def save_agy_health(data: dict):
    import os
    import json
    try:
        os.makedirs(os.path.dirname(HEALTH_CACHE_PATH), exist_ok=True)
        with open(HEALTH_CACHE_PATH, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def _agy_smoke_enabled() -> bool:
    """Return true only when token-consuming AGY quota smoke probes are enabled."""
    return os.getenv(AGY_SMOKE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}

def agy_health_allows_calls() -> bool:
    import time
    data = load_agy_health()
    cooldown_until = data.get("cooldown_until")
    if cooldown_until is not None:
        try:
            if time.time() < float(cooldown_until):
                return False
        except Exception:
            pass
    return data.get("production_callable", True) is not False

def run_agy_smoke_test() -> bool:
    try:
        from agent.copilot_acp_client import CopilotACPClient
        client = CopilotACPClient(
            acp_command="/home/lighthouse/.hermes/bin/agy_acp_bridge.py",
            acp_args=["--acp", "--stdio"],
            acp_cwd="/tmp/hermes-agy-chat"
        )
        response_text, _ = client._run_prompt(
            "请只回复：pong",
            model="models/gemini-flash-lite-latest",
            timeout_seconds=30.0
        )
        return "pong" in response_text.lower()
    except Exception:
        return False

def check_and_update_agy_health() -> bool:
    import time
    data = load_agy_health()
    now = time.time()
    
    cooldown_until = data.get("cooldown_until")
    if cooldown_until is not None:
        try:
            cooldown_val = float(cooldown_until)
            if now < cooldown_val:
                return False
            else:
                data["cooldown_until"] = None
                data["consecutive_production_failures"] = 0
                data["production_callable"] = True
                save_agy_health(data)
        except Exception:
            pass
            
    last_smoke_success = data.get("last_smoke_success", "")
    last_smoke_ts = 0.0
    if last_smoke_success:
        try:
            last_smoke_ts = time.mktime(time.strptime(last_smoke_success, "%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass
            
    if now - last_smoke_ts >= 300 or not last_smoke_success:
        success = run_agy_smoke_test()
        data["smoke_callable"] = success
        if success:
            data["last_smoke_success"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_agy_health(data)
        
    return data.get("production_callable", True) and data.get("smoke_callable", True)

def record_agy_success():
    import time
    data = load_agy_health()
    data["production_callable"] = True
    data["consecutive_production_failures"] = 0
    data["last_production_success"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data["cooldown_until"] = None
    save_agy_health(data)

def record_agy_failure(reason: str):
    import time
    data = load_agy_health()
    data["consecutive_production_failures"] = data.get("consecutive_production_failures", 0) + 1
    data["last_production_failure"] = time.strftime("%Y-%m-%d %H:%M:%S")
    
    if data["consecutive_production_failures"] >= 2:
        data["production_callable"] = False
        data["cooldown_until"] = time.time() + 600
        
    save_agy_health(data)
