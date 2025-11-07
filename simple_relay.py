# -*- coding: utf-8 -*-
"""\nSimple Telegram relay bot\n- 私聊中：管理员把消息转发给机器人 → 机器人并发分发到已配置的多个频道\n- 自动替换消息内 t.me/c/<id>/<msg> 的频道ID为目标频道ID\n- 私聊中：直接发送频道消息链接/@用户名/-100ID，可自动批量添加到转发列表\n依赖：python-telegram-bot==20.x\n环境变量：BOT_TOKEN, ADMIN_IDS(可选，逗号分隔)\n频道列表：channels.json（含 id/token/name/username）\n"""

import asyncio
import contextlib
import os
import re
import time
from typing import Dict, List, Tuple, Optional
from pathlib import Path

from telegram import (
    Update,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    MessageEntity,
    Message,  # for typing only
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, ChatMemberHandler, ContextTypes, filters
from telegram.error import RetryAfter, TimedOut, NetworkError
from telegram.request import HTTPXRequest

from channel_utils import normalize_channel_token, dedup_channels
from link_processor import process_telegram_links
from dotenv import load_dotenv


# 读取 .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()] if os.getenv("ADMIN_IDS") else []

# 使用脚本所在目录作为基准，避免工作目录差异
BASE_DIR = Path(__file__).resolve().parent
CHANNELS_FILE = BASE_DIR / "channels.txt"
CHANNELS_JSON = BASE_DIR / "channels.json"
DISCOVER_JSON = BASE_DIR / "discovered_channels.json"


def is_admin(user_id: int) -> bool:
    return (not ADMIN_IDS) or (user_id in ADMIN_IDS)


def load_channels() -> List[str]:
    path = CHANNELS_FILE
    if not path.exists():
        path.touch()
        return []
    with open(path, "r", encoding="utf-8") as f:
        rows = [r.strip() for r in f if r.strip() and not r.strip().startswith("#")]
    return dedup_channels(rows)


def add_channels_to_file(items: List[str]) -> Tuple[List[str], List[str]]:
    # 先按规范化后的 key 去重，避免同一输入重复两次
    uniq_norm_keys = []
    seen_keys = set()
    for raw in items:
        norm = normalize_channel_token(raw)
        if not norm:
            uniq_norm_keys.append((raw, None))
            continue
        key = norm.lower() if norm.startswith('@') else norm
        if key in seen_keys:
            continue
        seen_keys.add(key)
        uniq_norm_keys.append((raw, norm))

    added, skipped = [], []
    for raw, norm in uniq_norm_keys:
        if not norm:
            skipped.append(raw)
            continue
        existing = load_channels()
        keyset = {c.lower() if c.startswith('@') else c for c in existing}
        key = norm.lower() if norm.startswith('@') else norm
        if key in keyset:
            skipped.append(norm)
            continue
        with open(CHANNELS_FILE, "a", encoding="utf-8") as f:
            f.write(norm + "\n")
        added.append(norm)
    return added, skipped

# ===== JSON 频道存储与解析 =====
def _read_json(path: Path) -> Optional[dict]:
    try:
        if not path.exists():
            return None
        import json
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path: Path, data: dict) -> None:
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_channel_entries() -> List[dict]:
    data = _read_json(CHANNELS_JSON)
    if data and isinstance(data.get("channels"), list) and data["channels"]:
        return data["channels"]

    # 兼容旧版 channels.txt
    tokens = load_channels()
    entries: List[dict] = []
    for token in tokens:
        entries.append({
            "id": None,
            "token": token,
            "name": token,
            "username": token[1:] if token.startswith('@') else None
        })
    if entries:
        save_channel_entries(entries)
    return entries


def save_channel_entries(entries: List[dict]) -> None:
    _write_json(CHANNELS_JSON, {"channels": entries, "updated_at": int(time.time())})


