from __future__ import annotations

import html
import re

from app.config import Settings
from app.integrations.bitrix24.urls import (
    build_deal_url,
    build_lead_url,
)

_CRM_REFERENCE = re.compile(
    r"\[(?P<markdown_type>Лид|Сделка) #(?P<markdown_id>\d+)\]"
    r"\((?P<markdown_url>https://[^\s)]+)\)"
    r"|(?<!\[)(?P<plain_label>лид(?:а|у|е|ом|ы|ов)?|"
    r"сделк(?:а|и|у|е|ой)) #(?P<plain_id>\d+)",
    re.IGNORECASE,
)


def render_safe_crm_links_html(
    text: str,
    settings: Settings,
) -> str | None:
    """Render explicit CRM card references as safe Telegram HTML links.

    Trusted Markdown links must exactly match the configured Bitrix portal. Plain
    typed references such as ``Лид #123`` and ``Сделка #456`` receive a URL built
    locally from the same configuration. A bare ``#123`` is deliberately ignored
    because its CRM entity type is ambiguous.

    None means that no trusted CRM reference was found and the caller should send
    the original text without parse mode. All non-link text is escaped whenever
    HTML rendering is used, so arbitrary model output cannot inject Telegram markup.
    """

    if not text or not settings.bitrix24_configured:
        return None

    parts: list[str] = []
    cursor = 0
    trusted_links = 0

    for match in _CRM_REFERENCE.finditer(text):
        markdown_type = match.group("markdown_type")
        plain_label = match.group("plain_label")
        label_type = markdown_type or plain_label or ""
        entity_id = (
            match.group("markdown_id")
            or match.group("plain_id")
            or ""
        )
        is_lead = label_type.lower().startswith("лид")

        try:
            expected_url = (
                build_lead_url(
                    settings.bitrix24_webhook_url,
                    entity_id,
                )
                if is_lead
                else build_deal_url(
                    settings.bitrix24_webhook_url,
                    entity_id,
                )
            )
        except ValueError:
            return None

        candidate_url = match.group("markdown_url")
        if candidate_url is not None and candidate_url != expected_url:
            continue

        parts.append(
            html.escape(
                text[cursor:match.start()]
            )
        )
        label = (
            f"{markdown_type} #{entity_id}"
            if markdown_type is not None
            else match.group(0)
        )
        parts.append(
            '<a href="'
            + html.escape(
                expected_url,
                quote=True,
            )
            + '">'
            + html.escape(label)
            + "</a>"
        )
        cursor = match.end()
        trusted_links += 1

    if trusted_links == 0:
        return None

    parts.append(
        html.escape(
            text[cursor:]
        )
    )
    return "".join(parts)
