import os
import time

DURATION_SECONDS = 15

print("CPU_PROBE_STARTED", flush=True)
print(f"PID: {os.getpid()}", flush=True)
print(f"Duration: {DURATION_SECONDS} seconds", flush=True)

start = time.monotonic()
iterations = 0
value = 1

while time.monotonic() - start < DURATION_SECONDS:
    value = (value * 31 + 17) % 1_000_000_007
    iterations += 1

elapsed = time.monotonic() - start

print("CPU_PROBE_FINISHED", flush=True)
print(f"Wall time: {elapsed:.2f} seconds", flush=True)
print(f"Iterations: {iterations}", flush=True)
print(f"Final value: {value}", flush=True)
