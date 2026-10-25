import json

import httpx
import pytest

from scripts.configure_telegram import ProfileError, api_call, configure, read_profile, ROOT


def test_profile_public_and_chat_scopes_and_readback():
    profile = read_profile(ROOT / "assets/telegram/profile.json")
    assert {item["command"] for item in profile["commands"]} == {"start", "login", "menu", "payments", "history", "summary", "chats", "help"}
    public = [item for item in profile["commands"] if item["command"] in {"start", "help"}]
    requests = []

    def respond(request):
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content) if "application/json" in request.headers.get("content-type", "") else {}
        requests.append((method, payload))
        result = True
        if method == "getMe":
            result = {"id": 123, "username": "InKeepbot"}
        elif method in {"getMyName", "getMyDescription", "getMyShortDescription"}:
            key = {"getMyName": "name", "getMyDescription": "description", "getMyShortDescription": "short_description"}[method]
            result = {key: profile[key]}
        elif method == "getMyCommands":
            scope = payload.get("scope",{}).get("type")
            result = [] if scope == "chat" else profile["commands"] if scope == "all_private_chats" else public
        elif method == "getChatMenuButton":
            result = {"type": "commands"}
        elif method == "getUserProfilePhotos":
            result = {"total_count": 1, "photos": [[{"file_id": "fake-public-photo"}]]}
        return httpx.Response(200, json={"ok": True, "result": result})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = configure(client, "fake-test-token", "456", profile, "InKeepbot", b"\xff\xd8\xfffake")
    assert result["profile_verified"] and result["menu_verified"] and result["avatar_applied"]
    writes = [payload for method, payload in requests if method == "setMyCommands"]
    assert len(writes) == 8
    assert all(payload["commands"] == public for payload in writes if not payload.get("scope"))
    assert all(payload["commands"] == profile["commands"] for payload in writes if payload.get("scope",{}).get("type") == "all_private_chats")
    assert all(payload["commands"] == public for payload in writes if payload.get("scope",{}).get("type") in {"all_group_chats","all_chat_administrators"})
    deletes = [payload for method,payload in requests if method == "deleteMyCommands"]
    assert len(deletes) == 2 and all(p["scope"] == {"type":"chat","chat_id":"456"} for p in deletes)
    assert not any(method == "getChat" for method,_ in requests)
    assert not any(method == "sendMessage" for method, _ in requests)


def test_wrong_bot_cannot_change_profile():
    requests = []

    def respond(request):
        requests.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json={"ok": True, "result": {"username": "another_bot"}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProfileError, match="unexpected_bot_no_changes_made"):
            configure(client, "fake-test-token", "456", read_profile(ROOT / "assets/telegram/profile.json"), "InKeepbot")
    assert requests == ["getMe"]


def test_network_error_never_exposes_secret_url():
    token = "fake-sensitive-test-token"

    def respond(request):
        raise httpx.ReadTimeout("unsafe diagnostic " + str(request.url), request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProfileError) as exc:
            api_call(client, token, "setMyDescription", {"description": "test"})
    assert str(exc.value) == "telegram_network_result_unknown"
    assert token not in str(exc.value)


def test_malformed_command_rejected_before_network(tmp_path):
    profile = json.loads((ROOT / "assets/telegram/profile.json").read_text(encoding="utf-8"))
    profile["commands"][0]["command"] = "start@another_bot"
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(ProfileError, match="invalid_command"):
        read_profile(path)
