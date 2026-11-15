"""Check configuration, migrations, index identity and optionally the local model."""

import argparse

from alfa_bot.config import load_config
from alfa_bot.db import Database
from alfa_bot.knowledge.configured_embeddings import ConfiguredEmbedder
from alfa_bot.main import require_index
from alfa_bot.runtime import configure_runtime, require_migrations, run_cli


async def check(warm_model=False):
    settings = load_config()
    settings.validate_runtime()
    database = Database(settings.database_url)
    try:
        await require_migrations(database)
        await require_index(database, settings)
        print("Configuration, migrations and knowledge index: OK")
        if warm_model:
            model = ConfiguredEmbedder(settings)
            signature = await model.local.signature()
            if signature.revision != settings.embedding_revision:
                raise RuntimeError("Loaded model revision differs from the prepared index")
            print(f"Local model: {signature.model}, dimension={signature.dimension}: OK")
    finally:
        await database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warm-model", action="store_true")
    args = parser.parse_args()
    configure_runtime()
    run_cli(check(args.warm_model), operation="check")


if __name__ == "__main__":
    main()
