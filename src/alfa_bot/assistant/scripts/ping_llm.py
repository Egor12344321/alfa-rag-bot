import asyncio
import os

from langchain_openai import ChatOpenAI


async def main():
    chat = ChatOpenAI(
        model=os.environ["LLM_MODEL"],
        temperature=0,
    )
    reply = await chat.ainvoke("Ответь одним словом: работает?")
    print(reply.content)


if __name__ == "__main__":
    asyncio.run(main())
