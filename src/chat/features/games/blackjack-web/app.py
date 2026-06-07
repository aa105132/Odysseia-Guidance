import os
import httpx
import logging
import asyncio
import discord
from typing import List, Optional, Dict, Any, Tuple
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from dotenv import load_dotenv

# --- 灵石服务 ---
from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.features.games.config import blackjack_config
from src.chat.features.games.services.blackjack_service import blackjack_service
from src.chat.utils.database import chat_db_manager
from src.dashboard.service_registry import service_registry
from multiplayer_service import multiplayer_blackjack_service

def _strip_wrapping_quotes(value: Optional[str]) -> str:
    """去除环境变量值外层的一对引号，兼容被错误写成 '"xxx"' 的情况。"""
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) >= 2 and (
        (text[0] == '"' and text[-1] == '"')
        or (text[0] == "'" and text[-1] == "'")
    ):
        return text[1:-1].strip()
    return text


def _load_project_env() -> None:
    """尽可能加载项目根目录 .env，并记录实际检查路径。"""
    candidate_paths = [
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", ".env")
        ),
        os.path.abspath(os.path.join(os.getcwd(), ".env")),
    ]

    for dotenv_path in candidate_paths:
        if os.path.isfile(dotenv_path):
            loaded = load_dotenv(dotenv_path=dotenv_path, override=False)
            logging.getLogger(__name__).info(
                "Loaded .env file from: %s (loaded=%s)", dotenv_path, loaded
            )
            return

    logging.getLogger(__name__).warning(
        "No .env file found for blackjack-web. Checked paths: %s", candidate_paths
    )


_load_project_env()


def _resolve_discord_client_id() -> str:
    """统一解析 Discord Client ID，兼容历史变量名。"""
    return _strip_wrapping_quotes(
        os.getenv("DISCORD_CLIENT_ID") or os.getenv("VITE_DISCORD_CLIENT_ID")
    )


def _resolve_discord_bot_token() -> str:
    """解析 Bot Token，兼容历史变量名。"""
    return _strip_wrapping_quotes(
        os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN")
    )


def _build_activity_launch_url(
    discord_client_id: str, channel_id: int, guild_id: Optional[int]
) -> str:
    guild_path = str(guild_id) if guild_id is not None else "@me"
    return (
        f"https://discord.com/channels/{guild_path}/{channel_id}"
        f"?launch_activity={discord_client_id}"
    )


app = FastAPI()
log = logging.getLogger(__name__)

# --- 用户操作锁，防止竞态条件 ---
from cachetools import TTLCache


class LockCache(TTLCache):
    """一个在键缺失时创建 asyncio.Lock 的 TTLCache。"""

    def __missing__(self, key):
        lock = asyncio.Lock()
        self[key] = lock
        return lock


# 创建一个TTL缓存来存储用户锁，TTL设置为30分钟（1800秒）
# 减少TTL时间以防止锁对象积累，maxsize设置为100以限制内存使用
user_locks = LockCache(maxsize=100, ttl=1800)
room_locks = LockCache(maxsize=200, ttl=1800)

# Discord 活动会话(session_key) 与游戏房间(room_id)绑定（内存态，带TTL）
activity_room_bindings = TTLCache(maxsize=1000, ttl=21600)
room_activity_bindings = TTLCache(maxsize=1000, ttl=21600)


async def _record_game_result(bet_amount: int, payout_amount: int):
    """
    计算AI的净盈利并记录到数据库。
    - 如果玩家赢钱，AI的盈利为负。
    - 如果玩家输钱，AI的盈利为正。
    """
    try:
        net_win_loss = bet_amount - payout_amount
        await chat_db_manager.update_blackjack_net_win_loss(net_win_loss)
        log.info(f"已记录21点游戏结果到日报统计：AI净盈利 {net_win_loss}")
    except Exception as e:
        log.error(f"记录21点游戏结果到日报统计时出错: {e}", exc_info=True)


# --- 应用生命周期事件 ---
@app.on_event("startup")
async def startup_event():
    """在应用启动时初始化数据库表"""
    # --- 配置日志记录 ---
    # Uvicorn 默认的日志级别可能高于 INFO，导致我们自己的日志无法显示。
    # 在这里明确设置，以确保所有级别的日志都能在调试时看到。
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s"
    )

    log.info("Application startup: Initializing services...")

    # --- 新增：在初始化任何服务之前，首先连接数据库 ---
    from src.chat.utils.database import chat_db_manager

    await chat_db_manager.init_async()
    log.info("Database initialized.")

    await blackjack_service.initialize()
    log.info("Blackjack service initialized.")


@app.on_event("shutdown")
async def shutdown_event():
    """在应用关闭时断开数据库连接"""
    from src.chat.utils.database import chat_db_manager

    log.info("Application shutting down.")


# --- 中间件：添加详细的请求日志 ---
@app.middleware("http")
async def log_requests(request: Request, call_next):
    log.info(f"收到请求: {request.method} {request.url.path}")
    try:
        response = await call_next(request)
        log.info(
            f"请求完成: {request.method} {request.url.path} - 状态码: {response.status_code}"
        )
        return response
    except Exception as e:
        log.error(
            f"请求处理出错: {request.method} {request.url.path} - 错误: {e}",
            exc_info=True,
        )
        # 重新抛出异常，以便FastAPI的默认异常处理可以捕获它
        raise


# --- 安全性和依赖 ---
# auto_error=False 允许多选的认证，这样在没有token时就不会自动触发403错误
auth_scheme = HTTPBearer(auto_error=False)

# 本地开发时使用的固定测试用户ID
TEST_USER_ID = 999999999999999999


def _build_discord_avatar_url(user_data: Dict[str, Any]) -> str:
    user_id = str(user_data.get("id", "")).strip()
    avatar_hash = str(user_data.get("avatar", "") or "").strip()
    if user_id and avatar_hash:
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png?size=128"

    default_index = 0
    if user_id.isdigit():
        default_index = (int(user_id) >> 22) % 6
    return f"https://cdn.discordapp.com/embed/avatars/{default_index}.png"


