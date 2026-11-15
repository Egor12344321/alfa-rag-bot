# Отчёт об импорте и приёмке `kb`

Сверка схемы и дампа: 11 ноября 2026; подтверждение новых страниц и финальная приёмка: 12 ноября 2026. Отчёт не содержит текстов материалов или пользовательских данных.

## Повторная сверка рабочей публикации (12 ноября 2026)

- По последнему сообщённому состоянию рабочей `assistant_db` активен `knowledge-v1` с 10 статьями и 184 чанками без карточек каталога. В этой сессии подключиться к source/target БД не удалось: в окружении отсутствуют `ALFA_SOURCE_DSN`, `ALFA_DATABASE_URL` и `DATABASE_URL`, нет `~/.pgpass` и `~/.pg_service.conf`, а локальный PostgreSQL на `/tmp:5432` не отвечает. Поэтому текущие DSN, роль, ACL, `active_run_id` и счётчики рабочей БД повторно не подтверждены; активный run ID рабочей БД здесь неизвестен.
- `expanded_sources.py` явно перечисляет 15 таблиц: `Articles`, `Statuses`, `ArticleEmbedItems`, `Courses`, `CourseTypes`, `CourseTariffBindings`, `ContentSettings`, `ContentSettingsTopics`, `Topics`, `RecommendedItems`, `Documents`, `DocumentEmbedItems`, `Glossary`, `GlossaryEmbedItems`, `Webinars`. В прежней проверке `alfa_source_ro` имела `SELECT` на первых трёх. Если подключение подтвердит, что эти права сохранились и остальные отсутствуют, минимальный дополнительный GRANT — только на 12 нижеперечисленных таблиц; текущую ACL без подключения утверждать нельзя:

  ```sql
  GRANT SELECT ON TABLE
    public."Courses", public."CourseTypes", public."CourseTariffBindings",
    public."ContentSettings", public."ContentSettingsTopics", public."Topics",
    public."RecommendedItems", public."Documents", public."DocumentEmbedItems",
    public."Glossary", public."GlossaryEmbedItems", public."Webinars"
  TO alfa_source_ro;
  ```

  Сначала проверьте эффективные права для `alfa_source_ro` на каждом из 15 объектов; выдавайте только отсутствующие из списка выше от имени владельца/администратора source DB. `Lessons` в mapping не входят и права на них не нужны.
- Публикация `knowledge-v4` в рабочую БД и проверка активного `run_id` не выполнены. После восстановления подключений и проверки ACL продолжить штатным CLI:

  ```sh
  ALFA_SOURCE_DSN='...' ALFA_DATABASE_URL='...' ALFA_TARGET_DATABASE=assistant_db \
  ALFA_PIPELINE_VERSION=knowledge-v4 \
  ALFA_EMBEDDING_REVISION=614241f622f53c4eeff9890bdc4f31cfecc418b3 \
  HF_HOME=models/huggingface .venv/bin/alfa-kurs-ingest
  ```

  Затем только чтением запросить активный `kb.index_state.active_run_id`, статус run и счётчики `kb.materials`/`kb.chunks` по типам:

  ```sql
  SELECT r.id AS active_run_id, r.status, r.pipeline_version,
         m.source_table, m.kind, c.purpose,
         count(DISTINCT m.id) AS materials, count(c.id) AS chunks
  FROM kb.index_state s
  JOIN kb.ingestion_runs r ON r.id = s.active_run_id
  LEFT JOIN kb.materials m ON m.run_id = r.id
  LEFT JOIN kb.chunks c ON c.run_id = m.run_id AND c.material_id = m.id
  WHERE s.singleton = TRUE
  GROUP BY r.id, r.status, r.pipeline_version, m.source_table, m.kind, c.purpose
  ORDER BY m.source_table, c.purpose;
  ```

  Если входы совпадут с принятым корпусом, ожидаются 19 материалов: 12 статей, 5 курсов, 1 документ, 1 термин; 261 чанк: 254 статейных `knowledge`, 5 курсных `catalog`, 1 документный `knowledge`, 1 терминный `knowledge`. Тестовый run `ac446074-65f0-4beb-9cea-a8b6fceebb0f` не является подтверждением рабочей публикации.
