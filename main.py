"""
WeChatPadProMAX Webhook 中轉伺服器
接收微信訊息 → 本地大語言模型處理 → 回傳微信
"""

import asyncio
import base64
import json
import logging
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

try:
    import pilk

    _PILK_AVAILABLE = True
except ImportError:
    pilk = None  # type: ignore[assignment]
    _PILK_AVAILABLE = False

import httpx
import requests
from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from openai import AsyncOpenAI
from pydantic import BaseModel
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func, inspect, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

load_dotenv()

# ── 配置 ──────────────────────────────────────────────────────────────────────
LOCAL_API_BASE = os.getenv("LOCAL_API_BASE", "http://127.0.0.1:1234/v1")
LOCAL_API_KEY = os.getenv("LOCAL_API_KEY", "your-api-key-or-lm-studio")
LOCAL_MODEL_NAME = os.getenv("LOCAL_MODEL_NAME", "your-model-name")
PADPRO_URL = os.getenv("PADPRO_URL", "http://127.0.0.1:1238").rstrip("/")
PADPRO_AUTH_KEY = os.getenv("PADPRO_AUTH_KEY", "your-padpro-auth-key")
PADPRO_WXID = os.getenv("PADPRO_WXID", "your-wechat-wxid")
PADPRO_WXID_ENV_DEFAULT = PADPRO_WXID
PADPRO_WEBHOOK_PUBLIC_URL = os.getenv(
    "PADPRO_WEBHOOK_PUBLIC_URL",
    "http://127.0.0.1:9950/webhook",
).strip()
PADPRO_WEBHOOK_SECRET = os.getenv("PADPRO_WEBHOOK_SECRET", "").strip()
PADPRO_LOGIN_DEVICE_NAME = os.getenv("PADPRO_LOGIN_DEVICE_NAME", "MacBook Pro")
LOGIN_QR_TTL_SECONDS = int(os.getenv("LOGIN_QR_TTL_SECONDS", "300"))
PADPRO_HTTP_TIMEOUT = float(os.getenv("PADPRO_HTTP_TIMEOUT", "30"))
SYNC_POLL_INTERVAL = float(os.getenv("SYNC_POLL_INTERVAL", "5"))
SYNC_POLL_ENABLED = os.getenv("SYNC_POLL_ENABLED", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
SYNC_REQUEST_TIMEOUT = float(os.getenv("SYNC_REQUEST_TIMEOUT", "10"))
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "10"))
SLIDING_WINDOW_MESSAGE_LIMIT = int(os.getenv("SLIDING_WINDOW_MESSAGE_LIMIT", "30"))
MEMORY_EXTRACTION_INTERVAL = int(os.getenv("MEMORY_EXTRACTION_INTERVAL", "20"))
MEMORY_EXTRACTION_HISTORY_LIMIT = int(os.getenv("MEMORY_EXTRACTION_HISTORY_LIMIT", "20"))
DEFAULT_LLM_TEMPERATURE = float(os.getenv("LOCAL_LLM_TEMPERATURE", "0.7"))
MEMORY_ANALYSIS_SYSTEM_PROMPT = (
    "你是一個客觀的情報分析系統。請閱讀以下的舊有記憶與最新對話，萃取出該聯絡人的最新個人情報"
    "（如：職業、喜好、習慣、特殊事件），並與舊記憶合併。請只輸出更新後的記憶重點，"
    "總字數不超過 100 字，不要使用第一人稱。"
)
# 非同步 MySQL 驅動：mysql+asyncmy://（取代 aiomysql，與 uvloop 連線池相容性更佳）
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+asyncmy://root:changeme@db:3306/wechat_ai",
)
DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))
DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
APP_TIMEZONE = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Taipei"))
TEXT_MSG_TYPE = 1
IMAGE_MSG_TYPE = 3
VOICE_MSG_TYPE = 34
IMAGE_MESSAGE_DISPLAY = "[圖片訊息]"
VOICE_MESSAGE_DISPLAY = "[語音訊息]"
VOICE_MESSAGE_LLM_PROMPT = (
    "[系統提示：對方發了一段語音，請用符合你人設的口吻，撒嬌或委婉地請對方打字。]"
)
PHOTOS_DIR = os.getenv("PHOTOS_DIR", "/app/photos")
EPHEMERAL_IMAGES_DIR = os.path.join(PHOTOS_DIR, "ephemeral")
RECEIVED_IMAGES_DIR = os.path.join(PHOTOS_DIR, "received")
RECEIVED_VOICES_DIR = os.path.join(PHOTOS_DIR, "received", "voices")
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
ALLOWED_VOICE_EXTENSIONS = {".mp3", ".wav", ".amr"}
ALLOWED_RECEIVED_MEDIA_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | ALLOWED_VOICE_EXTENSIONS
IMAGE_MSG_TAG = "[IMAGE_MSG]"
VOICE_MSG_TAG = "[VOICE_MSG]"

# 靜態備援映射（資料庫為主；檔案需放在 PHOTOS_DIR 下）
PHOTO_LIBRARY: dict[str, str] = {
    "自拍": os.path.join(PHOTOS_DIR, "selfie1.jpg"),
    "吃飯": os.path.join(PHOTOS_DIR, "food.jpg"),
}

PHOTO_SEND_TAG_PATTERN = re.compile(r"\[發送照片:\s*([^\]]+?)\s*\]")
IMAGE_MSG_PATTERN = re.compile(r"\[IMAGE_MSG\][^\s\n]+")
VOICE_MSG_PATTERN = re.compile(r"\[VOICE_MSG\][^\s\n]+")
PHOTO_LIBRARY_EXHAUSTED_SUFFIX = "照片之前都給你看光了啦壓 哈哈"

FALLBACK_REPLIES = [
    "剛好手邊有點事在忙，晚點回你喔！",
    "我現在有點不方便打字，等等找你～",
    "剛剛去弄點東西吃沒看到訊息，我先忙一下晚點回！",
    "先去洗個澡，等下回覆喔 💦",
    "稍等我一下喔，手邊剛好有急事要處理 🙏",
    "剛好接了個電話，晚點再跟你說！",
]

DEFAULT_SYSTEM_PROMPT = (
    "你現在是一位 25 歲的熱情女孩，說話自然、口語化、帶點俏皮。"
    "你是微信上的 AI 助理，請根據上下文友善回覆，不要假裝自己是真人本人。"
    "回覆請簡短，適合即時聊天。"
)
DEFAULT_PROMPT_NAME = "預設人設"

# 執行時快取：由資料庫中 is_active=True 的人設載入
CURRENT_SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT
ACTIVE_PROMPT_ID: int | None = None

# AI 全域開關（可透過 POST /api/toggle 切換）
AI_ENABLED = True

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# 對話記憶已改由 DB 滑動視窗載入；保留變數僅供相容舊邏輯參考
conversation_memory: dict[str, list[dict[str, str]]] = defaultdict(list)


class LLMFallbackError(Exception):
    """LLM 呼叫失敗時觸發斷線救援，攜帶備用回覆台詞。"""

    def __init__(self, fallback_text: str, original: Exception):
        self.fallback_text = fallback_text
        self.original = original
        super().__init__(fallback_text)

# 前端即時推播 WebSocket 連線清單
active_frontend_connections: list[WebSocket] = []

# PadPro 強制同步背景任務
_force_sync_task: asyncio.Task[None] | None = None

# 掃碼登入暫存（uuid → session 狀態，重啟後失效）
_login_sessions: dict[str, dict[str, Any]] = {}

# ── 資料庫 ────────────────────────────────────────────────────────────────────
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=DB_POOL_RECYCLE,
    pool_timeout=DB_POOL_TIMEOUT,
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _safe_rollback(session: AsyncSession) -> None:
    """清除損壞的 transaction，避免殭屍連線污染後續請求。"""
    try:
        await session.rollback()
    except Exception as rollback_err:
        logger.warning("session.rollback() 失敗: %s", rollback_err, exc_info=True)


class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wx_id: Mapped[str] = mapped_column(String(128), index=True)
    content: Mapped[str] = mapped_column(Text)
    is_ai: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )


class ContactSettings(Base):
    __tablename__ = "contact_settings"

    wx_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    nickname: Mapped[str | None] = mapped_column(String(128), nullable=True)
    memory_summary: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    sent_photo_ids: Mapped[str] = mapped_column(Text, default="", nullable=False)
    assigned_profile_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("system_prompt_profiles.id"),
        nullable=True,
    )


class ToggleRequest(BaseModel):
    enabled: bool


class ContactNicknameRequest(BaseModel):
    nickname: str


class ContactAssignedProfileRequest(BaseModel):
    profile_id: int | None = None


class ContactMemoryRequest(BaseModel):
    memory_summary: str | None = None


class SystemPromptProfile(Base):
    __tablename__ = "system_prompt_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class SystemPromptRequest(BaseModel):
    content: str


class SystemPromptCreateRequest(BaseModel):
    name: str
    content: str


class SystemPromptUpdateRequest(BaseModel):
    name: str | None = None
    content: str | None = None


class LlmConfigCreateRequest(BaseModel):
    name: str
    provider: str = "Custom"
    api_key: str | None = None
    base_url: str
    model_name: str
    temperature: float = 0.7


