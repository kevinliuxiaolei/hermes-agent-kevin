#!/usr/bin/env python3
import sys
import os
import argparse
import yaml

# Add agent path to sys.path
sys.path.insert(0, "/home/lighthouse/.hermes/hermes-agent")

try:
    from agent.model_registry import list_models, get_model_by_alias
    from agent.quota_registry import get_all_quota_statuses, get_quota_snapshot
    from agent.model_selector import select_model_with_reason, get_selected_alias_from_config
    from agent.route_plan import build_route_plan
    from hermes_cli.config import load_config
except ImportError as exc:
    print(f"Failed to import agent modules: {exc}")
    sys.exit(1)


def _route_identity(entry):
    return (
        str((entry or {}).get("provider") or "").strip().lower(),
        str((entry or {}).get("model") or "").strip(),
        str((entry or {}).get("codex_home") or "").strip(),
    )


def validate_route_plan(plan):
    """Return invariant violations that would make execution failover unsafe."""
    issues = []
    primary_key = _route_identity(plan.primary)
    seen = set()
    if len(plan.fallbacks) + (1 if plan.primary else 0) > plan.max_attempts:
        issues.append("route count exceeds max_route_attempts")
    for entry in plan.fallbacks:
        key = _route_identity(entry)
        if not key[0] or not key[1]:
            issues.append("fallback missing provider/model")
            continue
        if plan.primary and key == primary_key:
            issues.append(f"fallback repeats primary: {key[0]}/{key[1]}")
        if key in seen:
            issues.append(f"duplicate fallback route: {key[0]}/{key[1]}")
        seen.add(key)
    return issues


