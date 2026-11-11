def parse_material(blocks: list[dict]) -> str:
    """Python 1: подтверждённый Order, обработчики Type, HTML и NBSP.

    Сохранять структуру заголовков/списков; избегать двойного Content/EmbedItems.
    Произвольные ссылки из HTML автоматически не загружать.
    """
    raise NotImplementedError("Проверить типы исходных блоков и реализовать очистку")