class LlmConfig(Base):
    __tablename__ = "llm_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="Custom")
    api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class PhotoAsset(Base):
    __tablename__ = "photo_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    keywords: Mapped[str] = mapped_column(String(512), nullable=False)
    prompt_hint: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(256), default="")
    profile_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("system_prompt_profiles.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )


class WechatAccount(Base):
    __tablename__ = "wechat_accounts"

    wx_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    nickname: Mapped[str | None] = mapped_column(String(128), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    auth_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    webhook_registered: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class SendMessageRequest(BaseModel):
    wx_id: str
    content: str = ""
    photo_id: int | None = None


class PhotoUpdateRequest(BaseModel):
    name: str | None = None
    keywords: str | None = None
    prompt_hint: str | None = None
    profile_id: int | None = None


class SendImageRequest(BaseModel):
    wx_id: str
    photo_id: int


app = FastAPI(title="WeChatPadPro Webhook Relay", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    global CURRENT_SYSTEM_PROMPT, ACTIVE_PROMPT_ID, _force_sync_task
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    os.makedirs(EPHEMERAL_IMAGES_DIR, exist_ok=True)
    os.makedirs(RECEIVED_IMAGES_DIR, exist_ok=True)
    os.makedirs(RECEIVED_VOICES_DIR, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_contact_settings_schema()
    await ensure_photo_assets_schema()
    await ensure_system_prompt_seed()
    await ensure_llm_configs_seed()
    CURRENT_SYSTEM_PROMPT, ACTIVE_PROMPT_ID = await load_active_system_prompt_from_db()
    await migrate_orphan_photo_assets()
    await load_primary_wechat_account_from_db()
    logger.info(
        "資料庫連線就緒，目前啟用人設 id=%s，長度=%d 字，照片目錄=%s",
        ACTIVE_PROMPT_ID,
        len(CURRENT_SYSTEM_PROMPT),
        PHOTOS_DIR,
    )
    logger.info(
        "資料庫連線池 | pre_ping=True recycle=%ds timeout=%ds driver=%s",
        DB_POOL_RECYCLE,
        DB_POOL_TIMEOUT,
        DATABASE_URL.split("://", 1)[0],
    )
    if SYNC_POLL_ENABLED:
        _force_sync_task = asyncio.create_task(force_sync_loop())
        logger.info(
            "PadPro 強制同步輪詢已啟動，間隔 %.1f 秒 | url=%s",
            SYNC_POLL_INTERVAL,
            _padpro_sync_url(),
        )
    else:
        logger.info("PadPro 強制同步輪詢已停用 (SYNC_POLL_ENABLED=false)")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _force_sync_task
    if _force_sync_task is None:
        return
    _force_sync_task.cancel()
    try:
        await _force_sync_task
    except asyncio.CancelledError:
        pass
    _force_sync_task = None
    logger.info("PadPro 強制同步輪詢已停止")


# ── 工具函式 ──────────────────────────────────────────────────────────────────

def _to_bool(value: Any) -> bool:
    """將資料庫或外部來源的值安全轉為 Python bool。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _xray_log(message: str) -> None:
    """同時輸出至 logger 與 stdout，方便 Docker 終端機除錯。"""
    logger.info(message)
    print(message, flush=True)


def _normalize_wxid(wxid: Any) -> str:
    """標準化 wxid 字串。"""
    if wxid is None:
        return ""
    return str(wxid).strip()


def _is_self_wxid(wxid: Any) -> bool:
    """判斷是否為本帳號 wxid（相容多種欄位格式）。"""
    normalized = _normalize_wxid(wxid)
    if not normalized:
        return False
    if normalized == PADPRO_WXID:
        return True
    # 相容 wxid_ 前綴與純數字 ID 的對照
    if PADPRO_WXID and normalized.endswith(PADPRO_WXID):
        return True
    if normalized.startswith("wxid_") and PADPRO_WXID in normalized:
        return True
    return False


def _is_chatroom_id(wxid: Any) -> bool:
    """判斷是否為微信群組 ID。"""
    return "@chatroom" in _normalize_wxid(wxid)


def _resolve_chatroom_id(
    data: dict,
    from_user: str,
    to_user: str | None,
    sender_id: str,
) -> str | None:
    """從 payload 各欄位找出群組 ID（必含 @chatroom）。"""
    candidates = [
        from_user,
        to_user,
        sender_id,
        _get_field(data, "FromUserName", "fromUser", "from_user", "from_wxid"),
        _get_field(data, "ToUserName", "toUser", "to_user", "to_wxid"),
        _get_field(data, "sender", "Sender"),
        _get_field(data, "chatroom", "ChatRoom", "room_id"),
    ]
    seen: set[str] = set()
    for raw in candidates:
        wxid = _normalize_wxid(raw)
        if not wxid or wxid in seen:
            continue
        seen.add(wxid)
        if _is_chatroom_id(wxid):
            return wxid
    return None


def _format_group_stored_content(speaker_id: str, text: str) -> str:
    """將群組訊息標註真實發言者後寫入資料庫。"""
    speaker = _normalize_wxid(speaker_id) or "unknown"
    if re.match(r"^\[[\w@.-]+\]:", text):
        return text
    return f"[{speaker}]: {text}"


def _resolve_self_chat_partner(
    data: dict,
    from_user: str,
    to_user: str | None,
    sender_id: str,
) -> str | None:
    """
    解析自己手機發送訊息的對話對象。
    優先使用 to_wxid（接收方），並提供群聊等 fallback。
    """
    candidates: list[str] = []

    for key in ("ToUserName", "toUser", "to_user", "to_wxid", "ToWxid"):
        value = _normalize_wxid(_get_field(data, key))
        if value:
            candidates.append(value)

    if to_user:
        candidates.append(_normalize_wxid(to_user))

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if _is_self_wxid(candidate):
            continue
        if candidate in ("filehelper", "newsapp", "fmessage"):
            continue
        return candidate

    # 群聊：自己對群發言時，對話對象為群 ID
    if _is_chatroom_id(from_user) and not _is_self_wxid(from_user):
        return from_user

    normalized_to = _normalize_wxid(to_user)
    if _is_chatroom_id(normalized_to):
        return normalized_to

    # 最後嘗試：若 speaker 不是自己，可能是轉發等特殊格式
    if speaker_id and not _is_self_wxid(sender_id) and not _is_chatroom_id(sender_id):
        return sender_id

    return None


async def ensure_contact_record(wx_id: str) -> None:
    """確保聯絡人存在於 ContactSettings（不存在則自動建檔，預設一般名單）。"""
    await get_or_create_contact_ai_enabled(wx_id)


def _get_field(data: dict, *keys: str) -> Any:
    """依序嘗試多個欄位名稱（相容 PascalCase / camelCase）。"""
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _normalize_msg_type(msg_type: Any) -> int | None:
    if msg_type is None:
        return None
    try:
        return int(msg_type)
    except (TypeError, ValueError):
        return None


def _extract_text_content(data: dict) -> str:
    """從 payload 提取文字內容。"""
    content = _get_field(data, "Content", "content", "text")
    if isinstance(content, dict):
        return str(content.get("content") or content.get("str") or "")
    if content is None:
        return ""
    return str(content).strip()


def _parse_group_message(from_user: str, content: str) -> tuple[str, str]:
    """
    群聊文字格式：「wxid_xxx:\\n正文」或「wxid_xxx: 正文」
    回傳 (實際發送者 wxid, 正文)
    """
    normalized_from = _normalize_wxid(from_user)
    if not _is_chatroom_id(normalized_from):
        return normalized_from, content

    match = re.match(r"^([\w@.-]+):\n(.*)$", content, re.DOTALL)
    if match:
        return _normalize_wxid(match.group(1)), match.group(2).strip()

    match = re.match(r"^([\w@.-]+):\s(.*)$", content, re.DOTALL)
    if match:
        return _normalize_wxid(match.group(1)), match.group(2).strip()

    return normalized_from, content


def _dump_voice_message_payload(data: dict, msg_id: Any) -> None:
    """將語音訊息 (msgType=34) 的完整 payload 傾印至日誌，供評估語音轉文字欄位。"""
    try:
        payload_dump = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except Exception:
        payload_dump = repr(data)
    logger.info(
        "[Webhook] 收到語音訊息 msgType=34 | msgId=%s | 完整 payload:\n%s",
        msg_id,
        payload_dump,
    )
    print(
        f"[Webhook] 收到語音訊息 msgType=34 | msgId={msg_id} | 完整 payload:\n{payload_dump}"
    )


def _dump_image_message_payload(data: dict, msg_id: Any) -> None:
    """將圖片訊息 (msgType=3) payload 傾印至日誌，供階段三下載實作參考。"""
    try:
        payload_dump = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except Exception:
        payload_dump = repr(data)
    logger.info(
        "[Webhook] 收到圖片訊息 msgType=3 | msgId=%s | 完整 payload:\n%s",
        msg_id,
        payload_dump,
    )
    _xray_log(
        f"[Webhook] 收到圖片訊息 msgType=3 | msgId={msg_id} | 嘗試下載並寫入"
    )


def _parse_message_header(data: dict) -> dict[str, Any] | None:
    """提取 Webhook 訊息共用欄位。"""
    from_user = _normalize_wxid(
        _get_field(
            data, "FromUserName", "fromUser", "from_user", "from_wxid", "sender",
        )
    )
    to_user = _normalize_wxid(
        _get_field(data, "ToUserName", "toUser", "to_user", "to_wxid")
    ) or None
    msg_type = _normalize_msg_type(_get_field(data, "MsgType", "msgType", "msg_type"))
    is_self = _get_field(data, "IsSelf", "isSelf", "is_self")
    msg_id = _get_field(data, "MsgId", "msgId", "newMsgId", "new_msg_id")

    if not from_user:
        _xray_log(f"[判斷] 略過訊息 msgId={msg_id}：缺少發送者 from_user")
        return None

    return {
        "from_user": from_user,
        "to_user": to_user,
        "msg_type": msg_type,
        "is_self": is_self,
        "msg_id": msg_id,
    }


def _is_message_from_self(data: dict, header: dict[str, Any]) -> bool:
    """判斷是否為本帳號發出（防止 AI 自言自語）。"""
    if _to_bool(header.get("is_self")):
        return True
    from_user = header["from_user"]
    if _is_self_wxid(from_user):
        return True
    sender = _normalize_wxid(_get_field(data, "sender", "Sender", "from_wxid"))
    if _is_self_wxid(sender):
        return True
    return False


def _resolve_incoming_routing(
    data: dict,
    header: dict[str, Any],
    display_text: str,
) -> dict[str, Any] | None:
    """解析聯絡人訊息的 chat_partner、stored_content 與 LLM 用純文字。"""
    from_user = header["from_user"]
    to_user = header["to_user"]
    msg_id = header["msg_id"]
    is_self = header.get("is_self")

    group_id = _resolve_chatroom_id(data, from_user, to_user, from_user)
    is_group_message = group_id is not None

    speaker_id, purified_text = _parse_group_message(
        group_id or from_user,
        display_text,
    )
    text = purified_text

    if is_group_message and _is_chatroom_id(speaker_id):
        fallback_speaker = _normalize_wxid(
            _get_field(data, "sender", "Sender", "from_wxid")
        )
        speaker_id = fallback_speaker or "unknown"

    if is_group_message:
        chat_partner = group_id
        stored_content = _format_group_stored_content(speaker_id, text)
    else:
        chat_partner = speaker_id
        stored_content = text

    return {
        "is_group_message": is_group_message,
        "chat_partner": chat_partner,
        "speaker_id": speaker_id,
        "from_user": from_user,
        "to_user": to_user,
        "content": text,
        "stored_content": stored_content,
        "reply_to": chat_partner,
        "msg_id": msg_id,
        "is_self": is_self,
    }


def _parse_self_message_payload(
    data: dict,
    header: dict[str, Any],
    display_text: str,
    *,
    is_voice_message: bool = False,
    is_image_message: bool = False,
) -> dict[str, Any] | None:
    """解析本帳號發出的訊息：寫入 DB，不觸發 AI。"""
    from_user = header["from_user"]
    to_user = header["to_user"]
    msg_id = header["msg_id"]
    is_self = header.get("is_self")

    group_id = _resolve_chatroom_id(data, from_user, to_user, from_user)
    is_group_message = group_id is not None

    speaker_id, purified = _parse_group_message(group_id or from_user, display_text)
    text = display_text
    if display_text not in (VOICE_MESSAGE_DISPLAY, IMAGE_MESSAGE_DISPLAY):
        text = purified

    chat_partner = _resolve_self_chat_partner(data, from_user, to_user, speaker_id)
    if is_group_message and chat_partner and not _is_chatroom_id(chat_partner):
        chat_partner = group_id

    logger.info(
        "[Webhook] 收到自己手機發出的訊息 | msgId=%s from=%s to=%s speaker=%s "
        "isSelf=%s group=%s 解析後 chat_partner=%s",
        msg_id,
        from_user,
        to_user,
        speaker_id,
        is_self,
        group_id,
        chat_partner,
    )

    if not chat_partner:
        _xray_log(
            f"[防護] 自己訊息無法判定 chat_partner，略過寫入 | msgId={msg_id} "
            f"from={from_user} to={to_user}"
        )
        return None

    stored_content = text
    if is_group_message:
        stored_content = _format_group_stored_content("我", text)

    _xray_log(
        f"[防護] 自己訊息已攔截 AI 流程 | msgId={msg_id} partner={chat_partner}"
    )

    return {
        "is_self_message": True,
        "is_group_message": is_group_message,
        "is_voice_message": is_voice_message,
        "is_image_message": is_image_message,
        "dispatch_route": "self_no_ai",
        "webhook_payload": data,
        "chat_partner": chat_partner,
        "speaker_id": speaker_id,
        "content": text,
        "stored_content": stored_content,
        "from_user": from_user,
        "to_user": to_user,
        "msg_id": msg_id,
    }


def _parse_message(data: dict) -> dict[str, Any] | None:
    """
    解析單則 Webhook 訊息並分流。
    Type 1 → AI 文字流程；Type 3 → 圖片佔位+背景下載；Type 34 → 語音佔位+背景下載；其餘略過。
    """
    header = _parse_message_header(data)
    if header is None:
        return None

    msg_type = header["msg_type"]
    msg_id = header["msg_id"]
    from_user = header["from_user"]

    if msg_type is None:
        _xray_log(f"[判斷] 略過訊息 msgId={msg_id}：缺少 msgType")
        return None

    # ── 自我迴圈防護：寫 DB、不進 AI ─────────────────────────────────────
    if _is_message_from_self(data, header):
        if msg_type == TEXT_MSG_TYPE:
            raw_content = _extract_text_content(data)
            if not raw_content:
                _xray_log(f"[判斷] 略過自己文字訊息 msgId={msg_id}：內容為空")
                return None
            return _parse_self_message_payload(data, header, raw_content)

        if msg_type == IMAGE_MSG_TYPE:
            _dump_image_message_payload(data, msg_id)
            return _parse_self_message_payload(
                data, header, IMAGE_MESSAGE_DISPLAY, is_image_message=True,
            )

        if msg_type == VOICE_MSG_TYPE:
            _dump_voice_message_payload(data, msg_id)
            return _parse_self_message_payload(
                data, header, VOICE_MESSAGE_DISPLAY, is_voice_message=True,
            )

        _xray_log(
            f"[防護] 自己訊息 msgType={msg_type} 不支援，略過 | msgId={msg_id}"
        )
        return None

    # ── 聯絡人訊息分流 ───────────────────────────────────────────────────
    if msg_type == TEXT_MSG_TYPE:
        raw_content = _extract_text_content(data)
        if not raw_content:
            _xray_log(f"[判斷] 略過訊息 msgId={msg_id} from={from_user}：內容為空")
            return None

        routing = _resolve_incoming_routing(data, header, raw_content)
        if routing is None:
            return None

        _xray_log(
            f"[解析] {'群組' if routing['is_group_message'] else '好友'}文字訊息 "
            f"msgId={msg_id} partner={routing['chat_partner']} "
            f"content={routing['content'][:80]}"
        )
        return {
            "is_self_message": False,
            "is_voice_message": False,
            "is_image_message": False,
            "dispatch_route": "ai_text",
            **routing,
        }

    if msg_type == IMAGE_MSG_TYPE:
        _dump_image_message_payload(data, msg_id)
        routing = _resolve_incoming_routing(data, header, IMAGE_MESSAGE_DISPLAY)
        if routing is None:
            return None

        _xray_log(
            f"[解析] 圖片訊息 msgId={msg_id} partner={routing['chat_partner']}"
        )
        return {
            "is_self_message": False,
            "is_voice_message": False,
            "is_image_message": True,
            "dispatch_route": "incoming_image",
            "webhook_payload": data,
            **routing,
        }

    if msg_type == VOICE_MSG_TYPE:
        _dump_voice_message_payload(data, msg_id)
        routing = _resolve_incoming_routing(data, header, VOICE_MESSAGE_DISPLAY)
        if routing is None:
            return None

        _xray_log(
            f"[解析] 語音訊息 msgId={msg_id} partner={routing['chat_partner']} | 嘗試背景下載"
        )
        return {
            "is_self_message": False,
            "is_voice_message": True,
            "is_image_message": False,
            "dispatch_route": "incoming_voice",
            "webhook_payload": data,
            **routing,
        }

    _xray_log(
        f"[判斷] 略過訊息 msgId={msg_id} from={from_user}："
        f"不支援的訊息類型 msgType={msg_type}"
    )
    return None


def _extract_messages(payload: dict) -> list[dict]:
    """從 webhook payload 提取訊息列表（支援單則或批次格式）。"""
    messages: list[dict] = []

    data_block = payload.get("Data") or payload.get("data")
    if isinstance(data_block, dict):
        batch = data_block.get("messages") or data_block.get("Messages")
        if isinstance(batch, list):
            messages.extend(batch)

    if not messages and _get_field(payload, "FromUserName", "fromUser", "from_user"):
        messages.append(payload)

    return messages


async def save_message(
    wx_id: str,
    content: str,
    is_ai: bool,
    background_tasks: BackgroundTasks | None = None,
) -> dict[str, Any] | None:
    """將訊息寫入資料庫，並推播至前端。"""
    msg_data: dict[str, Any] | None = None
    async with AsyncSessionLocal() as session:
        try:
            row = Message(wx_id=wx_id, content=content, is_ai=is_ai)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            msg_data = _message_to_dict(row)
        except Exception as exc:
            await _safe_rollback(session)
            logger.error(
                "儲存訊息至資料庫失敗 | wx_id=%s is_ai=%s | %s",
                wx_id,
                is_ai,
                exc,
                exc_info=True,
            )
            return None

    try:
        await broadcast_new_message(msg_data)
    except Exception as exc:
        logger.error(
            "推播新訊息至前端失敗 | wx_id=%s message_id=%s | %s",
            wx_id,
            msg_data.get("id") if msg_data else None,
            exc,
            exc_info=True,
        )

    if msg_data and not is_ai:
        await check_and_schedule_memory_extraction(wx_id, background_tasks)

    return msg_data


async def update_message_content(
    message_id: int,
    content: str,
) -> dict[str, Any] | None:
    """更新已存在訊息的 content（供背景媒體下載完成後替換佔位文字）。"""
    async with AsyncSessionLocal() as session:
        try:
            row = await session.get(Message, message_id)
            if row is None:
                return None
            row.content = content
            await session.commit()
            await session.refresh(row)
            return _message_to_dict(row)
        except Exception as exc:
            await _safe_rollback(session)
            logger.error(
                "更新訊息內容失敗 | message_id=%s | %s",
                message_id,
                exc,
                exc_info=True,
            )
            return None


async def broadcast_new_message(msg_data: dict[str, Any]) -> None:
    """將新訊息廣播給所有已連線的前端。"""
    if not active_frontend_connections:
        return

    payload = {"type": "new_message", "message": msg_data}
    dead_connections: list[WebSocket] = []

    for ws in active_frontend_connections:
        try:
            await ws.send_json(payload)
        except Exception:
            logger.warning("前端 WebSocket 推播失敗，將移除連線")
            dead_connections.append(ws)

    for ws in dead_connections:
        if ws in active_frontend_connections:
            active_frontend_connections.remove(ws)


async def broadcast_message_updated(msg_data: dict[str, Any]) -> None:
    """將訊息內容更新廣播給所有已連線的前端。"""
    if not active_frontend_connections:
        return

    payload = {"type": "message_updated", "message": msg_data}
    dead_connections: list[WebSocket] = []

    for ws in active_frontend_connections:
        try:
            await ws.send_json(payload)
        except Exception:
            logger.warning("前端 WebSocket 訊息更新推播失敗，將移除連線")
            dead_connections.append(ws)

    for ws in dead_connections:
        if ws in active_frontend_connections:
            active_frontend_connections.remove(ws)


async def get_or_create_contact_ai_enabled(wx_id: str) -> bool:
    """查詢聯絡人 AI 設定；若不存在則建立預設一般名單 (ai_enabled=False)。"""
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                select(ContactSettings).where(ContactSettings.wx_id == wx_id)
            )
            row = result.scalar_one_or_none()

            if row is None:
                default_ai_enabled = False
                row = ContactSettings(wx_id=wx_id, ai_enabled=default_ai_enabled)
                session.add(row)
                await session.commit()
                await session.refresh(row)
                kind = "群組" if _is_chatroom_id(wx_id) else "聯絡人"
                _xray_log(
                    f"[狀態] 查閱資料庫，{kind} {wx_id} 尚無設定，"
                    f"已自動建立 ai_enabled={default_ai_enabled}"
                )
                return default_ai_enabled

            raw_value = row.ai_enabled
            enabled = _to_bool(raw_value)
            _xray_log(
                f"[狀態] 查閱資料庫，聯絡人 {wx_id} 的 ai_enabled 狀態為: {enabled} "
                f"(raw={raw_value!r}, type={type(raw_value).__name__})"
            )
            return enabled
        except Exception as exc:
            await _safe_rollback(session)
            logger.error(
                "查詢/建立聯絡人 AI 設定失敗 | wx_id=%s | %s",
                wx_id,
                exc,
                exc_info=True,
            )
            return False


async def set_contact_nickname(wx_id: str, nickname: str | None) -> str | None:
    """設定聯絡人自訂暱稱；空字串視為清除。"""
    normalized_nickname = nickname.strip() if nickname else None
    if normalized_nickname == "":
        normalized_nickname = None

    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                select(ContactSettings).where(ContactSettings.wx_id == wx_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = ContactSettings(
                    wx_id=wx_id, ai_enabled=False, nickname=normalized_nickname
                )
                session.add(row)
            else:
                row.nickname = normalized_nickname
            await session.commit()
            await session.refresh(row)
            _xray_log(
                f"[狀態] 已更新聯絡人 {wx_id} nickname={row.nickname!r}"
            )
            return row.nickname
        except Exception as exc:
            await _safe_rollback(session)
            logger.error(
                "更新聯絡人暱稱失敗 | wx_id=%s | %s",
                wx_id,
                exc,
                exc_info=True,
            )
            raise


async def set_contact_ai_enabled(wx_id: str, enabled: bool) -> bool:
    """設定聯絡人 AI 開關狀態。"""
    normalized = _to_bool(enabled)
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                select(ContactSettings).where(ContactSettings.wx_id == wx_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = ContactSettings(wx_id=wx_id, ai_enabled=normalized)
                session.add(row)
            else:
                row.ai_enabled = normalized
            if normalized and row.assigned_profile_id is None and ACTIVE_PROMPT_ID is not None:
                row.assigned_profile_id = ACTIVE_PROMPT_ID
                _xray_log(
                    f"[狀態] 聯絡人 {wx_id} 首次開啟 AI，"
                    f"已自動綁定全域人設 id={ACTIVE_PROMPT_ID}"
                )
            await session.commit()
            await session.refresh(row)
            _xray_log(
                f"[狀態] 已更新聯絡人 {wx_id} ai_enabled={_to_bool(row.ai_enabled)} "
                f"(raw={row.ai_enabled!r})"
            )
            return _to_bool(row.ai_enabled)
        except Exception as exc:
            await _safe_rollback(session)
            logger.error(
                "更新聯絡人 AI 開關失敗 | wx_id=%s enabled=%s | %s",
                wx_id,
                normalized,
                exc,
                exc_info=True,
            )
            raise


async def set_contact_assigned_profile(
    wx_id: str,
    profile_id: int | None,
) -> tuple[int | None, str | None]:
    """設定聯絡人綁定的專屬 AI 人格。"""
    async with AsyncSessionLocal() as session:
        try:
            if profile_id is not None:
                profile_row = await session.get(SystemPromptProfile, profile_id)
                if profile_row is None:
                    raise HTTPException(status_code=404, detail="找不到此人設")

            result = await session.execute(
                select(ContactSettings).where(ContactSettings.wx_id == wx_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = ContactSettings(
                    wx_id=wx_id,
                    ai_enabled=False,
                    assigned_profile_id=profile_id,
                )
                session.add(row)
            else:
                row.assigned_profile_id = profile_id

            await session.commit()
            await session.refresh(row)

            profile_name: str | None = None
            if row.assigned_profile_id is not None:
                bound = await session.get(SystemPromptProfile, row.assigned_profile_id)
                profile_name = bound.name if bound else None

            _xray_log(
                f"[狀態] 已更新聯絡人 {wx_id} assigned_profile_id={row.assigned_profile_id} "
                f"profile_name={profile_name!r}"
            )
            return row.assigned_profile_id, profile_name
        except HTTPException:
            raise
        except Exception as exc:
            await _safe_rollback(session)
            logger.error(
                "更新聯絡人綁定人設失敗 | wx_id=%s profile_id=%s | %s",
                wx_id,
                profile_id,
                exc,
                exc_info=True,
            )
            raise


def _system_prompt_to_dict(row: SystemPromptProfile) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "content": row.content,
        "is_active": _to_bool(row.is_active),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def ensure_contact_settings_schema() -> None:
    """為既有資料庫補上 contact_settings 缺少的欄位。"""
    async with engine.begin() as conn:
        def migrate(sync_conn) -> None:
            inspector = inspect(sync_conn)
            if "contact_settings" not in inspector.get_table_names():
                return
            columns = {column["name"] for column in inspector.get_columns("contact_settings")}
            if "nickname" not in columns:
                sync_conn.execute(
                    text(
                        "ALTER TABLE contact_settings "
                        "ADD COLUMN nickname VARCHAR(128) NULL"
                    )
                )
                logger.info("已為 contact_settings 新增 nickname 欄位")
            if "memory_summary" not in columns:
                sync_conn.execute(
                    text(
                        "ALTER TABLE contact_settings "
                        "ADD COLUMN memory_summary TEXT NULL"
                    )
                )
                logger.info("已為 contact_settings 新增 memory_summary 欄位")
            if "sent_photo_ids" not in columns:
                sync_conn.execute(
                    text(
                        'ALTER TABLE contact_settings '
                        'ADD COLUMN sent_photo_ids TEXT NOT NULL DEFAULT ""'
                    )
                )
                logger.info("已為 contact_settings 新增 sent_photo_ids 欄位")
            if "assigned_profile_id" not in columns:
                sync_conn.execute(
                    text(
                        "ALTER TABLE contact_settings "
                        "ADD COLUMN assigned_profile_id INT NULL"
                    )
                )
                logger.info("已為 contact_settings 新增 assigned_profile_id 欄位")

        await conn.run_sync(migrate)


async def ensure_photo_assets_schema() -> None:
    """為既有資料庫補上 photo_assets 缺少的欄位。"""
    async with engine.begin() as conn:
        def migrate(sync_conn) -> None:
            inspector = inspect(sync_conn)
            if "photo_assets" not in inspector.get_table_names():
                return
            columns = {column["name"] for column in inspector.get_columns("photo_assets")}
            if "profile_id" not in columns:
                sync_conn.execute(
                    text(
                        "ALTER TABLE photo_assets "
                        "ADD COLUMN profile_id INT NULL"
                    )
                )
                logger.info("已為 photo_assets 新增 profile_id 欄位")

        await conn.run_sync(migrate)


async def migrate_orphan_photo_assets() -> None:
    """將未綁定人格的舊照片歸屬至目前全域啟用人設。"""
    if ACTIVE_PROMPT_ID is None:
        return

    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                select(PhotoAsset).where(PhotoAsset.profile_id.is_(None))
            )
            rows = list(result.scalars().all())
            if not rows:
                return

            for row in rows:
                row.profile_id = ACTIVE_PROMPT_ID
            await session.commit()
            logger.info(
                "已將 %d 張舊照片歸屬至啟用人設 id=%s",
                len(rows),
                ACTIVE_PROMPT_ID,
            )
        except Exception as exc:
            await _safe_rollback(session)
            logger.error("遷移舊照片 profile_id 失敗: %s", exc, exc_info=True)


def _build_time_awareness_block() -> str:
    """產生動態時間標籤，供 system prompt 使用。"""
    now = datetime.now(APP_TIMEZONE)
    weekday_names = [
        "星期一",
        "星期二",
        "星期三",
        "星期四",
        "星期五",
        "星期六",
        "星期日",
    ]
    weekday = weekday_names[now.weekday()]
    time_label = now.strftime(f"%Y年%m月%d日 {weekday} %H:%M")
    return (
        f"[系統當前時間：{time_label}]\n"
        "請根據此時間給出符合情境的回覆（例如半夜、早晨的問候）。"
    )


async def get_contact_memory_summary(wx_id: str) -> str | None:
    """讀取聯絡人長期記憶摘要；無資料時回傳 None。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ContactSettings.memory_summary).where(ContactSettings.wx_id == wx_id)
        )
        summary = result.scalar_one_or_none()
    if summary is None:
        return None
    normalized = str(summary).strip()
    return normalized or None


async def set_contact_memory_summary(
    wx_id: str,
    memory_summary: str | None,
) -> str | None:
    """設定聯絡人長期記憶摘要；空字串視為清除。"""
    normalized = memory_summary.strip() if memory_summary else None
    if normalized == "":
        normalized = None

    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                select(ContactSettings).where(ContactSettings.wx_id == wx_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = ContactSettings(
                    wx_id=wx_id,
                    ai_enabled=False,
                    memory_summary=normalized,
                )
                session.add(row)
            else:
                row.memory_summary = normalized
            await session.commit()
            await session.refresh(row)
            _xray_log(
                f"[狀態] 已更新聯絡人 {wx_id} memory_summary={row.memory_summary!r}"
            )
            return row.memory_summary
        except Exception as exc:
            await _safe_rollback(session)
            logger.error(
                "更新聯絡人長期記憶失敗 | wx_id=%s | %s",
                wx_id,
                exc,
                exc_info=True,
            )
            raise


async def count_messages_for_contact(wx_id: str) -> int:
    """統計聯絡人訊息總數（含雙方）。"""
    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.wx_id == wx_id)
        )
    return int(count or 0)


async def update_contact_memory_summary(wx_id: str) -> None:
    """背景任務：以 LLM 萃取並更新聯絡人長期記憶摘要。"""
    current_memory = await get_contact_memory_summary(wx_id)
    history = await load_sliding_window_history(
        wx_id,
        limit=MEMORY_EXTRACTION_HISTORY_LIMIT,
    )
    if not history:
        _xray_log(f"[記憶] {wx_id} 無對話紀錄可萃取，略過")
        return

    conversation_lines: list[str] = []
    for item in history:
        speaker = "AI" if item["role"] == "assistant" else "對方"
        conversation_lines.append(f"{speaker}：{item['content']}")
    conversation_text = "\n".join(conversation_lines)
    old_memory_text = current_memory if current_memory else "（無）"
    user_payload = (
        f"【舊有記憶】\n{old_memory_text}\n\n"
        f"【最新對話紀錄】\n{conversation_text}"
    )

    try:
        response = await create_active_llm_chat_completion(
            [
                {"role": "system", "content": MEMORY_ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_payload},
            ],
        )
        new_summary = (response.choices[0].message.content or "").strip()
        if not new_summary:
            _xray_log(f"[記憶] {wx_id} LLM 回傳空摘要，略過寫入")
            return
        if len(new_summary) > 120:
            new_summary = new_summary[:100] + "…"

        await set_contact_memory_summary(wx_id, new_summary)
        _xray_log(
            f"[記憶] 已自動更新 {wx_id} memory_summary={new_summary[:80]!r}"
        )
    except Exception:
        logger.exception("背景記憶萃取失敗 | wx_id=%s", wx_id)
        _xray_log(f"[錯誤] 背景記憶萃取失敗 | wx_id={wx_id}")


async def _safe_update_contact_memory_summary(wx_id: str) -> None:
    try:
        await update_contact_memory_summary(wx_id)
    except Exception:
        logger.exception("[記憶] 背景記憶萃取未預期錯誤 | wx_id=%s", wx_id)


def schedule_memory_extraction(
    wx_id: str,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    """將記憶萃取任務排入背景。"""
    if background_tasks is not None:
        background_tasks.add_task(_safe_update_contact_memory_summary, wx_id)
    else:
        asyncio.create_task(_safe_update_contact_memory_summary(wx_id))


async def check_and_schedule_memory_extraction(
    wx_id: str,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    """聯絡人訊息寫入後，每滿 N 則觸發一次背景記憶萃取。"""
    try:
        total = await count_messages_for_contact(wx_id)
        if total > 0 and total % MEMORY_EXTRACTION_INTERVAL == 0:
            _xray_log(
                f"[記憶] {wx_id} 訊息數達 {total}，排程背景記憶萃取"
            )
            schedule_memory_extraction(wx_id, background_tasks)
    except Exception:
        logger.exception("檢查記憶萃取觸發條件失敗 | wx_id=%s", wx_id)


async def get_contact_assigned_profile_id(wx_id: str) -> int | None:
    """讀取聯絡人綁定的專屬人格 ID。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ContactSettings.assigned_profile_id).where(
                ContactSettings.wx_id == wx_id
            )
        )
        return result.scalar_one_or_none()


async def resolve_effective_profile_id(wx_id: str) -> int | None:
    """聯絡人專屬人格優先，否則回退至全域啟用人設。"""
    assigned_id = await get_contact_assigned_profile_id(wx_id)
    if assigned_id is not None:
        return assigned_id
    return ACTIVE_PROMPT_ID


async def resolve_contact_profile(wx_id: str) -> tuple[str, int | None, str | None]:
    """
    解析聯絡人應使用的人設內容。
    回傳 (prompt_content, profile_id, profile_name)。
    """
    assigned_id = await get_contact_assigned_profile_id(wx_id)
    async with AsyncSessionLocal() as session:
        if assigned_id is not None:
            row = await session.get(SystemPromptProfile, assigned_id)
            if row is not None:
                return row.content, row.id, row.name

        result = await session.execute(
            select(SystemPromptProfile)
            .where(SystemPromptProfile.is_active.is_(True))
            .order_by(SystemPromptProfile.id)
            .limit(1)
        )
        active_row = result.scalar_one_or_none()
        if active_row is not None:
            return active_row.content, active_row.id, active_row.name

    return CURRENT_SYSTEM_PROMPT, ACTIVE_PROMPT_ID, DEFAULT_PROMPT_NAME


async def build_dynamic_system_prompt(
    wx_id: str,
    base_prompt: str | None = None,
) -> str:
    """
    動態組裝 system prompt：時間感知 → 長期記憶 → 專屬/全域核心人設。
    """
    parts: list[str] = [_build_time_awareness_block()]

    memory_summary = await get_contact_memory_summary(wx_id)
    if memory_summary:
        parts.append(f"[關於這位客人的重要記憶：{memory_summary}]")

    if base_prompt is None:
        base_prompt, _, _ = await resolve_contact_profile(wx_id)
    parts.append(base_prompt)
    return "\n\n".join(parts)


async def ensure_system_prompt_seed() -> None:
    """若尚無任何人設，建立預設人設並設為啟用。"""
    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count()).select_from(SystemPromptProfile)
        )
        if count and count > 0:
            active_count = await session.scalar(
                select(func.count())
                .select_from(SystemPromptProfile)
                .where(SystemPromptProfile.is_active.is_(True))
            )
            if not active_count:
                first = (
                    await session.execute(
                        select(SystemPromptProfile).order_by(SystemPromptProfile.id).limit(1)
                    )
                ).scalar_one()
                first.is_active = True
                await session.commit()
            return

        profile = SystemPromptProfile(
            name=DEFAULT_PROMPT_NAME,
            content=DEFAULT_SYSTEM_PROMPT,
            is_active=True,
        )
        session.add(profile)
        await session.commit()


async def load_active_system_prompt_from_db() -> tuple[str, int | None]:
    """從資料庫載入目前啟用的人設內容。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(SystemPromptProfile)
            .where(SystemPromptProfile.is_active.is_(True))
            .order_by(SystemPromptProfile.id)
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return DEFAULT_SYSTEM_PROMPT, None
        return row.content, row.id


async def activate_system_prompt_profile(prompt_id: int) -> SystemPromptProfile:
    """將指定人設設為啟用，並同步記憶體快取。"""
    global CURRENT_SYSTEM_PROMPT, ACTIVE_PROMPT_ID

    async with AsyncSessionLocal() as session:
        row = await session.get(SystemPromptProfile, prompt_id)
        if row is None:
            raise HTTPException(status_code=404, detail="找不到此人設")

        await session.execute(
            update(SystemPromptProfile).values(is_active=False)
        )
        row.is_active = True
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(row)

        CURRENT_SYSTEM_PROMPT = row.content
        ACTIVE_PROMPT_ID = row.id
        logger.info("已切換啟用人設 id=%s name=%s", row.id, row.name)
        return row


def _build_llm_messages(
    history: list[dict[str, str]],
    user_message: str,
    system_prompt: str | None = None,
) -> list[dict[str, str]]:
    """組合含 System Prompt 的 messages 陣列（system 固定為第一筆）。"""
    return [
        {"role": "system", "content": system_prompt or CURRENT_SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": user_message},
    ]


def _db_content_to_llm_text(content: str) -> str:
    """將資料庫訊息轉為適合餵給 LLM 的文字。"""
    text = (content or "").strip()
    if not text:
        return ""
    if IMAGE_MSG_TAG in text:
        text = IMAGE_MSG_PATTERN.sub("[已發送圖片]", text)
    if VOICE_MSG_TAG in text:
        text = VOICE_MSG_PATTERN.sub("[已發送語音]", text)
    return text.strip()


async def load_sliding_window_history(
    wx_id: str,
    limit: int = SLIDING_WINDOW_MESSAGE_LIMIT,
    exclude_user_message: str | None = None,
) -> list[dict[str, str]]:
    """
    從 messages 資料表載入滑動視窗歷史：DESC 取最近 N 筆，反轉為 ASC 時間軸。
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Message)
            .where(Message.wx_id == wx_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())

    rows.reverse()

    history: list[dict[str, str]] = []
    for row in rows:
        text = _db_content_to_llm_text(row.content)
        if not text:
            continue
        role = "assistant" if _to_bool(row.is_ai) else "user"
        history.append({"role": role, "content": text})

    if (
        exclude_user_message
        and history
        and history[-1]["role"] == "user"
        and history[-1]["content"] == exclude_user_message.strip()
    ):
        history = history[:-1]

    return history


def _llm_config_to_dict(row: LlmConfig, *, mask_api_key: bool = True) -> dict[str, Any]:
    api_key = row.api_key or ""
    masked_key = ""
    if api_key:
        if len(api_key) <= 8:
            masked_key = "*" * len(api_key)
        else:
            masked_key = f"{api_key[:4]}...{api_key[-4:]}"
    return {
        "id": row.id,
        "name": row.name,
        "provider": row.provider,
        "api_key": masked_key if mask_api_key else api_key,
        "has_api_key": bool(api_key),
        "base_url": row.base_url,
        "model_name": row.model_name,
        "temperature": row.temperature,
        "is_active": row.is_active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _infer_llm_provider_from_base_url(base_url: str) -> str:
    lower = (base_url or "").lower()
    if "api.openai.com" in lower:
        return "OpenAI"
    if "deepseek.com" in lower:
        return "DeepSeek"
    if "groq.com" in lower:
        return "Groq"
    if "11434" in lower or "ollama" in lower:
        return "Ollama"
    return "Custom"


async def ensure_llm_configs_seed() -> None:
    """若尚無 LLM 設定，將 .env 預設值寫入並設為啟用。"""
    async with AsyncSessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(LlmConfig))
        if count and count > 0:
            active_count = await session.scalar(
                select(func.count())
                .select_from(LlmConfig)
                .where(LlmConfig.is_active.is_(True))
            )
            if not active_count:
                first_row = (
                    await session.execute(
                        select(LlmConfig).order_by(LlmConfig.id).limit(1)
                    )
                ).scalar_one_or_none()
                if first_row is not None:
                    first_row.is_active = True
                    await session.commit()
            return

        provider = _infer_llm_provider_from_base_url(LOCAL_API_BASE)
        row = LlmConfig(
            name="預設（來自環境變數）",
            provider=provider,
            api_key=LOCAL_API_KEY or None,
            base_url=LOCAL_API_BASE,
            model_name=LOCAL_MODEL_NAME,
            temperature=DEFAULT_LLM_TEMPERATURE,
            is_active=True,
        )
        session.add(row)
        await session.commit()
        logger.info(
            "已建立預設 LLM 設定 | provider=%s model=%s base_url=%s",
            provider,
            LOCAL_MODEL_NAME,
            LOCAL_API_BASE,
        )


async def get_active_llm_config() -> LlmConfig | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(LlmConfig)
            .where(LlmConfig.is_active.is_(True))
            .order_by(LlmConfig.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


def build_openai_client_for_config(config: LlmConfig) -> AsyncOpenAI:
    api_key = (config.api_key or "").strip() or "not-needed"
    return AsyncOpenAI(api_key=api_key, base_url=config.base_url)


async def create_active_llm_chat_completion(
    messages: list[dict[str, str]],
    *,
    temperature: float | None = None,
) -> Any:
    """依資料庫中 is_active=True 的 LLM 設定動態建立 Client 並呼叫。"""
    config = await get_active_llm_config()
    if config is None:
        raise ValueError("未設定啟用中的 LLM 配置，請至系統設定新增並啟用")

    client = build_openai_client_for_config(config)
    temp = config.temperature if temperature is None else temperature
    return await client.chat.completions.create(
        model=config.model_name,
        messages=messages,
        temperature=temp,
    )


async def activate_llm_config(config_id: int) -> LlmConfig:
    async with AsyncSessionLocal() as session:
        row = await session.get(LlmConfig, config_id)
        if row is None:
            raise HTTPException(status_code=404, detail="找不到此 LLM 設定")

        await session.execute(update(LlmConfig).values(is_active=False))
        row.is_active = True
        await session.commit()
        await session.refresh(row)
        logger.info(
            "已切換啟用 LLM 設定 id=%s name=%s model=%s",
            row.id,
            row.name,
            row.model_name,
        )
        return row


async def get_local_llm_reply(sender_id: str, user_message: str) -> str:
    """呼叫本地大語言模型（DB 滑動視窗上下文）。"""
    history = await load_sliding_window_history(
        sender_id,
        limit=SLIDING_WINDOW_MESSAGE_LIMIT,
        exclude_user_message=user_message,
    )
    system_prompt = await build_dynamic_system_prompt(sender_id)
    photo_catalog = await build_photo_library_catalog(sender_id)
    if photo_catalog:
        system_prompt = f"{system_prompt}\n\n{photo_catalog}"

    try:
        response = await create_active_llm_chat_completion(
            _build_llm_messages(history, user_message, system_prompt),
        )
        reply = (response.choices[0].message.content or "").strip()
        if not reply:
            raise ValueError("本地模型回傳空內容")
        return reply
    except Exception as exc:
        fallback_text = random.choice(FALLBACK_REPLIES)
        logger.error(
            "[系統警告] AI 思考出錯或超時，已觸發斷線救援: %s。詳細錯誤: %s",
            fallback_text,
            exc,
        )
        _xray_log(
            f"[系統警告] AI 思考出錯或超時，已觸發斷線救援: {fallback_text}。"
            f"詳細錯誤: {exc}"
        )
        raise LLMFallbackError(fallback_text, exc) from exc


def _padpro_sync_url() -> str:
    """PadProMAX 強制同步 API（與 SendTxt 相同 authcode 驗證方式）。"""
    return f"{PADPRO_URL}/api/Msg/Sync?authcode={PADPRO_AUTH_KEY}"


async def force_sync_once(client: httpx.AsyncClient) -> None:
    """呼叫一次 /api/Msg/Sync，強制 PadPro 立刻拉取微信新訊息。"""
    response = await client.post(_padpro_sync_url(), json={})
    response.raise_for_status()


async def force_sync_loop() -> None:
    """
    背景輪詢：每 SYNC_POLL_INTERVAL 秒呼叫 PadPro /Msg/Sync，
    繞過 AutoSync 預設約 30 秒的延遲。
    """
    logger.info(
        "force_sync_loop 背景任務開始 | interval=%.1fs timeout=%.1fs",
        SYNC_POLL_INTERVAL,
        SYNC_REQUEST_TIMEOUT,
    )
    async with httpx.AsyncClient(timeout=SYNC_REQUEST_TIMEOUT) as client:
        while True:
            try:
                await force_sync_once(client)
            except httpx.TimeoutException as exc:
                logger.warning(
                    "[Sync輪詢] /api/Msg/Sync 連線超時，將於 %.1f 秒後重試: %s",
                    SYNC_POLL_INTERVAL,
                    exc,
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "[Sync輪詢] /api/Msg/Sync HTTP 錯誤，將於 %.1f 秒後重試: %s",
                    SYNC_POLL_INTERVAL,
                    exc,
                )
            except Exception as exc:
                logger.warning(
                    "[Sync輪詢] /api/Msg/Sync 失敗，將於 %.1f 秒後重試: %s",
                    SYNC_POLL_INTERVAL,
                    exc,
                )
            try:
                await asyncio.sleep(SYNC_POLL_INTERVAL)
            except asyncio.CancelledError:
                logger.info("force_sync_loop 收到取消信號，結束輪詢")
                raise


def send_wechat_message(to_wxid: str, content: str) -> dict:
    """透過 WeChatPadProMAX API 發送文字訊息。"""
    url = f"{PADPRO_URL}/api/Msg/SendTxt?authcode={PADPRO_AUTH_KEY}"
    payload = {
        "At": "",
        "Content": content,
        "ToWxid": to_wxid,
        "Type": 1,
    }

    logger.info("發送訊息 → to=%s, 字數=%d", to_wxid, len(content))
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()

    try:
        result = resp.json()
    except ValueError:
        result = {"raw": resp.text}

    logger.info("發送結果: %s", result)
    return result


def _image_file_to_base64(file_path: str) -> str:
    with open(file_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("ascii")


def send_wechat_image(to_wxid: str, file_path: str) -> dict:
    """透過 WeChatPadProMAX /api/Msg/UploadImg 發送圖片。"""
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"圖片檔案不存在: {file_path}")
    with open(file_path, "rb") as image_file:
        return send_wechat_image_bytes(to_wxid, image_file.read(), source=file_path)


def send_wechat_image_bytes(
    to_wxid: str,
    image_bytes: bytes,
    source: str = "clipboard",
) -> dict:
    """透過 Base64 直接發送圖片二進位資料。"""
    if not image_bytes:
        raise ValueError("圖片內容為空")

    url = f"{PADPRO_URL}/api/Msg/UploadImg?authcode={PADPRO_AUTH_KEY}"
    payload = {
        "Base64": base64.b64encode(image_bytes).decode("ascii"),
        "ToWxid": to_wxid,
    }

    logger.info("發送圖片 → to=%s, source=%s, bytes=%d", to_wxid, source, len(image_bytes))
    resp = requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()

    try:
        result = resp.json()
    except ValueError:
        result = {"raw": resp.text}

    logger.info("圖片發送結果: %s", result)
    return result


# ── PadPro 掃碼登入代理 ────────────────────────────────────────────────────────

def _padpro_api_url(path: str, **query: str) -> str:
    """組合 PadPro API URL（路徑需以 / 開頭，不含 /api 前綴）。"""
    normalized = path if path.startswith("/") else f"/{path}"
    params = [f"authcode={quote(PADPRO_AUTH_KEY, safe='')}"]
    for key, value in query.items():
        if value is not None and str(value) != "":
            params.append(f"{key}={quote(str(value), safe='')}")
    return f"{PADPRO_URL}/api{normalized}?{'&'.join(params)}"


async def _padpro_post_json(path: str, body: dict | None = None, **query: str) -> dict[str, Any]:
    url = _padpro_api_url(path, **query)
    async with httpx.AsyncClient(timeout=PADPRO_HTTP_TIMEOUT) as client:
        response = await client.post(url, json=body or {})
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text, "Success": False, "Message": "非 JSON 回應"}


def _extract_wxid_from_padpro_data(data: Any) -> str | None:
    """從 PadPro 登入/初始化回應中遞迴尋找 wxid。"""
    if data is None:
        return None
    if isinstance(data, str):
        normalized = _normalize_wxid(data)
        if normalized.startswith("wxid_"):
            return normalized
        return None
    if isinstance(data, dict):
        for key in ("userName", "UserName", "wxid", "Wxid", "wx_id"):
            if key not in data:
                continue
            found = _extract_wxid_from_padpro_data(data[key])
            if found:
                return found
        for value in data.values():
            found = _extract_wxid_from_padpro_data(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _extract_wxid_from_padpro_data(item)
            if found:
                return found
    return None


def _extract_nickname_from_padpro_data(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("nickName", "NickName", "nickname"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("string")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    for value in data.values():
        if isinstance(value, dict):
            found = _extract_nickname_from_padpro_data(value)
            if found:
                return found
    return None


def _is_padpro_login_success(body: dict[str, Any]) -> bool:
    if not body.get("Success"):
        return False
    message = str(body.get("Message") or "")
    code = body.get("Code")
    return message == "登陆成功" or code == 0


def _normalize_qr_base64(qr_base64: str) -> str:
    value = (qr_base64 or "").strip()
    if not value:
        return ""
    if value.startswith("data:image"):
        return value
    return f"data:image/png;base64,{value}"


def _cleanup_expired_login_sessions() -> None:
    now = time.time()
    expired = [
        session_uuid
        for session_uuid, session in _login_sessions.items()
        if now - float(session.get("created_at", now)) > LOGIN_QR_TTL_SECONDS
    ]
    for session_uuid in expired:
        _login_sessions.pop(session_uuid, None)


async def load_primary_wechat_account_from_db() -> None:
    """啟動時載入主要微信帳號至記憶體。"""
    global PADPRO_WXID
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WechatAccount)
            .where(WechatAccount.is_primary.is_(True))
            .order_by(WechatAccount.last_login_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            PADPRO_WXID = row.wx_id
            logger.info(
                "已載入主要微信帳號 | wx_id=%s nickname=%s webhook=%s",
                row.wx_id,
                row.nickname,
                row.webhook_registered,
            )


async def upsert_wechat_account(
    wx_id: str,
    *,
    nickname: str | None = None,
    device_id: str | None = None,
    webhook_registered: bool = False,
    set_primary: bool = True,
) -> WechatAccount:
    global PADPRO_WXID
    async with AsyncSessionLocal() as session:
        if set_primary:
            primary_rows = (
                await session.execute(select(WechatAccount).where(WechatAccount.is_primary.is_(True)))
            ).scalars().all()
            for item in primary_rows:
                item.is_primary = False

        result = await session.execute(
            select(WechatAccount).where(WechatAccount.wx_id == wx_id)
        )
        row = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if row is None:
            row = WechatAccount(
                wx_id=wx_id,
                nickname=nickname,
                device_id=device_id,
                auth_key=PADPRO_AUTH_KEY,
                is_primary=set_primary,
                webhook_registered=webhook_registered,
                last_login_at=now,
            )
            session.add(row)
        else:
            row.nickname = nickname or row.nickname
            row.device_id = device_id or row.device_id
            row.auth_key = PADPRO_AUTH_KEY
            row.is_primary = set_primary or row.is_primary
            row.webhook_registered = webhook_registered
            row.last_login_at = now

        await session.commit()
        await session.refresh(row)

    if set_primary:
        PADPRO_WXID = wx_id
    return row


async def padpro_get_login_qrcode() -> dict[str, Any]:
    _cleanup_expired_login_sessions()
    body = await _padpro_post_json(
        "/Login/GetQRMac",
        {"DeviceName": PADPRO_LOGIN_DEVICE_NAME, "oversea": False},
    )
    if not body.get("Success"):
        raise HTTPException(
            status_code=502,
            detail=body.get("Message") or "取得 QR Code 失敗",
        )

    data = body.get("Data") or {}
    session_uuid = str(data.get("Uuid") or data.get("uuid") or "").strip()
    if not session_uuid:
        raise HTTPException(status_code=502, detail="PadPro 未回傳 Uuid")

    qr_base64 = _normalize_qr_base64(str(data.get("QrBase64") or ""))
    if not qr_base64:
        raise HTTPException(status_code=502, detail="PadPro 未回傳 QrBase64")

    device_id = str(body.get("DeviceId") or data.get("DeviceId") or "").strip() or None
    _login_sessions[session_uuid] = {
        "uuid": session_uuid,
        "device_id": device_id,
        "qr_base64": qr_base64,
        "created_at": time.time(),
        "status": "pending",
        "message": "請使用微信掃描 QR Code",
        "setup_done": False,
    }
    _xray_log(f"[登入] 已產生 QR Code | uuid={session_uuid} device_id={device_id}")
    return {
        "uuid": session_uuid,
        "qr_base64": qr_base64,
        "device_id": device_id,
        "expired_time": data.get("ExpiredTime"),
    }


async def padpro_set_webhook(enabled: bool) -> dict[str, Any]:
    """設定 PadPro Webhook 開關（同一 authcode 下 URL 設定會保留）。"""
    if not PADPRO_WEBHOOK_PUBLIC_URL:
        if not enabled:
            return {"Success": True, "Message": "略過（未設定 PADPRO_WEBHOOK_PUBLIC_URL）"}
        raise HTTPException(
            status_code=500,
            detail="未設定 PADPRO_WEBHOOK_PUBLIC_URL，無法註冊 Webhook",
        )

    payload: dict[str, Any] = {
        "url": PADPRO_WEBHOOK_PUBLIC_URL,
        "enabled": enabled,
        "includeSelfMessage": True,
        "messageTypes": ["*"],
        "timeout": 10,
        "retryCount": 3,
    }
    if PADPRO_WEBHOOK_SECRET:
        payload["secret"] = PADPRO_WEBHOOK_SECRET

    body = await _padpro_post_json("/Webhook/Set", payload)
    if not body.get("Success"):
        action = "註冊" if enabled else "停用"
        raise HTTPException(
            status_code=502,
            detail=body.get("Message") or f"Webhook {action}失敗",
        )
    return body


async def padpro_register_webhook() -> dict[str, Any]:
    return await padpro_set_webhook(True)


async def padpro_unregister_webhook() -> dict[str, Any]:
    return await padpro_set_webhook(False)


async def clear_logged_in_wechat_account_state() -> None:
    """清除資料庫主要帳號標記，並還原記憶體中的 PADPRO_WXID。"""
    global PADPRO_WXID
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(WechatAccount).where(WechatAccount.is_primary.is_(True))
            )
        ).scalars().all()
        for row in rows:
            row.is_primary = False
            row.webhook_registered = False
        await session.commit()

    PADPRO_WXID = PADPRO_WXID_ENV_DEFAULT
    _xray_log(
        f"[登出] 已清除主要帳號狀態，PADPRO_WXID 還原為 {PADPRO_WXID or '(空)'}"
    )


async def padpro_logout_wechat() -> dict[str, Any]:
    """
    登出 PadPro 微信會話：停用 Webhook → LogOut → 清除本機帳號狀態。
    切換帳號前應先執行；下次掃碼登入會重新 Newinit + 註冊 Webhook。
    """
    webhook_disabled = False
    webhook_message: str | None = None
    try:
        webhook_body = await padpro_unregister_webhook()
        webhook_disabled = True
        webhook_message = str(webhook_body.get("Message") or "")
        _xray_log("[登出] 已停用 PadPro Webhook")
    except HTTPException as exc:
        logger.warning("登出時停用 Webhook 失敗 | %s", exc.detail)
        _xray_log(f"[登出] 停用 Webhook 失敗，繼續 LogOut | {exc.detail}")
    except Exception:
        logger.exception("登出時停用 Webhook 發生未預期錯誤")
        _xray_log("[登出] 停用 Webhook 未預期錯誤，繼續 LogOut")

    try:
        logout_body = await _padpro_post_json("/Login/LogOut", {})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"PadPro 登出連線失敗: {exc}") from exc

    logout_ok = logout_body.get("Success", True) or logout_body.get("Code") in (0, None)
    if not logout_ok:
        logger.warning(
            "PadPro LogOut 回應非成功 | message=%s",
            logout_body.get("Message"),
        )

    await clear_logged_in_wechat_account_state()
    _cleanup_expired_login_sessions()
    _login_sessions.clear()

    padpro_message = str(logout_body.get("Message") or "已登出")
    _xray_log(f"[登出] PadPro LogOut 完成 | message={padpro_message}")
    return {
        "success": logout_ok,
        "message": "已登出微信帳號，可重新掃碼綁定其他帳號",
        "webhook_disabled": webhook_disabled,
        "webhook_message": webhook_message,
        "padpro_message": padpro_message,
    }


async def padpro_complete_login_setup(
    session: dict[str, Any],
    check_body: dict[str, Any],
) -> dict[str, Any]:
    if session.get("setup_done"):
        return {
            "status": "success",
            "message": "登入已完成",
            "wxid": session.get("wxid"),
            "nickname": session.get("nickname"),
            "webhook_registered": session.get("webhook_registered", False),
        }

    init_body = await _padpro_post_json("/Login/Newinit")
    if not init_body.get("Success"):
        raise HTTPException(
            status_code=502,
            detail=init_body.get("Message") or "初始化失敗",
        )

    webhook_body = await padpro_register_webhook()

    wxid = (
        _extract_wxid_from_padpro_data(check_body.get("Data"))
        or _extract_wxid_from_padpro_data(init_body.get("Data"))
    )
    if not wxid:
        raise HTTPException(status_code=502, detail="登入成功但無法解析 wxid")

    nickname = (
        _extract_nickname_from_padpro_data(check_body.get("Data"))
        or _extract_nickname_from_padpro_data(init_body.get("Data"))
    )

    await upsert_wechat_account(
        wxid,
        nickname=nickname,
        device_id=session.get("device_id"),
        webhook_registered=True,
        set_primary=True,
    )

    session["setup_done"] = True
    session["status"] = "success"
    session["message"] = "登入成功"
    session["wxid"] = wxid
    session["nickname"] = nickname
    session["webhook_registered"] = True

    _xray_log(
        f"[登入] 全自動綁定完成 | wxid={wxid} nickname={nickname!r} "
        f"webhook={PADPRO_WEBHOOK_PUBLIC_URL}"
    )
    return {
        "status": "success",
        "message": "登入成功",
        "wxid": wxid,
        "nickname": nickname,
        "webhook_registered": True,
        "init_response": init_body.get("Message"),
        "webhook_response": webhook_body.get("Message"),
    }


async def padpro_check_login_status(session_uuid: str) -> dict[str, Any]:
    _cleanup_expired_login_sessions()
    session = _login_sessions.get(session_uuid)
    if session is None:
        return {
            "status": "expired",
            "message": "QR Code 已過期，請重新產生",
            "success": False,
        }

    if session.get("setup_done"):
        return {
            "status": "success",
            "message": session.get("message") or "登入成功",
            "success": True,
            "wxid": session.get("wxid"),
            "nickname": session.get("nickname"),
            "webhook_registered": session.get("webhook_registered", False),
        }

    check_body = await _padpro_post_json(
        "/Login/CheckMacQR",
        body={},
        uuid=session_uuid,
    )
    message = str(check_body.get("Message") or "")
    code = check_body.get("Code")

    if _is_padpro_login_success(check_body):
        result = await padpro_complete_login_setup(session, check_body)
        return {"success": True, **result}

    if code == -4:
        session["status"] = "slider_confirm"
        session["message"] = message or "請在微信上點擊確定後繼續等待"
        return {
            "status": "slider_confirm",
            "message": session["message"],
            "success": False,
        }

    if message:
        lowered = message.lower()
        if "已掃描" in message or "scanned" in lowered:
            session["status"] = "scanned"
        else:
            session["status"] = "pending"
        session["message"] = message
    else:
        session["status"] = "pending"
        session["message"] = "等待掃碼"

    return {
        "status": session["status"],
        "message": session["message"],
        "success": False,
    }


def _photo_asset_to_dict(
    row: PhotoAsset,
    profile_name: str | None = None,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "keywords": row.keywords,
        "prompt_hint": row.prompt_hint,
        "file_path": row.file_path,
        "original_filename": row.original_filename,
        "profile_id": row.profile_id,
        "profile_name": profile_name,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "url": f"/api/photos/{row.id}/file",
    }


def _split_keywords(keywords: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,，;；\s]+", keywords) if part.strip()]


def _normalize_keyword(keyword: str) -> str:
    return keyword.strip().lower()


def _is_allowed_image_filename(filename: str) -> bool:
    _, ext = os.path.splitext(filename.lower())
    return ext in ALLOWED_IMAGE_EXTENSIONS


def _build_saved_photo_path(original_filename: str) -> str:
    _, ext = os.path.splitext(original_filename.lower())
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        ext = ".jpg"
    return os.path.join(PHOTOS_DIR, f"{uuid.uuid4().hex}{ext}")


def _guess_image_extension(image_bytes: bytes) -> str:
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def _parse_image_xml_fields(raw_content: str) -> dict[str, str]:
    """從圖片訊息 XML rawContent 提取 CDN 欄位。"""
    if not raw_content or "<" not in raw_content:
        return {}
    fields: dict[str, str] = {}
    for match in re.finditer(
        r'\b(cdnmidimgurl|cdnthumburl|aeskey|cdnthumbaeskey|length|hdlength)\s*=\s*"([^"]*)"',
        raw_content,
        re.IGNORECASE,
    ):
        fields[match.group(1).lower()] = match.group(2)
    return fields


def _extract_image_meta_from_webhook(raw_data: dict[str, Any]) -> dict[str, Any]:
    """彙整 Webhook 圖片訊息下載所需欄位。"""
    image_block = _get_field(raw_data, "image", "Image") or {}
    if not isinstance(image_block, dict):
        image_block = {}

    raw_content = _extract_text_content(raw_data)
    if not raw_content:
        raw_content = str(
            _get_field(raw_data, "rawContent", "RawContent", "content", "Content") or ""
        ).strip()
    xml_fields = _parse_image_xml_fields(raw_content)

    def pick(*keys: str) -> str | None:
        for key in keys:
            value = image_block.get(key)
            if value is None:
                value = image_block.get(key[0].upper() + key[1:] if key else key)
            if value is None:
                value = xml_fields.get(key.lower())
            if value is not None and str(value).strip():
                return str(value).strip()
        return None

    msg_id = _get_field(
        raw_data, "MsgId", "msgId", "newMsgId", "new_msg_id",
    )
    data_len_raw = pick("hdlength", "length") or image_block.get("hdlength") or image_block.get("length")
    try:
        data_len = int(data_len_raw) if data_len_raw is not None else None
    except (TypeError, ValueError):
        data_len = None

    try:
        msg_id_int = int(msg_id) if msg_id is not None else None
    except (TypeError, ValueError):
        msg_id_int = None

    return {
        "cdn_mid_url": pick("cdnmidimgurl"),
        "cdn_thumb_url": pick("cdnthumburl"),
        "aes_key": pick("aeskey", "cdnthumbaeskey"),
        "thumb_aes_key": pick("cdnthumbaeskey", "aeskey"),
        "msg_id": msg_id_int,
        "data_len": data_len,
        "compress_type": 0,
    }


def _get_ci_field(obj: dict[str, Any], key: str) -> Any:
    if not isinstance(obj, dict):
        return None
    key_lower = key.lower()
    for field_key, value in obj.items():
        if isinstance(field_key, str) and field_key.lower() == key_lower:
            return value
    return None


def _decode_padpro_binary_candidate(candidate: Any) -> bytes | None:
    if isinstance(candidate, str) and len(candidate) > 16:
        payload = candidate
        if "base64," in payload:
            payload = payload.split("base64,", 1)[1]
        try:
            decoded = base64.b64decode(payload, validate=False)
            if decoded:
                return decoded
        except Exception:
            return None
    if isinstance(candidate, list) and candidate:
        try:
            decoded = bytes(int(x) & 0xFF for x in candidate)
            if decoded:
                return decoded
        except Exception:
            return None
    return None


def _extract_binary_from_padpro_response(body: dict[str, Any]) -> bytes | None:
    """從 PadPro 下載 API 回應中解析二進位圖片資料。"""
    if not isinstance(body, dict):
        return None

    data_node = _get_ci_field(body, "data") or body.get("Data")
    if isinstance(data_node, dict):
        for key in ("buffer", "bytes", "chunk", "content", "raw", "imageData"):
            decoded = _decode_padpro_binary_candidate(_get_ci_field(data_node, key))
            if decoded:
                return decoded

        inner = _get_ci_field(data_node, "data")
        if isinstance(inner, dict):
            for key in ("buffer", "bytes", "chunk", "content", "raw", "imageData"):
                decoded = _decode_padpro_binary_candidate(_get_ci_field(inner, key))
                if decoded:
                    return decoded

    candidates: list[Any] = []

    def walk(obj: Any, depth: int = 0) -> None:
        if depth > 10 or obj is None:
            return
        if isinstance(obj, dict):
            for key in (
                "Base64",
                "ImageBase64",
                "ThumbBase64",
                "buffer",
                "Buffer",
                "content",
                "bytes",
                "raw",
                "imageData",
            ):
                if key in obj and obj[key] not in (None, ""):
                    candidates.append(obj[key])
            for value in obj.values():
                walk(value, depth + 1)
        elif isinstance(obj, list) and obj and isinstance(obj[0], int):
            candidates.append(obj)

    walk(body.get("Data") or body.get("data") or body)

    for candidate in candidates:
        decoded = _decode_padpro_binary_candidate(candidate)
        if decoded:
            return decoded
    return None


async def _padpro_download_image_cdn(file_no: str, file_aes_key: str) -> bytes | None:
    if not file_no or not file_aes_key:
        return None
    try:
        body = await _padpro_post_json(
            "/Tools/CdnDownloadImage",
            {"fileNo": file_no, "fileAesKey": file_aes_key},
        )
    except Exception:
        logger.exception("CdnDownloadImage 失敗 | fileNo=%s", file_no[:32])
        return None
    if not body.get("Success", True) and body.get("Code") not in (0, None):
        logger.warning(
            "CdnDownloadImage 回應失敗 | fileNo=%s message=%s",
            file_no[:32],
            body.get("Message"),
        )
        return None
    image_bytes = _extract_binary_from_padpro_response(body)
    if not image_bytes:
        logger.warning(
            "CdnDownloadImage 無資料 | fileNo=%s message=%s keys=%s",
            file_no[:32],
            body.get("Message") or body.get("message"),
            list(body.keys()) if isinstance(body, dict) else type(body).__name__,
        )
    return image_bytes


def _build_download_img_payload(
    to_wxid: str,
    msg_id: int,
    data_len: int,
    compress_type: int,
    section_start: int,
    section_len: int,
) -> dict[str, Any]:
    """PadPro DownloadImg 需使用巢狀 section 物件（見 docs 範例）。"""
    return {
        "toWxid": to_wxid,
        "msgId": int(msg_id),
        "dataLen": int(data_len),
        "compressType": int(compress_type),
        "section": {
            "startPos": int(section_start),
            "dataLen": int(section_len),
        },
    }


async def _padpro_download_image_by_msg(
    to_wxid: str,
    msg_id: int,
    data_len: int,
    compress_type: int = 0,
) -> bytes | None:
    if not to_wxid or not msg_id or not data_len or data_len <= 0:
        return None

    chunk_size = 65536
    parts: list[bytes] = []
    offset = 0

    while offset < data_len:
        section_len = min(chunk_size, data_len - offset)
        try:
            body = await _padpro_post_json(
                "/Tools/DownloadImg",
                _build_download_img_payload(
                    to_wxid,
                    msg_id,
                    data_len,
                    compress_type,
                    offset,
                    section_len,
                ),
            )
        except Exception:
            logger.exception(
                "DownloadImg 失敗 | to=%s msgId=%s offset=%s compressType=%s",
                to_wxid,
                msg_id,
                offset,
                compress_type,
            )
            return None

        chunk = _extract_binary_from_padpro_response(body)
        if not chunk:
            logger.warning(
                "DownloadImg 無資料 | to=%s msgId=%s offset=%s compressType=%s message=%s keys=%s",
                to_wxid,
                msg_id,
                offset,
                compress_type,
                body.get("Message") or body.get("message"),
                list(body.keys()) if isinstance(body, dict) else type(body).__name__,
            )
            return None
        parts.append(chunk)
        offset += len(chunk)
        if len(chunk) < section_len:
            break

    return b"".join(parts) if parts else None


async def download_incoming_image_bytes(
    raw_data: dict[str, Any],
    chat_partner: str,
) -> bytes | None:
    """嘗試多種 PadPro API 下載客人傳入的圖片。"""
    meta = _extract_image_meta_from_webhook(raw_data)

    attempts: list[tuple[str, str]] = []
    if meta.get("cdn_mid_url") and meta.get("aes_key"):
        attempts.append((meta["cdn_mid_url"], meta["aes_key"]))
    if meta.get("cdn_thumb_url") and meta.get("thumb_aes_key"):
        thumb_pair = (meta["cdn_thumb_url"], meta["thumb_aes_key"])
        if thumb_pair not in attempts:
            attempts.append(thumb_pair)

    for file_no, aes_key in attempts:
        image_bytes = await _padpro_download_image_cdn(file_no, aes_key)
        if image_bytes:
            _xray_log(
                f"[圖片] CdnDownloadImage 成功 | partner={chat_partner} "
                f"bytes={len(image_bytes)}"
            )
            return image_bytes

    if meta.get("msg_id") and meta.get("data_len"):
        for compress_type in (1, meta.get("compress_type", 0)):
            image_bytes = await _padpro_download_image_by_msg(
                chat_partner,
                meta["msg_id"],
                meta["data_len"],
                compress_type,
            )
            if image_bytes:
                _xray_log(
                    f"[圖片] DownloadImg 成功 | partner={chat_partner} "
                    f"msgId={meta['msg_id']} compressType={compress_type} "
                    f"bytes={len(image_bytes)}"
                )
                return image_bytes

    logger.warning(
        "無法下載客人圖片 | partner=%s meta=%s",
        chat_partner,
        {k: v for k, v in meta.items() if v is not None},
    )
    return None


def persist_received_image_bytes(image_bytes: bytes) -> str:
    """儲存客人傳入圖片，回傳 token（供 /api/media/received/{token}/file）。"""
    ext = _guess_image_extension(image_bytes)
    os.makedirs(RECEIVED_IMAGES_DIR, exist_ok=True)
    token = uuid.uuid4().hex
    saved_path = os.path.join(RECEIVED_IMAGES_DIR, f"{token}{ext}")
    with open(saved_path, "wb") as output_file:
        output_file.write(image_bytes)
    return token


def build_received_image_message_content(token: str) -> str:
    return format_image_message_content(f"/api/media/received/{token}/file")


async def resolve_incoming_image_stored_content(
    parsed: dict[str, Any],
    raw_data: dict[str, Any],
) -> str:
    """
    下載客人圖片並組合 messages.content。
    失敗時回退為 [圖片訊息] 佔位文字。
    """
    chat_partner = parsed["chat_partner"]
    speaker_id = parsed.get("speaker_id") or chat_partner
    is_group = parsed.get("is_group_message", False)
    fallback = parsed.get("stored_content") or IMAGE_MESSAGE_DISPLAY

    image_bytes = await download_incoming_image_bytes(raw_data, chat_partner)
    if not image_bytes:
        return fallback

    token = persist_received_image_bytes(image_bytes)
    image_content = build_received_image_message_content(token)
    if is_group:
        return _format_group_stored_content(speaker_id, image_content)
    return image_content


def _parse_voice_xml_fields(raw_content: str) -> dict[str, str]:
    """從語音訊息 XML rawContent 提取 CDN 欄位。"""
    if not raw_content or "<" not in raw_content:
        return {}
    fields: dict[str, str] = {}
    for match in re.finditer(
        r'\b(voiceurl|aeskey|length|voicelength|voiceformat)\s*=\s*"([^"]*)"',
        raw_content,
        re.IGNORECASE,
    ):
        fields[match.group(1).lower()] = match.group(2)
    return fields


def _extract_voice_meta_from_webhook(raw_data: dict[str, Any]) -> dict[str, Any]:
    """彙整 Webhook 語音訊息下載所需欄位。"""
    voice_block = _get_field(raw_data, "voice", "Voice") or {}
    if not isinstance(voice_block, dict):
        voice_block = {}

    raw_content = _extract_text_content(raw_data)
    if not raw_content:
        raw_content = str(
            _get_field(raw_data, "rawContent", "RawContent", "content", "Content") or ""
        ).strip()
    xml_fields = _parse_voice_xml_fields(raw_content)

    def pick(*keys: str) -> str | None:
        for key in keys:
            value = voice_block.get(key)
            if value is None:
                camel = key[0].upper() + key[1:] if key else key
                value = voice_block.get(camel)
            if value is None:
                value = xml_fields.get(key.lower())
            if value is not None and str(value).strip():
                return str(value).strip()
        return None

    msg_id = _get_field(
        raw_data, "MsgId", "msgId", "newMsgId", "new_msg_id",
    )
    from_user = _normalize_wxid(
        _get_field(
            raw_data, "FromUserName", "fromUser", "from_user", "from_wxid", "sender",
        )
    )

    length_raw = pick("length")
    try:
        byte_length = int(length_raw) if length_raw is not None else None
    except (TypeError, ValueError):
        byte_length = None

    voice_length_raw = pick("voicelength")
    try:
        voice_length_ms = int(voice_length_raw) if voice_length_raw is not None else None
    except (TypeError, ValueError):
        voice_length_ms = None

    voice_format_raw = pick("voiceformat")
    try:
        voice_format = int(voice_format_raw) if voice_format_raw is not None else None
    except (TypeError, ValueError):
        voice_format = None

    try:
        msg_id_int = int(msg_id) if msg_id is not None else None
    except (TypeError, ValueError):
        msg_id_int = None

    base64_payload = pick("base64", "Base64")

    return {
        "voice_url": pick("voiceurl"),
        "aes_key": pick("aeskey"),
        "buf_id": pick("voiceurl"),
        "from_user": from_user,
        "msg_id": msg_id_int,
        "byte_length": byte_length,
        "voice_length_ms": voice_length_ms,
        "voice_format": voice_format,
        "base64": base64_payload,
    }


async def _padpro_download_voice(
    buf_id: str,
    from_user_name: str,
    msg_id: int,
    byte_length: int | None,
) -> bytes | None:
    if not buf_id or not from_user_name or not msg_id:
        return None
    payload: dict[str, Any] = {
        "bufid": buf_id,
        "fromUserName": from_user_name,
        "msgId": int(msg_id),
    }
    if byte_length and byte_length > 0:
        payload["length"] = int(byte_length)

    try:
        body = await _padpro_post_json("/Tools/DownloadVoice", payload)
    except Exception:
        logger.exception(
            "DownloadVoice 失敗 | from=%s msgId=%s",
            from_user_name,
            msg_id,
        )
        return None

    if not body.get("Success", True) and body.get("Code") not in (0, None):
        logger.warning(
            "DownloadVoice 回應失敗 | from=%s msgId=%s message=%s",
            from_user_name,
            msg_id,
            body.get("Message"),
        )
        return None

    voice_bytes = _extract_binary_from_padpro_response(body)
    if not voice_bytes:
        logger.warning(
            "DownloadVoice 無資料 | from=%s msgId=%s message=%s keys=%s",
            from_user_name,
            msg_id,
            body.get("Message") or body.get("message"),
            list(body.keys()) if isinstance(body, dict) else type(body).__name__,
        )
    return voice_bytes


def _is_silk_voice(data: bytes) -> bool:
    return data.startswith(b"#!SILK_V3") or (len(data) > 1 and data[0] == 0x02)


def _is_amr_voice(data: bytes) -> bool:
    return data.startswith(b"#!AMR")


def _is_mp3_voice(data: bytes) -> bool:
    return data.startswith(b"ID3") or data[:2] == b"\xff\xfb"


def convert_incoming_voice_to_mp3(voice_bytes: bytes) -> bytes | None:
    """將微信語音（SILK/AMR 等）轉為瀏覽器可播放的 MP3。"""
    if not voice_bytes:
        return None
    if _is_mp3_voice(voice_bytes):
        return voice_bytes

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "input.bin")
        with open(input_path, "wb") as input_file:
            input_file.write(voice_bytes)

        ffmpeg_input = input_path

        if _is_silk_voice(voice_bytes):
            if _PILK_AVAILABLE:
                silk_path = os.path.join(tmpdir, "input.silk")
                wav_path = os.path.join(tmpdir, "decoded.wav")
                shutil.copy(input_path, silk_path)
                try:
                    pilk.silk_to_wav(silk_path, wav_path, rate=24000)
                    if os.path.isfile(wav_path) and os.path.getsize(wav_path) > 0:
                        ffmpeg_input = wav_path
                except Exception:
                    logger.exception("pilk SILK 解碼失敗，改由 ffmpeg 嘗試")
            else:
                logger.warning("pilk 未安裝，SILK 語音可能無法轉檔")

        if _is_amr_voice(voice_bytes):
            amr_path = os.path.join(tmpdir, "input.amr")
            shutil.copy(input_path, amr_path)
            ffmpeg_input = amr_path

        mp3_path = os.path.join(tmpdir, "output.mp3")
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            ffmpeg_input,
            "-ac",
            "1",
            "-ar",
            "24000",
            "-acodec",
            "libmp3lame",
            "-q:a",
            "4",
            mp3_path,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=60,
                check=False,
            )
        except FileNotFoundError:
            logger.error("ffmpeg 未安裝，無法轉換語音")
            return None
        except subprocess.TimeoutExpired:
            logger.error("ffmpeg 語音轉檔逾時")
            return None

        if result.returncode != 0:
            logger.warning(
                "ffmpeg 語音轉檔失敗 | stderr=%s",
                (result.stderr or b"")[:400].decode(errors="replace"),
            )
            return None
        if not os.path.isfile(mp3_path) or os.path.getsize(mp3_path) == 0:
            return None
        with open(mp3_path, "rb") as mp3_file:
            return mp3_file.read()


async def download_incoming_voice_bytes(
    raw_data: dict[str, Any],
    chat_partner: str,
) -> bytes | None:
    """嘗試多種 PadPro API 下載客人傳入的語音。"""
    meta = _extract_voice_meta_from_webhook(raw_data)
    from_user = meta.get("from_user") or chat_partner

    if meta.get("base64"):
        decoded = _decode_padpro_binary_candidate(meta["base64"])
        if decoded:
            _xray_log(
                f"[語音] Webhook base64 直取成功 | partner={chat_partner} "
                f"bytes={len(decoded)}"
            )
            return decoded

    if meta.get("voice_url") and meta.get("aes_key"):
        voice_bytes = await _padpro_download_image_cdn(
            meta["voice_url"],
            meta["aes_key"],
        )
        if voice_bytes:
            _xray_log(
                f"[語音] CdnDownloadImage 成功 | partner={chat_partner} "
                f"bytes={len(voice_bytes)}"
            )
            return voice_bytes

    if meta.get("buf_id") and meta.get("msg_id") and from_user:
        voice_bytes = await _padpro_download_voice(
            meta["buf_id"],
            from_user,
            meta["msg_id"],
            meta.get("byte_length"),
        )
        if voice_bytes:
            _xray_log(
                f"[語音] DownloadVoice 成功 | partner={chat_partner} "
                f"msgId={meta['msg_id']} bytes={len(voice_bytes)}"
            )
            return voice_bytes

    logger.warning(
        "無法下載客人語音 | partner=%s meta=%s",
        chat_partner,
        {k: v for k, v in meta.items() if v is not None},
    )
    return None


def persist_received_voice_bytes(voice_mp3_bytes: bytes) -> str:
    """儲存客人傳入語音（MP3），回傳 token。"""
    os.makedirs(RECEIVED_VOICES_DIR, exist_ok=True)
    token = uuid.uuid4().hex
    saved_path = os.path.join(RECEIVED_VOICES_DIR, f"{token}.mp3")
    with open(saved_path, "wb") as output_file:
        output_file.write(voice_mp3_bytes)
    return token


def build_received_voice_message_content(token: str) -> str:
    return format_voice_message_content(f"/api/media/received/{token}/file")


async def resolve_incoming_voice_stored_content(
    parsed: dict[str, Any],
    raw_data: dict[str, Any],
) -> str:
    """下載客人語音並組合 messages.content，失敗時回退佔位文字。"""
    chat_partner = parsed["chat_partner"]
    speaker_id = parsed.get("speaker_id") or chat_partner
    is_group = parsed.get("is_group_message", False)
    fallback = parsed.get("stored_content") or VOICE_MESSAGE_DISPLAY

    voice_bytes = await download_incoming_voice_bytes(raw_data, chat_partner)
    if not voice_bytes:
        return fallback

    mp3_bytes = convert_incoming_voice_to_mp3(voice_bytes)
    if not mp3_bytes:
        logger.warning(
            "語音轉 MP3 失敗，保留佔位 | partner=%s bytes=%s",
            chat_partner,
            len(voice_bytes),
        )
        return fallback

    token = persist_received_voice_bytes(mp3_bytes)
    voice_content = build_received_voice_message_content(token)
    if is_group:
        return _format_group_stored_content(speaker_id, voice_content)
    return voice_content


def format_voice_message_content(voice_api_path: str, prefix: str = "") -> str:
    """組合可持久化至 messages.content 的語音標記。"""
    path = voice_api_path if voice_api_path.startswith("/") else f"/{voice_api_path}"
    body = f"{VOICE_MSG_TAG}{path}"
    return f"{prefix}{body}" if prefix else body


def format_image_message_content(image_api_path: str, prefix: str = "") -> str:
    """組合可持久化至 messages.content 的圖片標記。"""
    path = image_api_path if image_api_path.startswith("/") else f"/{image_api_path}"
    body = f"{IMAGE_MSG_TAG}{path}"
    return f"{prefix}{body}" if prefix else body


def persist_ephemeral_image_bytes(image_bytes: bytes, ext: str = ".png") -> str:
    """將圖片存至 ephemeral 目錄，回傳 token。"""
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        ext = ".png"
    os.makedirs(EPHEMERAL_IMAGES_DIR, exist_ok=True)
    token = uuid.uuid4().hex
    saved_path = os.path.join(EPHEMERAL_IMAGES_DIR, f"{token}{ext}")
    with open(saved_path, "wb") as output_file:
        output_file.write(image_bytes)
    return token


def persist_ephemeral_image_from_path(file_path: str) -> str:
    """複製既有圖片至 ephemeral 目錄，供歷史紀錄引用。"""
    _, ext = os.path.splitext(file_path.lower())
    with open(file_path, "rb") as image_file:
        return persist_ephemeral_image_bytes(image_file.read(), ext or ".jpg")


async def build_image_message_content(
    file_path: str,
    photo_id: int | None = None,
    prefix: str = "",
) -> str:
    """依圖片來源產生 [IMAGE_MSG] 格式 content。"""
    if photo_id is not None:
        return format_image_message_content(f"/api/photos/{photo_id}/file", prefix)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PhotoAsset).where(PhotoAsset.file_path == file_path).limit(1)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return format_image_message_content(f"/api/photos/{row.id}/file", prefix)

    token = persist_ephemeral_image_from_path(file_path)
    return format_image_message_content(f"/api/ephemeral_images/{token}/file", prefix)


def _parse_sent_photo_ids(raw: str | None) -> set[int]:
    """將逗號分隔的已發送照片 ID 字串轉為集合。"""
    if not raw:
        return set()
    sent_ids: set[int] = set()
    for part in raw.split(","):
        token = part.strip()
        if token.isdigit():
            sent_ids.add(int(token))
    return sent_ids


def _format_sent_photo_ids(sent_ids: set[int]) -> str:
    """將已發送照片 ID 集合轉回逗號分隔字串（例如 "1,3,5,"）。"""
    if not sent_ids:
        return ""
    return ",".join(str(photo_id) for photo_id in sorted(sent_ids)) + ","


async def get_contact_sent_photo_ids(wx_id: str) -> set[int]:
    """讀取聯絡人已發送過的照片 ID 清單。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ContactSettings.sent_photo_ids).where(ContactSettings.wx_id == wx_id)
        )
        raw = result.scalar_one_or_none()
    return _parse_sent_photo_ids(raw)


async def mark_photo_sent_to_contact(wx_id: str, photo_id: int | None) -> None:
    """將照片 ID 追加至聯絡人的 sent_photo_ids。"""
    if photo_id is None:
        return

    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                select(ContactSettings).where(ContactSettings.wx_id == wx_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = ContactSettings(
                    wx_id=wx_id,
                    ai_enabled=False,
                    sent_photo_ids="",
                )
                session.add(row)

            sent_ids = _parse_sent_photo_ids(row.sent_photo_ids)
            sent_ids.add(photo_id)
            row.sent_photo_ids = _format_sent_photo_ids(sent_ids)
            await session.commit()
        except Exception as exc:
            await _safe_rollback(session)
            logger.error(
                "更新聯絡人已發送照片紀錄失敗 | wx_id=%s photo_id=%s | %s",
                wx_id,
                photo_id,
                exc,
                exc_info=True,
            )


def _photo_row_matches_keyword(row: PhotoAsset, normalized_keyword: str) -> bool:
    candidates = {
        _normalize_keyword(row.name),
        *map(_normalize_keyword, _split_keywords(row.keywords)),
    }
    return normalized_keyword in candidates


async def _fetch_valid_photo_assets(profile_id: int | None) -> list[PhotoAsset]:
    """取得指定人格圖庫中檔案仍存在的照片。"""
    async with AsyncSessionLocal() as session:
        stmt = select(PhotoAsset).order_by(PhotoAsset.id.asc())
        if profile_id is not None:
            stmt = stmt.where(PhotoAsset.profile_id == profile_id)
        else:
            stmt = stmt.where(PhotoAsset.profile_id.is_(None))
        result = await session.execute(stmt)
        return [
            row
            for row in result.scalars()
            if os.path.isfile(row.file_path)
        ]


async def resolve_photo_for_contact(
    wx_id: str,
    keyword: str,
) -> tuple[str | None, str | None, int | None, str]:
    """
    依關鍵字為聯絡人解析照片，排除已發送過的 ID，必要時降級或回報耗盡。
    回傳 (路徑, 顯示名稱, photo_id, status)。
    status: ok | missing_file | exhausted
    """
    normalized = _normalize_keyword(keyword)
    if not normalized:
        return None, None, None, "missing_file"

    profile_id = await resolve_effective_profile_id(wx_id)
    sent_ids = await get_contact_sent_photo_ids(wx_id)
    all_assets = await _fetch_valid_photo_assets(profile_id)
    keyword_pool = [
        row for row in all_assets if _photo_row_matches_keyword(row, normalized)
    ]

    if not keyword_pool:
        if profile_id is None:
            for tag, path in PHOTO_LIBRARY.items():
                if _normalize_keyword(tag) == normalized and os.path.isfile(path):
                    return path, tag, None, "ok"
        return None, None, None, "missing_file"

    available_keyword = [row for row in keyword_pool if row.id not in sent_ids]
    if available_keyword:
        row = random.choice(available_keyword)
        return row.file_path, row.name, row.id, "ok"

    unsent_assets = [row for row in all_assets if row.id not in sent_ids]
    if unsent_assets:
        row = random.choice(unsent_assets)
        logger.info(
            "[圖片去重] 關鍵字 '%s' 的照片皆已發送過，改發其他未發送照片 id=%s",
            keyword.strip(),
            row.id,
        )
        _xray_log(
            f"[圖片去重] 關鍵字 '{keyword.strip()}' 已發完，"
            f"改發 photo_id={row.id} name={row.name}"
        )
        return row.file_path, row.name, row.id, "ok"

    return None, None, None, "exhausted"


async def resolve_photo_path_by_keyword(
    keyword: str,
    profile_id: int | None = None,
) -> tuple[str | None, str | None, int | None]:
    """相容舊呼叫：依關鍵字與人格圖庫解析照片。"""
    normalized = _normalize_keyword(keyword)
    if not normalized:
        return None, None, None

    all_assets = await _fetch_valid_photo_assets(profile_id)
    keyword_pool = [
        row for row in all_assets if _photo_row_matches_keyword(row, normalized)
    ]
    if keyword_pool:
        row = random.choice(keyword_pool)
        return row.file_path, row.name, row.id

    if profile_id is None:
        for tag, path in PHOTO_LIBRARY.items():
            if _normalize_keyword(tag) == normalized and os.path.isfile(path):
                return path, tag, None

    return None, None, None


async def build_photo_library_catalog(wx_id: str) -> str:
    """組合供 LLM 參考的照片庫說明文字（僅含該聯絡人綁定人格的圖庫）。"""
    lines = [
        "【你可發送的照片庫】",
        "當情境適合發照片時，可在回覆中加入隱藏標籤 [發送照片: 關鍵字]，",
        "系統會自動發送對應圖片並從文字中移除標籤。不要濫用，一次回覆最多 1～2 張。",
    ]

    profile_id = await resolve_effective_profile_id(wx_id)
    async with AsyncSessionLocal() as session:
        stmt = select(PhotoAsset).order_by(PhotoAsset.id.asc())
        if profile_id is not None:
            stmt = stmt.where(PhotoAsset.profile_id == profile_id)
        else:
            stmt = stmt.where(PhotoAsset.profile_id.is_(None))
        result = await session.execute(stmt)
        rows = [row for row in result.scalars() if os.path.isfile(row.file_path)]

    if rows:
        for row in rows:
            tags = "、".join(_split_keywords(row.keywords) or [row.name])
            hint = row.prompt_hint.strip() or "（無補充說明）"
            lines.append(f"- 關鍵字「{tags}」：{row.name} — {hint}")
    elif profile_id is None:
        for tag, path in PHOTO_LIBRARY.items():
            if os.path.isfile(path):
                lines.append(f"- 關鍵字「{tag}」：{os.path.basename(path)}")

    if len(lines) <= 3:
        return ""

    return "\n".join(lines)


async def process_ai_photo_tags(reply: str, to_wxid: str) -> tuple[str, list[str]]:
    """攔截 AI 回覆中的 [發送照片: 關鍵字] 標籤並觸發發圖（含去重與耗盡防呆）。"""
    sent_image_contents: list[str] = []
    keywords = PHOTO_SEND_TAG_PATTERN.findall(reply)
    library_exhausted = False

    for keyword in keywords:
        kw_stripped = keyword.strip()
        file_path, display_name, photo_id, status = await resolve_photo_for_contact(
            to_wxid,
            keyword,
        )

        if status == "exhausted":
            library_exhausted = True
            logger.warning(
                "[圖片去重] 聯絡人 %s 已看過圖庫全部照片，關鍵字 '%s' 無法再發送",
                to_wxid,
                kw_stripped,
            )
            _xray_log(
                f"[圖片去重] 聯絡人 {to_wxid} 圖庫已耗盡，"
                f"關鍵字 '{kw_stripped}' 改為純文字並附加提示"
            )
            continue

        if status == "missing_file" or not file_path or not os.path.isfile(file_path):
            logger.warning(
                "[系統警告] AI 試圖發送圖片關鍵字 '%s'，但在素材庫中查無實體檔案。"
                "已抹除標籤並轉為純文字發送。",
                kw_stripped,
            )
            _xray_log(
                f"[系統警告] AI 試圖發送圖片關鍵字 '{kw_stripped}'，"
                f"但在素材庫中查無實體檔案。已抹除標籤並轉為純文字發送。"
            )
            continue

        try:
            send_wechat_image(to_wxid, file_path)
            await mark_photo_sent_to_contact(to_wxid, photo_id)
            image_content = await build_image_message_content(file_path, photo_id)
            sent_image_contents.append(image_content)
            _xray_log(
                f"[圖片] 已發送照片 keyword={kw_stripped} "
                f"photo_id={photo_id} name={display_name} content={image_content}"
            )
        except (requests.RequestException, OSError) as exc:
            logger.exception("發送圖片失敗 | keyword=%s", keyword)
            logger.warning(
                "[系統警告] AI 試圖發送圖片關鍵字 '%s'，但在素材庫中查無實體檔案。"
                "已抹除標籤並轉為純文字發送。",
                kw_stripped,
            )
            _xray_log(
                f"[系統警告] AI 試圖發送圖片關鍵字 '{kw_stripped}'，"
                f"發送失敗已抹除標籤。詳細錯誤: {exc}"
            )

    cleaned = PHOTO_SEND_TAG_PATTERN.sub("", reply)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    if library_exhausted:
        if cleaned:
            cleaned = f"{cleaned} {PHOTO_LIBRARY_EXHAUSTED_SUFFIX}"
        else:
            cleaned = PHOTO_LIBRARY_EXHAUSTED_SUFFIX

    return cleaned, sent_image_contents


async def send_photo_to_contact(wx_id: str, photo_id: int) -> tuple[dict[str, Any], dict]:
    """依照片 ID 發送至指定聯絡人/群組。"""
    async with AsyncSessionLocal() as session:
        row = await session.get(PhotoAsset, photo_id)
        if row is None:
            raise HTTPException(status_code=404, detail="找不到此照片")
        if not os.path.isfile(row.file_path):
            raise HTTPException(status_code=404, detail="照片檔案不存在")
        photo_data = _photo_asset_to_dict(row)
        file_path = row.file_path

    result = send_wechat_image(wx_id, file_path)
    return photo_data, result


async def process_self_message(
    parsed: dict[str, Any],
    background_tasks: BackgroundTasks | None = None,
) -> bool:
    """
    處理自己從手機發出的訊息：自動建檔 → 無條件寫入 messages → 不觸發 AI。
    圖片下載耗時，改由背景任務完成後再更新 DB。
    """
    chat_partner = parsed["chat_partner"]
    raw_content = parsed.get("stored_content", parsed["content"])

    if raw_content.startswith("[手機回覆]"):
        content = raw_content
    elif parsed.get("is_group_message"):
        content = f"[手機回覆] {raw_content}"
    else:
        content = f"[手機回覆] {raw_content}"

    logger.info(
        "[Webhook] 收到自己手機發出的訊息，接收方 (chat_partner) 為: %s",
        chat_partner,
    )
    _xray_log(
        f"[流程] 準備寫入手機回覆 | msgId={parsed['msg_id']} "
        f"partner={chat_partner} group={parsed.get('is_group_message')} "
        f"content={raw_content[:80]}"
    )

    # 寫入前先確保聯絡人建檔（ContactSettings）
    try:
        await ensure_contact_record(chat_partner)
        _xray_log(f"[建檔] 已確認聯絡人 {chat_partner} 存在於 ContactSettings")
    except Exception:
        logger.exception("自動建檔失敗，仍嘗試寫入訊息 | chat_partner=%s", chat_partner)
        _xray_log(f"[錯誤] 聯絡人 {chat_partner} 自動建檔失敗，仍嘗試寫入 messages")

    saved = await save_message(chat_partner, content, is_ai=True, background_tasks=background_tasks)

    if saved and parsed.get("is_image_message") and parsed.get("webhook_payload"):
        schedule_incoming_media_processing(
            parsed,
            parsed["webhook_payload"],
            saved["id"],
            background_tasks,
            is_self=True,
        )

    if saved and parsed.get("is_voice_message") and parsed.get("webhook_payload"):
        schedule_incoming_media_processing(
            parsed,
            parsed["webhook_payload"],
            saved["id"],
            background_tasks,
            is_self=True,
        )

    if saved:
        logger.info("[Webhook] 已將手機回覆寫入資料庫，對象: %s", chat_partner)
        _xray_log(
            f"[Webhook] 已將手機回覆寫入資料庫，對象: {chat_partner} "
            f"message_id={saved.get('id')}"
        )
        return True

    logger.error("[Webhook] 手機回覆寫入資料庫失敗，對象: %s", chat_partner)
    _xray_log(f"[錯誤] 手機回覆寫入資料庫失敗 | chat_partner={chat_partner}")
    return False


async def process_ai_reply_background(
    chat_partner: str,
    reply_to: str,
    llm_input: str,
) -> None:
    """
    第二階段（背景）：滑動視窗 → LLM → 圖片標籤 → 發送微信 → 寫入 DB → WebSocket 推播。
    """
    _xray_log(f"[背景] 開始 AI 思考 | chat_partner={chat_partner}")

    try:
        ai_reply_raw = await get_local_llm_reply(chat_partner, llm_input)
        ai_reply, sent_image_contents = await process_ai_photo_tags(ai_reply_raw, reply_to)
        _xray_log(
            f"[背景] AI 回覆成功 | chat_partner={chat_partner} "
            f"reply={ai_reply[:80]} images={len(sent_image_contents)}"
        )
    except LLMFallbackError as fallback_exc:
        fallback_text = fallback_exc.fallback_text
        try:
            send_wechat_message(reply_to, fallback_text)
            _xray_log(f"[背景] 斷線救援訊息已發送 | to={reply_to}")
        except requests.RequestException:
            logger.exception("斷線救援訊息發送失敗 | to=%s", reply_to)
        await save_message(
            chat_partner,
            f"[系統救援] {fallback_text}",
            is_ai=True,
        )
        return
    except Exception:
        logger.exception("本地模型處理失敗 | chat_partner=%s", chat_partner)
        ai_reply = "抱歉，我暫時無法回覆，請稍後再試。"
        sent_image_contents = []

    try:
        if ai_reply:
            send_wechat_message(reply_to, ai_reply)
            _xray_log(f"[背景] 已透過 PadPro 發送文字回覆 | to={reply_to}")
    except requests.RequestException:
        logger.exception("發送微信訊息失敗 | to=%s", reply_to)

    if ai_reply:
        await save_message(chat_partner, ai_reply, is_ai=True)

    for image_content in sent_image_contents:
        await save_message(chat_partner, image_content, is_ai=True)

    if not ai_reply and not sent_image_contents:
        await save_message(chat_partner, "抱歉，我暫時無法回覆，請稍後再試。", is_ai=True)


async def _safe_process_ai_reply_background(ai_job: dict[str, Any]) -> None:
    """背景 AI 任務安全包裝，避免例外被靜默吞掉。"""
    try:
        await process_ai_reply_background(**ai_job)
    except Exception:
        logger.exception("[Webhook] 背景 AI 回覆失敗")
        _xray_log("[錯誤] 背景 AI 回覆失敗，請查看 traceback")


def schedule_ai_reply(
    ai_job: dict[str, Any],
    background_tasks: BackgroundTasks | None = None,
) -> None:
    """將 AI 回覆任務排入背景（HTTP 用 BackgroundTasks，WebSocket 用 asyncio.create_task）。"""
    if background_tasks is not None:
        background_tasks.add_task(_safe_process_ai_reply_background, ai_job)
    else:
        asyncio.create_task(_safe_process_ai_reply_background(ai_job))


async def process_incoming_media_background(
    parsed: dict[str, Any],
    raw_data: dict[str, Any],
    message_id: int,
    *,
    is_self: bool = False,
) -> None:
    """背景下載圖片/語音並更新已寫入的佔位訊息。"""
    chat_partner = parsed.get("chat_partner", "")
    dispatch_route = parsed.get("dispatch_route", "")
    try:
        if dispatch_route == "incoming_image":
            stored_content = await resolve_incoming_image_stored_content(parsed, raw_data)
            success_tag = IMAGE_MSG_TAG
        elif dispatch_route == "incoming_voice":
            stored_content = await resolve_incoming_voice_stored_content(parsed, raw_data)
            success_tag = VOICE_MSG_TAG
        else:
            return

        if success_tag not in stored_content:
            _xray_log(
                f"[媒體背景] 下載失敗，保留佔位 | route={dispatch_route} "
                f"message_id={message_id} partner={chat_partner}"
            )
            return

        if is_self and not stored_content.startswith("[手機回覆]"):
            final_content = f"[手機回覆] {stored_content}"
        else:
            final_content = stored_content

        updated = await update_message_content(message_id, final_content)
        if not updated:
            _xray_log(f"[媒體背景] 更新 DB 失敗 | message_id={message_id}")
            return

        await broadcast_message_updated(updated)
        _xray_log(
            f"[媒體背景] 已更新訊息 | route={dispatch_route} message_id={message_id} "
            f"partner={chat_partner}"
        )
    except Exception:
        logger.exception(
            "背景媒體處理失敗 | route=%s message_id=%s partner=%s",
            dispatch_route,
            message_id,
            chat_partner,
        )


async def _safe_process_incoming_media_background(
    parsed: dict[str, Any],
    raw_data: dict[str, Any],
    message_id: int,
    *,
    is_self: bool = False,
) -> None:
    try:
        await process_incoming_media_background(
            parsed,
            raw_data,
            message_id,
            is_self=is_self,
        )
    except Exception:
        logger.exception(
            "[媒體背景] 未預期錯誤 | message_id=%s",
            message_id,
        )


def schedule_incoming_media_processing(
    parsed: dict[str, Any],
    raw_data: dict[str, Any],
    message_id: int,
    background_tasks: BackgroundTasks | None = None,
    *,
    is_self: bool = False,
) -> None:
    """將圖片/語音下載任務排入背景，避免 Webhook HTTP 請求逾時。"""
    if background_tasks is not None:
        background_tasks.add_task(
            _safe_process_incoming_media_background,
            parsed,
            raw_data,
            message_id,
            is_self=is_self,
        )
    else:
        asyncio.create_task(
            _safe_process_incoming_media_background(
                parsed,
                raw_data,
                message_id,
                is_self=is_self,
            )
        )


async def handle_incoming_webhook_message(
    msg: dict,
    background_tasks: BackgroundTasks | None = None,
) -> tuple[bool, dict[str, Any] | None]:
    """
    第一階段：即時解析、寫入 DB、WebSocket 推播、依 dispatch_route 判斷是否需 AI。
    回傳 (是否已處理, AI 背景任務參數或 None)。
    """
    try:
        parsed = _parse_message(msg)
        if not parsed:
            _xray_log("[判斷] 訊息未通過解析，略過處理")
            return False, None

        if parsed.get("is_self_message") or parsed.get("dispatch_route") == "self_no_ai":
            handled = await process_self_message(parsed, background_tasks)
            return handled, None

        dispatch_route = parsed.get("dispatch_route", "ai_text")
        chat_partner = parsed["chat_partner"]
        speaker_id = parsed["speaker_id"]
        stored_content = parsed["stored_content"]
        llm_content = parsed["content"]
        reply_to = parsed["reply_to"]
        is_group = parsed.get("is_group_message", False)

        _xray_log(
            f"[流程] 收到{'群組' if is_group else '好友'}訊息 "
            f"route={dispatch_route} | msgId={parsed['msg_id']} "
            f"chat_partner={chat_partner} speaker={speaker_id} "
            f"content={stored_content[:80]}"
        )

        saved = await save_message(
            chat_partner,
            stored_content,
            is_ai=False,
            background_tasks=background_tasks,
        )
        if not saved:
            _xray_log(
                f"[錯誤] 訊息寫入資料庫失敗 | chat_partner={chat_partner} "
                f"speaker={speaker_id}"
            )
            return True, None

        if dispatch_route == "incoming_image":
            schedule_incoming_media_processing(
                parsed,
                parsed.get("webhook_payload") or msg,
                saved["id"],
                background_tasks,
            )
            _xray_log(
                f"[分流] 圖片訊息已寫入佔位，背景下載中 | partner={chat_partner} "
                f"message_id={saved['id']}"
            )
            return True, None

        if dispatch_route == "incoming_voice":
            schedule_incoming_media_processing(
                parsed,
                parsed.get("webhook_payload") or msg,
                saved["id"],
                background_tasks,
            )
            _xray_log(
                f"[分流] 語音訊息已寫入佔位，背景下載中 | partner={chat_partner} "
                f"message_id={saved['id']}"
            )
            return True, None

        contact_ai_enabled = await get_or_create_contact_ai_enabled(chat_partner)
        global_ai_enabled = _to_bool(AI_ENABLED)

        _xray_log(
            f"[狀態] AI 雙重驗證 | chat_partner={chat_partner} "
            f"speaker={speaker_id} 聯絡人開關={contact_ai_enabled} "
            f"全域開關={global_ai_enabled}"
        )

        if not contact_ai_enabled:
            _xray_log(f"[判斷] 對話對象 {chat_partner} 在一般名單，略過 AI")
            return True, None

        if not global_ai_enabled:
            _xray_log("[判斷] 全域 AI 總開關已關閉，略過 AI")
            return True, None

        if dispatch_route == "ai_text":
            llm_input = llm_content
        else:
            _xray_log(f"[分流] 未知 route={dispatch_route}，略過 AI")
            return True, None

        ai_job = {
            "chat_partner": chat_partner,
            "reply_to": reply_to,
            "llm_input": llm_input,
        }
        _xray_log(
            f"[流程] 雙重驗證通過，已排程背景 AI | chat_partner={chat_partner} "
            f"route={dispatch_route}"
        )
        return True, ai_job

    except Exception:
        logger.exception("[Webhook] handle_incoming_webhook_message 發生未預期錯誤")
        _xray_log("[錯誤] handle_incoming_webhook_message 發生未預期錯誤，請查看 traceback")
        return False, None


async def process_incoming_message(msg: dict) -> bool:
    """相容舊呼叫：即時處理並同步等待 AI（測試用，Webhook 請用 handle_incoming_webhook_message）。"""
    handled, ai_job = await handle_incoming_webhook_message(msg)
    if not handled:
        return False
    if ai_job:
        await process_ai_reply_background(**ai_job)
    return True


async def process_webhook_payload(payload: dict) -> int:
    """從 payload 提取訊息：即時寫入推播，AI 思考丟背景。"""
    messages = _extract_messages(payload)
    _xray_log(f"[Webhook] 提取到 {len(messages)} 則原始訊息")

    if not messages:
        _xray_log("[Webhook] payload 中無可解析訊息")
        return 0

    processed = 0
    for index, msg in enumerate(messages, start=1):
        _xray_log(f"[Webhook] 即時處理第 {index}/{len(messages)} 則訊息")
        try:
            handled, ai_job = await handle_incoming_webhook_message(msg)
            if handled:
                processed += 1
                if ai_job:
                    schedule_ai_reply(ai_job)
            else:
                _xray_log(f"[Webhook] 第 {index} 則訊息未通過解析，略過")
        except Exception:
            logger.exception("[Webhook] 單則訊息處理失敗，繼續下一則 | index=%s", index)
            _xray_log(f"[錯誤] 第 {index} 則訊息處理失敗，已略過並繼續")

    _xray_log(f"[Webhook] 本輪即時處理完成，共 {processed}/{len(messages)} 則")
    return processed


def _message_to_dict(row: Message) -> dict[str, Any]:
    return {
        "id": row.id,
        "wx_id": row.wx_id,
        "content": row.content,
        "is_ai": row.is_ai,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ── 路由 ──────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    active_llm = await get_active_llm_config()
    return {
        "status": "ok",
        "padpro_url": PADPRO_URL,
        "wxid": PADPRO_WXID,
        "local_api_base": active_llm.base_url if active_llm else LOCAL_API_BASE,
        "local_model": active_llm.model_name if active_llm else LOCAL_MODEL_NAME,
        "llm_config_id": active_llm.id if active_llm else None,
        "ai_enabled": AI_ENABLED,
    }


@app.get("/api/status")
async def api_status():
    return {"ai_enabled": AI_ENABLED}


@app.get("/api/wechat/account")
async def api_wechat_account():
    """取得目前主要綁定的微信帳號。"""
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WechatAccount)
                .where(WechatAccount.is_primary.is_(True))
                .limit(1)
            )
            row = result.scalar_one_or_none()
        if row is None:
            return {
                "success": True,
                "account": {
                    "wx_id": PADPRO_WXID,
                    "nickname": None,
                    "webhook_registered": False,
                    "source": "env",
                },
            }
        return {
            "success": True,
            "account": {
                "wx_id": row.wx_id,
                "nickname": row.nickname,
                "webhook_registered": row.webhook_registered,
                "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None,
                "source": "database",
            },
        }
    except Exception:
        logger.exception("讀取微信帳號失敗")
        return JSONResponse(status_code=500, content={"error": "database error"})


@app.post("/api/wechat/logout")
async def api_wechat_logout():
    """登出 PadPro 微信會話，並停用 Webhook（切換帳號前使用）。"""
    try:
        result = await padpro_logout_wechat()
        return {"success": result.get("success", True), **result}
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.detail})
    except httpx.HTTPError as exc:
        logger.exception("PadPro 登出連線失敗")
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": f"PadPro 連線失敗: {exc}"},
        )
    except Exception:
        logger.exception("微信登出失敗")
        return JSONResponse(status_code=500, content={"success": False, "error": "internal error"})


