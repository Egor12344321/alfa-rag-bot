class IngestionService:
    """Python 1: SELECT source -> policy -> parsing -> chunks -> vectors -> ready run.

    Доступ к source отдельной ролью только чтения. Сначала building run;
    переключать active_run_id только после проверок. Полный закрытый контент
    не передавать embedding-модели и не сохранять в рабочие chunks.
    """

    async def run(self) -> str:
        raise NotImplementedError("Python 1: реализовать pipeline подготовки данных")
