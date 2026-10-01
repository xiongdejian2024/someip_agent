"""只供 Linux 隔离虚拟网注入；生产解析不引用这些人工选项向量。"""

OPTION_CASES = {
    "eol": (bytes.fromhex("00000000"), True),
    "nop_eol": (bytes.fromhex("01000000"), True),
    "copied_code0": (bytes.fromhex("80020000"), True),
    "max_padding": (bytes.fromhex("01") + bytes(39), True),
    "record_route": (bytes.fromhex("0707040000000000"), True),
    "timestamp": (bytes.fromhex("440c05000000000000000000"), True),
    "bad_padding": (bytes.fromhex("00000100"), False),
    "missing_length": (bytes.fromhex("0101019e"), False),
    "short_length": (bytes.fromhex("9e010000"), False),
    "overrun": (bytes.fromhex("9e050000"), False),
}
