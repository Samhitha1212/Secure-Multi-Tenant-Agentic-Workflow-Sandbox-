import time

print("M1_REAL_SANDBOX_OK", flush=True)
print("2 + 3 =", 2 + 3, flush=True)

# Provides time to inspect the live Docker container.
time.sleep(8)

print("M1_REAL_SANDBOX_FINISHED", flush=True)