async def get_current_user_id(
    token: Optional[HTTPAuthorizationCredentials] = Depends(auth_scheme),
) -> int:
    """
    依赖项：从Bearer Token中获取用户信息并返回用户ID。
    在本地开发中，如果没有提供token，则返回一个固定的测试用户ID。
    """
    # 如果没有token（例如在本地开发环境中），返回测试用户ID
    if token is None:
        log.warning(f"未找到认证Token。回退到测试用户ID: {TEST_USER_ID}")
        return TEST_USER_ID

    # 如果有token，则执行原有的Discord API验证流程
    headers = {"Authorization": f"Bearer {token.credentials}"}
    log.info("正在从Discord API获取用户信息...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                "https://discord.com/api/users/@me", headers=headers
            )
            response.raise_for_status()
            user_data = response.json()
            user_id = int(user_data["id"])
            log.info(f"成功识别用户: {user_data['username']} ({user_id})")
            return user_id
        except httpx.HTTPStatusError as e:
            log.error(
                f"从Discord API获取用户信息失败。状态码: {e.response.status_code}，"
                f"响应: {e.response.text}",
                exc_info=True,
            )
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        except httpx.RequestError as e:
            log.error(f"请求Discord API时发生网络错误: {e}", exc_info=True)
            raise HTTPException(
                status_code=503,
                detail="Service Unavailable: Cannot connect to Discord API",
            )


async def get_current_user_profile(
    request: Request,
    token: Optional[HTTPAuthorizationCredentials] = Depends(auth_scheme),
) -> Dict[str, Any]:
    """
    获取当前用户完整资料（ID、昵称、头像）。
    - Discord嵌入模式：使用 Bearer Token 调 Discord /users/@me
    - 本地开发模式：支持 X-Dev-User-Id / X-Dev-Username / X-Dev-Avatar-Url
    """
    if token is None:
        raw_dev_user_id = _strip_wrapping_quotes(request.headers.get("X-Dev-User-Id"))
        raw_dev_username = _strip_wrapping_quotes(request.headers.get("X-Dev-Username"))
        raw_dev_avatar = _strip_wrapping_quotes(request.headers.get("X-Dev-Avatar-Url"))

        if raw_dev_user_id:
            try:
                user_id = int(raw_dev_user_id)
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="X-Dev-User-Id 必须是整数"
                )
        else:
            user_id = TEST_USER_ID

        username = raw_dev_username or f"测试玩家{str(user_id)[-4:]}"
        avatar_url = raw_dev_avatar or "/character/normal.webp"
        return {
            "user_id": user_id,
            "username": username,
            "avatar_url": avatar_url,
            "is_dev": True,
        }

    headers = {"Authorization": f"Bearer {token.credentials}"}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                "https://discord.com/api/users/@me", headers=headers
            )
            response.raise_for_status()
            user_data = response.json()
            user_id = int(user_data["id"])
            username = user_data.get("global_name") or user_data.get("username") or str(
                user_id
            )
            avatar_url = _build_discord_avatar_url(user_data)

            return {
                "user_id": user_id,
                "username": username,
                "avatar_url": avatar_url,
                "is_dev": False,
            }
        except httpx.HTTPStatusError as e:
            log.error(
                f"从Discord API获取用户资料失败。状态码: {e.response.status_code}，"
                f"响应: {e.response.text}",
                exc_info=True,
            )
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        except httpx.RequestError as e:
            log.error(f"请求Discord API时发生网络错误: {e}", exc_info=True)
            raise HTTPException(
                status_code=503,
                detail="Service Unavailable: Cannot connect to Discord API",
            )


class TokenRequest(BaseModel):
    code: str


class BetRequest(BaseModel):
    amount: int


class RoomRequest(BaseModel):
    room_id: str


class MultiplayerBetRequest(BaseModel):
    room_id: str
    amount: int


class MultiplayerReadyRequest(BaseModel):
    room_id: str
    ready: bool


class AutoJoinRoomRequest(BaseModel):
    session_key: str


class RecruitRoomRequest(BaseModel):
    room_id: str
    session_key: Optional[str] = None
    channel_id: Optional[str] = None
    guild_id: Optional[str] = None


@app.post("/api/token")
async def exchange_code_for_token(request: TokenRequest):
    """API: 用Discord返回的code换取access_token"""
    log.info(f"收到令牌交换请求，代码: '{request.code[:10]}...'")
    code = _strip_wrapping_quotes(request.code)
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    client_id = _resolve_discord_client_id()
    client_secret = _strip_wrapping_quotes(os.getenv("DISCORD_CLIENT_SECRET"))
    redirect_uri = _strip_wrapping_quotes(
        os.getenv("DISCORD_REDIRECT_URI") or os.getenv("DISCORD_OAUTH_REDIRECT_URI")
    )

    if not client_id or not client_secret:
        log.error(
            "服务器缺少 DISCORD_CLIENT_ID/VITE_DISCORD_CLIENT_ID 或 DISCORD_CLIENT_SECRET"
        )
        raise HTTPException(
            status_code=500, detail="Server is missing Discord credentials"
        )

    log.info(
        "OAuth配置检查: client_id_prefix=%s, client_secret_len=%s, redirect_uri=%s",
        (client_id[:8] + "...") if len(client_id) > 8 else client_id,
        len(client_secret),
        redirect_uri or "<empty>",
    )

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
    }
    if redirect_uri:
        data["redirect_uri"] = redirect_uri
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    log.info("正在向Discord API发送令牌交换请求...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://discord.com/api/oauth2/token", data=data, headers=headers
            )
            response.raise_for_status()
            log.info("成功交换代码获取令牌。")
            return JSONResponse(content=response.json())
        except httpx.HTTPStatusError as e:
            log.error(
                f"与Discord API交换代码失败。状态码: {e.response.status_code}，"
                f"响应: {e.response.text}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail="Failed to exchange code with Discord"
            )
        except httpx.RequestError as e:
            log.error(f"请求Discord API时发生网络错误: {e}", exc_info=True)
            raise HTTPException(
                status_code=503,
                detail="Service Unavailable: Cannot connect to Discord API",
            )


@app.get("/api/config")
async def get_public_config():
    """
    API: 返回前端初始化所需的公开配置（不包含敏感信息）。
    """
    client_id = _resolve_discord_client_id()
    if not client_id:
        log.error("服务器缺少 DISCORD_CLIENT_ID/VITE_DISCORD_CLIENT_ID")
        raise HTTPException(status_code=500, detail="Server is missing Discord client id")

    return JSONResponse(content={"discord_client_id": client_id})


