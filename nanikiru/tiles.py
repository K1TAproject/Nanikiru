"""Tile codes and inventory for riichi-4p-v1: one red five per suit."""

from collections import Counter


def validate_code(code):
    if (not isinstance(code, str) or len(code) != 2 or code[1] not in "mpsz"
            or code[0] not in ("1234567" if code[1] == "z" else "0123456789")):
        raise ValueError("Expected a tile code, including 0m/0p/0s for red fives")
    return code


def index(code):
    validate_code(code)
    return "mpsz".index(code[1]) * 9 + (5 if code[0] == "0" else int(code[0])) - 1


def tile34(value):
    if type(value) is not int or not 0 <= value < 34:
        raise ValueError("Tile index must be 0..33")
    return f"{value % 9 + 1}{'mpsz'[value // 9]}"


def physical_capacity(code):
    validate_code(code)
    return 1 if code[0] == "0" else (3 if code in ("5m", "5p", "5s") else 4)


def counts34(codes):
    """Normalize red fives for shapes, while checking physical inventory."""
    physical = Counter(codes)
    counts = [0] * 34
    for code, count in physical.items():
        if count > physical_capacity(code):
            raise ValueError("Physical tile inventory exceeded")
        counts[index(code)] += count
    if max(counts) > 4:
        raise ValueError("Tile inventory exceeds four copies")
    return counts
