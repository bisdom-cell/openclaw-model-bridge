# V27 回滚指南

> 如果 V27 变更导致系统崩溃，按以下步骤快速恢复到 V26 状态。

## 快速回滚（30秒内完成）

```bash
# 1. 停止所有服务
pkill -f tool_proxy.py
pkill -f adapter.py

# 2. 回滚代码到 V26 快照
cd ~/openclaw-model-bridge
git checkout v26-snapshot -- tool_proxy.py adapter.py health_check.sh restart.sh

# 3. 重启服务
nohup python3 ~/tool_proxy.py > ~/tool_proxy.log 2>&1 &
nohup python3 ~/adapter.py > ~/adapter.log 2>&1 &

# 4. 验证
curl http://localhost:5002/health
curl http://localhost:5001/v1/models
```

## 完整回滚（恢复整个仓库）

```bash
git checkout v26-snapshot
bash ~/restart.sh
```

## V27 新增文件（回滚时可安全删除）

| 文件 | 说明 |
|------|------|
| `proxy_filters.py` | 从 tool_proxy.py 提取的过滤逻辑 |
| `jobs_registry.yaml` | 统一任务注册表 |
| `check_registry.py` | 注册表校验脚本 |
| `ROLLBACK.md` | 本文件 |

这些文件删除不影响 V26 运行。

## 回滚后检查清单

- [ ] Gateway 端口 18789 可达
- [ ] Proxy 端口 5002 响应 /health
- [ ] Adapter 端口 5001 转发正常
- [ ] 发送一条测试消息确认全链路
