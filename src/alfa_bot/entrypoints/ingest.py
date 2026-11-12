import argparse
import sys


def main() -> None:
    if "--check-config" not in sys.argv[1:]:
        from alfa_bot.knowledge.cli import main as ingest

        ingest()
        return
    parser = argparse.ArgumentParser(description="Подготовка знаний: точка расширения Python 1")
    parser.add_argument("--check-config", action="store_true", help="Проверить наличие source URL")
    args = parser.parse_args()
    if args.check_config:
        from alfa_bot.infra.config import Settings

        settings = Settings()
        if settings.source_database_url is None:
            parser.error("Заполните SOURCE_DATABASE_URL отдельной ролью только SELECT")
        print("Конфигурация source задана; соединение и права не проверялись.")
        return
    parser.exit(2, "Ingestion ещё не реализован: knowledge/ingestion.py, владелец Python 1.\n")


if __name__ == "__main__":
    main()
