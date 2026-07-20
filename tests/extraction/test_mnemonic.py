from autocomp.extraction.mnemonic import extract_mnemonic_inventory
from autocomp.translation.models import RiskLevel, TextKind


def test_extracts_block_line_and_semicolon_comments_with_context():
    export = """/* Program: Pump_Control */
/* Section: 启动逻辑 */
LD M100 // 启动条件
OUT Y0 ; 启动输出
"""
    records = extract_mnemonic_inventory(export, source_name="pump.lst")
    assert [record.source_text for record in records] == [
        "Section: 启动逻辑",
        "启动条件",
        "启动输出",
    ]
    assert [record.location for record in records] == ["pump.lst:2", "pump.lst:3", "pump.lst:4"]
    assert all(record.kind == TextKind.COMMENT for record in records)
    assert records[-1].hierarchy == ("program: Pump_Control", "section: 启动逻辑")
    assert records[-1].context == "OUT Y0"


def test_hash_numeric_constants_are_logic_not_comments():
    records = extract_mnemonic_inventory("MOV #100 DM0 ; 设定值\n")
    assert [record.source_text for record in records] == ["设定值"]
    assert records[0].context == "MOV #100 DM0"


def test_comment_delimiters_inside_string_literals_are_logic():
    records = extract_mnemonic_inventory('MOV "A;B//C" DM0 ; 操作员文本\n')
    assert [record.source_text for record in records] == ["操作员文本"]
    assert records[0].context == 'MOV "A;B//C" DM0'


def test_cjk_string_literal_is_high_risk_inventory():
    records = extract_mnemonic_inventory('MOV "报警" DM0\n')
    assert len(records) == 1
    assert records[0].source_text == "报警"
    assert records[0].kind == TextKind.STRING_LITERAL
    assert records[0].risk == RiskLevel.HIGH
    assert records[0].requires_review


def test_unknown_cjk_line_uses_review_required_fallback():
    records = extract_mnemonic_inventory("未知格式：设备状态\n")
    assert len(records) == 1
    assert records[0].kind == TextKind.OTHER
    assert records[0].requires_review
    assert records[0].risk == RiskLevel.MEDIUM
    assert records[0].context == "unrecognized raw line"


def test_multiline_block_comment_uses_opening_line_number():
    records = extract_mnemonic_inventory("/* 第一行\n第二行 */\n")
    assert records[0].source_text == "第一行\n第二行"
    assert records[0].location == "mnemonic.lst:1"
