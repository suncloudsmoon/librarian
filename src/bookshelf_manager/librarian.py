"""
Defines a class called Librarian that is designed to mimic a real librarian's job via its functions as much as possible.
It also defines a dataclass called Config that stores all configuration settings necessary for the librarian.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from shutil import move

from dacite import from_dict
from prompt_toolkit import HTML
from prompt_toolkit import print_formatted_text as print
from send2trash import send2trash

from .catalog_manager import Book, CatalogManager
from .search_manager import SearchManager
from .sync import SyncClient, SyncServer
from .utils import Git, create_classification_cls, get_resource_path


default_embed_model = "sentence-transformers/all-minilm-l6-v2"


@dataclass
class Config:
    """Defines settings that the user can change if needed."""

    enable_version_history: bool = False
    classification_system: str = "dewey"
    exclude_thinking_tag: bool = True
    system_prompt: str = (
        "You are a concise librarian assistant. Use the supplied search-results input to answer the user's query and draw on those documents as evidence. Never mention internal databases, tools, or memory — present findings as if you just retrieved them. When citing, include only source metadata (title, authors, page, id, call_number) inline. Keep answers short, factual, and helpful."
    )
    chat_model: str = "qwen3-0.6b-gpu"
    embed_model: str = (
        get_resource_path("models/sentence-transformers/all-minilm-l6-v2")
        if Path("models/sentence-transformers/all-minilm-l6-v2").exists()
        else default_embed_model
    )

    index_denylist: list[str] = field(default_factory=list)


@dataclass
class LibrarianNotes:
    changes_since_last_version: int = 0


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

    def __init__(self, librarian_path, default_config: Config):
        """Initializes necessary attributes such as config, catalog manager, search manager, etc."""
        self.librarian_path = Path(librarian_path)
        self.config_path = self.librarian_path / "config.json"
        self.notes_path = self.librarian_path / "librarian_notes.json"

        self.config = load_from_file(Config, self.config_path, default_config)

        self.catalog_manager = CatalogManager(self.librarian_path)
        self.search_manager = SearchManager(
            path=self.librarian_path,
            embed_path=self.config.embed_model,
            chat_model=self.config.chat_model,
            system_prompt=self.config.system_prompt,
            catalog_manager=self.catalog_manager,
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
        if self.librarian_notes.changes_since_last_version >= 5:
            # Do backup
            git = Git(self.librarian_path.parent)
            git.commit(date.today().isoformat())
            self.librarian_notes.changes_since_last_version = 0

    def close(self):
        """Saves configuration files/catalog manager and closes resources held by search manager."""
        # Save configs
        save_to_file(self.config_path, self.config)
        save_to_file(self.notes_path, self.librarian_notes)

        # Save catalog manager
        self.catalog_manager.save()

        # closes the search manager
        self.search_manager.close()

    def is_database_mismatch(self) -> bool:
        return self.search_manager.is_database_mismatch(
            lambda book: book.id in self.config.index_denylist
        )

    def info(self, id: str) -> tuple:
        """Returns a Book object identified by id."""
        return self.catalog_manager.get(id)

    def exists(self, isbn: str) -> bool:
        return self.catalog_manager.exists(isbn)

    def add(self, path: Path, book: Book, cover_image: str):
        """Adds the given book located in path to the library."""

        if not path.exists():
            raise ValueError(f"{path} doesn't exist")

        # Count as a change
        self.librarian_notes.changes_since_last_version += 1

        # Add to catalog manager
        self.catalog_manager.add(book, cover_image)
        self.catalog_manager.save()

        try:
            # add to search manager
            self.search_manager.add(path, book.id)
        except ValueError:
            self.config.index_denylist.append(book.id)
            raise
        else:
            self.search_manager.index()
        finally:
            # path stuff
            book_path = self.get_book_path(book)
            os.makedirs(book_path.parent, exist_ok=True)
            move(path, book_path)

    def remove(self, id):
        """Removes the book specified by id from librarian."""
        # Count as a change
        self.librarian_notes.changes_since_last_version += 1

        self.delete_book(id)
        self.catalog_manager.remove(id)
        self.catalog_manager.save()
        self.search_manager.remove(id)
        if id in self.config.index_denylist:
            self.config.index_denylist.remove(id)

    def edit(self, book: Book):
        # Count as a change
        self.librarian_notes.changes_since_last_version += 1

        old_book = self.info(book.id)[0]
        old_path = self.get_book_path(old_book)
        new_path = self.get_book_path(book)

        os.makedirs(new_path.parent, exist_ok=True)
        move(old_path, new_path)
        try:
            os.removedirs(old_path.parent)
        except OSError:
            pass
        self.catalog_manager.edit(book)
        self.catalog_manager.save()

    def refresh(self):
        """Refreshes the vector database component. Necessary if the embed model is changed, for instance."""
        self.search_manager.refresh()

        paths_ids = self.get_paths_ids()
        for path, id in paths_ids:
            try:
                print(f"Indexing '{path}'")
                self.search_manager.add(path, id)
            except ValueError as err:
                print(HTML(f"<ansired>Indexing failure: {err}</ansired>"))
                self.config.index_denylist.append(id)
        self.search_manager.index()

    def get_paths_ids(self) -> list[tuple]:
        return [
            (self.get_book_path(book), book.id)
            for book in self.catalog_manager.books
            if book.id not in self.config.index_denylist
        ]

    def search(self, query: str):
        """Performs a semantic search based on the query by forwarding the request to the search manager."""
        results = self.search_manager.search_query(query)
        return results

    def question(self, query: str):
        return self.search_manager.question(query)

    def sync(self, is_client: bool, password: str, server_addr: tuple = None):
        home_dir = self.librarian_path.parent
        if is_client:
            client = SyncClient(password)
            client.start(
                directory=home_dir,
                server_addr=server_addr,
                exclude_paths=[],
                strict=False,
            )
        else:
            server = SyncServer(
                password=password,
                server_address=server_addr,
            )
            server.start(home_dir)

    def get_book_path(self, book: Book) -> Path:
        return Path(
            os.path.join(
                self.librarian_path.parent,
                self.classification_system.get_path(book.call_number),
                book.filename,
            )
        )

    def delete_book(self, id):
        book = self.info(id)[0]
        book_path = self.get_book_path(book)
        send2trash(book_path)
        try:
            os.removedirs(book_path.parent)
        except OSError:
            pass
