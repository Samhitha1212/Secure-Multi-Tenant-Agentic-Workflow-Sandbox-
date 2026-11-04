import subprocess
from pathlib import Path

children = []
target = 300

print("PID_LIMIT_TEST_STARTED", flush=True)


def read_cgroup_file(name):
    path = Path("/sys/fs/cgroup") / name

    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return "unavailable"


print("PID_LIMIT:", read_cgroup_file("pids.max"), flush=True)
print("INITIAL_PID_COUNT:", read_cgroup_file("pids.current"), flush=True)
print("MEMORY_LIMIT:", read_cgroup_file("memory.max"), flush=True)

try:
    for i in range(target):
        try:
            child = subprocess.Popen(
                ["sleep", "30"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            children.append(child)

        except OSError as exc:
            print(f"PROCESS_CREATION_BLOCKED at child={i}", flush=True)
            print(f"Error: {exc}", flush=True)
            print(f"Errno: {exc.errno}", flush=True)
            break

    print(f"CHILDREN_CREATED={len(children)}", flush=True)
    print("CURRENT_PID_COUNT:", read_cgroup_file("pids.current"), flush=True)

finally:
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
print("FINAL_PID_COUNT:", read_cgroup_file("pids.current"), flush=True)
print("PID_LIMIT_TEST_FINISHED", flush=True)