# Data-модуль «Альфа-Курс»

Этот каталог сохраняет самостоятельную версию реализации Python 1; встроенная
версия находится в `src/alfa_bot/knowledge` в корне репозитория. Все команды ниже
выполняются из `contrib/data-module` в отдельном виртуальном окружении. Не
устанавливайте этот пакет в окружение основного приложения: оба пакета содержат
`alfa_bot`. Текущая проверка готовности API не подтверждает работу бота целиком.

`schema.sql` здесь — схема, применённая при отдельной проверке модуля. Она
отличается от схемы каркаса ограничением карточек. Не применяйте этот файл
поверх Alembic-миграции проекта; согласуйте изменение через новую миграцию.

Модуль строит версионируемый индекс `assistant_db.kb`: разрешённые фрагменты для ответов (`purpose=knowledge`) и отдельно короткие карточки для подбора (`purpose=catalog`). Поиск использует `multilingual-e5-small` и pgvector. Исходный дамп читается только read-only; полные тексты уроков и пользовательские данные не импортируются.

Состояние проверки на 12 ноября 2026: расширенный корпус принят на отдельной тестовой БД — 19 материалов и 261 чанк (`knowledge-v4`, ревизия E5 `614241f622f53c4eeff9890bdc4f31cfecc418b3`). Публикация в рабочую `assistant_db` этой сессией не подтверждена: в окружении отсутствуют `ALFA_SOURCE_DSN` и `ALFA_DATABASE_URL`, локальный PostgreSQL недоступен. Не считайте тестовый run активным run рабочей БД; текущий статус и продолжение описаны в `docs/data_import_report.md`.

## Контракт модулей

- Импорт отдаёт `MaterialInput` с исходной таблицей и ID, заголовком, подтверждённой ссылкой и упорядоченными `SourceBlock`.
- Чанкер возвращает `ChunkInput`, сохраняя ID блоков в `source_blocks`; embedding-вход включает `passage:` и заголовок и укладывается в лимит 512 токенов.
- `KnowledgeService.retrieve(query, *, limit=6, run_id=None, external_context=True)` реализует интерфейс из архитектурного пакета. Результат содержит `run_id` и `Evidence` с текстом, названием, исходным ключом, URL и cosine distance.
- `KnowledgeService.recommend(query, profile, *, run_id, exclude=(), limit=1, source=None)` возвращает только разрешённые карточки из того же run; при `source` сначала ранжирует подходящие направленные связи `RecommendedItems`, затем использует векторный подбор. `Profile.free_only=True` жёстко оставляет материалы с подтверждённым `access_type='free'`.
- `KnowledgeService.get_card(source, *, run_id=None)` разрешает только активную карточку с разрешением на каталог и подтверждённым HTTPS URL.
- Импорт создаёт отдельный `ingestion_run`, а активным делает его только после успешной записи. Идентичный повтор для активной версии переиспользует существующий run.
- Run сохраняет immutable SHA загруженной модели. `local-cache` — только запрос к локальному кэшу; это значение никогда не записывается вместо revision модели.

## Подготовка PostgreSQL

Установите PostgreSQL с pgvector и создайте две отдельные базы: восстановленную `alfa_source` и рабочую `assistant_db`. Не восстанавливайте дамп поверх рабочей базы.

```sh
createdb alfa_source
pg_restore --no-owner --no-privileges --dbname=alfa_source alfa_course_hackathon.backup
createdb assistant_db
psql "$ALFA_DATABASE_URL" -v ON_ERROR_STOP=1 -f schema.sql
```

`ALFA_DATABASE_URL` должен указывать на `assistant_db`; схема включает `kb` и `app`. Для отдельной проверки применён контракт с `ingestion_runs`, `index_state`, `materials` и `chunks`.

Для минимальных прав ingestion-роль получает `SELECT` только к таблицам mapping. Документы и глоссарий читаются только для выбранных ID с отдельно проверенными официальными страницами; `Documents` и `Webinars` также используются для проверки типа/наличия целей рекомендательных связей. Полные тексты уроков и данные пользователей не читаются. Рабочая роль получает DML только к `kb`:

```sql
-- выполнить владельцем/администратором исходной БД
CREATE ROLE alfa_source_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT CONNECT ON DATABASE alfa_source TO alfa_source_ro;
GRANT USAGE ON SCHEMA public TO alfa_source_ro;
GRANT SELECT ON TABLE
  public."Articles", public."Statuses", public."ArticleEmbedItems",
  public."Courses", public."CourseTypes", public."CourseTariffBindings",
  public."ContentSettings", public."ContentSettingsTopics", public."Topics",
  public."RecommendedItems", public."Documents", public."DocumentEmbedItems",
  public."Glossary", public."GlossaryEmbedItems", public."Webinars"
TO alfa_source_ro;

-- выполнить владельцем/администратором assistant_db после schema.sql
CREATE ROLE alfa_kb_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT CONNECT ON DATABASE assistant_db TO alfa_kb_writer;
GRANT USAGE ON SCHEMA kb, public TO alfa_kb_writer;
GRANT USAGE ON TYPE public.vector TO alfa_kb_writer;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA kb TO alfa_kb_writer;
```

Задайте credentials через защищённое окружение или `.pgpass`, не вставляйте пароли в репозиторий.

Установите Python 3.11+ и зависимости проекта:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Для первого запуска без локальной копии модели задайте `HF_HOME=models/huggingface` и `ALFA_ALLOW_MODEL_DOWNLOAD=1`; после загрузки уберите `ALFA_ALLOW_MODEL_DOWNLOAD`, чтобы импорт использовал только кэш. `ALFA_EMBEDDING_REVISION` принимает commit или `local-cache`. В режиме `local-cache` loader сначала разрешает cache ref в snapshot SHA, затем этим snapshot загружает модель и токенизатор; в `kb.ingestion_runs` сохраняется полученный SHA.

## Подтверждение карты исходных данных

Текущий CLI использует `alfa_bot.knowledge.expanded_sources`: 12 статей, 5 карточек открытых курсов, один документ и один термин. `Documents` и `Glossary` не содержат `IsPublic`, а `Url` хранит slug. Поэтому доступ подтверждён поштучно результатом официального crawl/search, точный URL сверён со slug и canonical-страницей; непроверенные записи не допускаются только по статусу. `DocumentEmbedItems` и `GlossaryEmbedItems` задают исходные ID, порядок и HTML-блоки; короткое определение остаётся целым после очистки и чанкинга. Курс импортируется только как `Courses.Description`; уроки не читаются. Кастомная функция `build_mapping()` должна вернуть `SourceMapping` из `alfa_bot.knowledge.source` с:

- одним проверенным `SELECT`, который возвращает по строке на блок с колонками контракта;
- явным `read_tables` — только реально используемые таблицы, без уроков и активности пользователей;
- ограниченным allowlist `SourceKey`, подтверждёнными статусом, уровнем доступа и точным HTTPS URL;
- раздельными `answer_sources` и `recommend_sources`; доступ к каталогу не разрешает использовать полный текст как основание ответа;
- цену только из однозначного подтверждённого тарифа; `NULL` и `0` не переводятся в «бесплатно»;
- `external_approved` только если подтверждено право передавать полный текст внешней модели; иначе оставить пустым.

Сам extractor выполняет только read-only транзакцию и повторно проверяет статус, тип доступа, ссылку, согласованность полей и отсутствие неоднозначных/дублированных блоков. Результат официального crawl подтверждает публичный доступ к конкретной странице, но не передачу её текста внешней модели: для текущего корпуса `external_allowed=false`, поэтому демо и поиск проходят локально с `external_context=False`. Если новый источник не проверен, не добавляйте его в allowlist: importer завершится без публикации run, если не прошёл ни один материал.

## Импорт

Скопируйте DSN в переменные окружения; секреты не храните в репозитории. Перед импортом рабочая схема должна быть применена, а source DB открыта read-only ролью.

```sh
export ALFA_SOURCE_DSN='host=localhost dbname=alfa_source user=readonly'
export ALFA_DATABASE_URL='postgresql://.../assistant_db'
export ALFA_EMBEDDING_REVISION='614241f622f53c4eeff9890bdc4f31cfecc418b3'
export ALFA_PIPELINE_VERSION='knowledge-v4'
export HF_HOME='models/huggingface'
# Только если модель ещё не загружена:
# export ALFA_ALLOW_MODEL_DOWNLOAD=1
ALFA_TARGET_DATABASE='assistant_db' .venv/bin/alfa-kurs-ingest
```