@app.get("/api/wechat/login/qrcode")
async def api_wechat_login_qrcode():
    """向 PadPro 取得 Mac 登入 QR Code（Base64）。"""
    try:
        data = await padpro_get_login_qrcode()
        return {"success": True, **data}
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.detail})
    except httpx.HTTPError as exc:
        logger.exception("PadPro 取得 QR Code 連線失敗")
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": f"PadPro 連線失敗: {exc}"},
        )
    except Exception:
        logger.exception("取得登入 QR Code 失敗")
        return JSONResponse(status_code=500, content={"success": False, "error": "internal error"})


@app.get("/api/wechat/login/status")
async def api_wechat_login_status(uuid: str = Query(..., min_length=1)):
    """輪詢掃碼狀態；登入成功時自動 Newinit + 註冊 Webhook + 寫入資料庫。"""
    try:
        result = await padpro_check_login_status(uuid.strip())
        return {"success": result.get("success", False), **result}
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.detail})
    except httpx.HTTPError as exc:
        logger.exception("PadPro 檢查登入狀態連線失敗 | uuid=%s", uuid)
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": f"PadPro 連線失敗: {exc}"},
        )
    except Exception:
        logger.exception("檢查登入狀態失敗 | uuid=%s", uuid)
        return JSONResponse(status_code=500, content={"success": False, "error": "internal error"})


