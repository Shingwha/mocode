"""Feishu channel configuration defaults."""

from __future__ import annotations

_DEFAULTS = {
    "app_id": "",
    "app_secret": "",
    "encrypt_key": "",
    "verification_token": "",
    "domain": "feishu",  # "feishu" or "lark"
    "allow_from": ["*"],
    "group_policy": "mention",  # "open" or "mention"
    "reply_to_message": False,
}


def get_feishu_config(gateway_config: dict) -> dict:
    """Merge gateway_config with feishu defaults."""
    cfg = dict(_DEFAULTS)
    cfg.update(gateway_config)
    return cfg
