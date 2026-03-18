from __future__ import annotations

import os
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.tz import now_beijing


def _normalized(text: str) -> str:
    return re.sub(r"[\s_\-:]+", "", text.strip().lower())


def _compact(text: str) -> str:
    return " ".join(text.split())


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: list[dict[str, Any]] = []
        self.labels_by_for: dict[str, str] = {}
        self._in_label = False
        self._label_for = ""
        self._label_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k: (v or "") for k, v in attrs}
        if tag == "label":
            self._in_label = True
            self._label_for = attr_map.get("for", "")
            self._label_buffer = []
            return

        if tag not in {"input", "textarea", "select"}:
            return

        input_type = attr_map.get("type", "").strip().lower() if tag == "input" else tag
        if input_type in {"hidden", "submit", "button", "reset"}:
            return

        field_id = attr_map.get("id", "").strip()
        field_name = attr_map.get("name", "").strip()
        placeholder = attr_map.get("placeholder", "").strip()
        inline_label = _compact("".join(self._label_buffer)) if self._in_label else ""

        selector = ""
        if field_id:
            selector = f"#{field_id}"
        elif field_name:
            selector = f'[name="{field_name}"]'
        else:
            selector = tag

        self.fields.append(
            {
                "tag": tag,
                "input_type": input_type,
                "id": field_id,
                "name": field_name,
                "placeholder": placeholder,
                "inline_label": inline_label,
                "selector": selector,
            }
        )

    def handle_data(self, data: str) -> None:
        if not self._in_label:
            return
        text = _compact(data)
        if text:
            self._label_buffer.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag != "label":
            return
        label_text = _compact("".join(self._label_buffer))
        if self._label_for and label_text:
            self.labels_by_for[self._label_for] = label_text
        self._in_label = False
        self._label_for = ""
        self._label_buffer = []


_FIELD_HINTS: dict[str, list[str]] = {
    "name": ["name", "fullname", "realname", "姓名", "称呼"],
    "phone": ["phone", "mobile", "tel", "电话", "手机号", "手机"],
    "email": ["email", "mail", "邮箱"],
    "school": ["school", "university", "college", "学校", "院校"],
    "major": ["major", "专业"],
    "degree": ["degree", "education", "学历", "学位"],
    "project": ["project", "experience", "项目", "经历", "实习"],
    "summary": ["summary", "about", "intro", "bio", "自我介绍", "个人介绍"],
    "github": ["github", "gitlab", "代码仓库"],
}


def _infer_field_kind(*, name: str, field_id: str, placeholder: str, label: str) -> tuple[str | None, float]:
    normalized_text = " ".join(
        filter(
            None,
            [
                _normalized(name),
                _normalized(field_id),
                _normalized(placeholder),
                _normalized(label),
            ],
        )
    )
    if not normalized_text:
        return None, 0.0

    for kind, hints in _FIELD_HINTS.items():
        for hint in hints:
            if _normalized(hint) and _normalized(hint) in normalized_text:
                confidence = 0.92 if _normalized(hint) in _normalized(name + field_id) else 0.78
                return kind, confidence
    return None, 0.0


def _pick_profile_value(kind: str, profile: dict[str, str]) -> str | None:
    candidates: dict[str, list[str]] = {
        "name": ["name", "full_name", "姓名"],
        "phone": ["phone", "mobile", "手机号"],
        "email": ["email", "邮箱"],
        "school": ["school", "university", "学校"],
        "major": ["major", "专业"],
        "degree": ["degree", "学历"],
        "project": ["project", "project_summary", "项目经历"],
        "summary": ["summary", "self_intro", "自我介绍"],
        "github": ["github", "github_url"],
    }
    keys = candidates.get(kind, [])
    for key in keys:
        value = str(profile.get(key, "")).strip()
        if value:
            return value
    return None


def preview_form_autofill(html: str, profile: dict[str, str]) -> list[dict[str, Any]]:
    parser = _FormParser()
    parser.feed(html)
    parser.close()

    results: list[dict[str, Any]] = []
    for field in parser.fields:
        label = field.get("inline_label") or parser.labels_by_for.get(field.get("id") or "", "")
        inferred, confidence = _infer_field_kind(
            name=str(field.get("name") or ""),
            field_id=str(field.get("id") or ""),
            placeholder=str(field.get("placeholder") or ""),
            label=label,
        )
        suggested = _pick_profile_value(inferred, profile) if inferred else None
        if not suggested and inferred == "email":
            input_type = str(field.get("input_type") or "")
            if "email" in input_type:
                suggested = _pick_profile_value("email", profile)
                confidence = max(confidence, 0.68)
        results.append(
            {
                "selector": field.get("selector"),
                "tag": field.get("tag"),
                "input_type": field.get("input_type"),
                "field_name": field.get("name") or None,
                "label": label or None,
                "inferred_type": inferred,
                "suggested_value": suggested,
                "confidence": round(confidence, 2),
            }
        )
    return results


def _headless() -> bool:
    return os.getenv("BOSS_HEADLESS", "false").strip().lower() in {"1", "true", "yes"}


