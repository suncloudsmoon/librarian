"""
Defines a class called Librarian that is designed to mimic a real librarian's job via its functions as much as possible.
It also defines a dataclass called Config that stores all configuration settings necessary for the librarian.
"""

from io import BytesIO
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from shutil import move
from typing import Literal

from dacite import from_dict
import fitz
from prompt_toolkit import HTML
from prompt_toolkit import print_formatted_text as print
from send2trash import send2trash

from .catalog_manager import CatalogManager, Metadata
from .search_manager import SearchManager
from .sync import SyncClient, SyncServer
from .utils import Git, create_classification_cls, get_resource_path
from llama_index.llms.openai_like import OpenAILike


@dataclass
class Config:
    """Defines settings that the user can change if needed."""

    enable_enhanced_ocr: bool = False
    enable_version_history: bool = False
    classification_system: str = "dewey"
    system_prompt: str = (
        "You are a concise librarian assistant. Use the supplied search-results input to answer the user's query and draw on those documents as evidence. Never mention internal databases, tools, or memory — present findings as if you just retrieved them. When citing, include only source metadata (title, authors, page, id, call_number) inline. Keep answers short, factual, and helpful."
    )
    embed_model: str = "sentence-transformers/all-minilm-l6-v2"

    index_denylist: list[str] = field(default_factory=list)


@dataclass
class LibrarianNotes:
    changes: int = 0

@dataclass
class DocResult:
    cover_image_filepath: str
    metadata: Metadata


def load_from_file(cls, path, default_config):
    """Loads from file if the path exists, else will create and return a blank instance of this class."""
    path = Path(path)
    if path.exists():
        contents = path.read_text(encoding="utf-8")
        return from_dict(cls, json.loads(contents))
    else:
        return default_config


def save_to_file(path, config):
    """Dumps this dataclass into a JSON and then into a file."""
    contents = json.dumps(asdict(config), indent=4, ensure_ascii=False)
    Path(path).write_text(contents, encoding="utf-8")