- Полный `scripts/acceptance_check.py` на рабочей БД не запускался. Предыдущая отдельная тестовая приёмка остаётся подтверждённой историей ниже; она включала намеренный сбой и временное отключение карточек на тестовом run, но не заменяет требуемые read-only проверки рабочей БД (пять запросов, NDA, доверенность, `get_card()`, связанная рекомендация, `free_only`, `exclude`).
- В репозитории отсутствуют точка входа Telegram-бота Python #2 и генератор ответа. `KnowledgeService` реализует `retrieve()`, `recommend()` и `get_card()`; примеры показывают передачу `result.run_id` и `result.evidence[0].source` (или `None`). Acceptance-сценарий отдельно тестирует retrieval и рекомендации, но использует фиксированный `RELATED_SOURCE`, поэтому сквозной вызов от выбранного retrieve-фрагмента не проверен. Реальный KnowledgeService к рабочей БД, генерация ответа и полный сценарий «короткий вопрос → ответ со ссылкой → рекомендация курса» в этой сессии не проверены из-за отсутствующей точки входа/подключения.
- В mapping `external_approved=frozenset()`: у всех 19 материалов `external_allowed=false`. Для текущего корпуса разрешён только локальный контур (`external_context=False`). Внешняя LLM и генерация ответа в доступном коде не запускались.
- Следующая партия не импортировалась в тестовую БД: для отдельного тестового run также недоступны source/target подключения. URL и кандидаты следующей партии зафиксированы в подразделе «Кандидаты следующего расширения» ниже; до импорта каждому материалу нужна сверка с ID/статусом в source БД и отдельное подтверждение публичной страницы.

## Приёмка расширения `knowledge-v4` на отдельной тестовой БД (12 ноября 2026)

Новый mapping `articles-documents-glossary-and-course-catalog-reviewed-2026-11-12-v2` допускает **19 материалов**: 12 статей, 5 карточек курсов, 1 документ и 1 термин. Исходные ID и порядок `ArticleEmbedItems`, `DocumentEmbedItems` и `GlossaryEmbedItems` сохранены; для короткого определения «Доверенность» в индекс попадают все непустые Type=0 блоки, а пустой блок Order 10 исключён. Каталогные записи содержат только `Courses.Description`, уроки не импортируются.

### Подтверждение доступа и новые источники

