"""Pure, conservative classification of observed challenge UI."""

from __future__ import annotations

import re
from typing import Any


def classify_observation(observation: dict[str, Any]) -> dict[str, Any]:
    """Classify visible evidence without inferring a vendor from script names alone."""
    title = str(observation.get("title") or "")
    visible_text = str(observation.get("visible_text") or "")
    text = f"{title}\n{visible_text}"
    folded = text.casefold()
    error = str(observation.get("error") or "")
    status = observation.get("http_status")
    frames = observation.get("visible_widget_frames") or []
    signals = observation.get("resource_vendor_signals") or []
    evidence: list[str] = []

    def add(item: str) -> None:
        if item not in evidence:
            evidence.append(item)

    def result(access: str, kind: str | None, handler: str) -> dict[str, Any]:
        return {
            "access_status": "rate_limited" if status == 429 else access,
            "challenge_kind": kind,
            "vendor_signals": list(dict.fromkeys(str(s) for s in signals if s)),
            "evidence": evidence,
            "handler_candidate": handler,
        }

    # HTTP outcomes take precedence over any coincidental page text.
    if status == 404:
        add("HTTP 404")
        return result("not_found", None, "none")
    if isinstance(status, int) and 500 <= status <= 599:
        add(f"HTTP {status}")
        return result("server_error", None, "none")

    if error:
        add(f"network error: {error}")
        # Preserve any clear visible challenge classification below.
        low_error = error.casefold()
        if any(term in low_error for term in ("timeout", "timed out", "tls", "ssl", "certificate", "connection", "network")):
            network_error = True
        else:
            network_error = True
    else:
        network_error = False

    blocked_re = r"request blocked|access denied|you have been blocked|\bforbidden\b"
    short_interstitial = visible_text.strip()[:300].casefold()
    if re.search(blocked_re, title, re.IGNORECASE) or re.search(
        r"request blocked|you have been blocked", short_interstitial, re.IGNORECASE
    ):
        add("表示本文にブロック文言")
        return result("blocked", "unknown", "unsupported")

    # Identify explicit interstitial copy, independent of incidental JS/resource names.
    verification_patterns = (
        r"additional verification",
        r"checking your browser",
        r"checking security",
        r"verifying you(?:'|’)re not a bot",
        r"verify that you are human",
        r"verify you are human",
        r"confirm you are human",
        r"人間であることを確認",
        r"ブラウザーを確認しています",
    )

    tile_count = observation.get("fourget_tile_count") or 0
    if isinstance(tile_count, int) and tile_count >= 16:
        add(f"4getタイル数: {tile_count}（16以上）")
        return result("challenge_observed", "image_grid", "tinyclip_fourget")

    ddg_tile_count = observation.get("duckduckgo_tile_count") or 0
    if isinstance(ddg_tile_count, int) and ddg_tile_count >= 9:
        add(f"DuckDuckGoタイル数: {ddg_tile_count}（9以上）")
        return result("challenge_observed", "image_grid", "browser_interaction_candidate")

    ddg_bot_notice = re.search(r"unfortunately, bots use duckduckgo too", folded)
    ddg_challenge_prompt = re.search(r"please complete the following challenge", folded)
    ddg_select_squares = re.search(r"select all squares containing a[n]?\s+\w+", folded)
    if ddg_bot_notice and ddg_challenge_prompt and ddg_select_squares:
        add("DuckDuckGoのbot説明・challenge指示・タイル選択文")
        return result("challenge_observed", "image_grid", "browser_interaction_candidate")

    if observation.get("yandex_order_visible"):
        add("Yandex AdvancedCaptchaの画像順序UI")
        return result("challenge_observed", "image_order", "unsupported")

    captcha_inputs = observation.get("captcha_text_inputs") or 0
    captcha_images = observation.get("captcha_images") or 0
    if captcha_inputs > 0 and captcha_images > 0:
        add("captcha画像と明示captchaテキスト入力")
        return result("challenge_observed", "text_input", "text_ocr_candidate")

    frame_grid = False
    frame_checkbox = False
    visible_known_frame = False
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        vendor = str(frame.get("vendor") or "").casefold()
        known_vendor = any(v in vendor for v in ("recaptcha", "hcaptcha", "turnstile", "arkose", "geetest", "yandex_smartcaptcha"))
        if known_vendor:
            visible_known_frame = True
        if frame.get("image_grid_visible"):
            frame_grid = True
        if known_vendor and frame.get("checkbox_visible"):
            frame_checkbox = True
    if frame_grid:
        add("可視widget frame内に画像グリッド")
        return result("challenge_observed", "image_grid", "browser_interaction_candidate")
    if frame_checkbox:
        add("既知vendorの可視widget frame内にcheckbox")
        explicit_doc = any(re.search(p, folded) for p in verification_patterns)
        return result("challenge_observed" if explicit_doc else "widget_observed", "checkbox", "browser_interaction_candidate")

    slider_context = re.search(r"captcha|verify|verification|認証|認証にご協力", title, re.IGNORECASE)
    slider_action = re.search(r"drag.{0,50}(?:slider|piece|puzzle)|ドラッグ.{0,80}(?:パズル|抜けている部分)|click to verify", folded)
    if slider_context and slider_action:
        add("認証ページ内にslider/puzzle操作の指示")
        return result("challenge_observed", "slider_puzzle", "unsupported")

    if any(re.search(p, folded) for p in verification_patterns):
        add("表示本文にブラウザー検証の明示")
        return result("challenge_observed", "browser_verification", "browser_interaction_candidate")

    if visible_known_frame:
        add("既知vendorの可視widget frame")
        return result("widget_observed", "browser_verification", "browser_interaction_candidate")

    # Resource names and ordinary mentions are clues only; they do not prove a challenge.
    if isinstance(status, int) and status == 403:
        add("HTTP 403")
        return result("blocked", "unknown", "unsupported")
    if status == 429:
        add("HTTP 429")
        return result("rate_limited", None, "none")
    if network_error:
        return result("network_error", None, "none")
    if observation.get("visible_text_len", len(visible_text)) > 0 or visible_text.strip():
        add("本文を観測")
        return result("content_observed", None, "none")
    if status is not None:
        add(f"HTTP {status}")
    return result("no_content", None, "none")
