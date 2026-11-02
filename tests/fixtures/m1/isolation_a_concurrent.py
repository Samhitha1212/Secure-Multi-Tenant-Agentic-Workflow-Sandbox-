import time
from pathlib import Path

private_file = Path("/tmp/m1_tenant_a_private.txt")
private_file.write_text(
    "PRIVATE_DATA_FROM_EXECUTION_A",
    encoding="utf-8",
)

print("EXECUTION_A_FILE_CREATED", flush=True)
time.sleep(45)
print("EXECUTION_A_FINISHED", flush=True)
