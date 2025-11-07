"""
频道工具函数
提供频道标识的规范化与去重
"""

import re
from typing import Iterable, List, Optional


def normalize_channel_token(token: Optional[str]) -> Optional[str]:
    """将各种输入规范化为可写入 channels.txt 的形式。
    支持:
    - https://t.me/c/<id>/<msg>  -> -100<id>
    - https://t.me/<username>/<msg> -> @<username>
    - @username / -100... / 纯数字(自动补 -100)
    """
    if not token:
        return None
    s = token.strip()
    if not s:
        return None

    m = re.match(r"https?://t\.me/c/(\d+)(?:/\d+)?", s)
    if m:
        return f"-100{m.group(1)}"

    m2 = re.match(r"https?://t\.me/([A-Za-z0-9_]+)(?:/\d+)?", s)
    if m2:
        return f"@{m2.group(1)}"

    if s.startswith('@'):
        return '@' + s[1:].strip()

    if s.startswith('-') and s[1:].isdigit():
        return s

    if s.isdigit():
        return s if s.startswith('-100') else f"-100{s}"

    return None


def dedup_channels(channels: Iterable[str]) -> List[str]:
    """对频道列表去重（对 @username 忽略大小写）。"""
    seen = set()
    result: List[str] = []
    for ch in channels:
        norm = normalize_channel_token(ch) or ch
        key = norm.lower() if norm.startswith('@') else norm
        if key in seen:
            continue
        seen.add(key)
        result.append(norm)
    return result

