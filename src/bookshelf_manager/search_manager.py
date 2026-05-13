"""
This module contains three different classes: SearchResult, MemoryManager, and SearchManager.
Each of these classes serve a distinct purpose. For instance, the SearchResult object is returned from SearchManager.search().
While MemoryManager is used within the AI chat process in order to give the LLM memory.

Side notes
Intelligence may involve curiousity, but curiousity is random (appearing out of the blue)?
"""

import base64
import contextlib
import json
import logging
import os
import re
import sys
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from shutil import rmtree
from textwrap import dedent

os.environ["RUST_LOG"] = "error"

import pymupdf
from llama_index.core import VectorStoreIndex, StorageContext, load_index_from_storage
from llama_index.core.agent.workflow import FunctionAgent
from llama_index.core.memory import FactExtractionMemoryBlock, Memory, VectorMemoryBlock
from llama_index.core.schema import TextNode
from llama_index.core.vector_stores import (
    ExactMatchFilter,
    FilterCondition,
    MetadataFilters,
)
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.openai_like import OpenAILike
from llama_index.vector_stores.lancedb import LanceDBVectorStore
from pydantic import BaseModel, Field
from semchunk import chunkerify

from .catalog_manager import Book, CatalogManager, Metadata
from .utils import ask_image
from llama_index.core import Settings
from pix2text import Pix2Text


@dataclass
class SearchResult:
    metadata: Metadata
    cover_image: str
    page: int
    page_content: str


