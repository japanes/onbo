"""Shared media-URL helper.

A Q&A ``video_url`` (or a welcome video) is stored as a site-relative ``/media/...``
path — perfect for the web UI, which serves ``/media`` itself. Other channels
(Telegram) need an absolute URL, so we prefix ``media.base_url`` when set.
"""
from __future__ import annotations

from ..config import Settings


def media_url(settings: Settings, url: str) -> str:
    base = settings.media.base_url
    if base and url.startswith("/"):
        return base.rstrip("/") + url
    return url
