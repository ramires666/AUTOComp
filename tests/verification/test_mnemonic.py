from autocomp.verification.mnemonic import compare_mnemonic_exports, normalize_mnemonic_export


def test_comments_and_layout_do_not_change_logic():
    baseline = "LD M100 // Start pump\nOUT Y0\n"
    translated = "  LD   M100 // Start pump\n/* translated heading */\nOUT Y0\n"
    assert compare_mnemonic_exports(baseline, translated).identical


def test_quoted_protocol_text_is_preserved():
    assert normalize_mnemonic_export('MOV "A;B" DM0 ; note\n') == 'MOV "A;B" DM0 ; note\n'


def test_semicolon_comments_require_explicit_format_confirmation():
    comparison = compare_mnemonic_exports(
        "LD R1 ; 中文\n",
        "LD R1 ; English\n",
        semicolon_comments=True,
    )
    assert comparison.identical


def test_literal_whitespace_change_is_detected():
    assert not compare_mnemonic_exports('MOV "A  B" DM0\n', 'MOV "A B" DM0\n').identical


def test_operand_change_is_detected():
    assert not compare_mnemonic_exports("LD M100\n", "LD M101\n").identical


def test_hash_prefixed_constant_change_is_detected():
    assert not compare_mnemonic_exports("MOV #90 DM530\n", "MOV #100 DM530\n").identical


def test_multiline_block_comments_are_ignored():
    baseline = "LD R1\nOUT R2\n"
    candidate = "/* 寿命\n设置 */\nLD R1\nOUT R2\n"
    assert compare_mnemonic_exports(baseline, candidate).identical
