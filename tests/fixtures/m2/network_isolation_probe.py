import socket


def probe(name: str, host: str, port: int) -> None:
    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"{name}: UNEXPECTEDLY_REACHABLE")
    except OSError as exc:
        print(f"{name}: BLOCKED ({type(exc).__name__}: {exc})")


# Test direct IP connectivity, without relying on DNS.
probe("PUBLIC_IP_EGRESS", "1.1.1.1", 443)

# Test access to a service on the Docker host.
probe("HOST_BRIDGE_ACCESS", "172.30.0.1", 18092)
