"""Skill 配置加载器 — 解析 skills/jd-filter/SKILL.md。

遵循 OpenClaw Skills 规范：YAML frontmatter + Markdown body。
后端在运行时读取此文件，驱动方向门控正则和 LLM prompt 组装。
文件修改后下一次调用自动生效（热加载），无需重启。
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_SKILL_DIR = Path(__file__).resolve().parent.parent.parent / "skills" / "jd-filter"
_SKILL_FILE = _SKILL_DIR / "SKILL.md"

_cache: _SkillConfig | None = None
_cache_mtime: float = 0.0


@dataclass
class _SkillConfig:
    """jd-filter Skill 解析后的结构化配置。"""
    intent: str = ""
    strong_accept_keywords: list[str] = field(default_factory=list)
    accept_keywords: list[str] = field(default_factory=list)
    reject_keywords: list[str] = field(default_factory=list)
    title_block_keywords: list[str] = field(default_factory=list)
    title_require_app_keywords: list[str] = field(default_factory=list)
    reject_rules: list[str] = field(default_factory=list)
    accept_rules: list[str] = field(default_factory=list)
    principles: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)

    _strong_accept_re: re.Pattern | None = field(default=None, repr=False)
    _accept_re: re.Pattern | None = field(default=None, repr=False)
    _reject_re: re.Pattern | None = field(default=None, repr=False)

    @property
    def strong_accept_re(self) -> re.Pattern:
        if self._strong_accept_re is None:
            pattern = "|".join(re.escape(k) for k in self.strong_accept_keywords) if self.strong_accept_keywords else r"(?!)"
            self._strong_accept_re = re.compile(pattern, re.IGNORECASE)
        return self._strong_accept_re

    @property
    def accept_re(self) -> re.Pattern:
        if self._accept_re is None:
            all_kws = self.strong_accept_keywords + self.accept_keywords
            pattern = "|".join(re.escape(k) for k in all_kws) if all_kws else r"(?!)"
            self._accept_re = re.compile(pattern, re.IGNORECASE)
        return self._accept_re

    @property
    def reject_re(self) -> re.Pattern:
        if self._reject_re is None:
            pattern = "|".join(re.escape(k) for k in self.reject_keywords) if self.reject_keywords else r"(?!)"
            self._reject_re = re.compile(pattern, re.IGNORECASE)
        return self._reject_re


def _parse_skill_md(text: str) -> _SkillConfig:
    """解析 SKILL.md 文本为结构化配置。"""
    cfg = _SkillConfig()

    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]

    current_section: str = ""
    current_subsection: str = ""

    for line in body.splitlines():
        stripped = line.strip()

        if stripped.startswith("## "):
            current_section = stripped.lstrip("# ").strip()
            current_subsection = ""
            continue
        if stripped.startswith("### "):
            current_subsection = stripped.lstrip("# ").strip()
            continue

        if stripped.startswith("- ") or stripped.startswith("* "):
            item = stripped[2:].strip()
            if not item:
                continue

            section_lower = current_section.lower()
            subsection_lower = current_subsection.lower()

            if "intent" in section_lower:
                continue

            if "direction keywords" in section_lower:
                if subsection_lower.startswith("strong accept"):
                    cfg.strong_accept_keywords.append(item)
                elif subsection_lower.startswith("accept"):
                    cfg.accept_keywords.append(item)
                elif subsection_lower.startswith("reject"):
                    cfg.reject_keywords.append(item)
                elif subsection_lower.startswith("title require"):
                    cfg.title_require_app_keywords.append(item)
                elif subsection_lower.startswith("title block"):
                    cfg.title_block_keywords.append(item)

            elif "llm decision rules" in section_lower:
                if subsection_lower.startswith("reject"):
                    cfg.reject_rules.append(item)
                elif subsection_lower.startswith("accept"):
                    cfg.accept_rules.append(item)
                elif subsection_lower.startswith("principle"):
                    cfg.principles.append(item)

        if "intent" in current_section.lower() and stripped and not stripped.startswith("#"):
            if stripped.startswith(">") or stripped == "---":
                continue
            if cfg.intent:
                cfg.intent += " " + stripped
            else:
                cfg.intent = stripped

    # 解析 Parameters 表格
    in_params = "parameters" in current_section.lower()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and "parameters" in stripped.lower():
            in_params = True
            continue
        if stripped.startswith("## ") and "parameters" not in stripped.lower():
            if in_params:
                in_params = False
            continue
        if in_params and stripped.startswith("|") and "---" not in stripped:
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if len(cells) >= 2 and cells[0] not in ("参数", "Parameter", ""):
                key = cells[0]
                val = cells[1]
                cfg.parameters[key] = val

    return cfg


def load_jd_filter_config() -> _SkillConfig:
    """加载 jd-filter Skill 配置（带文件 mtime 缓存，热加载）。"""
    global _cache, _cache_mtime

    skill_path = Path(os.getenv("JD_FILTER_SKILL_PATH", str(_SKILL_FILE)))

    if not skill_path.is_file():
        if _cache is not None:
            return _cache
        return _SkillConfig()

    try:
        mtime = skill_path.stat().st_mtime
    except OSError:
        if _cache is not None:
            return _cache
        return _SkillConfig()

    if _cache is not None and mtime == _cache_mtime:
        return _cache

    text = skill_path.read_text(encoding="utf-8")
    _cache = _parse_skill_md(text)
    _cache_mtime = mtime
    return _cache


# ── 便捷接口供 boss_scan.py 和 workflow.py 使用 ──

def get_strong_accept_re() -> re.Pattern:
    return load_jd_filter_config().strong_accept_re

def get_accept_re() -> re.Pattern:
    return load_jd_filter_config().accept_re

def get_reject_re() -> re.Pattern:
    return load_jd_filter_config().reject_re

def get_title_block_keywords() -> list[str]:
    return load_jd_filter_config().title_block_keywords

def get_title_require_app_keywords() -> list[str]:
    return load_jd_filter_config().title_require_app_keywords

def get_reject_rules() -> list[str]:
    return load_jd_filter_config().reject_rules

def get_accept_rules() -> list[str]:
    return load_jd_filter_config().accept_rules

def get_principles() -> list[str]:
    return load_jd_filter_config().principles

def get_intent() -> str:
    return load_jd_filter_config().intent

def get_parameter(key: str, default: str = "") -> str:
    return load_jd_filter_config().parameters.get(key, default)
