import os
import time

CHUNK_MIB = 16
MAX_ALLOC_MIB = 1152

CHUNK_BYTES = CHUNK_MIB * 1024 * 1024
PAGE_SIZE = 4096

print("MEMORY_PROBE_STARTED", flush=True)
print(f"PID: {os.getpid()}", flush=True)

allocated_mib = 0
chunks = []

try:
    while allocated_mib < MAX_ALLOC_MIB:
        block = bytearray(CHUNK_BYTES)

        # Touch every page to commit physical memory.
        for offset in range(0, CHUNK_BYTES, PAGE_SIZE):
            block[offset] = 1

        chunks.append(block)
        allocated_mib += CHUNK_MIB

        print(f"ALLOCATED_MIB={allocated_mib}", flush=True)
        time.sleep(0.1)

except MemoryError:
    print("MEMORY_ALLOCATION_FAILED", flush=True)

print("MEMORY_PROBE_FINISHED", flush=True)
print(f"TOTAL_ALLOCATED_MIB={allocated_mib}", flush=True)
