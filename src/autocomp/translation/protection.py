"""Protect non-translatable PLC fragments before provider calls."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Addresses, numeric constants, model references, and standard mnemonic instructions.
_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"(?:X|Y|R|MR|LR|CR|DM|EM|FM|TM|CM|W|B|Z|L|T|C)\d+(?:\.\d+)?"
    r"|(?:KV|KZ|VT)-[A-Za-z0-9-]+"
    r"|(?:0x[0-9A-Fa-f]+|\d+(?:\.\d+)?)"
    r"|(?:LD|LDI|AND|ANI|OR|ORI|OUT|SET|RST|MOV|DMOV|CALL|RET|END)"
    r")(?![A-Za-z0-9_])"
)


@dataclass(frozen=True, slots=True)
class ProtectedText:
    text: str
    tokens: tuple[str, ...]


def protect_tokens(text: str) -> ProtectedText:
    tokens: list[str] = []

    def replace(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        return f"[[PLC_TOKEN_{len(tokens) - 1}]]"

    return ProtectedText(_TOKEN_RE.sub(replace, text), tuple(tokens))


def restore_tokens(text: str, tokens: tuple[str, ...]) -> str:
    restored = text
    for index, token in enumerate(tokens):
        marker = f"[[PLC_TOKEN_{index}]]"
        if restored.count(marker) != 1:
            raise ValueError(f"protected token marker {index} is missing or duplicated")
        restored = restored.replace(marker, token)
    if "[[PLC_TOKEN_" in restored:
        raise ValueError("translation contains an unknown protected token marker")
    return restored