def _profile_dir() -> Path:
    configured = os.getenv("BOSS_BROWSER_PROFILE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parents[1] / ".playwright" / "form_autofill").resolve()


def _screenshot_dir() -> Path:
    configured = os.getenv("BOSS_SCREENSHOT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parents[1] / "exports" / "screenshots").resolve()


def _timeout_ms() -> int:
    raw = os.getenv("FORM_AUTOFILL_TIMEOUT_MS", "20000").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 20000
    return max(5000, min(value, 120000))


def _safe_url_part(url: str) -> str:
    parsed = urlparse(url)
    raw = f"{parsed.netloc}{parsed.path}".strip("/") or "page"
    safe = re.sub(r"[^0-9a-zA-Z_-]+", "_", raw).strip("_")
    return (safe[:60] or "page").lower()


def _screenshot(page: Any, *, prefix: str, url: str) -> str | None:
    try:
        shot_dir = _screenshot_dir()
        shot_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_beijing().strftime("%Y%m%d_%H%M%S")
        file_name = f"{stamp}_{prefix}_{_safe_url_part(url)}.png"
        path = shot_dir / file_name
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return None


def _extract_form_html(page: Any) -> str:
    try:
        forms = page.locator("form")
        if forms.count() > 0:
            html = forms.first.evaluate("node => node.outerHTML")
            if isinstance(html, str) and html.strip():
                return html
    except Exception:
        pass
    return page.content()


def _playwright_error(exc: Exception) -> RuntimeError:
    message = str(exc)
    if (
        "Executable doesn't exist" in message
        or "Host system is missing dependencies" in message
        or "libnspr4.so" in message
    ):
        return RuntimeError(
            "Playwright Chromium browser is unavailable. Run: "
            "playwright install chromium && playwright install-deps chromium"
        )
    return RuntimeError(message)


def preview_form_autofill_url(url: str, profile: dict[str, str]) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("Playwright is not installed in current environment") from exc

    context = None
    try:
        with sync_playwright() as p:
            _profile_dir().mkdir(parents=True, exist_ok=True)
            _screenshot_dir().mkdir(parents=True, exist_ok=True)
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(_profile_dir()),
                    headless=_headless(),
                )
            except Exception as exc:
                raise _playwright_error(exc) from exc

            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(_timeout_ms())
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(900)
            html = _extract_form_html(page)
            fields = preview_form_autofill(html, profile)
            mapped = sum(1 for item in fields if item.get("suggested_value"))
            return {
                "url": url,
                "total_fields": len(fields),
                "mapped_fields": mapped,
                "screenshot_path": _screenshot(page, prefix="autofill_preview", url=url),
                "fields": fields,
            }
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Autofill URL preview failed: {exc}") from exc
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass


def _fill_one_field(page: Any, field: dict[str, Any]) -> tuple[bool, str | None]:
    selector = str(field.get("selector") or "").strip()
    tag = str(field.get("tag") or "").strip().lower()
    input_type = str(field.get("input_type") or "").strip().lower()
    value = str(field.get("suggested_value") or "").strip()

    if not selector:
        return False, "empty selector"
    if not value:
        return False, "empty suggested value"
    if tag == "input" and input_type in {"checkbox", "radio", "file"}:
        return False, f"unsupported input_type={input_type}"

    locator = page.locator(selector).first
    if locator.count() == 0:
        return False, "selector not found"

    try:
        if tag == "select":
            try:
                locator.select_option(value=value)
            except Exception:
                locator.select_option(label=value)
        else:
            locator.fill(value)
        return True, None
    except Exception as exc:
        return False, str(exc)


def fill_form_autofill_url(
    url: str,
    profile: dict[str, str],
    *,
    max_actions: int = 20,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("Playwright is not installed in current environment") from exc

    context = None
    try:
        with sync_playwright() as p:
            _profile_dir().mkdir(parents=True, exist_ok=True)
            _screenshot_dir().mkdir(parents=True, exist_ok=True)
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(_profile_dir()),
                    headless=_headless(),
                )
            except Exception as exc:
                raise _playwright_error(exc) from exc

            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(_timeout_ms())
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(900)

            html = _extract_form_html(page)
            fields = preview_form_autofill(html, profile)
            mapped = [item for item in fields if item.get("suggested_value")]

            attempted = 0
            filled = 0
            failed = 0
            actions: list[dict[str, Any]] = []
            for idx, field in enumerate(mapped):
                selector = str(field.get("selector") or "")
                value_preview = str(field.get("suggested_value") or "")[:120]
                if idx >= max_actions:
                    actions.append(
                        {
                            "selector": selector or "(unknown)",
                            "status": "skipped",
                            "reason": f"max_actions={max_actions} reached",
                            "value_preview": value_preview,
                        }
                    )
                    continue
                attempted += 1
                ok, reason = _fill_one_field(page, field)
                if ok:
                    filled += 1
                    actions.append(
                        {
                            "selector": selector,
                            "status": "filled",
                            "reason": None,
                            "value_preview": value_preview,
                        }
                    )
                else:
                    failed += 1
                    actions.append(
                        {
                            "selector": selector or "(unknown)",
                            "status": "failed",
                            "reason": (reason or "unknown")[:240],
                            "value_preview": value_preview,
                        }
                    )

            return {
                "url": url,
                "attempted_fields": attempted,
                "filled_fields": filled,
                "failed_fields": failed,
                "screenshot_path": _screenshot(page, prefix="autofill_filled", url=url),
                "actions": actions,
            }
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Autofill URL fill failed: {exc}") from exc
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