@app.post("/api/toggle")
async def api_toggle(body: ToggleRequest):
    global AI_ENABLED
    AI_ENABLED = body.enabled
    logger.info("AI 開關已切換為: %s", AI_ENABLED)
    return {"ai_enabled": AI_ENABLED}


@app.get("/api/system_prompt")
async def api_get_system_prompt():
    return {
        "id": ACTIVE_PROMPT_ID,
        "content": CURRENT_SYSTEM_PROMPT,
    }


@app.get("/api/system_prompts")
async def api_list_system_prompts():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(SystemPromptProfile).order_by(
                SystemPromptProfile.is_active.desc(),
                SystemPromptProfile.updated_at.desc(),
                SystemPromptProfile.id.desc(),
            )
        )
        rows = result.scalars().all()

    prompts = [_system_prompt_to_dict(row) for row in rows]
    active_id = next((row.id for row in rows if _to_bool(row.is_active)), None)
    return {
        "prompts": prompts,
        "active_id": active_id,
        "content": CURRENT_SYSTEM_PROMPT,
    }


@app.post("/api/system_prompts")
async def api_create_system_prompt(body: SystemPromptCreateRequest):
    name = body.name.strip()
    content = body.content.strip()
    if not name:
        return JSONResponse(status_code=400, content={"error": "name 不可為空"})
    if not content:
        return JSONResponse(status_code=400, content={"error": "content 不可為空"})

    async with AsyncSessionLocal() as session:
        profile = SystemPromptProfile(name=name, content=content, is_active=False)
        session.add(profile)
        await session.commit()
        await session.refresh(profile)

    logger.info("已新增人設 id=%s name=%s", profile.id, profile.name)
    return {"prompt": _system_prompt_to_dict(profile), "success": True}


