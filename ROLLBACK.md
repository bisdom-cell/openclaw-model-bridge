# 回滚指南

> 2026-09-09 重写。原 V27 版「30 秒回滚到 V26」已不可用：`v26-snapshot` tag 不存在，且当前 `adapter.py` / `tool_proxy.py` 依赖 `providers.d/` 插件装载、`FALLBACK_ORDER`、`proxy_filters.py` 与 `jobs_registry.yaml`，退回 V26 文件会让系统起不来。V27 时代原文见 git 历史（`git log -p -- ROLLBACK.md`）。

## 代码回滚（Mac Mini，工作日白天）

先找出引入故障的 commit 与它之前的健康 commit。

```bash
cd ~/openclaw-model-bridge && git log --oneline -8
```

把仓库退回健康 commit，auto_deploy 会在 2 分钟内按 FILE_MAP 同步运行时副本；急用时手动同步并重启三层服务。

```bash
cd ~/openclaw-model-bridge && git fetch origin main && git reset --hard <健康 commit>
```

```bash
bash ~/openclaw-model-bridge/auto_deploy.sh && bash ~/restart.sh
```

```bash
bash ~/openclaw-model-bridge/preflight_check.sh --full
```

仓库侧用 `git revert <坏 commit>` 走 PR 正式回退，让 main 与 Mac Mini 重新一致（Mac Mini 只能 `git reset --hard origin/main`，长期分叉会被下一次同步覆盖）。

## 单文件热修

只有某个运行时脚本出问题时，直接把运行时副本退回健康版本，不动仓库：

```bash
cd ~/openclaw-model-bridge && git show <健康 commit>:job_watchdog.sh > ~/job_watchdog.sh
```

注意 auto_deploy 的 md5 漂移检测会在下一小时报告仓库与运行时不一致，这是预期信号；仓库侧修好后自动收敛。

## Gateway（OpenClaw）回滚

不在本文范围。OpenClaw ≥6.x 的状态迁移是单向门，回滚有损，须按 `docs/gateway_upgrade_eval_v2026.4.md` 第 7.5 节用升级前的全量 `~/.openclaw` 快照恢复。

## 回滚后检查清单

- [ ] `curl http://localhost:18789` Gateway 可达
- [ ] `curl http://localhost:5002/health` Proxy 正常，`fallback_chain` 显示 4 跳
- [ ] `curl http://localhost:5001/health` Adapter 正常，`version` 为预期值
- [ ] Discord 或 WhatsApp 发一条测试消息确认全链路
- [ ] `bash job_smoke_test.sh` 定时任务面无回归