Перед запуском подключитесь к обеим БД и подтвердите `current_database()`, роль, доступность активного run и ACL. По прежней проверке `alfa_source_ro` имела `SELECT` на `Articles`, `Statuses` и `ArticleEmbedItems`; для расширенного mapping проверяйте и выдавайте только недостающие права из списка в отчёте. Не выдавайте права на `Lessons`.

Локальная mapping-конфигурация и приватные промежуточные данные размещаются в `private_data/`, исключённой из git и Docker build context. Первый run публикуется только если каждый включённый источник прошёл явную политику допуска и у него есть хотя бы один непустой фрагмент.

## Вызов трёх методов

```python
import asyncio
import os

from psycopg_pool import AsyncConnectionPool
from alfa_bot.knowledge.contracts import Profile, SourceKey
from alfa_bot.knowledge.embeddings import E5Embedder
from alfa_bot.knowledge.repository import KnowledgeRepository
from alfa_bot.knowledge.service import KnowledgeService


async def main():
    async with AsyncConnectionPool(os.environ["ALFA_DATABASE_URL"]) as pool:
        knowledge = KnowledgeService(KnowledgeRepository(pool), E5Embedder())
        result = await knowledge.retrieve(
            "Как подготовить бизнес-план?", limit=3, external_context=False
        )
        run_id = result.run_id
        for item in result.evidence:
            print(item.text, item.title, item.source, item.url, item.distance)

        answer_source = result.evidence[0].source if result.evidence else None
        cards = await knowledge.recommend(
            "С чего начать бизнес?",
            Profile(industry="предпринимательство", free_only=True),
            run_id=run_id,
            exclude=(SourceKey("Courses", "already-shown-course-id"),),
            limit=2,
            source=answer_source,
        )
        for card in cards:
            print(card.title, card.access_type, card.price, card.url)

        card = await knowledge.get_card(
            SourceKey("Courses", "39d56e76-2da6-4ac4-8c57-1e7ba2b856c7"),
            run_id=run_id,
        )
        print(card.title if card else "Карточка недоступна")


asyncio.run(main())
```

`distance` — cosine distance, не вероятность правильного ответа. `retrieve()` ищет только `purpose='knowledge'`, `answer_allowed=true` и бесплатные записи; `external_context=True` дополнительно требует `external_allowed`. Для Python #2 исходный ключ передаётся как `source=result.evidence[0].source` (или `None`, если evidence пустой); существующие вызовы `recommend()` без этого аргумента совместимы. Внутри выбранного `run_id` подходящие связанные карточки идут первыми, после фильтров активности, допуска, `free_only` и `exclude`; если связи нет или все связанные карточки недоступны, остаётся прежнее векторное ранжирование. Каталог не появляется в `retrieve()`. Все три метода могут получить один и тот же зафиксированный `run_id`. Несовпадение модели или immutable revision с run вызывает понятную ошибку до вычисления расстояний.

В Data-репозитории нет точки входа бота или генератора ответа Python #2. Пример выше фиксирует контракт интеграции: брать `run_id` из `retrieve()`, а `source` — из фрагмента, на котором основан ответ; при пустом `evidence` передавать `None`. Сам сценарий ответа со ссылкой и последующей рекомендацией требует запуска в проекте бота.

## Проверка

Приёмочный сценарий запускается только на отдельной тестовой БД после импорта. Он проверяет пять прежних top-1, поиск по документу и термину, выбор связанного курса, фильтрацию исключённой и недоступной карточки, отсутствие связи с fallback на векторный подбор, `free_only`, `get_card`, изоляцию `retrieve()`, ревизию E5 и сохранение активного run при ошибке записи:

```sh
ALFA_DATABASE_URL='postgresql://.../assistant_db_test' \
HF_HOME='models/huggingface' \
ALFA_EMBEDDING_REVISION='local-cache' \
.venv/bin/python scripts/acceptance_check.py
```

Результаты фактического прогона и исключения по корпусу записываются в `docs/data_import_report.md`. Для новой отдельной тестовой БД используйте `alfa_source` и `assistant_db` из примера выше; не запускайте восстановление дампа поверх рабочей базы.
