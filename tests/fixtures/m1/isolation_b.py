import time
from pathlib import Path

private_file = Path("/tmp/m1_tenant_a_private.txt")

if private_file.exists():
    print("ISOLATION_FAIL_FOREIGN_FILE_VISIBLE", flush=True)
    result = 41
else:
    print("ISOLATION_PASS_FOREIGN_FILE_NOT_VISIBLE", flush=True)
    result = 0

time.sleep(5)
raise SystemExit(result)