def load_discovered_entries() -> List[dict]:
    data = _read_json(DISCOVER_JSON)
    if data and isinstance(data.get("channels"), list):
        return data["channels"]
    return []


def save_discovered_entries(entries: List[dict]) -> None:
    _write_json(DISCOVER_JSON, {"channels": entries, "updated_at": int(time.time())})


async def add_channels_via_api(context: ContextTypes.DEFAULT_TYPE, items: List[str]) -> Tuple[List[str], List[str]]:
    # 去重 tokens
    uniq = []
    seen = set()
    for raw in items:
        norm = normalize_channel_token(raw)
        key = (norm or "").lower() if norm and norm.startswith('@') else (norm or raw)
        if not norm:
            uniq.append((raw, None))
            continue
        if key in seen:
            continue
        seen.add(key)
        uniq.append((raw, norm))

    entries = load_channel_entries()
    existing_keys = {str(e.get("id")) for e in entries} | { (e.get("token") or "").lower() for e in entries }

    added, skipped = [], []
    for raw, norm in uniq:
        if not norm:
            skipped.append(raw)
            continue
        try:
            chat = await context.bot.get_chat(norm)
            cid = chat.id
            name = chat.title or (('@' + chat.username) if getattr(chat, 'username', None) else str(cid))
            token_key = norm.lower() if norm.startswith('@') else norm
            if str(cid) in existing_keys or token_key in existing_keys:
                skipped.append(norm)
                continue
            entries.append({
                "id": cid,
                "token": norm,
                "name": name,
                "username": getattr(chat, 'username', None)
            })
            added.append(name)
            existing_keys.add(str(cid))
            existing_keys.add(token_key)
        except Exception:
            skipped.append(norm)

    if added:
        save_channel_entries(entries)
    return added, skipped


