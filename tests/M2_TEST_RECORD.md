### M2-01: Per-execution CPU and memory configuration — PASSED

- Added centralized ExecutionConfig.
- CPU and memory accepted through CLI.
- Resource configuration passed through LangGraph.
- Runner applies limits through OpenSandbox.
- Docker verified CPU = 1 core.
- Docker verified memory = 1 GiB.
- Runtime verified as runsc.
- PID limit observed: 4096.
- Resource probe completed successfully.
- Exit code: 0.

Note:
Actual resource-exhaustion enforcement tests are pending.


### M2-02: Bounded output collection — PASSED

- Implemented a shared stdout/stderr byte limit.
- Default output limit: 1 MiB.
- Tested with a 64 KiB output limit.
- Used a program generating 100,000 output lines.
- Output truncation detected successfully.
- Output retained within configured limit.
- Exit code: 0.
- Timed out: false.
- Sandbox execution completed successfully.

Result:
Output retention is bounded by the configured limit.


### M2-03: Process limit and process-exhaustion containment — PASSED

Configuration:
- OpenSandbox docker.pids_limit: 256
- Runtime: gVisor (runsc)

Test:
- Attempted to create 300 child processes.
- Used lightweight sleep processes.
- Docker observed maximum PIDs: 256.
- Process creation was blocked at the limit.
- Error reported by gVisor: Errno 12 (ENOMEM).
- Peak observed memory: 103.9 MiB / 512 MiB.
- All successfully created children were terminated and reaped.
- Execution completed without timeout.

Conclusion:
The sandbox process count reached the configured
256-PID ceiling and further process creation was rejected.

Evidence:
- m2-process-limit-light.log
- Docker process monitoring output
- Docker inspection confirming PidsLimit=256

Note:
The sandbox could not read the cgroup PID files internally.
Enforcement was verified through host-side Docker monitoring.