class Librarian:
    """Defines a librarian with attributes and methods not too different from a real librarian."""

    def __init__(
        self,
        librarian_path,
        default_config: Config,
        general_client: OpenAILike,
    ):
        """Initializes necessary attributes such as config, catalog manager, search manager, etc."""

        self.general_client = general_client

        self.librarian_path = Path(librarian_path)
        self.config_path = self.librarian_path / "config.json"
        self.notes_path = self.librarian_path / "notes.json"

        self.config = load_from_file(Config, self.config_path, default_config)

        self.catalog_manager = CatalogManager(self.librarian_path)
        self.search_manager = SearchManager(
            path=self.librarian_path,
            embed_path=self.config.embed_model,
            general_llm=self.general_client,
            system_prompt=self.config.system_prompt,
            catalog_manager=self.catalog_manager,
            enable_enhanced_ocr=self.config.enable_enhanced_ocr,
        )

        type = self.config.classification_system
        text = (self.librarian_path / f"{type}.json").read_text(encoding="utf-8")
        self.classification_system = create_classification_cls(type, text)

        # Notes taken by the librarian for making the user experience better
        self.librarian_notes = load_from_file(
            LibrarianNotes,
            self.notes_path,
            LibrarianNotes(),
        )
        if self.config.enable_version_history and self.librarian_notes.changes >= 5:
            # Do backup
            self.do_git_commit()

    def close(self):
        """Closes resources held by search manager."""
        self.search_manager.save()

    def is_database_mismatch(self) -> bool:
        """
        Checks whether there is a mismatch between the catalog and index and returns the condition.
        """
        return self.search_manager.is_database_mismatch(
            lambda metadata: metadata.id in self.config.index_denylist
        )

    def do_git_commit(self):
        git = Git(self.librarian_path.parent, self.librarian_path / ".git")
        git.stage(exclude_paths=[self.librarian_path / p for p in ("stores")])
        git.commit(date.today().isoformat())
        self.librarian_notes.changes = 0
        save_to_file(self.notes_path, self.librarian_notes)  # autosave

    def get_cover_image(self, path: Path):
        if path.suffix.lower().endswith("pdf"):
            pdf = fitz.open(path)
            page = pdf.load_page(0)
            pixels = page.get_pixmap(dpi=300)
            buffer = BytesIO(pixels.tobytes(output="jpeg"))
            cover_image = buffer.getvalue()
        else:
            cover_image = None
        return cover_image
    
    def get_all_documents(self) -> list[DocResult]:
        """
        Returns all the documents (and their associated cover images) contained within the library.
        """
        return [DocResult(cover_image_filepath=str(cover_image_path), metadata=metadata) for metadata, cover_image_path in self.catalog_manager]

    def add(self, path: Path, metadata: Metadata):
        """
        Adds the given document located in path to the library.

        Args:
            path: Full filesystem path where the document is located
            metadata: Metadata object populated with all the necessary attributes
        """

        if not path.exists():
            raise ValueError(f"{path} doesn't exist")

        # Count as a change
        self.librarian_notes.changes += 1
        save_to_file(self.notes_path, self.librarian_notes)  # autosave

        # Get cover image from PDF
        cover_image = self.get_cover_image(path)

        # Add to catalog manager
        self.catalog_manager.add(metadata, cover_image)

        try:
            # Add to search manager
            self.search_manager.add(path=path, id=metadata.id)
        except ValueError:
            self.config.index_denylist.append(metadata.id)
            save_to_file(self.config_path, self.config)  # autosave
            raise
        else:
            self.search_manager.index()
        finally:
            # path stuff
            book_path = self.get_document_path(metadata)
            os.makedirs(book_path.parent, exist_ok=True)
            move(path, book_path)

    def remove(self, id: str):
        """
        Removes the document specified by id from librarian.

        Args:
            id: The document denoted by ID (UUID)
        """
        # Count as a change
        self.librarian_notes.changes += 1
        save_to_file(self.notes_path, self.librarian_notes)  # autosave

        self.delete_book(id)
        self.catalog_manager.remove(id)
        self.search_manager.remove(id)
        if id in self.config.index_denylist:
            self.config.index_denylist.remove(id)
            save_to_file(self.config_path, self.config)  # autosave

    def edit(self, modified_metadata: Metadata):
        """
        Replaces the metadata of a document with the modified_metadata.

        Args:
            modified_metadata: The new, modified metadata
        """
        # Count as a change
        self.librarian_notes.changes += 1
        save_to_file(self.notes_path, self.librarian_notes)  # autosave

        old_file = self.info(modified_metadata.id)[0]
        old_path = self.get_document_path(old_file)
        new_path = self.get_document_path(modified_metadata)

        os.makedirs(new_path.parent, exist_ok=True)
        move(old_path, new_path)
        try:
            os.removedirs(old_path.parent)
        except OSError:
            pass

        # Get cover image from PDF
        cover_image = self.get_cover_image(new_path)

        self.catalog_manager.edit(modified_metadata, cover_image)

    def refresh(self):
        """Refreshes the vector database component. Necessary if the embed model is changed, for instance."""
        self.search_manager.refresh()

        paths_ids = self.get_paths_ids()
        for path, id in paths_ids:
            try:
                print(f"Indexing '{path}'")
                self.search_manager.add(
                    path=path,
                    id=id,
                )
            except ValueError as err:
                print(HTML(f"<ansired>Indexing failure: {err}</ansired>"))
                self.config.index_denylist.append(id)
                save_to_file(self.config_path, self.config)  # autosave
        self.search_manager.index()

    def get_paths_ids(self) -> list[tuple]:
        return [
            (self.get_document_path(metadata), metadata.id)
            for metadata, cover_image in self.catalog_manager
            if metadata.id not in self.config.index_denylist
        ]

    def search(self, query: str, search_type: Literal["semantic", "fts"] = "semantic"):
        """
        Performs a semantic search based on the query by forwarding the request to the search manager.

        Args:
            query: specifies the nature of the request
            search_type: specifies the type of search (semantic or fts)
        """
        if search_type == "semantic":
            results = self.search_manager.semantic_search(query)
        elif search_type == "fts":
            results = self.search_manager.fts_search(query)
        else:
            raise NotImplementedError(f"unknown search_type {search_type}")
        return results

    def question(self, query: str):
        """
        Prompts the llm with query and returns the answer.

        Args:
            query: Specifies the nature of the request
        """
        return self.search_manager.question(query)

    def sync(self, is_client: bool, password: str, server_addr: tuple = None):
        """
        Initiates the syncing sequence by specifying whether you are the server or client. The server sends the files, while the
        receiver receives the files from the server (passive).

        Args:
            is_client: Specifies whether you could act as the client (True) or server (False)
            password: Contains the passphrase in order to send data with encryption
            server_addr: Is a tuple specifying either the server address (for the client to connect to) or the server binding address (typically set to 0.0.0.0:1230). Tuple example: ('0.0.0.0', 1230)
        """
        home_dir = self.librarian_path.parent
        if is_client:
            client = SyncClient(password)
            client.start(
                directory=home_dir,
                server_addr=server_addr,
                exclude_paths=[],
                exclude_patterns=["**/.DS_Store"],
                strict=False,
            )
        else:
            server = SyncServer(
                password=password,
                server_port=server_addr[1],
            )
            server.start(home_dir)

    def get_doc_path_by_id(self, id: str) -> str:
        """
        Returns the path to the document identified by the ID

        Args:
            id: The document denoted by id (UUID)
        """
        return str(self.get_document_path(self.info(id)[0]))

    def get_document_path(self, metadata: Metadata) -> Path:
        """
        Returns the path to the
        """
        return Path(
            os.path.join(
                self.librarian_path.parent,
                self.classification_system.get_path(metadata.call_number),
                metadata.filename,
            )
        )

    def delete_book(self, id: str):
        document = self.info(id)[0]
        doc_path = self.get_document_path(document)
        send2trash(doc_path)
        try:
            os.removedirs(doc_path.parent)
        except OSError:
            pass

    def info(self, id: str) -> tuple:
        """
        Returns a metadata object and the path to the cover image of the document.

        Args:
            id: The document denoted by id (UUID)
        """
        return self.catalog_manager[id]

    def exists(self, id: str) -> bool:
        """
        Returns whether or not the material identified by id exists in the catalog manager.

        Args:
            id: The document denoted by id (UUID)
        """
        return id in self.catalog_manager

    def __getitem__(self, id: str) -> tuple:
        """Returns a Book object identified by id."""
        return self.info(id)

    def __contains__(self, id: str):
        self.exists(id)
