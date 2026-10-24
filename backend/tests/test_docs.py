from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.security import hash_password


def test_swagger_assets_are_local_and_schema_stays_authenticated(tmp_path):
    password = "Offline docs test password!"
    cfg = Settings(tmp_path / "docs.sqlite3", "admin", hash_password(password), "d" * 64)
    with TestClient(create_app(cfg)) as client:
        assert client.get("/api/v1/docs").status_code == 401
        assert client.get("/api/v1/openapi.json").status_code == 401
        response = client.post("/api/v1/auth/login", json={"username": "admin", "password": password})
        assert response.status_code == 200
        page = client.get("/api/v1/docs")
        assert page.status_code == 200
        assert "/api/v1/docs-assets/swagger-ui-bundle.js" in page.text
        assert "/api/v1/docs-assets/swagger-ui.css" in page.text
        assert "/api/v1/docs-assets/ru.js" in page.text
        assert '<html lang="ru">' in page.text
        assert '"validatorUrl": null' in page.text
        assert "https://" not in page.text and "http://" not in page.text
        assert "https://" not in page.headers["Content-Security-Policy"]
        schema = client.get("/api/v1/openapi.json").json()
        assert schema["components"]["securitySchemes"]["csrfHeader"]["name"] == "X-CSRF-Token"
        assert schema["paths"]["/api/v1/users"]["post"]["security"] == [{"sessionCookie": [], "csrfHeader": []}]
        assert schema["info"]["title"] == "Aéza · API контроля продлений"
        assert "не проводит платёж" in schema["info"]["description"]
        settings = client.get("/api/v1/settings").json()
        assert settings["product_name"] == "Aéza · Контроль продлений"
        assert settings["integration_mode"] == "manual"
        assert settings["reminder_days"] == 3
        assert "telegram_bot_token" not in settings and "admin_password_hash" not in settings
        assert schema["paths"]["/api/v1/auth/login"]["post"]["summary"] == "Войти в систему"
        assert schema["paths"]["/api/v1/auth/login"]["post"]["tags"] == ["Авторизация"]
        assert schema["components"]["schemas"]["Login"]["properties"]["username"]["title"] == "Логин"


def test_swagger_asset_allowlist_and_types(tmp_path):
    cfg = Settings(tmp_path / "assets.sqlite3", "admin", hash_password("Offline asset password!"), "a" * 64)
    with TestClient(create_app(cfg)) as client:
        for filename, media in [("swagger-ui-bundle.js", "text/javascript"), ("swagger-ui.css", "text/css")]:
            asset = client.get("/api/v1/docs-assets/" + filename)
            assert asset.status_code == 200
            assert media in asset.headers["Content-Type"]
            assert len(asset.content) > 100000
        for filename in ["LICENSE", "SOURCE.md", ".env", "%5c%5cexample.invalid%5cshare", "..%5cconfig.py"]:
            assert client.get("/api/v1/docs-assets/" + filename).status_code == 404


def test_validation_messages_are_russian_without_reflecting_passwords(tmp_path):
    password = "Russian validation password!"
    cfg = Settings(tmp_path / "russian.sqlite3", "admin", hash_password(password), "r" * 64)
    with TestClient(create_app(cfg)) as client:
        invalid = client.post("/api/v1/auth/login", json={"username": 123, "password": "", "extra": "secret"})
        assert invalid.status_code == 422
        for error in invalid.json()["detail"]:
            assert any("а" <= char.lower() <= "я" for char in error["msg"])
        assert "secret" not in invalid.text
        assert "Input should" not in invalid.text and "Field required" not in invalid.text


def test_all_documentation_display_metadata_is_russian():
    import re
    schema = create_app().openapi()
    allowed_tags = {"Система", "Авторизация", "Регулярные платежи", "Периоды оплаты", "Итоги", "Пользователи"}
    for methods in schema["paths"].values():
        for operation in methods.values():
            assert set(operation["tags"]) <= allowed_tags
            for key in ("summary", "description"):
                assert re.search("[А-ЯЁа-яё]", operation[key])
            for response in operation["responses"].values():
                assert re.search("[А-ЯЁа-яё]", response["description"])
            for parameter in operation.get("parameters", []):
                assert re.search("[А-ЯЁа-яё]", parameter["description"])
                assert re.search("[А-ЯЁа-яё]", parameter["schema"]["title"])

    def titles(node):
        if isinstance(node, dict):
            if "title" in node:
                assert re.search("[А-ЯЁа-яё]", node["title"]), node["title"]
            for value in node.values():
                titles(value)
        elif isinstance(node, list):
            for value in node:
                titles(value)

    titles(schema)
