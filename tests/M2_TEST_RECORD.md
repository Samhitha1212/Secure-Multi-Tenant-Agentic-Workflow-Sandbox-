### M2-01: Per-execution CPU and memory configuration — PASSED

- Added centralized ExecutionConfig.
- CPU and memory accepted through CLI.
- Resource configuration passed through LangGraph.
- Runner applies limits through OpenSandbox.
- Docker verified CPU = 1 core.
- Docker verified memory = 1 GiB.
- Runtime verified as runsc.
- Initial PID limit at this checkpoint: 4096 (later reduced to 256 and verified in M2-03).
- Resource probe completed successfully.
- Exit code: 0.

Checkpoint note:
At the M2-01 checkpoint, dedicated resource-exhaustion
tests were pending. Subsequent process, CPU and memory
enforcement tests are documented in M2-03, M2-05 and M2-06.


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


### M2-04: Network isolation — PASSED

Configuration:
- OpenSandbox version: 1.2.0
- Runtime: gVisor (runsc)
- Docker network: m2-sandbox-isolated-test
- Network driver: bridge
- Network subnet: 172.30.0.0/24
- Docker network Internal: true
- Inter-container communication: disabled (ICC=false)
- Published sandbox ports: 127.0.0.1 only
- Host firewall rule: DROP new connections from 172.30.0.0/24
  arriving through br-217a662d5b23.

Integration test:
- Executed through the LangGraph workflow and OpenSandbox runner.
- Real Python execution inside a gVisor sandbox.
- Workflow execution succeeded with exit code 0.

Network tests:

1. Public IP egress:
   - Target: 1.1.1.1:443
   - Result: BLOCKED
   - Error: Network is unreachable

2. Sandbox-to-host access:
   - Target: 172.30.0.1:18092
   - Result: BLOCKED
   - Error: Connection timed out
   - Host firewall DROP counter increased.

3. System DNS:
   - Target: example.com
   - Result: BLOCKED
   - Error: Temporary failure in name resolution

4. Direct DNS over UDP:
   - Target: 1.1.1.1:53
   - Result: BLOCKED
   - Error: Network is unreachable

5. Inter-container communication:
   - Tested using containers attached to the internal Docker network.
   - Result: Connection blocked.

6. OpenSandbox execution and proxy:
   - A real Python workflow executed successfully on the named network.
   - Sandbox output was returned to the application.
   - Sandbox cleanup verified.

Important execution references:
- Successful real workflow:
  Execution ID: 5d0c4fc2-db0d-45dd-8044-2afe18c9dbdd
  Sandbox ID: 9e638e9a-e3b2-4d0a-99ea-d55c75f6034d

- Network isolation probe:
  Execution ID: 20ba0417-c9b8-437e-9ad9-30d261e80b25
  Sandbox ID: 2ee0c132-77c4-4bdb-a607-abfe74c41b56

- DNS isolation probe:
  Execution ID: f9d85df7-adc8-4e97-969c-638299c8c626
  Sandbox ID: fddaddf5-a7f3-46c8-b378-2e537ccc76b5

Firewall observation:
- DROP counter increased from 10 to 35 packets.
- Final observed counter: 35 packets, 2324 bytes.

Limitations:
- OpenSandbox networkPolicy is not supported with this
  user-defined Docker network and current gVisor configuration.
- DNS-over-TCP, DNS-over-HTTPS and DNS-over-TLS were not tested.
- The host firewall rule is currently a runtime iptables rule.
  It is not automatically persistent across host reboots.
- The tests establish the behavior of the network paths tested;
  they do not prove that every possible network path is blocked.

Conclusion:
The internal Docker network, gVisor runtime and host
firewall rule provide demonstrated isolation against the
tested public-IP, DNS, host-access and inter-container paths.

### M2-05: CPU quota enforcement — PASSED

Configuration:
- CPU limit: 500m
- Docker NanoCpus: 500000000
- cgroup CPU quota: 50000 100000
- Runtime: gVisor (runsc)
- Memory limit: 512Mi

