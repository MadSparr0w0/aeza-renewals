/* Russian labels for the locally hosted Swagger UI.
 * API paths, field names and request/response code samples remain unchanged.
 * Text-node updates preserve Swagger's React components and event handlers.
 */
(function () {
  "use strict";
  document.documentElement.lang = "ru";

  var words = new Map(Object.entries({
    "Authorize": "Авторизация",
    "Authorization": "Авторизация",
    "Available authorizations": "Настройка авторизации",
    "Authorized": "Доступ сохранён",
    "Logout": "Удалить доступ",
    "Close": "Закрыть",
    "Apply credentials": "Сохранить данные доступа",
    "Remove authorization": "Удалить данные доступа",
    "Try it out": "Проверить запрос",
    "Execute": "Выполнить",
    "Clear": "Очистить",
    "Cancel": "Отмена",
    "Reset": "Сбросить",
    "Download": "Скачать",
    "Parameters": "Параметры",
    "Parameter": "Параметр",
    "No parameters": "Нет параметров",
    "Request body": "Тело запроса",
    "Request URL": "Адрес запроса",
    "Request duration": "Время выполнения",
    "Responses": "Ответы",
    "Response": "Ответ",
    "Server response": "Ответ сервера",
    "Response body": "Тело ответа",
    "Response headers": "Заголовки ответа",
    "Response content type": "Тип содержимого ответа",
    "Parameter content type": "Тип содержимого параметра",
    "Media type": "Тип данных",
    "Media Type": "Тип данных",
    "Controls": "Задаёт заголовок",
    "header.": ".",
    "Code": "Код",
    "Details": "Подробности",
    "Description": "Описание",
    "Name": "Имя",
    "Type": "Тип",
    "Format": "Формат",
    "Value": "Значение",
    "Value:": "Значение:",
    "Name:": "Имя:",
    "In:": "Расположение:",
    "header": "HTTP-заголовок",
    "query": "параметр адреса",
    "path": "часть адреса",
    "(apiKey)": "(ключ API)",
    "Username:": "Логин:",
    "Password:": "Пароль:",
    "Scopes": "Права доступа",
    "Links": "Ссылки",
    "No links": "Нет ссылок",
    "Schema": "Схема",
    "Schemas": "Схемы данных",
    "Models": "Модели данных",
    "Model": "Модель данных",
    "Example Value": "Пример значения",
    "Edit Value": "Изменить значение",
    "Example": "Пример",
    "Examples": "Примеры",
    "Example Description": "Описание примера",
    "Servers": "Серверы",
    "Server variables": "Переменные сервера",
    "Computed URL:": "Полученный адрес:",
    "Select a definition": "Выберите спецификацию",
    "Explore": "Открыть",
    "Filter by tag or path": "Найти раздел или адрес",
    "Send empty value": "Отправить пустое значение",
    "Available values": "Допустимые значения",
    "Default value": "По умолчанию",
    "required": "обязательно",
    "Required": "Обязательно",
    "(required)": "(обязательно)",
    "deprecated": "устарело",
    "Deprecated": "Устарело",
    "(deprecated)": "(устарело)",
    "Undocumented": "Ответ не описан",
    "Successful Response": "Успешный ответ",
    "Validation Error": "Ошибка проверки данных",
    "Error": "Ошибка",
    "Errors": "Ошибки",
    "Warning": "Предупреждение",
    "Failed to load API definition.": "Не удалось загрузить описание API.",
    "Failed to fetch": "Не удалось выполнить запрос",
    "Fetch error": "Ошибка загрузки",
    "Parser error": "Ошибка разбора данных",
    "Resolver error": "Ошибка обработки схемы",
    "Internal Server Error": "Внутренняя ошибка сервера",
    "Bad Request": "Некорректный запрос",
    "Unauthorized": "Требуется вход",
    "Forbidden": "Нет доступа",
    "Not Found": "Не найдено",
    "Unprocessable Entity": "Некорректные данные",
    "Too Many Requests": "Слишком много запросов",
    "Please correct the following validation errors and try again.": "Исправьте перечисленные ошибки и повторите запрос.",
    "Required field is not provided": "Обязательное поле не заполнено",
    "Value must be an integer": "Значение должно быть целым числом",
    "Value must be a number": "Значение должно быть числом",
    "Value must be a string": "Значение должно быть строкой",
    "Value must be a boolean": "Укажите логическое значение",
    "Value must be a valid JSON": "Введите корректный JSON",
    "Unrecognized response type; displaying content as text.": "Неизвестный тип ответа. Содержимое показано как текст.",
    "Curl": "Команда curl",
    "Copy": "Копировать",
    "Copy to clipboard": "Копировать в буфер",
    "Copy path to clipboard": "Копировать адрес в буфер",
    "Copy cURL command to clipboard": "Копировать команду curl",
    "Copy code to clipboard": "Копировать код",
    "Copied": "Скопировано",
    "Collapse operation": "Свернуть операцию",
    "Expand operation": "Развернуть операцию",
    "Expand all": "Развернуть всё",
    "Collapse all": "Свернуть всё",
    "Hide": "Скрыть",
    "Show": "Показать",
    "Hide all": "Скрыть всё",
    "Show all": "Показать всё",
    "Jump to definition": "Перейти к схеме",
    "Jump to path": "Перейти к адресу",
    "authorization button unlocked": "Настроить авторизацию",
    "authorization button locked": "Авторизация настроена",
    "Loading": "Загрузка",
    "Loading...": "Загрузка…",
    "Login": "Вход",
    "RecurringCreate": "Создание регулярного платежа",
    "OccurrencePatch": "Изменение оплаты",
    "UserCreate": "Создание пользователя",
    "UserPatch": "Изменение пользователя",
    "HTTPValidationError": "Ошибка проверки запроса",
    "ValidationError": "Ошибка проверки поля",
    "string": "строка",
    "integer": "целое число",
    "number": "число",
    "boolean": "логическое значение",
    "object": "объект",
    "array": "массив",
    "any": "любое значение",
    "null": "пустое значение",
    "bytes": "байт",
    "ms": "мс"
  }));

  var prefixes = [
    ["Available values", "Допустимые значения"],
    ["Default value", "По умолчанию"],
    ["Minimum length", "Минимальная длина"],
    ["Maximum length", "Максимальная длина"],
    ["Min length", "Минимальная длина"],
    ["Max length", "Максимальная длина"],
    ["Min items", "Минимальное число элементов"],
    ["Max items", "Максимальное число элементов"],
    ["Error", "Ошибка"]
  ];
  var skip = "script,style,pre,code,textarea,input,.microlight";

  function translate(value) {
    var core = value.trim();
    if (core === "header.") return ".";
    var result = words.get(core);
    if (result === undefined) {
      for (var i = 0; i < prefixes.length; i++) {
        var prefix = prefixes[i][0];
        if (core.startsWith(prefix + ":")) {
          result = prefixes[i][1] + core.slice(prefix.length);
          break;
        }
      }
    }
    if (result === undefined) {
      var match = /^(string|integer|number|boolean|object|array)\s*(\(\$[^)]+\))$/.exec(core);
      if (match) result = words.get(match[1]) + match[2];
    }
    return result === undefined ? value : value.replace(core, result);
  }

  function translateAttributes(element) {
    ["aria-label", "title", "placeholder"].forEach(function (name) {
      var value = element.getAttribute(name);
      if (value === null) return;
      var translated = translate(value);
      if (translated !== value) element.setAttribute(name, translated);
    });
  }

  function localize(root) {
    if (!root || !root.isConnected) return;
    if (root.nodeType === Node.TEXT_NODE) {
      if (root.parentElement && !root.parentElement.closest(skip)) {
        var translated = translate(root.nodeValue);
        if (translated !== root.nodeValue) root.nodeValue = translated;
      }
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE) return;
    translateAttributes(root);
    if (root.closest(skip)) return;
    root.querySelectorAll("[aria-label],[title],[placeholder]").forEach(translateAttributes);
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    var node;
    while ((node = walker.nextNode())) {
      if (!node.parentElement || node.parentElement.closest(skip)) continue;
      var text = translate(node.nodeValue);
      if (text !== node.nodeValue) node.nodeValue = text;
    }
  }

  var pending = new Set();
  var scheduled = false;
  function schedule(root) {
    pending.add(root);
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(function () {
      scheduled = false;
      var roots = Array.from(pending);
      pending.clear();
      roots.forEach(localize);
    });
  }

  var observer = new MutationObserver(function (records) {
    records.forEach(function (record) {
      if (record.type === "attributes" || record.type === "characterData") {
        schedule(record.target);
      } else {
        record.addedNodes.forEach(schedule);
      }
    });
  });
  observer.observe(document.documentElement, {
    subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: ["aria-label", "title", "placeholder"]
  });
  localize(document.body);
}());
