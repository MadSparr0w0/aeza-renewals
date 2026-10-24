# Vendored Swagger UI

Package: swagger-ui-dist 5.34.0
Publisher/source: Swagger API / SmartBear
Upstream: https://github.com/swagger-api/swagger-ui
Registry metadata: https://registry.npmjs.org/swagger-ui-dist/5.34.0
Tarball: https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-5.34.0.tgz
Integrity: sha512-H6tAIjr3XqE31PbkZl0OFya1ziRLDfTwh3J6OHdumPtPCLfjTnyLeagl/55kFNZcTOz9a9QI9WP0FXldwiGJag==
License: Apache-2.0; upstream LICENSE and bundled third-party license notices are included unchanged.

Only swagger-ui-bundle.js, swagger-ui.css, LICENSE and swagger-ui-bundle.js.LICENSE.txt were copied.
Normal app startup and documentation never fetch npm or CDN resources.
Swagger's external schema validator is disabled; API requests stay same-origin.
Reproduce with python scripts/vendor_swagger.py. Update VERSION/INTEGRITY deliberately.

Файл ru.js — локальная русификация интерфейса этого проекта, а не часть исходного пакета Swagger UI. Он меняет подписи и доступные названия элементов, сохраняя адреса API, поля и примеры запросов/ответов. После обновления Swagger UI нужно проверить окно авторизации, схемы и выполнение запроса на русском.
