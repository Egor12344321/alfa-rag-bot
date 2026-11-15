"""Use the configured local model with the contribution's synchronous API."""

from alfa_bot.knowledge.local_embeddings import LocalEmbeddings


class ConfiguredEmbedder:
    def __init__(self, settings):
        self.settings = settings
        self.local = LocalEmbeddings(settings)
        self.model_name = settings.embedding_model

    @property
    def resolved_revision(self):
        return self.local._get_signature().revision

    @property
    def tokenizer(self):
        return self.local._get_tokenizer()

    def encode_query(self, text):
        # Called in asyncio.to_thread by KnowledgeService.
        return self.local._encode([text], query=True)[0]

    def encode_passage_batch(self, items):
        return self.local._encode([f"{title}\n{chunk.text}" for title, chunk in items], query=False)
