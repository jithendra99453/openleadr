- [x] Implement acceptance-gated Sheets telemetry so it only runs when DR event accepted as yes
- [ ] Ensure telemetry loop stops sending once event duration expires (clear flag + clear event window)
- [ ] Update VEN client state transitions on accept/decline/cancel to keep telemetry consistent
- [ ] Quick manual verification via logs; optional pytest run