class SimpleRelay:
    def __init__(self) -> None:
        self.sent_cache: Dict[Tuple[int, str], float] = {}
        # 6 小时内防重复
        self.sent_ttl = 6 * 3600
        # 每条消息对目标频道的并发扇出上限
        self.max_concurrency = 8
        # 媒体组缓存/定时任务
        self.media_group_buffer: Dict[str, List] = {}
        self.media_group_tasks: Dict[str, asyncio.Task] = {}
        # 进度消息（单条复用）相关
        self.progress_edit_throttle = 0.5
        self.progress_messages: Dict[int, Message] = {}
        self.progress_tokens: Dict[int, float] = {}
        # 媒体组去重与顺序控制
        self.processed_groups: Dict[str, float] = {}
        self.group_ttl = 7200  # 2 小时
        self.group_order: Dict[str, int] = {}
        self.group_next_seq = 0
        self.group_seq_counter = 0

    def _entry_display(self, e: dict) -> str:
        return e.get("name") or e.get("username") or e.get("token") or str(e.get("id"))

    def _build_remove_keyboard(self, entries: List[dict]) -> InlineKeyboardMarkup:
        buttons: List[List[InlineKeyboardButton]] = []
        row: List[InlineKeyboardButton] = []
        for e in entries:
            disp = self._entry_display(e)
            if e.get("id") is not None:
                data = f"remove:id:{e['id']}"
            else:
                tok = e.get("token") or ""
                data = f"remove:tok:{tok}"
            btn = InlineKeyboardButton(text=f"🗑️ {disp}", callback_data=data)
            row.append(btn)
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        # 操作按钮
        buttons.append([InlineKeyboardButton(text="关闭", callback_data="remove:close")])
        return InlineKeyboardMarkup(buttons)

    def _src_key(self, u: Update) -> str:
        m = u.message
        if not m:
            return ""
        if getattr(m, "forward_from_chat", None) and getattr(m, "forward_from_message_id", None):
            return f"src:{m.forward_from_chat.id}:{m.forward_from_message_id}"
        # 退化：用文本hash或自身ID
        text = (m.text or m.caption or "").strip()
        if text:
            return f"hash:{hash(text) & 0xFFFFFFFF}"
        return f"self:{m.chat.id}:{m.message_id}"

    def _prune_cache(self) -> None:
        now = time.time()
        to_del = [k for k, t0 in self.sent_cache.items() if now - t0 > self.sent_ttl]
        for k in to_del:
            self.sent_cache.pop(k, None)

    def _prune_groups(self) -> None:
        now = time.time()
        expired = [k for k, t0 in self.processed_groups.items() if now - t0 > self.group_ttl]
        for k in expired:
            self.processed_groups.pop(k, None)

    async def _resolve_chat_id(self, context: ContextTypes.DEFAULT_TYPE, token: str) -> int:
        # token 可能是 -100id 或 @username
        if token.startswith('@'):
            chat = await context.bot.get_chat(token)
            return chat.id
        return int(token)

    async def cmd_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_admin(update.effective_user.id):
            return
        entries = load_channel_entries()
        if not entries:
            await update.message.reply_text("暂无频道（使用 /add 添加或直接发送链接）")
            return
        lines = ["当前转发目标:"]
        for e in entries:
            disp = e.get("name") or e.get("username") or e.get("token") or str(e.get("id"))
            token = e.get("token")
            lines.append(f"• {disp} ({token})")
        await update.message.reply_text("\n".join(lines))

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_admin(update.effective_user.id):
            return
        help_text = (
            "Simple Relay 机器人已就绪\n\n"
            "常用命令:\n"
            "• /add <@user|-100id|链接> …  批量添加目标频道\n"
            "• /list                  查看已添加的频道\n"
            "• /remove <名称|@user|-100id>  删除指定频道\n\n"
            "• /joined 快捷加入bot所在频道频道\n\n"           
            "使用方式:\n"
            "1) 直接把频道消息链接/@用户名/-100ID 发给我，会自动解析加入\n"
            "2) 把要分发的消息(文本/图片/视频/相册)转发给我，我会并发分发到所有频道\n"
            "3) 分发进度会实时更新在一条消息里(✅成功/❌失败)\n"
        )
        await update.message.reply_text(help_text)

    async def cmd_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_admin(update.effective_user.id):
            return
        if not context.args:
            await update.message.reply_text("用法: /add <@user|-100id|链接> ...（支持多个，空格分隔）")
            return
        text = " ".join(context.args)
        tokens = re.split(r"[\s,;\n]+", text)
        added, skipped = await add_channels_via_api(context, tokens)
        msg = []
        if added:
            msg.append("已添加:\n" + "\n".join(added))
        if skipped:
            msg.append("跳过(无效或已存在):\n" + "\n".join(skipped))
        await update.message.reply_text("\n\n".join(msg) if msg else "无有效输入")

    async def cmd_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_admin(update.effective_user.id):
            return
        entries = load_channel_entries()
        if not context.args:
            if not entries:
                await update.message.reply_text("列表为空")
                return
            kb = self._build_remove_keyboard(entries)
            await update.message.reply_text("请选择要移除的频道：", reply_markup=kb)
            return
        # 兼容旧用法：/remove <名称|@user|-100id>
        target_raw = " ".join(context.args).strip()
        if not entries:
            await update.message.reply_text("列表为空")
            return
        target_norm = normalize_channel_token(target_raw) or target_raw

        def match(e: dict) -> bool:
            if e.get("name") == target_raw:
                return True
            if target_norm and (str(e.get("id")) == target_norm or e.get("token") == target_norm):
                return True
            if e.get("username") and ('@' + e.get("username")) == target_norm:
                return True
            return False

        kept = [e for e in entries if not match(e)]
        if len(kept) == len(entries):
            await update.message.reply_text("未找到")
            return
        save_channel_entries(kept)
        await update.message.reply_text("已移除")

    # ========== 发现加入的频道，并一键加入工作列表 ==========
    async def on_my_chat_member(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        upd = update.my_chat_member
        if not upd:
            return
        chat = upd.chat
        if getattr(chat, 'type', '') != 'channel':
            return
        try:
            new_status = getattr(upd.new_chat_member, 'status', '')
        except Exception:
            new_status = ''
        entries = load_discovered_entries()
        if new_status in ('administrator', 'creator', 'member'):
            # upsert
            found = False
            for e in entries:
                if str(e.get('id')) == str(chat.id):
                    e['name'] = getattr(chat, 'title', None) or e.get('name')
                    e['username'] = getattr(chat, 'username', None)
                    found = True
                    break
            if not found:
                entries.append({
                    'id': chat.id,
                    'name': getattr(chat, 'title', None) or str(chat.id),
                    'username': getattr(chat, 'username', None),
                })
            save_discovered_entries(entries)
        elif new_status in ('left', 'kicked', 'restricted'):
            kept = [e for e in entries if str(e.get('id')) != str(chat.id)]
            if len(kept) != len(entries):
                save_discovered_entries(kept)

    async def on_channel_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # 观察到频道动态，记录到已加入列表
        m = update.channel_post
        if not m:
            return
        chat = m.chat
        if getattr(chat, 'type', '') != 'channel':
            return
        entries = load_discovered_entries()
        for e in entries:
            if str(e.get('id')) == str(chat.id):
                # 已存在，更新名字/用户名
                e['name'] = getattr(chat, 'title', None) or e.get('name')
                e['username'] = getattr(chat, 'username', None)
                save_discovered_entries(entries)
                return
        entries.append({
            'id': chat.id,
            'name': getattr(chat, 'title', None) or str(chat.id),
            'username': getattr(chat, 'username', None),
        })
        save_discovered_entries(entries)

    async def cmd_joined(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_admin(update.effective_user.id):
            return
        discovered = load_discovered_entries()
        entries = load_channel_entries()
        existing_ids = {str(e.get('id')) for e in entries}
        candidates = [d for d in discovered if str(d.get('id')) not in existing_ids]
        if not candidates:
            await update.message.reply_text("暂无可添加的频道（可将机器人设为频道管理员后再试）")
            return
        # 构建添加键盘
        buttons = []
        row = []
        for d in candidates:
            disp = d.get('name') or (('@' + d.get('username')) if d.get('username') else str(d.get('id')))
            data = f"addjoined:id:{d.get('id')}"
            row.append(InlineKeyboardButton(text=f"➕ {disp}", callback_data=data))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton(text="关闭", callback_data="addjoined:close")])
        await update.message.reply_text("识别到以下未加入工作列表的频道：", reply_markup=InlineKeyboardMarkup(buttons))

    async def cb_add_joined(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_admin(update.effective_user.id):
            await update.callback_query.answer("无权限", show_alert=True)
            return
        q = update.callback_query
        data = q.data or ''
        if data == 'addjoined:close':
            await q.answer()
            with contextlib.suppress(Exception):
                await q.message.edit_reply_markup(reply_markup=None)
            return
        parts = data.split(':', 2)
        if len(parts) < 3:
            await q.answer()
            return
        _, kind, val = parts
        if kind != 'id':
            await q.answer()
            return
        # 调用已有添加逻辑
        token = str(val)
        added, skipped = await add_channels_via_api(context, [token])
        if added:
            await q.answer("已添加")
        else:
            await q.answer("已存在/失败")
        # 重新渲染剩余候选
        discovered = load_discovered_entries()
        entries = load_channel_entries()
        existing_ids = {str(e.get('id')) for e in entries}
        candidates = [d for d in discovered if str(d.get('id')) not in existing_ids]
        try:
            if candidates:
                # 重建键盘
                buttons = []
                row = []
                for d in candidates:
                    disp = d.get('name') or (('@' + d.get('username')) if d.get('username') else str(d.get('id')))
                    data2 = f"addjoined:id:{d.get('id')}"
                    row.append(InlineKeyboardButton(text=f"➕ {disp}", callback_data=data2))
                    if len(row) == 2:
                        buttons.append(row)
                        row = []
                if row:
                    buttons.append(row)
                buttons.append([InlineKeyboardButton(text="关闭", callback_data="addjoined:close")])
                await q.message.edit_text("识别到以下未加入工作列表的频道：", reply_markup=InlineKeyboardMarkup(buttons))
            else:
                await q.message.edit_text("已全部添加或暂无可添加的频道")
        except Exception:
            pass

    async def cb_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # 处理按钮点击移除频道
        if not is_admin(update.effective_user.id):
            await update.callback_query.answer("无权限", show_alert=True)
            return
        q = update.callback_query
        try:
            data = q.data or ""
            if data == "remove:close":
                await q.answer()
                # 尝试删除键盘
                with contextlib.suppress(Exception):
                    await q.message.edit_reply_markup(reply_markup=None)
                return

            parts = data.split(":", 2)
            if len(parts) < 3:
                await q.answer()
                return
            _, kind, val = parts

            entries = load_channel_entries()
            before = len(entries)
            if kind == "id":
                kept = [e for e in entries if str(e.get("id")) != str(val)]
            else:
                kept = [e for e in entries if (e.get("token") or "") != val]
            if len(kept) == before:
                await q.answer("未找到/已移除")
                return
            save_channel_entries(kept)
            await q.answer("已移除")

            # 重新渲染剩余列表
            if kept:
                kb = self._build_remove_keyboard(kept)
                text = "请选择要移除的频道："
                try:
                    await q.message.edit_text(text=text, reply_markup=kb)
                except Exception:
                    # 回退仅编辑键盘
                    await q.message.edit_reply_markup(reply_markup=kb)
            else:
                try:
                    await q.message.edit_text("列表为空")
                except Exception:
                    pass
        except Exception:
            with contextlib.suppress(Exception):
                await update.callback_query.answer("出错了")

    async def auto_parse_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # 私聊文本中自动解析频道链接/标识并加入
        if not is_admin(update.effective_user.id):
            return
        msg = update.message
        if not msg or not msg.text:
            return
        text = msg.text
        tokens: List[str] = []
        tokens += re.findall(r"https?://t\.me/[^\s]+", text)
        tokens += [t for t in re.split(r"[\s,;\n]+", text) if t]
        if not tokens:
            return
        added, skipped = await add_channels_via_api(context, tokens)
        if added or skipped:
            out = []
            if added:
                out.append("已添加:\n" + "\n".join(added))
            if skipped:
                out.append("跳过(无效或已存在):\n" + "\n".join(skipped))
            await msg.reply_text("\n\n".join(out))

    async def handle_forward(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # 私聊：非命令，且包含文本/媒体
        if not is_admin(update.effective_user.id):
            return
        m = update.message
        if not m:
            return

        channels = load_channel_entries()
        if not channels:
            await m.reply_text("尚未配置转发频道。使用 /add 添加，或直接发送消息链接。")
            return

        # 媒体组：缓冲后统一按相册发送
        if getattr(m, 'media_group_id', None):
            await self._buffer_media_group(m, context, channels)
            return

        src_key = self._src_key(update)
        sem = asyncio.Semaphore(self.max_concurrency)

        # 初始化进度消息
        progress = await self._init_progress_message(m, channels)
        progress["src_key"] = src_key

        async def send_to_one(entry: dict):
            async with sem:
                key = None
                try:
                    cid = int(entry.get("id")) if entry.get("id") is not None else await self._resolve_chat_id(context, entry.get("token"))
                    key = (cid, src_key)
                    self._prune_cache()
                    if src_key and key in self.sent_cache:
                        return True, key
                    ok = await self._send_one(m, cid, context)
                    if ok:
                        self.sent_cache[key] = time.time()
                    return ok, key
                except Exception:
                    return False, key

        results = await asyncio.gather(*(send_to_one(e) for e in channels), return_exceptions=True)
        await self._finalize_progress_message(progress, channels, results, fallback_key=True)

    def _src_group_key(self, m) -> str:
        # 尽量包含来源 chat 信息，回退 media_group_id
        if getattr(m, 'forward_from_chat', None) and getattr(m, 'media_group_id', None):
            return f"group:{m.forward_from_chat.id}:{m.media_group_id}"
        if getattr(m, 'media_group_id', None):
            return f"group::{m.media_group_id}"
        return f"group:self:{getattr(m, 'chat', None).id if getattr(m, 'chat', None) else 'na'}"

    async def _buffer_media_group(self, m, context: ContextTypes.DEFAULT_TYPE, channels: List[dict]):
        gid = str(m.media_group_id)
        buf = self.media_group_buffer.setdefault(gid, [])
        buf.append(m)
        # 记录首次出现顺序（用于跨相册顺序发送）
        if gid not in self.group_order:
            self.group_order[gid] = self.group_seq_counter
            self.group_seq_counter += 1
        # 防抖：每次来新的一条都重置 2 秒定时
        if gid in self.media_group_tasks:
            try:
                self.media_group_tasks[gid].cancel()
            except Exception:
                pass
        self.media_group_tasks[gid] = asyncio.create_task(self._flush_media_group(gid, context, channels))

    async def _flush_media_group(self, gid: str, context: ContextTypes.DEFAULT_TYPE, channels: List[dict]):
        entered_sequence = False
        my_seq = -1
        try:
            await asyncio.sleep(2)
            msgs = self.media_group_buffer.get(gid, [])
            if not msgs:
                return
            msgs.sort(key=lambda x: getattr(x, 'message_id', 0))

            txt_msg = None
            for mm in msgs:
                if mm.caption or mm.text:
                    txt_msg = mm
                    break

            sem = asyncio.Semaphore(self.max_concurrency)
            group_src_key = self._src_group_key(msgs[0])
            self._prune_groups()
            if group_src_key in self.processed_groups:
                return
            self.processed_groups[group_src_key] = time.time()

            my_seq = self.group_order.get(gid, 0)
            while my_seq != self.group_next_seq:
                await asyncio.sleep(0.1)
            entered_sequence = True

            progress = await self._init_progress_message(msgs[0], channels)
            progress["src_key"] = group_src_key

            async def send_album_to_one(entry: dict):
                async with sem:
                    key = None
                    try:
                        cid = int(entry.get("id")) if entry.get("id") is not None else await self._resolve_chat_id(context, entry.get("token"))
                        key = (cid, group_src_key)
                        self._prune_cache()
                        if key in self.sent_cache:
                            return True, key

                        caption = ""
                        caption_entities = None
                        if txt_msg and (txt_msg.caption or txt_msg.text):
                            caption, caption_entities = self._process_links_for_ptb(txt_msg, cid)

                        media_list = []
                        for i, mm in enumerate(msgs):
                            if mm.photo:
                                media = InputMediaPhoto(
                                    media=mm.photo[-1].file_id,
                                    caption=caption if i == 0 else None,
                                    caption_entities=caption_entities if i == 0 and caption_entities else None,
                                )
                            elif mm.video:
                                media = InputMediaVideo(
                                    media=mm.video.file_id,
                                    caption=caption if i == 0 else None,
                                    caption_entities=caption_entities if i == 0 and caption_entities else None,
                                )
                            elif mm.document:
                                media = InputMediaDocument(
                                    media=mm.document.file_id,
                                    caption=caption if i == 0 else None,
                                    caption_entities=caption_entities if i == 0 and caption_entities else None,
                                )
                            else:
                                continue
                            media_list.append(media)

                        if not media_list:
                            return False, key

                        await self._send_media_group_no_retry(context.bot, chat_id=cid, media=media_list)
                        self.sent_cache[key] = time.time()
                        return True, key
                    except Exception:
                        return False, key

            results = await asyncio.gather(*(send_album_to_one(e) for e in channels), return_exceptions=True)
            await self._finalize_progress_message(progress, channels, results, fallback_key=False)
        finally:
            # 释放顺序锁，允许下一个相册继续处理
            if entered_sequence:
                try:
                    self.group_next_seq += 1
                except Exception:
                    pass
            self.media_group_buffer.pop(gid, None)
            task = self.media_group_tasks.pop(gid, None)
            if task and not task.done():
                task.cancel()

    async def _send_one(self, m, cid: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
        try:
            if m.text:
                text, entities = self._process_links_for_ptb(m, cid)
                await self._send_with_backoff(
                    context.bot.send_message,
                    chat_id=cid,
                    text=text,
                    entities=entities or None,
                    disable_web_page_preview=False,
                )
            elif m.photo:
                caption, entities = self._process_links_for_ptb(m, cid)
                await self._send_with_backoff(
                    context.bot.send_photo,
                    chat_id=cid,
                    photo=m.photo[-1].file_id,
                    caption=caption,
                    caption_entities=entities or None,
                )
            elif m.video:
                caption, entities = self._process_links_for_ptb(m, cid)
                await self._send_with_backoff(
                    context.bot.send_video,
                    chat_id=cid,
                    video=m.video.file_id,
                    caption=caption,
                    caption_entities=entities or None,
                )
            elif m.document:
                caption, entities = self._process_links_for_ptb(m, cid)
                await self._send_with_backoff(
                    context.bot.send_document,
                    chat_id=cid,
                    document=m.document.file_id,
                    caption=caption,
                    caption_entities=entities or None,
                )
            else:
                return False
            return True
        except Exception:
            return False

    async def _send_with_backoff(self, func, max_retries: int = 3, **kwargs):
        attempt = 0
        while attempt < max_retries:
            try:
                return await func(**kwargs)
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except (TimedOut, NetworkError):
                attempt += 1
                await asyncio.sleep(1 * attempt)
            except Exception:
                attempt += 1
                await asyncio.sleep(1)
        raise RuntimeError("send failed after retries")

    async def _send_media_group_no_retry(self, bot, *, chat_id, media):
        """发送相册：不做不确定性的重试，避免重复发送。
        遇到 RetryAfter 仅等待一次后再尝试一次，其它异常直接抛出。
        """
        try:
            return await bot.send_media_group(chat_id=chat_id, media=media)
        except RetryAfter as e:
            # 仅等待一次再发一次，避免多次重复
            await asyncio.sleep(e.retry_after + 1)
            return await bot.send_media_group(chat_id=chat_id, media=media)

    async def _init_progress_message(self, reply_to_message, channel_entries: List[dict]) -> dict:
        lines = ["分发进度:"]
        index = {}
        for i, e in enumerate(channel_entries, start=1):
            name = e.get("name") or e.get("username") or e.get("token") or str(e.get("id"))
            lines.append(f"⌛ {name}")
            # 以 id 为主，缺失时退化到 token，避免多个 None 覆盖同一项
            idx_key = str(e.get("id")) if e.get("id") is not None else (e.get("token") or str(i))
            index[idx_key] = i
        text = "\n".join(lines)
        chat_id = reply_to_message.chat_id
        # 每次分发创建新的进度消息，避免只编辑旧消息导致“看起来没弹出”
        msg = await reply_to_message.reply_text(text)
        self.progress_messages[chat_id] = msg
        token = time.time()
        self.progress_tokens[chat_id] = token
        progress = {"message": msg, "lines": lines, "index": index, "last": 0.0, "chat_id": chat_id, "token": token}
        return progress

    async def _finalize_progress_message(self, progress: dict, entries: List[dict], results: List, fallback_key: bool) -> None:
        """任务完成后，统一更新分发进度。"""
        try:
            chat_id = progress.get("chat_id")
            token = progress.get("token")
            if chat_id is None or self.progress_tokens.get(chat_id) != token:
                return

            for entry, result in zip(entries, results):
                status = False
                key = None
                if isinstance(result, tuple):
                    status, key = result
                else:
                    status = result is True
                if not status and key and key in self.sent_cache:
                    status = True

                if not status and fallback_key and key is None:
                    # 尝试根据 entry 重建 key
                    target_id = entry.get("id")
                    src_key = progress.get("src_key")
                    if target_id is not None and src_key:
                        fallback = (int(target_id), src_key)
                        if fallback in self.sent_cache:
                            status = True

                name = entry.get("name") or entry.get("username") or entry.get("token") or str(entry.get("id"))
                idx_key = str(entry.get("id")) if entry.get("id") is not None else (entry.get("token") or "")
                idx = progress["index"].get(idx_key)
                if not idx:
                    continue
                mark = "✅" if status else "❌"
                progress["lines"][idx] = f"{mark} {name}"

            progress["last"] = time.time()
            new_text = "\n".join(progress["lines"])
            await progress["message"].edit_text(new_text)
        except Exception:
            pass

    def _process_links_for_ptb(self, message, target_chat_id) -> Tuple[str, List]:
        """将 t.me/c/<id>/<msg> 链接替换为目标频道，并保留 text_link 实体。
        返回 (new_text, new_entities)
        """
        text = message.text or message.caption or ""
        entities = message.entities or message.caption_entities or []

        # 1) 明文替换
        new_text = process_telegram_links(text, target_chat_id)

        # 2) 替换 text_link 实体
        target_id_str = str(target_chat_id)
        if target_id_str.startswith('-100'):
            new_channel_id = target_id_str[4:]
        else:
            new_channel_id = target_id_str.lstrip('-')

        pattern = r'https://t\.me/c/(\d+)/(\d+)'
        new_entities: List[MessageEntity] = []
        for ent in entities:
            try:
                if getattr(ent, 'type', None) == 'text_link' and getattr(ent, 'url', None):
                    m = re.match(pattern, ent.url)
                    if m:
                        msg_id = m.group(2)
                        new_url = f"https://t.me/c/{new_channel_id}/{msg_id}"
                        new_ent = MessageEntity(type='text_link', offset=ent.offset, length=ent.length, url=new_url)
                        new_entities.append(new_ent)
                    else:
                        new_entities.append(ent)
                else:
                    new_entities.append(ent)
            except Exception:
                new_entities.append(ent)

        return new_text, new_entities


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN 未配置")

    relay = SimpleRelay()
    # 稳定网络层：增大超时、使用 HTTP/1.1、稍大的连接池，减小 httpx 断连概率
    request = HTTPXRequest(
        connection_pool_size=8,
        http_version="1.1",
        read_timeout=30.0,
        write_timeout=30.0,
        connect_timeout=10.0,
        pool_timeout=5.0,
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()

    # 命令
    app.add_handler(CommandHandler("start", relay.cmd_start))
    app.add_handler(CommandHandler("list", relay.cmd_list))
    app.add_handler(CommandHandler("add", relay.cmd_add))
    app.add_handler(CommandHandler("remove", relay.cmd_remove))

    # 私聊自动解析添加
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & (~filters.COMMAND), relay.auto_parse_add))

    # 私聊转发处理（管理员把消息转发给机器人）
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (~filters.COMMAND), relay.handle_forward))
    # 回调按钮处理：移除频道
    app.add_handler(CallbackQueryHandler(relay.cb_remove, pattern=r"^remove:"))

    print("Simple Relay Bot started. Ctrl+C to stop.")
    # 让 getUpdates 更稳健：
    # - timeout: 长轮询超时（服务端）。
    # - read_timeout 等：客户端 socket 超时，需要略大于 timeout。
    app.run_polling(
        poll_interval=0.5,
        timeout=30,
        read_timeout=35.0,
        write_timeout=35.0,
        connect_timeout=15.0,
        pool_timeout=10.0,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped.")











