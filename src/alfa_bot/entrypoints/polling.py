import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальный Telegram polling: точка расширения")
    parser.parse_args()
    # Inbox.accept должен завершить COMMIT до подтверждения update через offset.
    # Одновременно с webhook polling не включать.
    parser.exit(2, "Polling ещё не реализован: adapters/telegram.py и infra/inbox.py.\n")


if __name__ == "__main__":
    main()
