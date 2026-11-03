import os
import time

print("RESOURCE_PROBE_STARTED", flush=True)
print(f"Process ID: {os.getpid()}", flush=True)

time.sleep(25)

print("RESOURCE_PROBE_FINISHED", flush=True)
