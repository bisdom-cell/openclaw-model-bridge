### Environment

- **OpenClaw version**: 2026.3.13
- **Node.js**: v25.6.1
- **OS**: macOS (Apple Silicon, Mac Mini)
- **Process manager**: launchd (`ai.openclaw.gateway`)
- **WhatsApp mode**: personal (web)

### Description

After upgrading to OpenClaw 2026.3.13, **all** `openclaw message send` CLI calls fail with:

```
GatewayClientRequestError: No active WhatsApp Web listener (account: default)
```

Meanwhile, the daemon's **auto-reply outbound** (via `web-auto-reply` module) works perfectly — inbound messages are received and auto-replied to without issues.

This means the two outbound paths behave differently:
1. **Internal auto-reply** (daemon → `web-auto-reply` → WhatsApp): **WORKS**
2. **CLI send** (CLI → WebSocket RPC → Gateway → check listener → WhatsApp): **BROKEN**

### Steps to Reproduce

1. Install or upgrade to OpenClaw 2026.3.13: `openclaw update`
2. Ensure WhatsApp is linked: `openclaw channels list` shows `linked: true, enabled: true`
3. Send an inbound WhatsApp message → auto-reply works (confirms session is valid)
4. Run CLI send:
   ```bash
   openclaw message send --target "+852XXXXXXXX" --message "test"
   ```
5. **Result**: `GatewayClientRequestError: No active WhatsApp Web listener (account: default)`

### Relevant Log Entries

**Gateway log shows the daemon IS connected to WhatsApp:**
```
[whatsapp] Listening for personal WhatsApp inbound messages.
[whatsapp] Auto-replied to +852XXXXXXXX ← outbound works internally!
```

**But CLI send is rejected:**
```
[ws] ⇄ res ✗ send errorCode=UNAVAILABLE
```

### Additional Context: `openclaw channels login` causes 440 conflict

Running `openclaw channels login` while the daemon is already connected triggers a WhatsApp session conflict:

```
status=440 Unknown Stream Errored (conflict)
web reconnect: non-retryable close status; stopping monitor
```

This permanently stops the `web-reconnect` module, making things worse. The `channels login` CLI creates a **competing** WhatsApp Web session that conflicts with the daemon's existing one.

### Attempted Workarounds (none worked)

- `openclaw channels login` → causes 440 conflict (see above)
- `openclaw daemon restart` → same error after restart
- `openclaw daemon restart` + `openclaw channels login` → 440 conflict
- Adding `--channel whatsapp --account default` flags → same error
- Cleaning `~/.openclaw/delivery-queue/` + restart → same error

### Impact

All programmatic WhatsApp sends are broken:
- Cron job notifications (ArXiv, HN, freight watcher, health reports, etc.)
- Any automation relying on `openclaw message send`
- Delivery queue accumulates undeliverable messages

### Analysis

The CLI `message send` connects via WebSocket to `127.0.0.1:18789` and sends a "send" RPC request. The gateway checks for an active "WhatsApp Web listener" — but this check does **not** reflect the actual daemon connection state. The daemon's internal `web-auto-reply` module bypasses this check and can send successfully.

Potentially related change in 2026.3.13 CHANGELOG:
> Gateway/client requests: reject unanswered gateway RPC calls after a bounded timeout

This may have introduced a stricter listener check or broken the listener registration for the daemon process.

### Expected Behavior

`openclaw message send` should work when the daemon has an active WhatsApp connection (as evidenced by working auto-replies).