@app.put("/api/system_prompts/{prompt_id}")
async def api_update_system_prompt_profile(
    prompt_id: int,
    body: SystemPromptUpdateRequest,
):
    global CURRENT_SYSTEM_PROMPT

    async with AsyncSessionLocal() as session:
        row = await session.get(SystemPromptProfile, prompt_id)
        if row is None:
            raise HTTPException(status_code=404, detail="找不到此人設")

        if body.name is not None:
            name = body.name.strip()
            if not name:
                return JSONResponse(status_code=400, content={"error": "name 不可為空"})
            row.name = name

        if body.content is not None:
            content = body.content.strip()
            if not content:
                return JSONResponse(status_code=400, content={"error": "content 不可為空"})
            row.content = content

        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(row)

        if _to_bool(row.is_active):
            CURRENT_SYSTEM_PROMPT = row.content
            logger.info("已更新啟用人設 id=%s", row.id)

    return {"prompt": _system_prompt_to_dict(row), "success": True}


@app.post("/api/system_prompts/{prompt_id}/activate")
async def api_activate_system_prompt(prompt_id: int):
    row = await activate_system_prompt_profile(prompt_id)
    return {
        "prompt": _system_prompt_to_dict(row),
        "content": CURRENT_SYSTEM_PROMPT,
        "active_id": row.id,
        "success": True,
    }