Workload:
- CPU-intensive Python integer calculation loop.
- Execution duration: approximately 15 seconds.
- Workflow exit code: 0.
- Workflow status: succeeded.

Host-side cgroup measurements:
- CPU periods: 153
- Throttled periods: 150
- Throttling ratio: approximately 98%.
- CPU usage: 7650109 microseconds.
- Throttled time: 16271951 microseconds.

Conclusion:
The host-side cgroup measurements demonstrate that
the configured 500m CPU quota was actively enforced
under a CPU-intensive workload.

Evidence:
- tests/evidence/2026-11-04/m2-cpu-workflow.log
- tests/evidence/2026-11-04/m2-cpu-cgroup.log


### M2-06: Memory and swap enforcement — DEMONSTRATED

Configuration:
- RAM limit: 512Mi (536870912 bytes).
- Combined RAM and swap limit: 1Gi (1073741824 bytes).
- cgroup memory.max: 536870912.
- cgroup memory.swap.max: 536870912.
- Runtime: gVisor (runsc).

Test 1 — 640MiB allocation:
- Python allocated 640MiB successfully.
- Peak RAM accounting reached approximately 512MiB.
- Swap usage reached approximately 151MiB.
- No OOM kill occurred.
- Workflow completed successfully.

Test 2 — 1152MiB allocation:
- Python attempted to allocate 1152MiB in 16MiB chunks.
- Output reached ALLOCATED_MIB=992 before stopping.
- Host-side memory.current approached 512MiB.
- Host-side memory.swap.current approached 512MiB.
- memory.events max: 2040.
- memory.events oom: 1.
- memory.events oom_kill: 1.
- The container cgroup was subsequently removed.
- Workflow reported timed_out after 30 seconds.

Conclusion:
The cgroup measurements demonstrate enforcement of
the configured 512MiB RAM plus 512MiB swap resource
ceiling under a memory-intensive workload.

Follow-up:
The original memory experiment exposed that the runner
reported the OOM scenario as a timeout. This was addressed
in M2-07 by querying OpenSandbox runtime diagnostics before
sandbox cleanup and distinguishing confirmed OOM termination
from an ordinary execution timeout.

When Docker reports OOMKilled:
- Runner sets oom_killed=true.
- Runner sets timed_out=false.
- Runner reports exit code 137.
- LangGraph classifies the result as resource_exhausted.
- CLI exits with code 137.

If diagnostics are unavailable, the runner conservatively
preserves the ordinary timeout classification.

Evidence:
- tests/evidence/2026-11-04/m2-memory-workflow.log
- tests/evidence/2026-11-04/m2-memory-cgroup.log
- tests/evidence/2026-11-04/m2-memory-swap.log
- tests/evidence/2026-11-04/m2-memory-oom-workflow.log
- tests/evidence/2026-11-04/m2-memory-oom-cgroup.log

### M2-07: OOM detection and workflow classification — PASSED

Configuration:
- CPU limit: 500m.
- Memory limit: 512Mi.
- Execution timeout: 30 seconds.
- Runtime: gVisor (runsc).

Workload:
- Used tests/fixtures/m2/memory_limit_probe.py.
- Python allocated memory in 16MiB increments.
- Output reached ALLOCATED_MIB=992 before termination.

OpenSandbox diagnostics:
- Sandbox state: Failed.
- Docker event: OOMKilled.
- Container exit code: 137.

Runner result:
- exit_code: 137.
- timed_out: false.
- oom_killed: true.
- Output preserved up to the termination point.

Workflow and CLI result:
- Workflow status: resource_exhausted.
- CLI exit code: 137.

Conclusion:
The end-to-end test demonstrates that the runner detects
a Docker-reported OOM kill before sandbox cleanup, and that
the workflow distinguishes resource exhaustion from an
ordinary execution timeout.

Evidence:
- tests/evidence/2026-11-04/m2-memory-oom-diagnostic-workflow.log
- tests/evidence/2026-11-04/m2-memory-oom-final.log

Note:
An HTTP 502 proxy readiness response occurred during startup
and was successfully retried. The execution and OOM
classification completed successfully.
