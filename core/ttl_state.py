"""
In-memory TTL device registry and state.
Used by TTL APIs and WebSocket status updates.
"""
TTL_DEVICES = [
    "TTL_A",
    "TTL_B",
    "TTL_C",
    "TTL_D",
]

TTL_STATE = {
    name: {
        "level": "LOW",
        "override": False,
        "value": False,
    }
    for name in TTL_DEVICES
}