def main():
    parser = argparse.ArgumentParser(description="Hermes Model Strategy Diagnostic Tool")
    parser.add_argument("--refresh-quota", action="store_true", help="Force refresh of quota registry data")
    args = parser.parse_args()

    print("==================================================")
    print("Hermes Model Routing Doctor")
    print("==================================================\n")

    status = "PASS"
    gaps = []

    # 1. Config and selected model alias
    print("--- 1. Configuration & Selected Model Alias ---")
    config = {}
    try:
        config = load_config() or {}
        print("Config file loaded successfully.")
    except Exception as exc:
        print(f"Error loading config: {exc}")
        status = "FAIL"
        gaps.append("Cannot load config.yaml")

    selected_alias = get_selected_alias_from_config(config)
    print(f"selected_model_alias from config: {selected_alias}")
    if not selected_alias:
        print("Warning: selected_model_alias is not set in config.")

    # 2. Model Registry Check
    print("\n--- 2. Model Registry Verification ---")
    models = list_models()
    print(f"Total models registered: {len(models)}")
    
    # Check Gemini context size is 1M
    gemini_models = [m for m in models if m.family == "gemini"]
    for m in gemini_models:
        if m.context_size != "1M" or m.context_limit_tokens != 1000000:
            print(f"FAIL: Gemini model {m.alias} context limit is not 1M (size: {m.context_size}, tokens: {m.context_limit_tokens})")
            status = "PARTIAL" if status == "PASS" else status
            gaps.append(f"Gemini model {m.alias} context limit mismatch")

    # 3. Quota Registry Check
    print("\n--- 3. Quota Registry & Families ---")
    try:
        quota_statuses = get_all_quota_statuses(refresh=args.refresh_quota)
        for fam in ["agy_gemini", "agy_claude", "agy_gpt", "codex_plus", "codex_business"]:
            qst = quota_statuses.get(fam)
            if qst:
                avail_str = "available" if qst.available else f"unavailable ({qst.reason})"
                pct = qst.quota_5h_percent if qst.quota_5h_percent is not None else qst.remaining_percent
                print(f"  Quota Family {fam:<16}: {avail_str:<18} | Remaining: {pct}% | Reset: {qst.reset_5h or qst.reset_in}")
            else:
                print(f"  FAIL: Quota Family {fam:<16} not found in quota_statuses")
                status = "PARTIAL" if status == "PASS" else status
                gaps.append(f"Missing quota family {fam}")
        for fam in ["agy_gemini", "agy_claude", "agy_gpt"]:
            qst = quota_statuses.get(fam)
            executable = [
                model.alias
                for model in models
                if model.quota_family == fam and model.enabled and model.supports_execute
            ]
            if qst and qst.available and not executable:
                print(f"  FAIL: {fam} quota is available but no executable model is registered.")
                status = "PARTIAL" if status == "PASS" else status
                gaps.append(f"{fam} quota available but execution bridge unavailable")
            elif qst and qst.available:
                print(f"  PASS: {fam} has executable routes: {executable}")
    except Exception as exc:
        print(f"Error reading Quota Registry: {exc}")
        status = "FAIL"
        gaps.append(f"Quota Registry read failed: {exc}")

    # Check CODEX_HOME paths
    from agent.quota_registry import CODEX_HOME_PLUS, CODEX_HOME_BUSINESS
    print(f"CODEX_HOME for codex_plus    : {CODEX_HOME_PLUS}")
    print(f"CODEX_HOME for codex_business: {CODEX_HOME_BUSINESS}")
    if CODEX_HOME_PLUS != "/home/lighthouse/.codex-plus":
        status = "PARTIAL" if status == "PASS" else status
        gaps.append("CODEX_HOME_PLUS path mismatch")
    if CODEX_HOME_BUSINESS != "/home/lighthouse/.codex-business":
        status = "PARTIAL" if status == "PASS" else status
        gaps.append("CODEX_HOME_BUSINESS path mismatch")

    # 4. Selector Decisions for tasks
    print("\n--- 4. Selector Decisions (chat/light/cron/analysis) ---")
    for task in ["chat", "light", "cron", "analysis"]:
        try:
            sel = select_model_with_reason(task=task, preferred_alias=selected_alias, refresh_quota=False)
            entry = sel.entry
            if entry:
                print(f"  Task {task:<10}: Selected {entry.alias:<18} (reason: {sel.reason})")
            else:
                print(f"  Task {task:<10}: None selected (reason: {sel.reason})")
        except Exception as exc:
            print(f"  Task {task:<10}: Error selecting: {exc}")
            status = "PARTIAL" if status == "PASS" else status
            gaps.append(f"Selector error for task {task}: {exc}")

    # 5. RoutePlan and failure progression
    print("\n--- 5. RoutePlan Contract & Failure Progression ---")
    model_cfg = config.get("model") or {}
    for task in ["chat", "cron"]:
        try:
            plan = build_route_plan(
                task=task,
                preferred_alias=selected_alias,
                current_provider=model_cfg.get("provider"),
                current_model=model_cfg.get("model") or model_cfg.get("default"),
                current_base_url=model_cfg.get("base_url"),
                config=config,
            )
            progression = []
            if plan.primary:
                progression.append(
                    f"{plan.primary.get('provider')}/{plan.primary.get('model')}"
                )
            progression.extend(
                str(entry.get("alias") or f"{entry.get('provider')}/{entry.get('model')}")
                for entry in plan.fallbacks
            )
            print(
                f"  RoutePlan for {task:<5}: mode={plan.mode}, "
                f"routes={len(progression)}/{plan.max_attempts}"
            )
            print(f"    Failure simulation: {' -> '.join(progression) or '(empty)'}")
            issues = validate_route_plan(plan)
            for issue in issues:
                print(f"    FAIL: {issue}")
                gaps.append(f"RoutePlan {task}: {issue}")
                status = "PARTIAL" if status == "PASS" else status
            if not progression:
                gaps.append(f"RoutePlan {task} is empty")
                status = "PARTIAL" if status == "PASS" else status
        except Exception as exc:
            print(f"  Error building RoutePlan for {task}: {exc}")
            gaps.append(f"RoutePlan error for {task}: {exc}")
            status = "PARTIAL" if status == "PASS" else status

    # 6. Path Restrictions and residues Check
    print("\n--- 6. Path Restrictions & Old residues ---")
    root_router_exists = os.path.exists("/root/.ai-router")
    root_codex_exists = os.path.exists("/root/.codex")
    print(f"  /root/.ai-router exists: {root_router_exists}")
    print(f"  /root/.codex exists    : {root_codex_exists}")
    if root_router_exists or root_codex_exists:
        print("FAIL: accessing or referencing root paths is forbidden.")
        status = "PARTIAL" if status == "PASS" else status
        gaps.append("Root configuration paths detected")

    # Check for legacy menus / gpt-4 in config
    has_legacy_residues = False
    try:
        model_sections = {
            "model": config.get("model"),
            "model_aliases": config.get("model_aliases"),
            "fallback_providers": config.get("fallback_providers"),
            "smart_model_routing": config.get("smart_model_routing"),
            "cron_model_routing": config.get("cron_model_routing")
        }
        config_text = yaml.dump(model_sections)
        if "gpt-4" in config_text or "gpt-4-mini" in config_text:
            print("  FAIL: Legacy 'gpt-4' or 'gpt-4-mini' found in config.yaml model settings.")
            has_legacy_residues = True
            status = "PARTIAL" if status == "PASS" else status
            gaps.append("Legacy gpt-4 residues in config.yaml")
    except Exception:
        pass

    if not has_legacy_residues:
        print("  PASS: No legacy gpt-4 or gpt-4-mini references found in config.yaml model settings.")

    # 7. Summary
    print("\n==================================================")
    print(f"DIAGNOSTIC STATUS: {status}")
    if gaps:
        print("Gaps found:")
        for gap in gaps:
            print(f" - {gap}")
    print("==================================================")

if __name__ == "__main__":
    main()
