"""
This module contains three different classes: SearchResult, MemoryManager, and SearchManager.
Each of these classes serve a distinct purpose. For instance, the SearchResult object is returned from SearchManager.search().
While MemoryManager is used within the AI chat process in order to give the LLM memory.
"""

import os
import sys
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

import lancedb
from lancedb.rerankers import LinearCombinationReranker
from langchain.tools import tool
from langchain_community.chat_models import ChatLlamaCpp
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.embeddings import LlamaCppEmbeddings
from langchain_community.vectorstores import LanceDB
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from llama_cpp import llama_n_ctx_train
from semchunk import chunkerify

from catalog_manager import Book, CatalogManager


@dataclass
class SearchResult(Book):
    page: int
    page_content: str

    def get_book(self):
        items = asdict(self)
        del items["page"]
        del items["page_content"]
        return Book(**items)


class MemoryManager:

    def __init__(self, get_len_tokens, max_tokens=2048):
        self.get_len_tokens = get_len_tokens  # is a lambda function
        self.max_tokens = max_tokens
        self.memory = []

    def token_count(self):
        total_count = 0
        for msg in self.memory:
            total_count += self.get_len_tokens(msg.content)
        return total_count

    def limit(self):
        """Keeps the total token length under a certain limit."""
        count = self.token_count()
        if count > self.max_tokens and len(self.memory) > 1:
            index = 1 if type(self.memory[0]) is SystemMessage else 0
            self.memory.pop(index)
            self.limit()

    def add(self, message):
        self.limit()
        self.memory.append(message)

    def clear(self):
        self.memory.clear()

    def get(self, index: int = None):
        if index:
            return self.memory[index]
        else:
            return self.memory


class SearchManager:

    def __init__(
        self,
        path: str,
        embed_path: str,
        chat_path: str,
        system_prompt: str,
        catalog_manager: CatalogManager,
    ):
        sys.stderr = open(os.devnull, "w")  # so prompt_toolkit can work
        warnings.simplefilter("ignore")

        self.embed_path = embed_path
        self.chat_path = chat_path
        self.catalog_manager = catalog_manager

        self.create_embedding_client()

        self.db = lancedb.connect(f"{path}/vectordb")
        self.create_vector_store()

        self.chat_model = None
        self.tools = None
        self.tool_names = None
        self.get_len_tokens = lambda text: len(
            self.embed_model.client.tokenize(text.encode("utf-8"), False)
        )
        self.memory = MemoryManager(self.get_len_tokens)
        self.memory.add(SystemMessage(content=system_prompt))

    def create_vector_store(self, mode="append"):
        self.vector_store = LanceDB(
            connection=self.db,
            embedding=self.embed_model,
            mode="append",
            reranker=LinearCombinationReranker(),
        )

    def close(self):
        self.embed_model.client.close()
        if self.chat_model:
            self.chat_model.client.close()

    def refresh(self):
        """Refreshes the entire vector database (maybe the embed model changed)."""
        self.create_embedding_client()
        self.create_vector_store(mode="overwrite")

    def is_database_mismatch(self, is_ignored) -> bool:
        if not "vectorstore" in list(self.db.table_names()):
            return any(not is_ignored(book) for book in self.catalog_manager.books)

        tbl = self.db.open_table("vectorstore")
        return any(
            book.filename.lower().endswith(".pdf")
            and not self.exists(book, tbl)
            and not is_ignored(book)
            for book in self.catalog_manager.books
        )

    def exists(self, book: Book, tbl) -> bool:
        return tbl.count_rows(filter=f"`metadata`.`id` = '{book.id}'") > 0

    def index(self):
        rows = self.db.open_table("vectorstore").count_rows()
        if rows >= 5000:
            self.vector_store.create_index(vector_col="vector")
            return True
        else:
            return False

    def add(self, path: Path, id: str):
        if not path.name.endswith(".pdf"):
            return

        n_ctx = 256
        if llama_n_ctx_train(self.embed_model.client.model) < n_ctx:
            raise ValueError(f"{self.embed_path} has a context length below {n_ctx}")
        chunker = chunkerify(
            self.get_len_tokens,
            n_ctx,
        )

        loader = PyPDFLoader(path)
        docs = loader.load()
        documents = []
        for i, doc in enumerate(docs):
            text = doc.page_content
            clean_content = " ".join(text.split())
            chunks = chunker(
                clean_content, overlap=0.10
            )  # overlap should be 10%-20% max
            for chunk in chunks:
                documents.append(
                    Document(page_content=chunk, metadata={"id": id, "page": i + 1})
                )
        if documents:
            self.vector_store.add_documents(documents)
        else:
            raise ValueError(f"could not extract text from '{path}'")

    def remove(self, id: str):
        self.vector_store.delete(filter=f"`metadata`.`id` = {id}")

    def search(self, query: str, k: int = 4) -> list[SearchResult]:
        results = self.vector_store.similarity_search_with_relevance_scores(query, k)
        filtered_results = [
            result for result, score in results if score < 0 or score > 0.4
        ]
        meta_results = self.get_meta_results(filtered_results)
        return meta_results

    def get_meta_results(self, results: list[Document]) -> list[SearchResult]:
        return [
            SearchResult(
                page=result.metadata["page"],
                page_content=result.page_content,
                **asdict(self.catalog_manager.get(result.metadata["id"])),
            )
            for result in results
        ]

    def create_embedding_client(self):
        self.embed_model = LlamaCppEmbeddings(
            model_path=self.embed_path,
            n_ctx=0,
            f16_kv=True,
        )

    def initialize_model(self):
        if self.chat_model is None:
            n_ctx = 4096  # original ChatGPT context length
            self.chat_model = ChatLlamaCpp(
                model_path=self.chat_path,
                n_ctx=n_ctx,
                max_tokens=n_ctx / 2,
                n_gpu_layers=99,
                f16_kv=True,
            )

            if llama_n_ctx_train(self.chat_model.client.model) < n_ctx:
                raise ValueError(
                    f"{self.chat_path} has a context length less then {n_ctx}"
                )

    @property
    def rich_search(self):
        @tool(parse_docstring=True)
        def search(query: str) -> str:
            """Performs a book database search based on the query.

            Args:
                query: Keep the query limited to keywords for best results.
            """
            results = self.search(query)
            return str([asdict(result) for result in results])

        return search

    def parse_tool_calls(self, tool_calls: dict, tool_names: dict):
        tool_results = []
        for tool_call in tool_calls:
            name = tool_call["name"]
            if name in tool_names:
                args = tool_call["args"]
                tool_results.append(tool_names[name].invoke(input=args))
        return tool_results

    def question(
        self,
        query: str,
    ):
        # Dynamic loading of the model
        self.initialize_model()

        # Search via keywords
        tool_caller = self.chat_model.bind_tools(
            tools=[self.rich_search],
            tool_choice={"type": "function", "function": {"name": "search"}},
        )
        output = tool_caller.invoke(query).model_dump()
        tool_result = self.parse_tool_calls(
            tool_calls=output["tool_calls"],
            tool_names={
                "search": self.rich_search,
            },
        )[0]

        # Get the output
        self.memory.add(HumanMessage(content=f"User query:\n{query}"))
        self.memory.add(
            HumanMessage(content=f"Database search results:\n{tool_result}")
        )
        output = self.chat_model.invoke(self.memory.get()).model_dump()
        self.memory.add(AIMessage(content=output["content"]))
        return self.memory.get(-1).content
