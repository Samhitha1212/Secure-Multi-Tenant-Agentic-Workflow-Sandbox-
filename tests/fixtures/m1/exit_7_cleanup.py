import time

print("NONZERO_EXIT_CLEANUP_TEST_STARTED", flush=True)
time.sleep(8)
print("NONZERO_EXIT_CLEANUP_TEST_FINISHED", flush=True)
raise SystemExit(7)
