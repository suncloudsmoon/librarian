"""
This module contains three different classes: SearchResult, MemoryManager, and SearchManager.
Each of these classes serve a distinct purpose. For instance, the SearchResult object is returned from SearchManager.search().
While MemoryManager is used within the AI chat process in order to give the LLM memory.
"""

import json
import logging
import os
import re
import sys
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

import lancedb
from lancedb.rerankers import LinearCombinationReranker
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.vectorstores import LanceDB
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from openai import OpenAI
from semchunk import chunkerify

from .catalog_manager import Book, CatalogManager

if sys.platform == "win32":
    from foundry_local import FoundryLocalManager

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
        self.memory: list[dict] = []

    def token_count(self):
        total_count = 0
        for msg in self.memory:
            total_count += self.get_len_tokens(msg["content"])
        return total_count

    def limit(self):
        """Keeps the total token length under a certain limit."""
        count = self.token_count()
        if count > self.max_tokens and len(self.memory) > 1:
            index = 1 if self.memory[0]["role"] == "system" else 0
            self.memory.pop(index)
            self.limit()

    def add(self, message: dict):
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
        path: Path,
        embed_path: str,
        chat_model: str,
        system_prompt: str,
        catalog_manager: CatalogManager,
    ):
        sys.stderr = open(os.devnull, "w")  # so prompt_toolkit can work
        warnings.simplefilter("ignore")

        self.embed_path = embed_path
        self.chat_model = chat_model
        self.catalog_manager = catalog_manager

        self.create_embedding_client()

        self.db = lancedb.connect(path / "vectordb")
        self.create_vector_store()

        self.foundry_local = None
        self.chat_client = None

        logging.getLogger("transformers.tokenization_utils_base").setLevel(
            logging.ERROR
        )
        self.get_len_tokens = lambda text: len(
            self.embed_model._client.tokenizer.tokenize(text)
        )
        self.memory = MemoryManager(self.get_len_tokens)
        self.memory.add({"role": "system", "content": system_prompt})

    def create_vector_store(self, mode="append"):
        self.vector_store = LanceDB(
            connection=self.db,
            embedding=self.embed_model,
            mode="append",
            reranker=LinearCombinationReranker(),
        )

    def create_embedding_client(self):
        self.embed_model = HuggingFaceEmbeddings(model_name=self.embed_path)

    def close(self):
        if self.chat_client:
            self.chat_client.client.close()

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
        if self.embed_model._client.tokenizer.model_max_length < n_ctx:
            raise ValueError(f"{self.embed_path} has a context length below {n_ctx}")
        chunker = chunkerify(
            self.get_len_tokens,
            n_ctx,
        )

        loader = PyPDFLoader(path)
        docs = loader.load()
        documents = []
        for index, doc in enumerate(docs):
            text = doc.page_content
            clean_content = " ".join(text.split())
            chunks = chunker(
                clean_content, overlap=0.10
            )  # overlap should be 10%-20% max
            for chunk in chunks:
                documents.append(
                    Document(page_content=chunk, metadata={"id": id, "page": index + 1})
                )
        if documents:
            self.vector_store.add_documents(documents)
        else:
            raise ValueError(f"could not extract text from '{path}'")

    def remove(self, id: str):
        self.vector_store.delete(filter=f"`metadata`.`id` = '{id}'")

    def search_query(self, query: str, k: int = 4) -> list[SearchResult]:
        results = self.vector_store.similarity_search_with_relevance_scores(query, k)
        filtered_results = [result for result, score in results if score > 0.1]
        meta_results = self.get_meta_results(filtered_results)
        return meta_results

    def get_meta_results(self, results: list[Document]) -> list[SearchResult]:
        return [
            SearchResult(
                page=result.metadata["page"],
                page_content=result.page_content,
                **asdict(self.catalog_manager.get(result.metadata["id"])[0]),
            )
            for result in results
        ]

    def search(self, query: str) -> str:
        results = self.search_query(query)
        return str([asdict(result) for result in results])

    def parse_function_calls(self, function_calls: dict, tools: dict):
        results = []
        for function_call in function_calls:
            name = function_call["name"]
            arguments = function_call["arguments"]
            if name in tools:
                results.append(tools[name](**arguments))
        return results

    def question(
        self,
        query: str,
    ):
        # Dynamic loading of the model
        if self.foundry_local is None:
            self.foundry_local = FoundryLocalManager()
        if self.chat_client is None:
            self.chat_client = OpenAI(
                api_key=self.foundry_local.api_key, base_url=self.foundry_local.endpoint
            )

        # First, tool calling
        response = self.chat_client.chat.completions.create(
            model=self.chat_model,
            messages=[
                {
                    "role": "system",
                    "content": 'You are a keyword extractor that extracts relevant keywords from a user\'s query and calls a search function called "search" with keywords separated by commas in a single sentence.',
                },
                {"role": "user", "content": f"User Query:\n{query}"},
            ],
            tools=[
                {
                    "name": "search",
                    "description": "Performs a book database search based on the query.",
                    "parameters": {
                        "query": {
                            "description": "Keep the query limited to keywords for best results.",
                            "type": "string",
                        }
                    },
                }
            ],
            temperature=0.00001,
            max_tokens=1024,
            top_p=1.0,
        )

        message = response.choices[0].message.content
        funcresults = re.search(r"functools\[.*\]", message, re.DOTALL)
        functools = (
            funcresults
            .group()
            .replace("functools", "")
            if funcresults else None
        )
        if functools:
            function_calls = json.loads(functools)
            function_result = self.parse_function_calls(
                function_calls=function_calls, tools={"search": self.search}
            )[0]

        # Second, the real output
        self.memory.add({"role": "user", "content": f"User query:\n{query}"})
        if functools:
            self.memory.add(
                {"role": "user", "content": f"Database search results:\n{function_result}"}
            )

        response = (
            self.chat_client.chat.completions.create(
                model=self.chat_model,
                messages=self.memory.get(),
                temperature=0.5,
                max_tokens=4096,
            )
            .choices[0]
            .message
        )
        self.memory.add(
            {"role": response.role, "content": response.content.lstrip(":").strip()}
        )
        return self.memory.get(-1)["content"]