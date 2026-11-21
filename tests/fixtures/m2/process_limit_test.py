import subprocess
import sys

children = []
target = 300

print("PID_LIMIT_TEST_STARTED", flush=True)

try:
    for i in range(target):
        try:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import time; time.sleep(60)",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            children.append(child)

        except OSError as exc:
            print(
                f"PROCESS_CREATION_BLOCKED at child={i}: {exc}",
                flush=True,
            )
            break

    print(f"CHILDREN_CREATED={len(children)}", flush=True)

finally:
    # Terminate all children before waiting for them.
    for child in children:
        if child.poll() is None:
            child.terminate()

    for child in children:
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()

print("ALL_CHILDREN_REAPED", flush=True)
print("PID_LIMIT_TEST_FINISHED", flush=True)
