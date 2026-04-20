## Summary
<!-- 1-3 句话描述变更的目的和价值 -->


## Changes
<!-- 列出主要变更点 -->
- 

## Impact Scope
<!-- 勾选受影响的组件 -->
- [ ] Proxy (tool_proxy.py / proxy_filters.py)
- [ ] Adapter (adapter.py)
- [ ] Gateway (OpenClaw config)
- [ ] KB system (kb_*.py/sh)
- [ ] Cron jobs (jobs_registry.yaml / job scripts)
- [ ] Monitoring (job_watchdog.sh / slo_checker.py)
- [ ] Config (config.yaml / config_loader.py)
- [ ] CI/CD (.github/workflows / auto_deploy.sh)
- [ ] Documentation only

## Risk Assessment
<!-- 变更风险评估 -->
- **Blast radius**: <!-- small/medium/large -->
- **Reversibility**: <!-- instant rollback / needs manual intervention / irreversible -->
- **Requires restart**: <!-- yes/no — which service? -->

## Rollback Plan
<!-- 如果出问题，如何回滚？ -->
```bash
# 回滚命令（示例）
cd ~/openclaw-model-bridge && git fetch origin main && git reset --hard <previous-commit>
bash ~/restart.sh
```

## Test Plan
- [ ] `python3 test_tool_proxy.py` (proxy_filters)
- [ ] `python3 test_check_registry.py` (registry)
- [ ] `python3 test_adapter.py` (adapter)
- [ ] `python3 test_config_slo.py` (config/SLO)
- [ ] `bash full_regression.sh` (all 430+ tests)
- [ ] `bash preflight_check.sh --full` (Mac Mini)
- [ ] WhatsApp E2E business test
- [ ] Other: <!-- 描述 -->

## Related
<!-- Issue/PR 链接 -->
- 
