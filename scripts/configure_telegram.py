"""Apply the checked-in bot profile without printing credentials or secret URLs."""
import argparse
import json
import os
from pathlib import Path
import re

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


class ProfileError(Exception):
    """Only fixed, non-secret error codes belong in this exception."""


def read_profile(path):
    profile = json.loads(path.read_text(encoding="utf-8"))
    for key, maximum in (("name", 64), ("description", 512), ("short_description", 120)):
        if not isinstance(profile.get(key), str) or not 1 <= len(profile[key]) <= maximum:
            raise ProfileError("invalid_profile_" + key)
    commands = profile.get("commands")
    if not isinstance(commands, list) or not 1 <= len(commands) <= 100:
        raise ProfileError("invalid_commands")
    names = set()
    for item in commands:
        if (not isinstance(item, dict) or not isinstance(item.get("command"), str)
                or not re.fullmatch(r"[a-z0-9_]{1,32}", item["command"])
                or item["command"] in names or not isinstance(item.get("description"), str)
                or not 1 <= len(item["description"]) <= 256):
            raise ProfileError("invalid_command")
        names.add(item["command"])
    return profile


def api_call(client, token, method, payload=None, photo=None):
    kwargs = {"json": payload or {}}
    if photo is not None:
        kwargs = {"data": {"photo": json.dumps({"type": "static", "photo": "attach://avatar"})},
                  "files": {"avatar": ("avatar.jpg", photo, "image/jpeg")}}
    try:
        with client.stream("POST", "https://api.telegram.org/bot" + token + "/" + method, **kwargs) as response:
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > 131072:
                    raise ProfileError("telegram_response_too_large")
            data = json.loads(content)
            if response.status_code != 200 or not isinstance(data, dict) or data.get("ok") is not True:
                code = data.get("error_code") if isinstance(data, dict) else None
                raise ProfileError("telegram_rejected_" + str(code if isinstance(code, int) else response.status_code))
            return data.get("result")
    except httpx.HTTPError:
        raise ProfileError("telegram_network_result_unknown") from None
    except (ValueError, UnicodeDecodeError):
        raise ProfileError("telegram_invalid_response") from None


def configure(client, token, chat_id, profile, expected_bot, photo=None):
    bot = api_call(client, token, "getMe")
    if not isinstance(bot, dict) or str(bot.get("username", "")).casefold() != expected_bot.lstrip("@").casefold():
        raise ProfileError("unexpected_bot_no_changes_made")
    if chat_id and not re.fullmatch(r"-?[1-9][0-9]*", chat_id):
        raise ProfileError("invalid_legacy_chat_no_changes_made")
    public_commands = [item for item in profile["commands"] if item["command"] in {"start", "help"}]
    for language in ("", "ru"):
        for method, key in (("setMyName", "name"), ("setMyDescription", "description"),
                            ("setMyShortDescription", "short_description")):
            if api_call(client, token, method, {key: profile[key], "language_code": language}) is not True:
                raise ProfileError("profile_update_not_confirmed")
        api_call(client, token, "setMyCommands", {"commands": public_commands, "language_code": language})
        api_call(client, token, "setMyCommands", {"commands": profile["commands"], "language_code": language,
                                                 "scope": {"type": "all_private_chats"}})
        api_call(client, token, "setMyCommands", {"commands": public_commands, "language_code": language,
                                                 "scope": {"type": "all_group_chats"}})
        api_call(client, token, "setMyCommands", {"commands": public_commands, "language_code": language,
                                                 "scope": {"type": "all_chat_administrators"}})
        # Remove the previous release's per-chat finance menu. This ID is only
        # for command cleanup, never a notification destination or access grant.
        if chat_id:
            api_call(client, token, "deleteMyCommands", {"language_code": language,
                                                       "scope": {"type": "chat", "chat_id": chat_id}})
    api_call(client, token, "setChatMenuButton", {"menu_button": {"type": "commands"}})
    if photo is not None:
        if api_call(client, token, "setMyProfilePhoto", photo=photo) is not True:
            raise ProfileError("avatar_update_not_confirmed")
    for language in ("", "ru"):
        for method, key in (("getMyName", "name"), ("getMyDescription", "description"),
                            ("getMyShortDescription", "short_description")):
            result = api_call(client, token, method, {"language_code": language})
            if not isinstance(result, dict) or result.get(key) != profile[key]:
                raise ProfileError("profile_readback_mismatch")
        if api_call(client, token, "getMyCommands", {"language_code": language}) != public_commands:
            raise ProfileError("public_commands_readback_mismatch")
        for scope, expected in (({"type": "all_private_chats"}, profile["commands"]),
                                ({"type": "all_group_chats"}, public_commands),
                                ({"type": "all_chat_administrators"}, public_commands)):
            if api_call(client, token, "getMyCommands", {"language_code": language,"scope": scope}) != expected:
                raise ProfileError("commands_readback_mismatch")
        if chat_id and api_call(client, token, "getMyCommands", {"language_code": language,
                                    "scope": {"type": "chat", "chat_id": chat_id}}) != []:
            raise ProfileError("legacy_commands_not_removed")
    menu = api_call(client, token, "getChatMenuButton")
    if not isinstance(menu, dict) or menu.get("type") != "commands":
        raise ProfileError("menu_readback_mismatch")
    if photo is not None:
        photos = api_call(client, token, "getUserProfilePhotos", {"user_id": bot["id"], "limit": 1})
        if not isinstance(photos, dict) or not photos.get("photos"):
            raise ProfileError("avatar_readback_missing")
    return {"bot": bot["username"], "profile_verified": True, "commands": len(profile["commands"]),
            "legacy_commands_removed": bool(chat_id), "avatar_applied": photo is not None, "menu_verified": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Оформление Telegram-бота из локальных файлов")
    parser.add_argument("--expected-bot", required=True, help="Ожидаемый username бота без токена")
    parser.add_argument("--profile", type=Path, default=ROOT / "assets/telegram/profile.json")
    parser.add_argument("--photo", type=Path, help="Новый аватар в JPEG; без параметра текущий аватар сохраняется")
    parser.add_argument("--dry-run", action="store_true", help="Проверить файлы без обращения к Telegram")
    args = parser.parse_args(argv)
    try:
        profile = read_profile(args.profile)
        photo = args.photo.read_bytes() if args.photo is not None else None
        if photo is not None and (not photo.startswith(b"\xff\xd8\xff") or len(photo) > 10 * 1024 * 1024):
            raise ProfileError("avatar_requires_jpeg_under_10mb")
        if args.dry_run:
            print(json.dumps({"profile_valid": True, "commands": len(profile["commands"]), "photo_valid": photo is not None}))
            return
        values = dotenv_values(ROOT / ".env", interpolate=False)
        token = os.getenv("TELEGRAM_BOT_TOKEN", values.get("TELEGRAM_BOT_TOKEN") or "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", values.get("TELEGRAM_CHAT_ID") or "").strip()
        if not token:
            raise ProfileError("telegram_not_configured")
        with httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False) as client:
            result = configure(client, token, chat_id, profile, args.expected_bot, photo)
        print(json.dumps(result, ensure_ascii=False))
    except ProfileError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        raise SystemExit(1) from None
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"ok": False, "error": "invalid_local_profile_files"}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
