"""Skills and subagents are only usable if Claude Code can parse their YAML header: an unquoted ': ' in a
description silently dropped the fundamental-analyst for a whole release. Every header must parse and
carry the fields Claude Code needs."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1] / ".claude"
FILES = sorted(ROOT.glob("agents/*.md")) + sorted(ROOT.glob("skills/*/SKILL.md"))


@pytest.mark.parametrize("path", FILES, ids=[f"{p.parent.name}/{p.name}" for p in FILES])
def test_header_parses_with_name_and_description(path):
    match = re.match(r"---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    assert match, "missing --- header ---"
    header = yaml.safe_load(match.group(1))
    assert isinstance(header, dict) and header.get("name") and header.get("description")
    expected = path.stem if path.parent.name == "agents" else path.parent.name
    assert header["name"] == expected