@app.delete("/api/system_prompts/{prompt_id}")
async def api_delete_system_prompt(prompt_id: int):
    global CURRENT_SYSTEM_PROMPT, ACTIVE_PROMPT_ID

    async with AsyncSessionLocal() as session:
        total = await session.scalar(
            select(func.count()).select_from(SystemPromptProfile)
        )
        if total is not None and total <= 1:
            return JSONResponse(
                status_code=400,
                content={"error": "至少需保留一筆人設"},
            )

        row = await session.get(SystemPromptProfile, prompt_id)
        if row is None:
            raise HTTPException(status_code=404, detail="找不到此人設")

        was_active = _to_bool(row.is_active)
        await session.delete(row)
        await session.commit()

        if was_active:
            replacement = (
                await session.execute(
                    select(SystemPromptProfile).order_by(SystemPromptProfile.id).limit(1)
                )
            ).scalar_one()
            replacement.is_active = True
            replacement.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(replacement)
            CURRENT_SYSTEM_PROMPT = replacement.content
            ACTIVE_PROMPT_ID = replacement.id
            logger.info(
                "已刪除啟用人設，自動切換至 id=%s name=%s",
                replacement.id,
                replacement.name,
            )
            return {
                "success": True,
                "active_id": replacement.id,
                "content": CURRENT_SYSTEM_PROMPT,
            }

    return {"success": True, "active_id": ACTIVE_PROMPT_ID, "content": CURRENT_SYSTEM_PROMPT}