@app.get("/api/user")
async def get_user_info(user_id: int = Depends(get_current_user_id)):
    """
    API: 获取当前用户信息，包括灵石余额。
    """
    log.info(f"正在获取用户 {user_id} 的余额")
    try:
        # --- 新增：在加载游戏时，自动清理该用户任何卡住的旧游戏 ---
        balance = await coin_service.get_balance(user_id)

        # --- 本地开发专属：为测试用户自动创建账户并补充余额 ---
        if user_id == TEST_USER_ID and (balance is None or balance < 5000):
            amount_to_add = 10000 - (balance or 0)
            log.warning(
                f"测试用户 {user_id} 余额不足或不存在。正在补充 {amount_to_add} 硬币至10000。"
            )
            balance = await coin_service.add_coins(
                user_id, amount_to_add, "本地开发自动补充"
            )

        # --- 安全检查和日志记录 ---
        # 如果用户的余额记录因某种原因（例如数据异常）为空，这是一个严重问题
        if balance is None:
            log.critical(
                f"CRITICAL: 用户 {user_id} 的余额查询结果为 None，这表示数据库中可能存在数据损坏或异常。请立即检查 user_coins 表。"
            )
            # 返回一个明确的错误，而不是一个可能引起误解的 0
            raise HTTPException(
                status_code=500,
                detail="无法加载您的余额，您的账户数据可能存在异常。请联系管理员进行检查。",
            )

        log.info(f"用户 {user_id} 的余额为 {balance}")

        # --- 从配置文件获取荷官阈值 ---
        dealer_thresholds = blackjack_config.DEALER_BET_THRESHOLDS

        return JSONResponse(
            content={
                "user_id": str(user_id),
                "balance": balance,
                "dealer_thresholds": dealer_thresholds,
            }
        )
    except Exception:
        log.error(f"获取用户 {user_id} 余额失败。", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get user balance")


def _normalize_room_id(room_id: str) -> str:
    normalized = str(room_id or "").strip().upper()
    if not normalized:
        raise HTTPException(status_code=400, detail="room_id 不能为空")
    return normalized


def _room_lock_key(room_id: str) -> str:
    return f"multi:{room_id}"


def _session_lock_key(session_key: str) -> str:
    return f"multi:session:{session_key}"


def _normalize_session_key(session_key: str) -> str:
    normalized = str(session_key or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="session_key 不能为空")
    if len(normalized) > 200:
        normalized = normalized[:200]
    return normalized


def _parse_int_like_id(value: Optional[str]) -> Optional[int]:
    text = str(value or "").strip()
    if not text or not text.isdigit():
        return None
    return int(text)


def _extract_channel_context_from_session_key(
    session_key: Optional[str],
) -> Tuple[Optional[int], Optional[int]]:
    raw = str(session_key or "").strip()
    if not raw.startswith("channel:"):
        return None, None

    parts = raw.split(":", 2)
    if len(parts) != 3:
        return None, None

    guild_raw = parts[1]
    channel_raw = parts[2]
    guild_id = int(guild_raw) if guild_raw.isdigit() else None
    channel_id = int(channel_raw) if channel_raw.isdigit() else None
    return guild_id, channel_id


def _build_channel_session_key(guild_id: Optional[int], channel_id: int) -> str:
    guild_part = str(guild_id) if guild_id is not None else "dm"
    return f"channel:{guild_part}:{channel_id}"


async def _run_coro_in_bot_loop(
    bot: discord.Client, coroutine: Any, timeout: float = 15.0
) -> Any:
    bot_loop = getattr(bot, "loop", None)
    if bot_loop is None or bot_loop.is_closed():
        raise RuntimeError("Discord Bot 事件循环不可用")

    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop is bot_loop:
        return await coroutine

    future = asyncio.run_coroutine_threadsafe(coroutine, bot_loop)
    wrapped_future = asyncio.wrap_future(future)
    try:
        return await asyncio.wait_for(wrapped_future, timeout=timeout)
    except asyncio.TimeoutError as exc:
        future.cancel()
        raise RuntimeError("等待 Discord Bot 响应超时，请稍后重试") from exc


async def _send_recruit_message_via_bot(
    bot: discord.Client,
    *,
    room_id: str,
    user_id: int,
    username: str,
    discord_client_id: str,
    channel_id: int,
    guild_id: Optional[int],
    bot_token: str,
) -> Dict[str, str]:
    async def _task() -> Dict[str, str]:
        channel = bot.get_channel(channel_id)
        if channel is None:
            channel = await bot.fetch_channel(channel_id)

        if not isinstance(channel, discord.abc.Messageable):
            raise ValueError("目标频道不支持发送消息")

        resolved_guild_id = getattr(getattr(channel, "guild", None), "id", None)
        effective_guild_id = resolved_guild_id if resolved_guild_id is not None else guild_id

        launch_url: Optional[str] = None
        create_invite = getattr(channel, "create_invite", None)
        if callable(create_invite):
            try:
                invite = await create_invite(
                    max_age=3600,
                    max_uses=0,
                    unique=True,
                    target_type=discord.InviteTarget.embedded_application,
                    target_application=discord.Object(id=int(discord_client_id)),
                    reason=f"blackjack recruit room={room_id} host={user_id}",
                )
                launch_url = invite.url
            except Exception as e:
                log.warning("discord.py 创建活动邀请失败，将尝试 HTTP API 兜底: %s", e)

        if not launch_url and bot_token:
            launch_url = await _create_activity_invite_via_http(
                channel_id=channel_id,
                discord_client_id=discord_client_id,
                bot_token=bot_token,
                room_id=room_id,
                user_id=user_id,
            )

        if not launch_url:
            raise RuntimeError("无法创建活动邀请链接，请检查机器人是否具备创建邀请与活动权限")

        recruit_view = discord.ui.View(timeout=3600)
        recruit_view.add_item(
            discord.ui.Button(
                label=f"启动活动并加入房间 {room_id}",
                style=discord.ButtonStyle.link,
                url=launch_url,
            )
        )

        recruit_text = (
            f"<@{user_id}> 正在招募队友参与多人 21 点对战。\n"
            f"房间号：`{room_id}`\n"
            f"发起人：{username}\n"
            "点击下方按钮启动活动后可自动进入该房间。"
        )

        message = await channel.send(recruit_text, view=recruit_view)
        return {
            "invite_url": launch_url,
            "message_id": str(message.id),
            "channel_id": str(channel_id),
            "guild_id": str(effective_guild_id) if effective_guild_id is not None else "dm",
        }

    return await _run_coro_in_bot_loop(bot, _task())


async def _create_activity_invite_via_http(
    *,
    channel_id: int,
    discord_client_id: str,
    bot_token: str,
    room_id: str,
    user_id: int,
) -> Optional[str]:
    payload = {
        "max_age": 3600,
        "max_uses": 0,
        "temporary": False,
        "unique": True,
        "target_type": 2,  # embedded_application
        "target_application_id": str(discord_client_id),
    }
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/invites",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 401:
                raise RuntimeError("DISCORD_TOKEN 无效，无法创建活动邀请") from exc
            if status_code == 403:
                raise PermissionError("机器人缺少创建邀请或发起活动权限，无法生成活动链接") from exc
            if status_code == 404:
                raise ValueError("目标频道不存在，无法生成活动链接") from exc
            raise RuntimeError(f"Discord API 创建活动邀请失败，状态码: {status_code}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError("无法连接 Discord API，创建活动邀请失败") from exc

    data = response.json() if response.content else {}
    invite_url = str(data.get("url") or "").strip()
    if invite_url:
        return invite_url

    invite_code = str(data.get("code") or "").strip()
    if invite_code:
        return f"https://discord.gg/{invite_code}"

    raise RuntimeError("创建活动邀请成功但未返回有效链接")


async def _send_recruit_message_via_http(
    *,
    room_id: str,
    user_id: int,
    username: str,
    discord_client_id: str,
    channel_id: int,
    guild_id: Optional[int],
    bot_token: str,
) -> Dict[str, str]:
    launch_url = await _create_activity_invite_via_http(
        channel_id=channel_id,
        discord_client_id=discord_client_id,
        bot_token=bot_token,
        room_id=room_id,
        user_id=user_id,
    )

    recruit_text = (
        f"<@{user_id}> 正在招募队友参与多人 21 点对战。\n"
        f"房间号：`{room_id}`\n"
        f"发起人：{username}\n"
        "点击下方按钮启动活动后可自动进入该房间。"
    )

    payload = {
        "content": recruit_text,
        "allowed_mentions": {"parse": ["users"]},
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": f"启动活动并加入房间 {room_id}",
                        "url": launch_url,
                    }
                ],
            }
        ],
    }

    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 401:
                raise RuntimeError("DISCORD_TOKEN 无效，无法调用 Discord Bot API") from exc
            if status_code == 403:
                raise PermissionError("机器人缺少频道权限，无法发送招募消息") from exc
            if status_code == 404:
                raise ValueError("目标频道不存在，或机器人未加入该频道") from exc
            raise RuntimeError(
                f"Discord API 发送招募消息失败，状态码: {status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError("无法连接 Discord API，请稍后重试") from exc

    data = response.json()
    response_channel_id = str(data.get("channel_id") or channel_id)

    response_guild_raw = str(data.get("guild_id") or "").strip()
    response_guild_id: Optional[int] = (
        int(response_guild_raw) if response_guild_raw.isdigit() else guild_id
    )

    return {
        "invite_url": launch_url,
        "message_id": str(data.get("id")),
        "channel_id": response_channel_id,
        "guild_id": str(response_guild_id) if response_guild_id is not None else "dm",
    }


async def _send_recruit_message(
    *,
    room_id: str,
    user_id: int,
    username: str,
    discord_client_id: str,
    channel_id: int,
    guild_id: Optional[int],
) -> Dict[str, str]:
    bot = service_registry.bot
    bot_ready = bool(bot is not None and bot.is_ready())
    bot_token = _resolve_discord_bot_token()

    if bot_ready and bot is not None:
        try:
            return await _send_recruit_message_via_bot(
                bot=bot,
                room_id=room_id,
                user_id=user_id,
                username=username,
                discord_client_id=discord_client_id,
                channel_id=channel_id,
                guild_id=guild_id,
                bot_token=bot_token,
            )
        except Exception as e:
            if not bot_token:
                raise
            log.warning(
                "进程内 Bot 发送招募失败，将回退到 HTTP Bot API: %s",
                e,
                exc_info=True,
            )

    if not bot_token:
        bot_status = service_registry.get_bot_status()
        raise RuntimeError(
            "Discord Bot 当前未就绪，且本服务未配置 DISCORD_TOKEN。"
            f"当前状态: {bot_status.get('status')}"
        )

    if not bot_ready:
        bot_status = service_registry.get_bot_status()
        log.warning(
            "进程内 Bot 不可用（status=%s），使用 HTTP Bot API 发送招募消息",
            bot_status.get("status"),
        )

    return await _send_recruit_message_via_http(
        room_id=room_id,
        user_id=user_id,
        username=username,
        discord_client_id=discord_client_id,
        channel_id=channel_id,
        guild_id=guild_id,
        bot_token=bot_token,
    )


def _bind_session_room(session_key: str, room_id: str) -> None:
    activity_room_bindings[session_key] = room_id
    room_activity_bindings[room_id] = session_key


def _unbind_session_by_room(room_id: str) -> None:
    session_key = room_activity_bindings.pop(room_id, None)
    if session_key:
        activity_room_bindings.pop(session_key, None)


def _find_player_in_room_state(room_state: Dict[str, Any], user_id: int) -> Optional[Dict[str, Any]]:
    for player in room_state.get("players", []):
        try:
            if int(player.get("user_id")) == user_id:
                return player
        except Exception:
            continue
    return None


async def _ensure_user_balance(user_id: int) -> int:
    balance = await coin_service.get_balance(user_id)

    if user_id == TEST_USER_ID and (balance is None or balance < 5000):
        amount_to_add = 10000 - (balance or 0)
        log.warning(
            f"测试用户 {user_id} 余额不足或不存在。正在补充 {amount_to_add} 硬币至10000。"
        )
        balance = await coin_service.add_coins(
            user_id, amount_to_add, "本地开发自动补充"
        )

    if balance is None:
        raise HTTPException(
            status_code=500,
            detail="无法加载余额，账户数据可能异常，请联系管理员。",
        )
    return balance


async def _try_settle_multiplayer_round(room_id: str):
    """
    多人局结算（幂等）：
    - 仅当房间 finished 且尚未提交时执行一次
    - 为每位玩家发放 payout
    - 记录 AI 净盈亏日报（总下注 - 总派彩）
    """
    try:
        settlement = multiplayer_blackjack_service.settle_if_finished(room_id)
    except ValueError:
        return

    if not settlement.get("committed"):
        return

    payouts = settlement.get("payouts", {})
    for uid, amount in payouts.items():
        if int(amount) > 0:
            await coin_service.add_coins(
                int(uid),
                int(amount),
                f"多人21点房间{room_id}结算派彩",
            )

    bet_total = int(settlement.get("bet_total", 0))
    payout_total = int(settlement.get("payout_total", 0))
    await _record_game_result(bet_total, payout_total)


@app.post("/api/game/start")
async def start_game(
    bet_request: BetRequest, user_id: int = Depends(get_current_user_id)
):
    """
    API: 玩家下注并开始一个新游戏
    """
    bet_amount = bet_request.amount
    if bet_amount <= 0:
        raise HTTPException(status_code=400, detail="Bet amount must be positive")

    log.info(f"用户 {user_id} 正在下注 {bet_amount} 开始新游戏")
    async with user_locks[user_id]:
        # 检查并清理任何卡住的旧游戏
        if await blackjack_service.get_active_game(user_id):
            log.warning(
                f"用户 {user_id} 有一个正在进行的游戏。为了开始新游戏，旧游戏将被没收。"
            )
            await blackjack_service.delete_game(user_id)

        # 扣除赌注
        new_balance = await coin_service.remove_coins(
            user_id, bet_amount, "21点游戏下注"
        )
        if new_balance is None:
            raise HTTPException(status_code=402, detail="Insufficient funds")

        try:
            # 创建游戏
            game = await blackjack_service.start_game(user_id, bet_amount)

            # --- 新增：如果游戏在发牌时就已结束（例如21点），立即处理派彩 ---
            final_balance = new_balance
            if game.game_state.startswith("finished"):
                payout_amount = 0
                reason = "21点游戏结算"

                if game.game_state == "finished_blackjack":
                    payout_amount = int(game.bet_amount * 2.5)  # Blackjack 3:2赔率
                    reason = "21点游戏Blackjack获胜"
                elif game.game_state == "finished_push":
                    payout_amount = game.bet_amount  # 平局，退还赌注
                    reason = "21点游戏平局"
                # 注意: 'finished_loss' (庄家21点) 的派彩为0

                if payout_amount > 0:
                    final_balance = await coin_service.add_coins(
                        user_id, payout_amount, reason
                    )

                log.info(
                    f"用户 {user_id} 在发牌时结束游戏。结果: {game.game_state}。赌注: {game.bet_amount}。派彩: {payout_amount}。"
                )

                # 游戏已结束，立即删除记录
                await blackjack_service.delete_game(user_id)
                # --- 记录游戏结果 ---
                await _record_game_result(game.bet_amount, payout_amount)

            return JSONResponse(
                content={
                    "success": True,
                    "game": game.to_dict(),
                    "new_balance": final_balance,  # 返回更新后的余额
                }
            )
        except Exception as e:
            log.error(f"为用户 {user_id} 开始游戏时出错: {e}", exc_info=True)
            # 退还赌注
            await coin_service.add_coins(user_id, bet_amount, "21点游戏开始失败退款")
            raise HTTPException(status_code=500, detail="Could not start the game.")


# TODO: Re-implement double down with server-side logic


@app.post("/api/game/forfeit")
async def forfeit_game(user_id: int = Depends(get_current_user_id)):
    """
    API: 玩家放弃当前游戏
    用于解决玩家因任何原因（如网络断开、浏览器关闭）被卡在游戏中的问题。
    """
    log.warning(f"用户 {user_id} 正在请求放弃当前游戏。")
    async with user_locks[user_id]:
        active_game = await blackjack_service.get_active_game(user_id)
        if not active_game:
            log.info(f"用户 {user_id} 请求放弃游戏，但没有活跃游戏。")
            # 即使没有游戏，也返回成功，因为最终状态是一致的（没有活跃游戏）
            return JSONResponse(
                content={"success": True, "message": "No active game to forfeit."},
                status_code=200,
            )

        log.info(f"用户 {user_id} 已放弃赌注为 {active_game.bet_amount} 的游戏。")
        # 直接删除游戏记录，赌注不退还
        await blackjack_service.delete_game(user_id)

        # --- 记录游戏结果 ---
        await _record_game_result(active_game.bet_amount, 0)  # 投降，派彩为0

        return JSONResponse(
            content={"success": True, "message": "Game forfeited successfully."},
            status_code=200,
        )


@app.post("/api/game/double")
async def double_down(user_id: int = Depends(get_current_user_id)):
    """API: 玩家双倍下注"""
    async with user_locks[user_id]:
        game = await blackjack_service.get_active_game(user_id)
        if not game:
            raise HTTPException(
                status_code=400, detail="No active game to double down on."
            )

        if len(game.player_hand) != 2:
            raise HTTPException(
                status_code=409,
                detail="You can only double down on your initial two cards.",
            )

        double_amount = game.bet_amount
        new_balance = await coin_service.remove_coins(
            user_id, double_amount, "21点游戏双倍下注"
        )
        if new_balance is None:
            raise HTTPException(
                status_code=402, detail="Insufficient funds to double down."
            )

        try:
            game = await blackjack_service.double_down(user_id, double_amount)

            payout_amount = 0
            reason = "21点游戏结算"

            if game.game_state == "finished_win":
                payout_amount = game.bet_amount * 2
                reason = "21点游戏获胜"
            elif game.game_state == "finished_blackjack":
                payout_amount = int(game.bet_amount * 2.5)
                reason = "21点游戏Blackjack获胜"
            elif game.game_state == "finished_push":
                payout_amount = game.bet_amount
                reason = "21点游戏平局"

            final_balance = new_balance
            if payout_amount > 0:
                final_balance = await coin_service.add_coins(
                    user_id, payout_amount, reason
                )

            log.info(
                f"User {user_id} doubled down. Result: {game.game_state}. New Bet: {game.bet_amount}. Payout: {payout_amount}."
            )

            await blackjack_service.delete_game(user_id)

            # --- 记录游戏结果 ---
            await _record_game_result(game.bet_amount, payout_amount)

            return JSONResponse(
                content={
                    "success": True,
                    "game": game.to_dict(),
                    "new_balance": final_balance,
                }
            )
        except ValueError as e:
            await coin_service.add_coins(user_id, double_amount, "21点双倍下注失败退款")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            await coin_service.add_coins(user_id, double_amount, "21点双倍下注失败退款")
            log.error(
                f"Error during double down for user {user_id}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail="An error occurred during the double down action.",
            )


@app.post("/api/game/hit")
async def player_hit(user_id: int = Depends(get_current_user_id)):
    """API: 玩家要牌"""
    async with user_locks[user_id]:
        try:
            game = await blackjack_service.player_hit(user_id)
            new_balance = await coin_service.get_balance(user_id)

            # 如果玩家爆牌，游戏结束并结算
            if game.game_state == "finished_loss":
                log.info(f"User {user_id} busted. Bet of {game.bet_amount} lost.")
                await blackjack_service.delete_game(user_id)
                # --- 记录游戏结果 ---
                await _record_game_result(game.bet_amount, 0)  # 爆牌，派彩为0
                return JSONResponse(
                    content={
                        "success": True,
                        "game": game.to_dict(),
                        "new_balance": new_balance,
                    }
                )

            return JSONResponse(content={"success": True, "game": game.to_dict()})
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            log.error(f"Error during player hit for user {user_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail="An error occurred during the hit action."
            )


@app.post("/api/game/stand")
async def player_stand(user_id: int = Depends(get_current_user_id)):
    """API: 玩家停牌，庄家行动并结算"""
    async with user_locks[user_id]:
        try:
            game = await blackjack_service.player_stand(user_id)

            payout_amount = 0
            reason = "21点游戏结算"

            if game.game_state == "finished_win":
                payout_amount = game.bet_amount * 2
                reason = "21点游戏获胜"
            elif game.game_state == "finished_blackjack":
                payout_amount = int(game.bet_amount * 2.5)  # Blackjack 3:2
                reason = "21点游戏Blackjack获胜"
            elif game.game_state == "finished_push":
                payout_amount = game.bet_amount
                reason = "21点游戏平局"
            # 'finished_loss' has a payout_amount of 0

            new_balance = await coin_service.get_balance(user_id)
            if payout_amount > 0:
                new_balance = await coin_service.add_coins(
                    user_id, payout_amount, reason
                )

            log.info(
                f"User {user_id} finished game. Result: {game.game_state}. Bet: {game.bet_amount}. Payout: {payout_amount}."
            )

            # 游戏结束，删除记录
            await blackjack_service.delete_game(user_id)

            # --- 记录游戏结果 ---
            await _record_game_result(game.bet_amount, payout_amount)

            return JSONResponse(
                content={
                    "success": True,
                    "game": game.to_dict(),
                    "new_balance": new_balance,
                }
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            log.error(
                f"Error during player stand for user {user_id}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500, detail="An error occurred during the stand action."
            )


@app.get("/api/profile")
async def get_profile(user: Dict[str, Any] = Depends(get_current_user_profile)):
    balance = await _ensure_user_balance(int(user["user_id"]))
    return JSONResponse(
        content={
            "success": True,
            "user_id": str(user["user_id"]),
            "username": user["username"],
            "avatar_url": user["avatar_url"],
            "balance": balance,
        }
    )


@app.post("/api/multi/room/auto-join")
async def multi_auto_join_room(
    request: AutoJoinRoomRequest,
    user: Dict[str, Any] = Depends(get_current_user_profile),
):
    session_key = _normalize_session_key(request.session_key)
    user_id = int(user["user_id"])
    username = str(user["username"])
    avatar_url = str(user["avatar_url"])

    async with room_locks[_session_lock_key(session_key)]:
        room_id = activity_room_bindings.get(session_key)
        room_state: Optional[Dict[str, Any]] = None

        if room_id:
            async with room_locks[_room_lock_key(room_id)]:
                try:
                    room_state = multiplayer_blackjack_service.join_room(
                        room_id=room_id,
                        user_id=user_id,
                        username=username,
                        avatar_url=avatar_url,
                    )
                except ValueError as e:
                    error_message = str(e)
                    if "房间不存在" in error_message or "已关闭" in error_message:
                        _unbind_session_by_room(room_id)
                        room_id = None
                    else:
                        raise HTTPException(status_code=400, detail=error_message)

        if not room_id:
            async with room_locks["multi:create"]:
                room_state = multiplayer_blackjack_service.create_room(
                    user_id=user_id,
                    username=username,
                    avatar_url=avatar_url,
                )
                room_id = str(room_state["room_id"])
                _bind_session_room(session_key, room_id)

        balance = await _ensure_user_balance(user_id)
        return JSONResponse(
            content={
                "success": True,
                "room": room_state,
                "viewer_balance": balance,
                "session_key": session_key,
            }
        )


@app.post("/api/multi/room/create")
async def multi_create_room(user: Dict[str, Any] = Depends(get_current_user_profile)):
    async with room_locks["multi:create"]:
        try:
            room_state = multiplayer_blackjack_service.create_room(
                user_id=int(user["user_id"]),
                username=str(user["username"]),
                avatar_url=str(user["avatar_url"]),
            )
            balance = await _ensure_user_balance(int(user["user_id"]))
            return JSONResponse(
                content={
                    "success": True,
                    "room": room_state,
                    "viewer_balance": balance,
                }
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/multi/room/join")
async def multi_join_room(
    request: RoomRequest,
    user: Dict[str, Any] = Depends(get_current_user_profile),
):
    room_id = _normalize_room_id(request.room_id)
    async with room_locks[_room_lock_key(room_id)]:
        try:
            room_state = multiplayer_blackjack_service.join_room(
                room_id=room_id,
                user_id=int(user["user_id"]),
                username=str(user["username"]),
                avatar_url=str(user["avatar_url"]),
            )
            balance = await _ensure_user_balance(int(user["user_id"]))
            return JSONResponse(
                content={
                    "success": True,
                    "room": room_state,
                    "viewer_balance": balance,
                }
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/multi/room/recruit")
async def multi_recruit_room(
    request: RecruitRoomRequest,
    user: Dict[str, Any] = Depends(get_current_user_profile),
):
    room_id = _normalize_room_id(request.room_id)
    user_id = int(user["user_id"])
    username = str(user["username"])

    normalized_session_key = ""
    if request.session_key:
        normalized_session_key = _normalize_session_key(request.session_key)

    async with room_locks[_room_lock_key(room_id)]:
        try:
            room_state = multiplayer_blackjack_service.get_room_state(room_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if not _find_player_in_room_state(room_state, user_id):
            raise HTTPException(status_code=403, detail="你不在该房间中，请先加入房间")

        if normalized_session_key:
            _bind_session_room(normalized_session_key, room_id)

    requested_channel_id = _parse_int_like_id(request.channel_id)
    requested_guild_id = _parse_int_like_id(request.guild_id)

    context_key = normalized_session_key or str(room_activity_bindings.get(room_id) or "")
    session_guild_id, session_channel_id = _extract_channel_context_from_session_key(
        context_key
    )

    channel_id = requested_channel_id or session_channel_id
    guild_id = requested_guild_id if requested_guild_id is not None else session_guild_id
    if channel_id is None:
        raise HTTPException(status_code=400, detail="无法识别 Discord 频道，请在活动内发起招募")

    channel_session_key = _build_channel_session_key(guild_id, channel_id)
    _bind_session_room(channel_session_key, room_id)

    discord_client_id = _resolve_discord_client_id()
    if not discord_client_id or not discord_client_id.isdigit():
        raise HTTPException(status_code=500, detail="服务器缺少有效的 DISCORD_CLIENT_ID 配置")

    try:
        recruit_payload = await _send_recruit_message(
            room_id=room_id,
            user_id=user_id,
            username=username,
            discord_client_id=discord_client_id,
            channel_id=channel_id,
            guild_id=guild_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except discord.Forbidden:
        raise HTTPException(status_code=403, detail="机器人缺少频道权限，无法发送招募消息")
    except discord.HTTPException as e:
        raise HTTPException(status_code=502, detail=f"发送招募消息失败: {e}")
    except Exception as e:
        log.error(f"发送招募消息异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="发送招募消息失败，请稍后重试")

    balance = await _ensure_user_balance(user_id)
    return JSONResponse(
        content={
            "success": True,
            "room": room_state,
            "viewer_balance": balance,
            "room_id": room_id,
            "channel_id": recruit_payload["channel_id"],
            "guild_id": recruit_payload["guild_id"],
            "invite_url": recruit_payload["invite_url"],
            "message_id": recruit_payload["message_id"],
            "bound_session_key": channel_session_key,
        }
    )


@app.post("/api/multi/room/leave")
async def multi_leave_room(
    request: RoomRequest,
    user: Dict[str, Any] = Depends(get_current_user_profile),
):
    room_id = _normalize_room_id(request.room_id)
    user_id = int(user["user_id"])

    async with room_locks[_room_lock_key(room_id)]:
        try:
            # 若本局已结束但还没入账，先做一次幂等结算
            await _try_settle_multiplayer_round(room_id)

            room_before = multiplayer_blackjack_service.get_room_state(room_id)
            player_before = _find_player_in_room_state(room_before, user_id)
            if not player_before:
                raise ValueError("你不在该房间中")

            # 等待阶段离开房间，退还已下注金额
            if room_before.get("state") == "waiting":
                bet_amount = int(player_before.get("bet_amount") or 0)
                if bet_amount > 0:
                    await coin_service.add_coins(
                        user_id,
                        bet_amount,
                        f"多人21点房间{room_id}离房退还下注",
                    )

            leave_result = multiplayer_blackjack_service.leave_room(room_id, user_id)
            if isinstance(leave_result, dict) and leave_result.get("room_closed"):
                _unbind_session_by_room(room_id)

            balance = await _ensure_user_balance(user_id)
            return JSONResponse(
                content={
                    "success": True,
                    "room": leave_result,
                    "viewer_balance": balance,
                }
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/multi/room/{room_id}")
async def multi_get_room(
    room_id: str,
    user: Dict[str, Any] = Depends(get_current_user_profile),
):
    normalized_room_id = _normalize_room_id(room_id)
    user_id = int(user["user_id"])

    try:
        room_state = multiplayer_blackjack_service.get_room_state(normalized_room_id)
        if not _find_player_in_room_state(room_state, user_id):
            raise HTTPException(status_code=403, detail="你不在该房间中，请先加入房间")

        balance = await _ensure_user_balance(user_id)
        return JSONResponse(
            content={
                "success": True,
                "room": room_state,
                "viewer_balance": balance,
            }
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/multi/room/bet")
async def multi_set_bet(
    request: MultiplayerBetRequest,
    user: Dict[str, Any] = Depends(get_current_user_profile),
):
    room_id = _normalize_room_id(request.room_id)
    user_id = int(user["user_id"])
    new_amount = int(request.amount)

    if new_amount <= 0:
        raise HTTPException(status_code=400, detail="下注金额必须大于0")

    async with room_locks[_room_lock_key(room_id)]:
        additional_deducted = 0
        try:
            room_before = multiplayer_blackjack_service.get_room_state(room_id)
            player_before = _find_player_in_room_state(room_before, user_id)
            if not player_before:
                raise ValueError("你不在该房间中")

            old_amount = int(player_before.get("bet_amount") or 0)
            additional = max(0, new_amount - old_amount)
            refund = max(0, old_amount - new_amount)

            if additional > 0:
                new_balance = await coin_service.remove_coins(
                    user_id, additional, f"多人21点房间{room_id}下注补差额"
                )
                if new_balance is None:
                    raise HTTPException(status_code=402, detail="余额不足，无法下注")
                additional_deducted = additional

            room_state = multiplayer_blackjack_service.set_bet(
                room_id=room_id, user_id=user_id, amount=new_amount
            )

            if refund > 0:
                await coin_service.add_coins(
                    user_id, refund, f"多人21点房间{room_id}减少下注退还差额"
                )

            balance = await _ensure_user_balance(user_id)
            return JSONResponse(
                content={
                    "success": True,
                    "room": room_state,
                    "viewer_balance": balance,
                }
            )
        except HTTPException:
            if additional_deducted > 0:
                await coin_service.add_coins(
                    user_id,
                    additional_deducted,
                    f"多人21点房间{room_id}下注失败退款",
                )
            raise
        except ValueError as e:
            if additional_deducted > 0:
                await coin_service.add_coins(
                    user_id,
                    additional_deducted,
                    f"多人21点房间{room_id}下注失败退款",
                )
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            if additional_deducted > 0:
                await coin_service.add_coins(
                    user_id,
                    additional_deducted,
                    f"多人21点房间{room_id}下注异常退款",
                )
            log.error(f"多人下注失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="设置下注失败")


@app.post("/api/multi/room/ready")
async def multi_set_ready(
    request: MultiplayerReadyRequest,
    user: Dict[str, Any] = Depends(get_current_user_profile),
):
    room_id = _normalize_room_id(request.room_id)
    user_id = int(user["user_id"])
    ready = bool(request.ready)

    async with room_locks[_room_lock_key(room_id)]:
        try:
            room_state = multiplayer_blackjack_service.set_ready(
                room_id=room_id,
                user_id=user_id,
                ready=ready,
            )
            balance = await _ensure_user_balance(user_id)
            return JSONResponse(
                content={
                    "success": True,
                    "room": room_state,
                    "viewer_balance": balance,
                }
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/multi/room/continue-ready")
async def multi_continue_ready(
    request: RoomRequest,
    user: Dict[str, Any] = Depends(get_current_user_profile),
):
    room_id = _normalize_room_id(request.room_id)
    user_id = int(user["user_id"])

    async with room_locks[_room_lock_key(room_id)]:
        deducted_amount = 0
        try:
            room_before = multiplayer_blackjack_service.get_room_state(room_id)
            player_before = _find_player_in_room_state(room_before, user_id)
            if not player_before:
                raise ValueError("你不在该房间中")

            room_stage = str(room_before.get("state") or "")
            if room_stage in ("playing", "dealer_turn"):
                raise ValueError("本局进行中，暂时无法继续准备")

            current_bet = int(player_before.get("bet_amount") or 0)
            if room_stage == "waiting" and current_bet > 0:
                if bool(player_before.get("is_ready")):
                    room_state = room_before
                else:
                    room_state = multiplayer_blackjack_service.set_ready(
                        room_id=room_id,
                        user_id=user_id,
                        ready=True,
                    )

                balance = await _ensure_user_balance(user_id)
                return JSONResponse(
                    content={
                        "success": True,
                        "room": room_state,
                        "viewer_balance": balance,
                    }
                )

            continue_bet = int(
                player_before.get("last_bet_amount")
                or player_before.get("bet_amount")
                or 0
            )
            if continue_bet <= 0:
                raise ValueError("没有可沿用的下注金额，请先手动设置下注")

            new_balance = await coin_service.remove_coins(
                user_id,
                continue_bet,
                f"多人21点房间{room_id}继续准备下注",
            )
            if new_balance is None:
                raise HTTPException(
                    status_code=402,
                    detail=f"余额不足，继续准备需要 {continue_bet} 灵石",
                )
            deducted_amount = continue_bet

            room_state = multiplayer_blackjack_service.continue_ready(
                room_id=room_id,
                user_id=user_id,
            )
            balance = await _ensure_user_balance(user_id)
            return JSONResponse(
                content={
                    "success": True,
                    "room": room_state,
                    "viewer_balance": balance,
                }
            )
        except HTTPException:
            if deducted_amount > 0:
                await coin_service.add_coins(
                    user_id,
                    deducted_amount,
                    f"多人21点房间{room_id}继续准备失败退款",
                )
            raise
        except ValueError as e:
            if deducted_amount > 0:
                await coin_service.add_coins(
                    user_id,
                    deducted_amount,
                    f"多人21点房间{room_id}继续准备失败退款",
                )
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            if deducted_amount > 0:
                await coin_service.add_coins(
                    user_id,
                    deducted_amount,
                    f"多人21点房间{room_id}继续准备异常退款",
                )
            log.error(f"多人继续准备失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="继续准备失败")


@app.post("/api/multi/room/start")
async def multi_start_round(
    request: RoomRequest,
    user: Dict[str, Any] = Depends(get_current_user_profile),
):
    room_id = _normalize_room_id(request.room_id)
    user_id = int(user["user_id"])

    async with room_locks[_room_lock_key(room_id)]:
        try:
            multiplayer_blackjack_service.start_round(room_id, user_id)
            await _try_settle_multiplayer_round(room_id)
            room_state = multiplayer_blackjack_service.get_room_state(room_id)
            balance = await _ensure_user_balance(user_id)
            return JSONResponse(
                content={
                    "success": True,
                    "room": room_state,
                    "viewer_balance": balance,
                }
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/multi/room/hit")
async def multi_hit(
    request: RoomRequest,
    user: Dict[str, Any] = Depends(get_current_user_profile),
):
    room_id = _normalize_room_id(request.room_id)
    user_id = int(user["user_id"])

    async with room_locks[_room_lock_key(room_id)]:
        try:
            multiplayer_blackjack_service.hit(room_id, user_id)
            await _try_settle_multiplayer_round(room_id)
            room_state = multiplayer_blackjack_service.get_room_state(room_id)
            balance = await _ensure_user_balance(user_id)
            return JSONResponse(
                content={
                    "success": True,
                    "room": room_state,
                    "viewer_balance": balance,
                }
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/multi/room/stand")
async def multi_stand(
    request: RoomRequest,
    user: Dict[str, Any] = Depends(get_current_user_profile),
):
    room_id = _normalize_room_id(request.room_id)
    user_id = int(user["user_id"])

    async with room_locks[_room_lock_key(room_id)]:
        try:
            multiplayer_blackjack_service.stand(room_id, user_id)
            await _try_settle_multiplayer_round(room_id)
            room_state = multiplayer_blackjack_service.get_room_state(room_id)
            balance = await _ensure_user_balance(user_id)
            return JSONResponse(
                content={
                    "success": True,
                    "room": room_state,
                    "viewer_balance": balance,
                }
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


# --- 静态文件服务 (仅在生产构建后生效) ---
static_files_path = os.path.join(
    os.path.dirname(__file__),
    "dist",
)

# 仅当dist目录存在时 (即前端已构建)，才挂재静态文件
if os.path.isdir(static_files_path):
    print(f"Serving static files from: {static_files_path}")
    # 将整个 dist 目录挂载为静态文件目录
    # html=True 参数会自动为根路径提供 index.html
    app.mount("/", StaticFiles(directory=static_files_path, html=True), name="static")
else:
    print(
        "INFO:     Frontend 'dist' directory not found. Static file serving is disabled."
    )
    print("INFO:     This is normal in development when using the Vite dev server.")


# 运行命令: uvicorn src.chat.features.games.blackjack-web.app:app --reload --port 8000
