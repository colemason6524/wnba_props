# systemd units (Azure VM) — reference copies

These are copies of the live user units on the Azure VM
(`~/.config/systemd/user/sports-wnba-*`). The VM files are authoritative;
if you change a unit on the VM, copy it back here, and vice versa.

| Unit | Schedule (America/Detroit) |
| --- | --- |
| `sports-wnba-daily.timer` | daily 10:56 |
| `sports-wnba-shadow-capture.timer` | hourly :13, 10:00–22:00 (09:13/23:13 removed Sep 2026 — dead hours, zero games lost on the Jul–Aug schedule) |
| `sports-wnba-shadow-grade.timer` | daily 06:17 |

Prerequisites on the VM:

- checkout at `~/wnba_props` on clean `main` (+ stdlib venv at `~/wnba_props/.venv`)
- frozen v2 worktree at `~/wnba_props_shadow` (`git worktree add --track -b codex/wnba-shadow-v2 ~/wnba_props_shadow origin/codex/wnba-shadow-v2`)
- secrets in `~/.config/wnba_props/env` (mode 600), including `WNBA_PROPS_DISCORD_WEBHOOK_URL`
- user lingering enabled (`loginctl enable-linger azureuser` was already on)

Install / update:

```bash
cp scripts/systemd/sports-wnba-* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now sports-wnba-daily.timer sports-wnba-shadow-capture.timer sports-wnba-shadow-grade.timer
systemctl --user list-timers | grep wnba
```

Season end: `systemctl --user disable --now sports-wnba-daily.timer sports-wnba-shadow-capture.timer sports-wnba-shadow-grade.timer`; re-enable next season.