- Документ `Documents/defc5abc-e692-4ae8-87f6-987d791705b0`, «Соглашение о неразглашении», опубликован, не удалён, не помечен `NoIndex`; `Published` пустой, поэтому один статус дампа не использован как основание доступа. Публичная страница и содержимое подтверждены официальным crawl/search по [точному URL](https://kurs.alfabank.ru/documents/soglashenie-o-nerazglashenii/). Исходный блок: `DocumentEmbedItems.Id=7b6d8668-bae4-480a-b3e7-fcac3509d153`, `Order=1`.
- Термин `Glossary/58e6c342-30d9-4300-bf15-183e799402a2`, «Доверенность», опубликован, не удалён, не помечен `NoIndex`, имеет `Published`. Официальный crawl/search подтвердил полное определение на [точном URL](https://kurs.alfabank.ru/vocabulary/doverennost/). Сохранены шесть непустых блоков с исходными ID в порядке `Order` 1, 3, 4, 5, 7, 8; пустой блок Order 10 отбрасывается.
- Добавлена статья `Articles/fba0ba5a-45d3-40be-9d57-3872f5ff226a`, «Топ-8 курсов по маркетплейсам», [официальная страница](https://kurs.alfabank.ru/articles/top-8-kursov-po-marketplejsam/).
- Для реальной рекомендательной связи добавлена статья `Articles/5197669c-e7c3-4465-9a16-e7eeb13ee37c`, «Перерасчёт стоимости патента: правила в 2026 году», [официальная страница](https://kurs.alfabank.ru/articles/pereraschyot-stoimosti-patenta-est-li-smysl-i-kakie-pravila/). Направленные `RecommendedItems` ведут от этой статьи к двум включённым открытым курсам: `Courses/b9045d6e-07ad-407f-a305-472e82fef797` ([«Бизнес в сфере услуг»](https://kurs.alfabank.ru/courses/biznes-bez-trevog-zapusk-proekta-v-sfere-uslug/)) и `Courses/b3f8439b-f5c0-4c97-b283-a62a524c29dc` ([«Маникюрная студия»](https://kurs.alfabank.ru/courses/biznes-bez-skolov-manikyurnaya-studiya-mechty/)). Их `CourseType='Открытый'`; используется только публичное описание. Относительный порядок связей берётся из `RecommendedItems` и сохраняется в provenance.
- Дополнительно включён курс `Courses/d0aa4598-3da2-478b-bbf8-bda81b231b58`, «Маркетплейсы: с чего начать и как преуспеть», с [официальной карточкой](https://kurs.alfabank.ru/courses/marketpleysy-s-chego-nachat-kak-preuspet/); страница помечена как бесплатная. Для всех пяти открытых курсов `price` и `currency` пусты. Платные карточки и цены в этой версии не импортированы.
- Подтверждение выполнено по публичным результатам crawl/search официального домена 12 ноября 2026: страницы содержали точный заголовок, текст и соответствовали slug дампа. Прямой fetch нескольких страниц возвращал 502, поэтому успешный HTTP-статус origin не заявляется. Публичная страница не подтверждает право внешней передачи: `external_allowed=false` для всего корпуса.
- Из оставшихся пригодных по структуре записей `Documents` остаются **218** без индивидуального подтверждения страницы/доступа; также у них пустое `Published`. В `Glossary` остаются **290** структурных кандидатов без индивидуального подтверждения страницы/доступа. Они исключены с конкретной причиной `public_access_unconfirmed`; slug отдельно не превращается в проверенный URL. Для остальных статей и курсов требуются индивидуальные сверки canonical URL и доступа до расширения allowlist.

### Кандидаты следующего расширения

Следующие IDs и состояние дампа сверены только с локальным backup; они не были перепроверены в рабочем source DB и не включены в mapping/run. Официальный поиск подтверждает публичные страницы и exact URL для документов, термина и бесплатного курса. Публичность не даёт допуска для внешней LLM: до получения отдельного разрешения все новые записи должны получить `external_allowed=false`.

- Документ «Опись документов», `Documents/51cd597c-e06b-406e-b0a5-093ec1388c7d`: [официальная страница](https://kurs.alfabank.ru/documents/opis-dokumentov/). Документ «Соглашение об использовании факсимильной подписи», `Documents/3f1aa224-0976-48b2-9477-df8007facc11`: [официальная страница](https://kurs.alfabank.ru/documents/soglashenie-ob-ispolzovanii-faksimilnoj-podpisi/). В backup оба имеют статус «Опубликовано», `IsDeleted=false`, `NoIndex=false`, но `Published=NULL`; перед импортом нужна повторная проверка карточек и текстов в source DB и подтверждение публичного доступа к конкретному содержимому.
- Термин «Машиночитаемая доверенность», `Glossary/edc6b5dc-f10d-48d2-98d2-1f0dc29036eb`: [официальная страница с определением](https://kurs.alfabank.ru/vocabulary/mashinochitaemaya-doverennost/). В backup статус «Опубликовано», `IsDeleted=false`, `NoIndex=false`; ID, блоки и статус перед импортом также нужно перепроверить в source DB.
- Бесплатный курс «Где брать клиентов: продвижение в Яндексе», `Courses/eb371f34-34ea-4cb3-991a-cbbea147eda2`, slug `gde-brat-klientov-prodvizhenie-v-yandekse`: [официальная карточка и описание](https://kurs.alfabank.ru/courses/gde-brat-klientov-prodvizhenie-v-yandekse/). В backup курс опубликован как открытый, не удалён и не `NoIndex`; при включении в каталог импортировать только публичное описание.
- Платный курс «УСН на маркетплейсах: особенности налогового учёта», `Courses/28e2a868-0b58-4fd6-af4d-7aa268ae91b4`, slug `usn-na-marketplejsah-osobennosti-nalogovogo-uchyota`. В backup `CourseType='Платный'`, `Published` заполнен, запись не удалена и не `NoIndex`; `Courses.Cost=3990` совпадает с ровно одним основным тарифом в `CourseTariffBindings`. [Официальный каталог курсов](https://kurs.alfabank.ru/courses/) показывает курс и 3 990 ₽. Предполагаемый URL карточки — `https://kurs.alfabank.ru/courses/usn-na-marketplejsah-osobennosti-nalogovogo-uchyota/`, но прямой запрос завершился ошибкой кеша/502. Поэтому тариф в backup подтверждён, а доступ к полной публичной карточке/описанию и актуальность этого тарифа в рабочей source DB пока нет; до такой сверки не добавлять эту карточку и не выдавать цену в каталоге.
- Из-за отсутствия source/target DSN следующая партия не импортировалась отдельным тестовым run. Как только тестовая БД станет доступна, повторно проверьте source ID/статусы/тарифы, добавьте только прошедшие проверку источники в отдельный mapping, выполните импорт на отдельный тестовый target и проверьте count/retrieval до любой дальнейшей публикации. Эта работа не должна менять уже принятый набор из 19 материалов.

### Отдельная PostgreSQL/pgvector-приёмка

Дамп восстановлен в изолированную временную `alfa_source_test`, схема проекта применена к временной `assistant_db`; рабочая БД не использовалась. Модель `intfloat/multilingual-e5-small` загружена из локального кэша; в run зафиксирована ревизия `614241f622f53c4eeff9890bdc4f31cfecc418b3`.

- Новый активный тестовый run: **`ac446074-65f0-4beb-9cea-a8b6fceebb0f`**, `knowledge-v4`, PostgreSQL 18.6 / pgvector 0.8.7. Материалы: 12 статей, 5 курсов, 1 документ, 1 термин (19 всего). Чанки: 254 статейных, 5 catalog, 1 документный, 1 терминный (261 всего: 256 `knowledge`, 5 `catalog`); 13 связей с темами, 4 рекомендательные связи.
- Все пять исходных запросов сохранили ожидаемый top-1. Запрос по NDA нашёл `Documents/defc5abc-e692-4ae8-87f6-987d791705b0`; запрос по определению доверенности — `Glossary/58e6c342-30d9-4300-bf15-183e799402a2`. Проверены исходные ID и порядок блоков обоих типов.
- `recommend(..., source=SourceKey('Articles', '5197669c-e7c3-4465-9a16-e7eeb13ee37c'))` вернул первый связанный курс. Исключение этой цели вывело вторую; отключение обеих целей от каталога дало прежний векторный fallback. Источник без допустимой связи не изменил векторное ранжирование. Проверены `free_only`, исключение всех пяти курсов, `get_card()` для доступной/отсутствующей/неразрешённой карточки и изоляция catalog от `retrieve()`.
- Повторный CLI-импорт `knowledge-v4` вернул тот же run ID; дубликатов материалов и чанков нет. Предыдущий тестовый run `e8a7a035-71be-4170-932d-1c0ce9286a18` остался доступен и прошёл контрольный retrieval на той же ревизии. Несовпадение SHA модели отклонено до сравнения векторов. Намеренная ошибка записи создала `failed` run и оставила новый активный pointer без изменений.
- Права проверены на тестовом кластере. `alfa_source_ro`: SELECT на расширенные таблицы mapping, без SELECT на `Lessons` и без UPDATE в источнике. `alfa_kb_writer`: SELECT/INSERT/UPDATE к `kb`, без USAGE к `app` и без SELECT на `app.users`. CLI-import успешно прошёл под этими ролями.
- Демо прогнано только локально: `retrieve(..., external_context=False)`. Внешняя LLM передача не запускалась, так как ни для одной записи нет подтверждённого допуска.
- Рабочая `assistant_db` в этом запуске недоступна: DSN отсутствует в окружении, а PostgreSQL на стандартном локальном адресе не слушает. Поэтому тестовый run выше не является рабочим, активный run production-БД и публикация не подтверждены. После настройки credentials штатная команда публикации: `ALFA_SOURCE_DSN='...' ALFA_DATABASE_URL='...' ALFA_TARGET_DATABASE=assistant_db HF_HOME=models/huggingface ALFA_EMBEDDING_REVISION=local-cache .venv/bin/alfa-kurs-ingest`.
- Совместимый контракт для Python #2: передавать `source=result.evidence[0].source` (или `None`, если evidence пустой) в необязательный keyword-only `recommend(..., source=None)`. Старые вызовы остаются рабочими. В найденных чатах Codex отдельный активный поток Python #2 не обозначен, поэтому непосредственное подтверждение со стороны Python #2 не получено.

Повторяемые команды и приёмочные проверки описаны в README и `scripts/acceptance_check.py`. Предыдущая контрольная версия 10 статей/184 чанка и приёмка расширения 10+2 сохранены ниже как исторические результаты, а не как состояние нового тестового run.

## Исторический baseline (10 статей/184 чанка; 11 ноября)

### Среда и выбор схемы

- Дамп — PostgreSQL custom archive, создан PostgreSQL 16.15; TOC содержит 412 объектов.
- Дамп восстановлен в отдельную временную БД `alfa_source` на PostgreSQL 18.6. Рабочую БД не смешивали с исходной.
- Приложенная документация сообщает о 62 таблицах и 102 128 записях.
- В проекте до этой реализации кода/миграций не было. Используем `kb` из `План/alfa_rag_team_package/schema.sql` с `ingestion_runs`, `index_state`, `materials`, `chunks` и `vector(384)`. Упрощённая схема из `alfa_bot_team_plan.html` (6 таблиц, `init.sql`, очистка таблиц при импорте) с версионированной схемой `kb` расходится; её здесь не применяем.

### DDL и условия допуска

Проверенные ключи и связи:

- `Articles.Id` — PK.
- `Articles.StatusId` → `Statuses.Id`.
- `ArticleEmbedItems.Id` — PK.
- `ArticleEmbedItems.ArticleId` → `Articles.Id` с `ON DELETE CASCADE`.
- Порядок текстовых блоков — `ArticleEmbedItems.Order`; ties стабилизируются по `Id`.
- В выбранных записях содержательные строки находятся в `ArticleEmbedItems.Type = 0`; у блоков других типов `HtmlContent` пустой. У десяти выбранных материалов нет пустых `Order` или дублированных блоков.
- `Articles.Url` хранит slug, а не полный URL. Canonical URL для каждого кандидата сверялся с публичной страницей на `kurs.alfabank.ru`.

`Statuses` содержит значения «Опубликовано», «Черновик», «Архив», «Снято с публикации», а также состояния прохождения курса. Допускается только точное значение «Опубликовано» вместе с `IsPublic=true`, `IsClosed=false`, `IsLesson=false`, `IsDeleted=false`, `NoIndex=false` и непустым `Published`. `IsPublic` само по себе недостаточно.

Курсы в первый индекс не включались: `CourseTypes` содержит «Открытый», «Платный», «Закрытый» и «По подписке», но это не подтверждает доступность текста уроков. `Lessons`, `LessonEmbedItems`, таблицы пользователей и активности запросом не читаются. Тарифы, цены, рейтинги и пользовательские binding-таблицы не используются.

Для выбранных публичных статей установлена проекция `access_type=free`, `answer_allowed=true`. Отдельное право передачи текста внешнему LLM не подтверждалось, поэтому `external_allowed=false`; поиск с `external_context=True` не покажет эти записи. Для локального поиска/тестового вызова используется `external_context=False`.

### Включённые записи

Для каждой строки сайт Альфа-Курс отдал результат поиска с canonical URL и содержимым страницы; дата последнего crawl была от текущего дня до предыдущего месяца. Прямой fetch через инструмент `open` завершился 502/timeout, а `curl` не смог проверить TLS-цепочку (локальная среда вернула self-signed certificate). Поэтому HTTP-статус origin не заявляется. URL и публичная доступность подтверждены недавними результатами поиска официального домена; если последующая проверка из доверенной сети не подтвердит страницу, запись нужно убрать из allowlist.

| Source ID | Название | URL | Статус/флаги | Блоки |
|---|---|---|---|---:|
| `f70c43ee-bc3d-48d0-bf24-abdeca927b48` | Восемь шагов для развития личного бренда | [ссылка](https://kurs.alfabank.ru/articles/vosem-shagov-dlya-razvitiya-lichnogo-brenda/) | опубликована, public, open | 7 |
| `4edc4abf-1f3c-41db-be25-25dfae8486b0` | Исследование покупателей для производителей и оптовиков | [ссылка](https://kurs.alfabank.ru/articles/issledovanie-pokupatelej-dlya-proizvoditelej-i-optovikov/) | опубликована, public, open | 23 |
| `5a5d6364-9ffc-465f-aa4f-3401aeb3e877` | Как бизнесу меньше зависеть от курса валют | [ссылка](https://kurs.alfabank.ru/articles/kak-biznesu-menshe-zaviset-ot-kursa-valyut/) | опубликована, public, open | 18 |
| `a0de1a97-1261-49bd-89e1-ac3bba5c530f` | Как использовать теорию поколений в маркетинге | [ссылка](https://kurs.alfabank.ru/articles/kak-ispolzovat-teoriyu-pokolenij-v-marketinge/) | опубликована, public, open | 19 |
| `f079c693-aafb-46f3-987b-6d071cc5c1f1` | Как преодолеть спад продаж после Нового года | [ссылка](https://kurs.alfabank.ru/articles/kak-preodolet-spad-prodazh-posle-novogo-goda/) | опубликована, public, open | 48 |
| `d1e67c81-f364-4724-bfac-d02293e87cb8` | Как собрать целевую аудиторию: открываем сезон охоты на клиентов | [ссылка](https://kurs.alfabank.ru/articles/kak-sobrat-celevuyu-auditoriyu-otkryvaem-sezon-ohoty-na-klientov/) | опубликована, public, open | 83 |
| `28c8ec93-d65b-4382-81f2-183915480f6b` | Кризисы роста компании и как их преодолеть | [ссылка](https://kurs.alfabank.ru/articles/krizisy-rosta-kompanii-i-kak-ih-preodolet/) | опубликована, public, open | 15 |
| `7ed5b9e2-d183-4440-b467-f6ce6fcea1be` | Постоплата товаров и услуг: что это и как с ней работать | [ссылка](https://kurs.alfabank.ru/articles/postoplata-tovarov-i-uslug-chto-ehto-i-kak-s-nej-rabotat/) | опубликована, public, open | 6 |
| `2cf5e7cc-6abb-47b5-afc9-ab672a641b99` | Как маркировать товары на Wildberries | [ссылка](https://kurs.alfabank.ru/articles/kak-markirovat-tovary-na-wildberries/) | опубликована, public, open | 30 |
| `2586489a-b699-493c-911d-030e43fbc4f1` | Как маркировать товары на Ozon | [ссылка](https://kurs.alfabank.ru/articles/kak-markirovat-tovary-na-ozon/) | опубликована, public, open | 30 |

### Результат фактического прогона

`src/alfa_bot/knowledge/first_pass_sources.py` содержит эти десять ключей, SQL mapping и точные URL. Запрос ограничен этим allowlist, статусом, флагами доступа и текстовым `Type=0`; extractor дополнительно проверяет соответствие URL allowlist и согласованность блоков. В память embeddings передаются только эти публичные материалы.

- Source extractor в read-only транзакции: **279 строк блоков**, **10 материалов**, **0 исключений**.
- Подключение к `alfa_source` выполнено ролью `alfa_source_ro`: она имеет `SELECT` только на `Articles`, `Statuses`, `ArticleEmbedItems`, не имеет `INSERT/UPDATE` и не имеет даже `SELECT` на `Lessons`.
- Рабочая роль `alfa_kb_writer` имеет `SELECT/INSERT/UPDATE` в `kb`, но не имеет `USAGE` на `app` и не читает `app.users`.
- Импорт `intfloat/multilingual-e5-small`, revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`: run `0f5d325f-5289-4bc2-9c90-7725f5fb05eb`, статус `ready`.
- В активном run: **10 материалов**, **184 чанка**, в каждом есть lineage на исходный блок; max `token_count=400`, over-limit=0, размерность векторов ровно **384**.
- Повторный полный импорт дал тот же run ID; новых `materials` и `chunks` не появилось.
- Отказ публикации проверен отдельным синтетическим run с намеренно неверным `kind`: PostgreSQL `CheckViolation`, run отмечен `failed`, частичных материалов нет, прежний `active_run_id` остался тем же.
- Retrieval вызывается с `external_context=False` в локальном контуре. Для default `external_context=True` эти материалы намеренно не выдаются (0 результатов), пока команда отдельно не разрешит внешнюю передачу текста.

### Короткие вопросы и фактический top-1

`distance` — cosine distance. Для всех пяти вопросов ожидаемый ID совпал с top-1; top-3 возвращал три разных источника.

| Вопрос | Ожидаемый source ID | Найденный top-1 | Distance |
|---|---|---|---:|
| Как собрать и проанализировать целевую аудиторию? | `d1e67c81-f364-4724-bfac-d02293e87cb8` | `d1e67c81-f364-4724-bfac-d02293e87cb8` | 0.097841 |
| Что такое постоплата и чем она отличается от предоплаты? | `7ed5b9e2-d183-4440-b467-f6ce6fcea1be` | `7ed5b9e2-d183-4440-b467-f6ce6fcea1be` | 0.064926 |
| Как маркировать товары на Wildberries? | `2cf5e7cc-6abb-47b5-afc9-ab672a641b99` | `2cf5e7cc-6abb-47b5-afc9-ab672a641b99` | 0.058443 |
| Что помогает развивать личный бренд предпринимателя? | `f70c43ee-bc3d-48d0-bf24-abdeca927b48` | `f70c43ee-bc3d-48d0-bf24-abdeca927b48` | 0.093240 |
| Как производителю изучить покупателей и понять их выбор? | `4edc4abf-1f3c-41db-be25-25dfae8486b0` | `4edc4abf-1f3c-41db-be25-25dfae8486b0` | 0.106934 |

Чанкер также проверен отдельно на synthetic tokenizer: длинный HTML разбился по смысловым границам, IDs блоков сохранились, каждый чанк укладывался в тестовый лимит; короткий разрешённый материал остаётся целым, запрещённый не индексируется. Эти проверки не подменяют реальные E5/pgvector результаты выше.

На настоящем tokenizer E5 отдельно обработан искусственный длинный HTML: **16 чанков**, максимальный полный embedding input — **499 токенов** при жёстком лимите 512, вектор — 384 числа; lineage сохранился. Предварительный подсчёт длинного текста использует fast tokenizer напрямую, поэтому не передаёт длинный input модели и не вызывает предупреждение о выходе за её максимальный размер.
