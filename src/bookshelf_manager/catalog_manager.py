"""Defines a class called Book and a CatalogManager to keep track of books and where they are located."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from dacite import from_dict
from send2trash import send2trash

# Book version 1.1
@dataclass
class Book:
    id: str
    filename: str
    isbn: str
    call_number: str
    title: str
    authors: list[str]
    publisher: str
    series: str
    edition: str
    volume: str
    year: int
    url: str
    description: str
    notes: str

    def __lt__(self, other: "Book"):
        return self.authors[0].casefold() < other.authors[0].casefold()

    def __str__(self):
        result = ""
        for key, val in asdict(self).items():
            key = key.replace("_", " ").title()
            if isinstance(val, list):
                result += f"{key}: {str(val)[1:-1].replace("'", "")}\n"
            else:
                result += f"{key}: {val}\n"
        return result


class CoverImageManager:
    """Manages cover images stored in a directory."""

    def __init__(self, dir: Path):
        self.dir = dir
        self.dir.mkdir(exist_ok=True)
    
    def get_path(self, id: str):
        return (self.dir / id).with_suffix(".jpg")
    
    def add(self, id: str, cover_image: bytes):
        path = self.get_path(id)
        path.write_bytes(cover_image)

    def remove(self, id: str):
        path = self.get_path(id)
        if path.exists():
            send2trash()

    def get(self, id: str) -> bytes | None:
        path = self.get_path(id)
        return path.read_bytes() if path.exists() else None


class CatalogManager:
    """Manages a library catalog."""

    def __init__(self, dir: Path):
        self.catalog_json = dir / "catalog.json"
        self.cover_image_manager = CoverImageManager(dir / "cover_images")
        self.books = []
        if self.catalog_json.exists():
            contents = json.loads(self.catalog_json.read_text(encoding="utf-8"))
            self.books = [from_dict(Book, data) for data in contents]

    def save(self):
        for book in self.books:
            book.authors.sort()
        book_list = [asdict(book) for book in sorted(self.books)]
        contents = json.dumps(book_list, indent=4, ensure_ascii=False)
        self.catalog_json.write_text(contents, encoding="utf-8")

    def __exists_id(self, id: str) -> bool:
        """Checks if a book exists within the library catalog."""
        for book in self.books:
            if book.id == id:
                return True
        return False

    def add(self, book: Book, cover_image: str):
        """Adds the given book to the library catalog."""
        if self.__exists_id(book.id):
            raise ValueError(f"book {book.id} already exists")
        self.books.append(book)
        if cover_image:
            self.cover_image_manager.add(book.id, cover_image)
        self.save()

    def remove(self, id: str):
        """Removes the given book from the library catalog."""
        if not self.__exists_id(id):
            raise LookupError(f"the book {id} doesn't exist")
        for book in self.books:
            if book.id == id:
                self.books.remove(book)
                self.cover_image_manager.remove(id)
                break
        self.save()

    def edit(self, edited_book: Book):
        for i, book in enumerate(self.books):
            if book.id == edited_book.id:
                self.books[i] = edited_book
                break
        self.save()

    def get(self, id: str) -> tuple:
        for book in self.books:
            if book.id == id:
                cover_image = self.cover_image_manager.get(id)
                return (book, cover_image)
        raise LookupError(f"book {id} doesn't exist")

    def exists(self, isbn: str) -> bool:
        for book in self.books:
            if book.isbn == isbn and isbn != "0" * 13:
                return True
        return False
