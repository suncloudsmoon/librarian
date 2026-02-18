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

import kuzu
import pymupdf
from llama_index.core import PropertyGraphIndex, StorageContext, load_index_from_storage
from llama_index.core.agent.workflow import FunctionAgent
from llama_index.core.graph_stores import SimplePropertyGraphStore
from llama_index.core.graph_stores.types import KG_RELATIONS_KEY
from llama_index.core.indices.property_graph import (
    LLMSynonymRetriever,
    SimpleLLMPathExtractor,
    VectorContextRetriever,
)
from llama_index.core.memory import FactExtractionMemoryBlock, Memory, VectorMemoryBlock
from llama_index.core.schema import TextNode
from llama_index.core.vector_stores import (
    ExactMatchFilter,
    FilterCondition,
    MetadataFilters,
)
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.graph_stores.kuzu import KuzuPropertyGraphStore
from llama_index.llms.openai_like import OpenAILike
from llama_index.vector_stores.lancedb import LanceDBVectorStore
from pydantic import BaseModel, Field
from semchunk import chunkerify

from .catalog_manager import Book, CatalogManager, Metadata
from .utils import ask_image
from llama_index.core import Settings


class OCRResult(BaseModel):
    text: str
    refused: bool = Field(description="If the image shown represents a cover of a book, table of contents, references, or index, set refused to True. Otherwise, set refused to False.")


@dataclass
class SearchResult:
    metadata: Metadata
    cover_image: bytes
    page: int
    page_content: str


class SearchManager:

    def __init__(
        self,
        path: Path,
        embed_path: str,
        general_llm: OpenAILike,
        ocr_llm: OpenAILike,
        system_prompt: str,
        catalog_manager: CatalogManager,
        enable_enhanced_ocr: bool,
    ):
        sys.stderr = open(os.devnull, "w")  # so prompt_toolkit can work
        warnings.simplefilter("ignore")

        self.embed_path = embed_path
        self.catalog_manager = catalog_manager
        Settings.llm = self.general_llm = general_llm
        self.ocr_llm = ocr_llm
        self.enable_enhanced_ocr = enable_enhanced_ocr

        self.create_embed_client()

        self.get_len_tokens = lambda text: len(
            Settings.embed_model._client.tokenizer.tokenize(text)
        )

        self.stores_path = path / "stores"
        self.llamaindex_dir = self.stores_path / "llamaindex"
        self.create_stores(self.stores_path)

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
            tools=[self.search_entity_relations, self.search_for_books],
            llm=self.general_llm,
            system_prompt=system_prompt,
        )
        self.extractor = SimpleLLMPathExtractor(llm=self.general_llm)
        self.graph_retriever = self.llamaindex.as_retriever(
            sub_retrievers=[
                VectorContextRetriever(
                    graph_store=self.llamaindex.property_graph_store,
                    embed_model=Settings.embed_model,
                ),
                LLMSynonymRetriever(
                    graph_store=self.llamaindex.property_graph_store,
                    llm=self.general_llm,
                ),
            ]
        )

    def create_stores(self, stores_dir: Path):
        stores_dir.mkdir(exist_ok=True)
        vector_store = LanceDBVectorStore(
            uri=str(stores_dir / "vectordb"), table_name="documents"
        )
        self.graphdb = kuzu.Database(stores_dir / "graphdb")
        self.graph_store = KuzuPropertyGraphStore(db=self.graphdb)

        if self.llamaindex_dir.exists():
            storage_context = StorageContext.from_defaults(
                persist_dir=str(self.llamaindex_dir),
                vector_store=vector_store,
                property_graph_store=self.graph_store,
            )
            self.llamaindex = load_index_from_storage(storage_context=storage_context)
        else:
            self.llamaindex_dir.mkdir()
            property_graph_store = SimplePropertyGraphStore()
            storage_context = StorageContext.from_defaults(
                vector_store=vector_store,
                property_graph_store=self.graph_store,
            )

            self.llamaindex = PropertyGraphIndex.from_documents(
                documents=[],
                storage_context=storage_context,
                property_graph_store=property_graph_store,
            )
            self.save()
        self.db = vector_store.client

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
        nodes = []
        for num, page in enumerate(pdf, start=1):
            if self.enable_enhanced_ocr:
                image_bytes = page.get_pixmap().tobytes(output="jpg")
                image_data = base64.urlsafe_b64encode(image_bytes).decode()
                system_prompt = "Extract all text shown in the text except for page numbers, figure numbers, and figure captions. Represent any math expressions using the LaTeX notation. If the image shown represents a cover of a book, table of contents, references, or index, set refused to True. Otherwise, set refused to False."
                
                # switch to plain text output and a tool call to refuse it?
                ocr_result: OCRResult = ask_image(
                    self.ocr_llm, system_prompt, OCRResult, image_data
                )
                if not ocr_result.refused:
                    text = ocr_result.text.replace("\\n", "\n").replace("\'", "'").replace('\"', '"')
                else:
                    continue
            else:
                text = page.get_text()

            clean_content = " ".join(text.split())
            chunks = chunker(
                clean_content, overlap=0.10
            )  # overlap should be 10%-20% max
            for chunk in chunks:
                nodes.append(TextNode(text=chunk, metadata={"id": id, "page": num}))
        if nodes:
            self.llamaindex.insert_nodes(nodes, kg_extractors=[self.extractor])
            self.save()
        else:
            raise RuntimeError(f"could not extract text from '{path}'")

    def remove(self, id: str):
        filters = MetadataFilters(filters=[ExactMatchFilter(key="id", value=id)])
        self.llamaindex.delete_nodes(filters=filters)
        self.save()

    def search_entity_relations(self, query: str) -> str:
        """
        Search the knowledge graph and return the plaintext triples
        in the form: source -[relation]-> target

        Always try to use this function before calling search_for_books.
        If the information returned from this function is not relevant based on the user's query,
        ignore this function's output entirely.
        """
        rels = [
            rel
            for r in self.graph_retriever.retrieve(query)
            for rel in r.node.metadata.get(KG_RELATIONS_KEY, [])
        ]
        return (
            "\n".join(f"{x.source_id} -[{x.label}]-> {x.target_id}" for x in rels[:50])
            or "No results found"
        )

    def search_for_books(self, query: str) -> str:
        """
        Searches a vector database full of books for relevant text chunks based on the query.
        The result contains the book's metadata, text chunk, and the page number.
        """
        results = self.graph_retriever.retrieve(query)
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

            metadata, cover_image = self.catalog_manager.get(id)
            search_results.append(
                SearchResult(
                    metadata=metadata,
                    cover_image=cover_image,
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
