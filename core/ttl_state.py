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