@app.post("/api/system_prompt")
async def api_update_system_prompt(body: SystemPromptRequest):
    """快速更新目前啟用人設的內容（相容舊版前端）。"""
    global CURRENT_SYSTEM_PROMPT

    content = body.content.strip()
    if not content:
        return JSONResponse(
            status_code=400,
            content={"error": "content 不可為空"},
        )

    if ACTIVE_PROMPT_ID is None:
        CURRENT_SYSTEM_PROMPT = content
        logger.info("System Prompt 已更新（無 DB 紀錄），長度=%d 字", len(content))
        return {"content": CURRENT_SYSTEM_PROMPT, "success": True}

    async with AsyncSessionLocal() as session:
        row = await session.get(SystemPromptProfile, ACTIVE_PROMPT_ID)
        if row is None:
            CURRENT_SYSTEM_PROMPT = content
            return {"content": CURRENT_SYSTEM_PROMPT, "success": True}

        row.content = content
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()

    CURRENT_SYSTEM_PROMPT = content
    logger.info("System Prompt 已更新 id=%s，長度=%d 字", ACTIVE_PROMPT_ID, len(content))
    return {
        "id": ACTIVE_PROMPT_ID,
        "content": CURRENT_SYSTEM_PROMPT,
        "success": True,
    }


@app.get("/api/contacts")
async def api_contacts():
    try:
        contacts_subq = (
            select(
                Message.wx_id,
                func.max(Message.created_at).label("last_time"),
            )
            .group_by(Message.wx_id)
            .subquery()
        )

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(
                    contacts_subq.c.wx_id,
                    contacts_subq.c.last_time,
                    func.coalesce(ContactSettings.ai_enabled, False).label("ai_enabled"),
                    ContactSettings.nickname,
                    ContactSettings.memory_summary,
                    ContactSettings.assigned_profile_id,
                    SystemPromptProfile.name.label("profile_name"),
                )
                .outerjoin(
                    ContactSettings,
                    contacts_subq.c.wx_id == ContactSettings.wx_id,
                )
                .outerjoin(
                    SystemPromptProfile,
                    ContactSettings.assigned_profile_id == SystemPromptProfile.id,
                )
                .order_by(contacts_subq.c.last_time.desc())
            )
            rows = result.all()

        return {
            "contacts": [
                {
                    "wx_id": row.wx_id,
                    "nickname": row.nickname,
                    "memory_summary": row.memory_summary,
                    "last_time": row.last_time.isoformat() if row.last_time else None,
                    "ai_enabled": bool(row.ai_enabled),
                    "assigned_profile_id": row.assigned_profile_id,
                    "profile_name": row.profile_name,
                }
                for row in rows
            ],
            "count": len(rows),
        }
    except Exception:
        logger.exception("讀取聯絡人清單失敗")
        return JSONResponse(status_code=500, content={"error": "database error"})


@app.post("/api/contacts/{wx_id}/toggle_ai")
async def toggle_contact_ai(wx_id: str, body: ToggleRequest):
    try:
        enabled = await set_contact_ai_enabled(wx_id, body.enabled)
        logger.info("聯絡人 AI 開關已更新 | wx_id=%s | ai_enabled=%s", wx_id, enabled)
        return {"wx_id": wx_id, "ai_enabled": enabled}
    except Exception:
        logger.exception("更新聯絡人 AI 開關失敗 | wx_id=%s", wx_id)
        return JSONResponse(status_code=500, content={"error": "database error"})


@app.put("/api/contacts/{wx_id}/nickname")
async def update_contact_nickname(wx_id: str, body: ContactNicknameRequest):
    wx_id = wx_id.strip()
    if not wx_id:
        return JSONResponse(status_code=400, content={"error": "wx_id 不可為空"})

    try:
        nickname = await set_contact_nickname(wx_id, body.nickname)
        return {
            "wx_id": wx_id,
            "nickname": nickname,
            "success": True,
        }
    except Exception:
        logger.exception("更新聯絡人暱稱失敗 | wx_id=%s", wx_id)
        return JSONResponse(status_code=500, content={"error": "database error"})


