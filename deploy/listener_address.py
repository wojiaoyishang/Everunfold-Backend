"""Select the container's interface address for the configured Docker network."""

import ipaddress
import socket
import struct
from collections.abc import Mapping


def listener_host(env: Mapping[str, str]) -> str:
    cidrs = env.get("EVERUNFOLD_LISTEN_CIDRS", "")
    if not cidrs:
        return env.get("API_HOST", "0.0.0.0")

    import fcntl

    subnets = [ipaddress.ip_network(part) for part in cidrs.split(",")]
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as handle:
        for _, interface in socket.if_nameindex():
            request = struct.pack("256s", interface.encode()[:15])
            try:
                response = fcntl.ioctl(handle.fileno(), 0x8915, request)
            except OSError:
                continue
            addresses.add(ipaddress.IPv4Address(response[20:24]))
    try:
        with open("/proc/net/if_inet6") as interfaces:
            for line in interfaces:
                addresses.add(ipaddress.IPv6Address(int(line.split()[0], 16)))
    except FileNotFoundError:
        pass
    candidates = {
        address
        for address in addresses
        if not address.is_loopback
        and not address.is_link_local
        and any(address in subnet for subnet in subnets)
    }
    preferred = {address for address in candidates if address.version == 4} or candidates
    if len(preferred) != 1:
        raise ValueError("无法在所选 Docker 网络上确定唯一监听地址，请核对容器网络。")
    return str(preferred.pop())
