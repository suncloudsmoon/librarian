"""Defines a class called Book and a CatalogManager to keep track of books and where they are located."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from dacite import from_dict
from send2trash import send2trash


@dataclass
class Metadata:
    """For defining metadata for different mediums such as music, audio recordings, movies, etc."""
    id: str
    hash: str
    filename: str
    call_number: str
    type: str
    data: Book | None

    def __lt__(self, other: "Metadata"):
        return self.filename.casefold() < other.filename.casefold()

    def __str__(self):
        result = ""
        for key, val in asdict(self).items():
            key = key.replace("_", " ").title()
            if isinstance(val, Book):
                result += '\n' + str(val)
            elif isinstance(val, list):
                result += f"{key}: {str(val)[1:-1].replace("'", "")}\n"
            else:
                result += f"{key}: {val}\n"
        return result


@dataclass
class Book:
    """Book version 1.1"""
    isbn: str
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
            send2trash(path)

    def get(self, id: str) -> bytes | None:
        path = self.get_path(id)
        return path.read_bytes() if path.exists() else None


class CatalogManager:
    """Manages a library catalog."""

    def __init__(self, home_dir: Path):
        self.catalog_dir = home_dir / "catalog"
        self.catalog_dir.mkdir(exist_ok=True)
        self.cover_image_manager = CoverImageManager(home_dir / "cover_images")

    def save(self, metadata: Metadata):
        contents = json.dumps(asdict(metadata), ensure_ascii=False)
        path = self.catalog_dir / f"{metadata.id}.json"
        path.write_text(contents, encoding="utf-8")

    def exists(self, id: str):
        if len(id) == 13:
            # is isbn
            id = [
                meta for meta in self if meta.type == "book" and meta.data.isbn == id
            ][0]
        path = self.catalog_dir / f"{id}.json"
        return path.exists()

    def add(self, metadata: Metadata, cover_image: str):
        """Adds the given metadata to the library catalog."""
        if self.__contains__(metadata.id):
            raise ValueError(f"metadata {metadata.id} already exists")
        self.save(metadata)
        if cover_image:
            self.cover_image_manager.add(metadata.id, cover_image)

    def remove(self, id: str):
        """Removes metadata associated with the given id from the library catalog."""
        if not self.__contains__(id):
            raise KeyError(f"the metadata with id {id} does not exist")
        path = self.catalog_dir / f"{id}.json"
        send2trash(path)
        self.cover_image_manager.remove(id)

    def edit(self, metadata: Metadata, cover_image: bytes):
        self.save(metadata)
        self.cover_image_manager.remove(metadata.id)
        self.cover_image_manager.add(metadata.id, cover_image)

    def get(self, id: str) -> Metadata:
        path = self.catalog_dir / f"{id}.json"
        try:
            contents = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise LookupError(f"book {id} doesn't exist")

        metadata = from_dict(Metadata, json.loads(contents))
        cover_image = self.cover_image_manager.get(id)
        return (metadata, cover_image)
    

    def glob_over_dir(self) -> list[Path]:
        return list(self.catalog_dir.glob("*.json"))

    def get_num_books(self) -> int:
        files = self.glob_over_dir()
        return len(files)
    
    def __iter__(self):
        self.iter_paths = self.glob_over_dir()
        return self
    
    def __next__(self):
        if self.iter_paths:
            id = self.iter_paths[0].name[:-5]
            del self.iter_paths[0]
            return self.get(id)
        else:
            raise StopIteration

    def __len__(self):
        return self.get_num_books()

    def __contains__(self, id: str):
        self.exists(id)

    def __getitem__(self, id: str) -> Metadata:
        return self.get(id)

    def __delitem__(self, id: str):
        self.remove(id)
