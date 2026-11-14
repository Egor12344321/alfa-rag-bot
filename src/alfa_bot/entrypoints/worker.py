import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Reminder scheduler и sender: точка расширения")
    parser.parse_args()
    # После реализации: build_services -> reminders.enqueue_due -> outbox.deliver_pending.
    # Сначала восстановить expired lease, затем короткий цикл с graceful shutdown.
    parser.exit(2, "Worker ещё не реализован: reminders/service.py и infra/outbox.py.\n")


if __name__ == "__main__":
    main()