class SearchManager:

    def __init__(
        self,
        path: Path,
        embed_path: str,
        general_llm: OpenAILike,
        system_prompt: str,
        catalog_manager: CatalogManager,
        enable_enhanced_ocr: bool,
    ):
        sys.stderr = open(os.devnull, "w")  # so prompt_toolkit can work
        warnings.simplefilter("ignore")

        self.embed_path = embed_path
        self.catalog_manager = catalog_manager
        self.enable_enhanced_ocr = enable_enhanced_ocr

        Settings.llm = self.general_llm = general_llm

        self.create_embed_client()
        
        self.stores_path = path / "stores"
        self.llamaindex_dir = self.stores_path / "llamaindex"
        self.create_stores(self.stores_path)

        self.get_len_tokens = lambda text: len(
            Settings.embed_model._model.tokenizer.tokenize(text)
        )

        # memory stuff
        blocks = [
            FactExtractionMemoryBlock(
                name="extracted_info", llm=self.general_llm, max_facts=50, priority=1
            ),
            VectorMemoryBlock(
                name="agent_memory",
                vector_store=LanceDBVectorStore(
                    uri=str(self.stores_path / "vectordb"), table_name="agent_memory"
                ),
                priority=2,
                embed_model=Settings.embed_model,
                similarity_top_k=2,
                retrieval_context_window=5,
            ),
        ]
        self.memory = Memory.from_defaults(
            session_id="my_session",
            token_limit=2048,
            memory_blocks=blocks,
            insert_method="system",
        )

        self.chat_agent = FunctionAgent(
            tools=[self.search_for_books],
            llm=self.general_llm,
            system_prompt=system_prompt,
        )

    def create_stores(self, stores_dir: Path):
        stores_dir.mkdir(exist_ok=True)
        self.vector_store = LanceDBVectorStore(
            uri=str(stores_dir / "vectordb"), table_name="documents"
        )

        if self.llamaindex_dir.exists():
            storage_context = StorageContext.from_defaults(
                persist_dir=str(self.llamaindex_dir),
                vector_store=self.vector_store,
            )
            self.llamaindex = load_index_from_storage(storage_context=storage_context)
        else:
            self.llamaindex_dir.mkdir()
            storage_context = StorageContext.from_defaults(
                vector_store=self.vector_store,
            )

            self.llamaindex = VectorStoreIndex.from_documents(
                documents=[],
                storage_context=storage_context,
                vector_store=self.vector_store,
            )
            self.save()
        self.db = self.vector_store.client

    def create_embed_client(self):
        with contextlib.redirect_stdout(None):
            Settings.embed_model = HuggingFaceEmbedding(model_name=str(self.embed_path))

    def save(self):
        self.llamaindex.storage_context.persist(self.llamaindex_dir)

    def refresh(self):
        """Starts from scratch for all the components."""
        self.create_embed_client()
        rmtree(self.stores_path)
        self.create_stores(self.stores_path)

    def is_database_mismatch(self, is_ignored) -> bool:
        if not "documents" in list(self.db.table_names()):
            return any(not is_ignored(meta) for meta, cover_image in self.catalog_manager)

        tbl = self.db.open_table("documents")
        return any(
            meta.filename.lower().endswith(".pdf")
            and not self.exists(meta, tbl)
            and not is_ignored(meta)
            for meta, cover_image in self.catalog_manager
        )

    def exists(self, metadata: Metadata, tbl):
        return tbl.count_rows(filter=f"`metadata`.`id` = '{metadata.id}'") > 0

    def index(self) -> bool:
        vectorstore = self.db.open_table("documents")
        vectorstore.create_fts_index("text", language="English", replace=True)

        rows = vectorstore.count_rows()
        if rows >= 5000:
            vectorstore.create_index(vector_col="vector")
            return True
        else:
            return False

    def add(
        self,
        path: Path,
        id: str,
    ):
        if not path.name.endswith(".pdf"):
            raise ValueError("librarian does not support indexing files other than PDF")

        n_ctx = 256
        if Settings.embed_model._model.tokenizer.model_max_length < n_ctx:
            raise RuntimeError(f"{self.embed_path} has a context length below {n_ctx}")
        chunker = chunkerify(
            self.get_len_tokens,
            n_ctx,
        )

        pdf = pymupdf.open(path)
        if self.enable_enhanced_ocr:
            p2t = Pix2Text.from_config()
        
        nodes = []
        for num, page in enumerate(pdf, start=1):
            if self.enable_enhanced_ocr:
                doc = p2t.recognize_pdf(path, page_numbers=[page])
                text = doc.to_markdown("output-md")
            else:
                text = page.get_text()

            clean_content = " ".join(text.split())
            chunks = chunker(
                clean_content, overlap=0.10
            )  # overlap should be 10%-20% max
            for chunk in chunks:
                nodes.append(TextNode(text=chunk, metadata={"id": id, "page": num}))
        if nodes:
            self.llamaindex.insert_nodes(nodes)
            self.save()
        else:
            raise RuntimeError(f"could not extract text from '{path}'")

    def remove(self, id: str):
        filters = MetadataFilters(filters=[ExactMatchFilter(key="id", value=id)])
        self.llamaindex.delete_nodes(filters=filters)
        self.save()

    def search_for_books(self, query: str) -> str:
        """
        Searches a vector database full of books for relevant text chunks based on the query.
        The result contains the book's metadata, text chunk, and the page number.
        """
        results = self.llamaindex.as_retriever().retrieve(query)
        top_results = [result for result in results if result.score > 0.1]
        search_results = self.get_search_results(type="nodes", results=top_results)

        output = ""
        for result in search_results:
            metadata = result.metadata
            if metadata.type == "book":
                book: Book = metadata.data
                output += dedent(
                    f"""\
                    ---
                    Metadata Type: Book
                    Title: {book.title}
                    Authors: {book.authors}
                    Year: {book.year}
                    Description: "{book.description}"
                    Page: {result.page}
                    Text: "{result.page_content}"
                    ---
                    """
                )
        return output or "No results found"

    def semantic_search(
        self, query: str, filters: MetadataFilters | None = None, k: int = 4
    ) -> list[SearchResult]:
        retriever = self.llamaindex.as_retriever(filters=filters)
        results = retriever.retrieve(query)
        top_results = [result for result in results if result.score >= 0.1]
        return self.get_search_results(type="nodes", results=top_results)

    def fts_search(self, query: str, k: int = 4) -> list[SearchResult]:
        table = self.db.open_table("documents")
        rows = table.search(query).limit(k).tolist()
        return self.get_search_results(type="rows", results=rows)

    def get_search_results(self, type: str, results) -> list[SearchResult]:
        search_results = []
        for result in results:
            if type == "nodes":
                early_metadata, text = result.metadata, result.text
                id, page = early_metadata["id"], early_metadata["page"]
            elif type == "rows":
                text = result.get("text")
                early_metadata = result["metadata"]
                id, page = early_metadata["id"], early_metadata["page"]
            else:
                raise NotImplementedError(f"unknown type {type}")

            metadata, cover_image_path = self.catalog_manager.get(id)
            search_results.append(
                SearchResult(
                    metadata=metadata,
                    cover_image=str(cover_image_path),
                    page=page,
                    page_content=text,
                )
            )
        return search_results

    def parse_function_calls(self, function_calls: dict, tools: dict):
        results = []
        for function_call in function_calls:
            name = function_call["name"]
            arguments = function_call["arguments"]
            if name in tools:
                results.append(tools[name](**arguments))
        return results

    async def chat(self, prompt: str):
        response = await self.chat_agent.run(user_msg=prompt, memory=self.memory)
        return str(response)
