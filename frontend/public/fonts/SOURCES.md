# Локальные шрифты Aéza

Типографика соответствует гайдбуку Aéza 2026: основной шрифт Exo 2,
дополнительный — Inter. Заголовки, основной текст, суммы и даты используют
Exo 2; элементы управления, навигация и подписи — Inter. Для сумм и дат
включены табличные цифры.

Exo 2 Variable, Fontsource 5.3.0:
https://registry.npmjs.org/@fontsource-variable/exo-2/-/exo-2-5.3.0.tgz
https://github.com/google/fonts/tree/main/ofl/exo2
Лицензия: `exo-2/LICENSE.txt` — SIL Open Font License 1.1.

Inter Variable, Fontsource 5.3.0:
https://registry.npmjs.org/@fontsource-variable/inter/-/inter-5.3.0.tgz
https://github.com/rsms/inter
Лицензия: `inter/LICENSE.txt` — SIL Open Font License 1.1.

Файлы WOFF2 хранятся в `src/assets/fonts/` и включаются Vite в локальную
сборку. Используются кириллица, латиница и подмножество символов валют.
Во время работы приложение не запрашивает шрифты у внешних сервисов.

Компактный знак é без изменения исходных SVG-контуров получен с
официального сайта Aéza:
https://aeza.ru/_next/static/media/e.14a68307.svg
