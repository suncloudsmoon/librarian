"""Defines a class called Book and a CatalogManager to keep track of books and where they are located."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from dacite import from_dict


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


class CatalogManager:
    """Manages a library catalog."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.books = []
        if self.path.exists():
            contents = json.loads(self.path.read_text(encoding="utf-8"))
            self.books = [from_dict(Book, data) for data in contents]

    def save(self):
        for book in self.books:
            book.authors.sort()
        book_list = [asdict(book) for book in sorted(self.books)]
        contents = json.dumps(book_list, indent=4, ensure_ascii=False)
        self.path.write_text(contents, encoding="utf-8")

    def __exists_id(self, id: str) -> bool:
        """Checks if a book exists within the library catalog."""
        for book in self.books:
            if book.id == id:
                return True
        return False

    def add(self, book: Book):
        """Adds the given book to the library catalog."""
        if self.__exists_id(book.id):
            raise ValueError(f"book {book.id} already exists")
        self.books.append(book)

    def remove(self, id: str):
        """Removes the given book from the library catalog."""
        if not self.__exists_id(id):
            raise LookupError(f"the book {id} doesn't exist")
        for book in self.books:
            if book.id == id:
                self.books.remove(book)
                break

    def edit(self, edited_book: Book):
        for i, book in enumerate(self.books):
            if book.id == edited_book.id:
                self.books[i] = edited_book
                break

    def get(self, id: str) -> Book:
        for book in self.books:
            if book.id == id:
                return book
        raise LookupError(f"book {id} doesn't exist")

    def exists(self, isbn: str) -> bool:
        for book in self.books:
            if book.isbn == isbn and isbn != "0" * 13:
                return True
        return False
