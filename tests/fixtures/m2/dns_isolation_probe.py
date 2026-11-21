import socket
import struct

# Test the system-configured DNS resolver.
try:
    results = socket.getaddrinfo(
        "example.com", 443, type=socket.SOCK_STREAM
    )
    addresses = sorted({result[4][0] for result in results})
    print(f"SYSTEM_DNS: RESOLVED {addresses}")
except OSError as exc:
    print(f"SYSTEM_DNS: BLOCKED ({type(exc).__name__}: {exc})")


# Test direct DNS over UDP to a public resolver, bypassing system DNS.
try:
    name = b"".join(
        bytes([len(label)]) + label
        for label in b"example.com".split(b".")
    ) + b"\x00"

    query = struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    query += name + struct.pack("!HH", 1, 1)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(3)
        sock.sendto(query, ("1.1.1.1", 53))
        response, _ = sock.recvfrom(512)

    if len(response) >= 12 and response[:2] == b"\x12\x34":
        print("DIRECT_DNS_UDP: RESPONDED")
    else:
        print("DIRECT_DNS_UDP: INVALID_RESPONSE")

except OSError as exc:
    print(f"DIRECT_DNS_UDP: BLOCKED ({type(exc).__name__}: {exc})")