@app.put("/api/contacts/{wx_id}/assigned_profile")
@app.post("/api/contacts/{wx_id}/assigned_profile")
async def update_contact_assigned_profile(wx_id: str, body: ContactAssignedProfileRequest):
    wx_id = wx_id.strip()
    if not wx_id:
        return JSONResponse(status_code=400, content={"error": "wx_id 不可為空"})

    try:
        profile_id, profile_name = await set_contact_assigned_profile(
            wx_id,
            body.profile_id,
        )
        return {
            "wx_id": wx_id,
            "assigned_profile_id": profile_id,
            "profile_name": profile_name,
            "success": True,
        }
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
    except Exception:
        logger.exception("更新聯絡人綁定人設失敗 | wx_id=%s", wx_id)
        return JSONResponse(status_code=500, content={"error": "database error"})


@app.put("/api/contacts/{wx_id}/memory")
async def update_contact_memory(wx_id: str, body: ContactMemoryRequest):
    wx_id = wx_id.strip()
    if not wx_id:
        return JSONResponse(status_code=400, content={"error": "wx_id 不可為空"})

    try:
        memory_summary = await set_contact_memory_summary(wx_id, body.memory_summary)
        return {
            "wx_id": wx_id,
            "memory_summary": memory_summary,
            "success": True,
        }
    except Exception:
        logger.exception("更新聯絡人長期記憶失敗 | wx_id=%s", wx_id)
        return JSONResponse(status_code=500, content={"error": "database error"})


@app.get("/api/messages")
async def api_messages(
    limit: int = Query(default=50, ge=1, le=500),
    wx_id: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
):
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(Message).order_by(Message.created_at.desc()).limit(limit)
            if wx_id:
                stmt = stmt.where(Message.wx_id == wx_id)
            if keyword and keyword.strip():
                stmt = stmt.where(Message.content.like(f"%{keyword.strip()}%"))
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            rows.reverse()
        return {
            "messages": [_message_to_dict(row) for row in rows],
            "count": len(rows),
            "keyword": keyword.strip() if keyword else None,
        }
    except Exception:
        logger.exception("讀取對話紀錄失敗")
        return JSONResponse(status_code=500, content={"error": "database error"})


@app.get("/api/test_llm")
async def api_test_llm():
    try:
        active_llm = await get_active_llm_config()
        response = await create_active_llm_chat_completion(
            _build_llm_messages(
                [],
                "這是一條系統測試訊息，請簡短回覆『連線正常』。",
            ),
        )
        message = (response.choices[0].message.content or "").strip()
        if not message:
            raise ValueError("模型回傳空內容")
        return {
            "success": True,
            "message": message,
            "config": _llm_config_to_dict(active_llm) if active_llm else None,
        }
    except Exception as e:
        logger.exception("LLM 連線測試失敗")
        return {"success": False, "error": str(e)}


@app.get("/api/llm-configs")
async def api_list_llm_configs():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(LlmConfig).order_by(
                LlmConfig.is_active.desc(),
                LlmConfig.updated_at.desc(),
                LlmConfig.id.desc(),
            )
        )
        rows = result.scalars().all()

    configs = [_llm_config_to_dict(row) for row in rows]
    active_id = next((row.id for row in rows if row.is_active), None)
    return {"configs": configs, "active_id": active_id}


@app.post("/api/llm-configs")
async def api_create_llm_config(body: LlmConfigCreateRequest):
    name = body.name.strip()
    provider = body.provider.strip() or "Custom"
    base_url = body.base_url.strip()
    model_name = body.model_name.strip()
    if not name:
        return JSONResponse(status_code=400, content={"error": "name 不可為空"})
    if not base_url:
        return JSONResponse(status_code=400, content={"error": "base_url 不可為空"})
    if not model_name:
        return JSONResponse(status_code=400, content={"error": "model_name 不可為空"})

    temperature = body.temperature
    if temperature < 0 or temperature > 2:
        return JSONResponse(
            status_code=400,
            content={"error": "temperature 需介於 0 與 2 之間"},
        )

    api_key = body.api_key.strip() if body.api_key else None

    async with AsyncSessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(LlmConfig))
        row = LlmConfig(
            name=name,
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            temperature=temperature,
            is_active=not count,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

    logger.info("已新增 LLM 設定 id=%s name=%s model=%s", row.id, row.name, row.model_name)
    return {"config": _llm_config_to_dict(row), "success": True}


@app.put("/api/llm-configs/{config_id}/activate")
async def api_activate_llm_config(config_id: int):
    try:
        row = await activate_llm_config(config_id)
        return {"success": True, "config": _llm_config_to_dict(row)}
    except HTTPException:
        raise
    except Exception:
        logger.exception("啟用 LLM 設定失敗 | id=%s", config_id)
        return JSONResponse(status_code=500, content={"error": "database error"})


@app.delete("/api/llm-configs/{config_id}")
async def api_delete_llm_config(config_id: int):
    async with AsyncSessionLocal() as session:
        row = await session.get(LlmConfig, config_id)
        if row is None:
            return JSONResponse(status_code=404, content={"error": "找不到此 LLM 設定"})
        if row.is_active:
            return JSONResponse(
                status_code=400,
                content={"error": "無法刪除使用中的設定，請先啟用其他模型"},
            )
        await session.delete(row)
        await session.commit()

    logger.info("已刪除 LLM 設定 id=%s", config_id)
    return {"success": True}


@app.post("/api/send_message")
async def api_send_message(body: SendMessageRequest):
    wx_id = body.wx_id.strip()
    content = (body.content or "").strip()
    photo_id = body.photo_id

    if not wx_id:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "wx_id 不可為空"},
        )
    if not content and photo_id is None:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "content 與 photo_id 至少需填一項"},
        )

    try:
        saved_messages: list[dict[str, Any]] = []

        if content:
            send_wechat_message(wx_id, content)
            text_saved = await save_message(wx_id, f"[網頁回覆] {content}", is_ai=True)
            if text_saved:
                saved_messages.append(text_saved)

        if photo_id is not None:
            photo_row, _ = await send_photo_to_contact(wx_id, photo_id)
            photo_saved = await save_message(
                wx_id,
                format_image_message_content(
                    f"/api/photos/{photo_row['id']}/file",
                    prefix="[網頁回覆] ",
                ),
                is_ai=True,
            )
            if photo_saved:
                saved_messages.append(photo_saved)

        if not saved_messages:
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "訊息已發送但寫入資料庫失敗"},
            )

        logger.info(
            "網頁手動發送成功 | wx_id=%s text=%s photo_id=%s",
            wx_id,
            bool(content),
            photo_id,
        )
        return {
            "success": True,
            "message": saved_messages[-1],
            "messages": saved_messages,
        }
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "error": exc.detail},
        )
    except requests.RequestException as e:
        logger.exception("手動發送訊息失敗 | wx_id=%s", wx_id)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )
    except Exception as e:
        logger.exception("手動發送訊息發生未預期錯誤 | wx_id=%s", wx_id)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.post("/api/send_message/paste")
async def api_send_message_with_paste(
    wx_id: str = Form(...),
    content: str = Form(""),
    file: UploadFile = File(...),
):
    """手動發送：支援從剪貼簿貼上的截圖（不寫入圖片庫）。"""
    wx_id = wx_id.strip()
    text = content.strip()

    if not wx_id:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "wx_id 不可為空"},
        )

    content_type = (file.content_type or "").lower()
    if content_type and not content_type.startswith("image/"):
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "僅支援貼上圖片"},
        )

    file_bytes = await file.read()
    if not file_bytes:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "貼上的圖片為空"},
        )

    try:
        saved_messages: list[dict[str, Any]] = []

        if text:
            send_wechat_message(wx_id, text)
            text_saved = await save_message(wx_id, f"[網頁回覆] {text}", is_ai=True)
            if text_saved:
                saved_messages.append(text_saved)

        send_wechat_image_bytes(
            wx_id,
            file_bytes,
            source=file.filename or "clipboard_paste",
        )
        ext = os.path.splitext(file.filename or ".png")[1] or ".png"
        token = persist_ephemeral_image_bytes(file_bytes, ext)
        image_saved = await save_message(
            wx_id,
            format_image_message_content(
                f"/api/ephemeral_images/{token}/file",
                prefix="[網頁回覆] ",
            ),
            is_ai=True,
        )
        if image_saved:
            saved_messages.append(image_saved)

        if not saved_messages:
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "訊息已發送但寫入資料庫失敗"},
            )

        logger.info(
            "網頁貼圖發送成功 | wx_id=%s text=%s bytes=%d",
            wx_id,
            bool(text),
            len(file_bytes),
        )
        return {
            "success": True,
            "message": saved_messages[-1],
            "messages": saved_messages,
        }
    except requests.RequestException as e:
        logger.exception("貼圖發送失敗 | wx_id=%s", wx_id)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )
    except Exception as e:
        logger.exception("貼圖發送發生未預期錯誤 | wx_id=%s", wx_id)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.post("/api/send_image")
async def api_send_image(body: SendImageRequest):
    wx_id = body.wx_id.strip()
    if not wx_id:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "wx_id 不可為空"},
        )

    try:
        photo_row, _ = await send_photo_to_contact(wx_id, body.photo_id)
        saved = await save_message(
            wx_id,
            format_image_message_content(f"/api/photos/{photo_row['id']}/file"),
            is_ai=True,
        )
        if not saved:
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "圖片已發送但寫入資料庫失敗"},
            )
        return {"success": True, "message": saved, "photo": photo_row}
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "error": exc.detail},
        )
    except requests.RequestException as e:
        logger.exception("手動發送圖片失敗 | wx_id=%s", wx_id)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.get("/api/photos")
async def api_list_photos():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PhotoAsset).order_by(PhotoAsset.id.desc())
        )
        rows = result.scalars().all()
        profile_ids = {row.profile_id for row in rows if row.profile_id is not None}
        profile_names: dict[int, str] = {}
        if profile_ids:
            profile_result = await session.execute(
                select(SystemPromptProfile.id, SystemPromptProfile.name).where(
                    SystemPromptProfile.id.in_(profile_ids)
                )
            )
            profile_names = {
                profile_id: name for profile_id, name in profile_result.all()
            }
    return {
        "photos": [
            _photo_asset_to_dict(row, profile_names.get(row.profile_id))
            for row in rows
        ],
        "count": len(rows),
    }


@app.post("/api/photos")
async def api_upload_photo(
    file: UploadFile = File(...),
    name: str = Form(...),
    keywords: str = Form(...),
    prompt_hint: str = Form(""),
    profile_id: int | None = Form(None),
):
    clean_name = name.strip()
    clean_keywords = keywords.strip()
    if not clean_name:
        return JSONResponse(status_code=400, content={"error": "name 不可為空"})
    if not clean_keywords:
        return JSONResponse(status_code=400, content={"error": "keywords 不可為空"})
    if profile_id is None:
        return JSONResponse(status_code=400, content={"error": "profile_id 不可為空"})
    if not file.filename or not _is_allowed_image_filename(file.filename):
        return JSONResponse(
            status_code=400,
            content={"error": f"僅支援圖片格式: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}"},
        )

    saved_path = _build_saved_photo_path(file.filename)
    file_bytes = await file.read()
    if not file_bytes:
        return JSONResponse(status_code=400, content={"error": "上傳檔案為空"})

    with open(saved_path, "wb") as output_file:
        output_file.write(file_bytes)

    async with AsyncSessionLocal() as session:
        profile_row = await session.get(SystemPromptProfile, profile_id)
        if profile_row is None:
            return JSONResponse(status_code=404, content={"error": "找不到此人設"})

        row = PhotoAsset(
            name=clean_name,
            keywords=clean_keywords,
            prompt_hint=prompt_hint.strip(),
            file_path=saved_path,
            original_filename=file.filename,
            profile_id=profile_id,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        profile_name = profile_row.name

    logger.info(
        "已上傳照片 id=%s name=%s profile_id=%s path=%s",
        row.id,
        row.name,
        profile_id,
        saved_path,
    )
    return {
        "success": True,
        "photo": _photo_asset_to_dict(row, profile_name),
    }


@app.put("/api/photos/{photo_id}")
async def api_update_photo(photo_id: int, body: PhotoUpdateRequest):
    async with AsyncSessionLocal() as session:
        row = await session.get(PhotoAsset, photo_id)
        if row is None:
            raise HTTPException(status_code=404, detail="找不到此照片")

        if body.name is not None:
            clean_name = body.name.strip()
            if not clean_name:
                return JSONResponse(status_code=400, content={"error": "name 不可為空"})
            row.name = clean_name

        if body.keywords is not None:
            clean_keywords = body.keywords.strip()
            if not clean_keywords:
                return JSONResponse(status_code=400, content={"error": "keywords 不可為空"})
            row.keywords = clean_keywords

        if body.prompt_hint is not None:
            row.prompt_hint = body.prompt_hint.strip()

        if body.profile_id is not None:
            profile_row = await session.get(SystemPromptProfile, body.profile_id)
            if profile_row is None:
                return JSONResponse(status_code=404, content={"error": "找不到此人設"})
            row.profile_id = body.profile_id

        await session.commit()
        await session.refresh(row)
        profile_name = None
        if row.profile_id is not None:
            profile_row = await session.get(SystemPromptProfile, row.profile_id)
            profile_name = profile_row.name if profile_row else None

    return {"success": True, "photo": _photo_asset_to_dict(row, profile_name)}


@app.delete("/api/photos/{photo_id}")
async def api_delete_photo(photo_id: int):
    async with AsyncSessionLocal() as session:
        row = await session.get(PhotoAsset, photo_id)
        if row is None:
            raise HTTPException(status_code=404, detail="找不到此照片")

        file_path = row.file_path
        await session.delete(row)
        await session.commit()

    if file_path and os.path.isfile(file_path):
        try:
            os.remove(file_path)
        except OSError:
            logger.exception("刪除照片檔案失敗 | path=%s", file_path)

    return {"success": True}


@app.get("/api/photos/{photo_id}/file")
async def api_get_photo_file(photo_id: int):
    async with AsyncSessionLocal() as session:
        row = await session.get(PhotoAsset, photo_id)
        if row is None:
            raise HTTPException(status_code=404, detail="找不到此照片")
        if not os.path.isfile(row.file_path):
            raise HTTPException(status_code=404, detail="照片檔案不存在")

    return FileResponse(row.file_path, filename=row.original_filename or os.path.basename(row.file_path))


@app.get("/api/ephemeral_images/{token}/file")
async def api_get_ephemeral_image_file(token: str):
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        raise HTTPException(status_code=400, detail="無效的圖片 token")

    matched_path = None
    for ext in ALLOWED_IMAGE_EXTENSIONS:
        candidate = os.path.join(EPHEMERAL_IMAGES_DIR, f"{token}{ext}")
        if os.path.isfile(candidate):
            matched_path = candidate
            break

    if matched_path is None:
        raise HTTPException(status_code=404, detail="找不到此圖片")

    return FileResponse(matched_path, filename=os.path.basename(matched_path))


@app.get("/api/media/received/{token}/file")
async def api_get_received_media_file(token: str):
    """讀取客人傳入並已下載的圖片或語音檔案。"""
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        raise HTTPException(status_code=400, detail="無效的媒體 token")

    matched_path = None
    for ext in ALLOWED_RECEIVED_MEDIA_EXTENSIONS:
        for base_dir in (RECEIVED_IMAGES_DIR, RECEIVED_VOICES_DIR):
            candidate = os.path.join(base_dir, f"{token}{ext}")
            if os.path.isfile(candidate):
                matched_path = candidate
                break
        if matched_path:
            break

    if matched_path is None:
        raise HTTPException(status_code=404, detail="找不到此媒體檔案")

    return FileResponse(matched_path, filename=os.path.basename(matched_path))


@app.post("/webhook")
async def webhook_post(request: Request, background_tasks: BackgroundTasks):
    """接收 WeChatPadProMAX HTTP Webhook：即時寫入推播，AI 思考丟背景。"""
    _xray_log("[Webhook] 收到 HTTP POST /webhook 請求")

    try:
        payload = await request.json()
    except Exception:
        logger.exception("Webhook 收到無效 JSON")
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid json"})

    messages = _extract_messages(payload)
    _xray_log(f"[Webhook] HTTP payload 提取到 {len(messages)} 則訊息")

    if not messages:
        _xray_log("[Webhook] HTTP 無可解析訊息")
        return {"status": "ok", "processed": 0}

    processed = 0
    for index, msg in enumerate(messages, start=1):
        _xray_log(f"[Webhook] HTTP 即時處理第 {index} 則訊息")
        try:
            handled, ai_job = await handle_incoming_webhook_message(msg, background_tasks)
            if handled:
                processed += 1
                if ai_job:
                    schedule_ai_reply(ai_job, background_tasks)
            else:
                _xray_log(f"[Webhook] HTTP 第 {index} 則訊息未通過解析，略過")
        except Exception:
            logger.exception("[Webhook] HTTP 單則訊息處理失敗 | index=%s", index)
            _xray_log(f"[錯誤] HTTP 第 {index} 則訊息處理失敗，已略過並繼續")

    _xray_log(f"[Webhook] HTTP 即時處理完成 {processed} 則，AI 任務已排程背景")
    return {"status": "ok", "processed": processed}


@app.websocket("/ws/frontend")
async def frontend_ws(websocket: WebSocket):
    """前端即時訊息推播 WebSocket。"""
    await websocket.accept()
    active_frontend_connections.append(websocket)
    logger.info(
        "前端 WebSocket 已連線，目前 %d 個連線",
        len(active_frontend_connections),
    )

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info("前端 WebSocket 客戶端已斷線")
    finally:
        if websocket in active_frontend_connections:
            active_frontend_connections.remove(websocket)
        logger.info(
            "前端 WebSocket 連線已移除，剩餘 %d 個",
            len(active_frontend_connections),
        )


@app.websocket("/webhook")
async def webhook_ws(websocket: WebSocket):
    """接收 WeChatPadProMAX WebSocket 推送。"""
    await websocket.accept()
    _xray_log("[Webhook] WebSocket /webhook 客戶端已連線")

    try:
        while True:
            try:
                data = await websocket.receive_json()
                _xray_log(f"[Webhook] 收到 WS 訊息: {data}")

                processed = await process_webhook_payload(data)
                _xray_log(f"[Webhook] WS 本輪處理完成 processed={processed}")
            except Exception:
                logger.exception("WebSocket 單則訊息處理失敗，繼續監聽")
                _xray_log("[錯誤] WebSocket 單則訊息處理失敗，繼續監聽")
    except WebSocketDisconnect:
        _xray_log("[Webhook] WebSocket 客戶端已斷線")
