import os
import re
import sys
import webbrowser
from atexit import register
from pathlib import Path
from shutil import copy, get_terminal_size
from textwrap import fill
from uuid import uuid4

from markdown import markdown
from prompt_toolkit import HTML
from prompt_toolkit import print_formatted_text as print
from prompt_toolkit import prompt

from catalog_manager import Book
from librarian import Config, Librarian
from search_manager import SearchResult
from utils import defined_classifications, get_resource_path

if sys.platform == "win32":
    import win32api
    import win32con
    from py_setenv import setenv


class App:
    """Defines a library management application that enables the user to control a library via the command-line."""

    def get_app_path(self):
        return Path(get_resource_path("")).parent

    @staticmethod
    def get_env_paths(contents: str):
        return [path.lower() for path in contents.split(";")]

    def install(self, user=False):
        if sys.platform == "win32":
            contents = setenv("Path", user=user)
            lower_paths = self.get_env_paths(contents)

            original_path = str(self.get_app_path())
            lower_path = original_path.lower()
            if lower_path not in lower_paths:
                setenv("Path", original_path, append=True, user=user)
        else:
            raise NotImplementedError(
                "installation for non-Windows OS is not supported yet"
            )

    def uninstall(self):
        if sys.platform == "win32":
            privileges = [False, True]
            for user in privileges:
                contents = setenv("Path", user=user)
                original_paths = contents.split(";")
                lower_paths = self.get_env_paths(contents)

                lower_path = str(self.get_app_path()).lower()
                if lower_path in lower_paths:
                    original_paths.pop(lower_paths.index(lower_path))
                    setenv("Path", ";".join(original_paths), user=user)
        else:
            raise NotImplementedError(
                "uninstallation for non-Windows OS is not supported yet"
            )

    def process_args(self):
        if len(sys.argv) > 1:
            command = sys.argv[1]
            if command == "--install":
                mode = sys.argv[2]
                user = False if mode == "system" else True
                self.install(user)
            elif command == "--uninstall":
                self.uninstall()
            else:
                raise ValueError(f"unknown command {command}")
            return True

    def create_librarian_dir(self, library_path):
        os.mkdir(library_path)
        if sys.platform == "win32":
            win32api.SetFileAttributes(library_path, win32con.FILE_ATTRIBUTE_HIDDEN)

    def do_chore(self):
        # Chores
        library_path = os.path.join(os.getcwd(), ".librarian")
        default_config = Config()
        if not os.path.exists(library_path):
            print(
                "Welcome to Librarian, a simple program that helps you make sense of documents."
            )
            self.create_librarian_dir(library_path)
            while True:
                try:
                    answer = (
                        prompt(
                            "The default classification system is Dewey Decimal Classification (DDC), do you want to change it? (y/n) "
                        )
                        .strip()
                        .lower()
                    )
                    if answer == "y":
                        question = f"Choose one of the following\n"
                        for system in defined_classifications:
                            question += f"  {system}\n"
                        default_config.classification_system = (
                            prompt(question).strip().lower()
                        )
                    elif answer != "n":
                        raise ValueError(f"invalid input {answer}")
                    break
                except Exception as err:
                    print(HTML(f"<ansired>{err}</ansired>"))

            copy(
                get_resource_path(f"data/{default_config.classification_system}.json"),
                library_path,
            )

        # Initializing librarian
        self.librarian = Librarian(library_path, default_config)
        register(self.cleanup)

    def start(self):
        do_once = True
        while True:
            try:
                try:
                    query = prompt(">>> ").strip()
                except KeyboardInterrupt:
                    break
                else:
                    if do_once:
                        try:
                            self.do_chore()
                            self.db_refresh_check()
                        except Exception as err:
                            print(HTML(f"<ansired>{err}</ansired>"))
                            os._exit(1)
                        finally:
                            do_once = False

                if query.startswith(":"):
                    comps = query.split()
                    command = comps[0].lower()
                    match command:
                        case ":add":
                            path = Path(
                                query.removeprefix(comps[0]).lstrip().replace('"', "")
                            )
                            try:
                                book = self.get_book_info(extension=path.suffix)
                            except KeyboardInterrupt:
                                continue
                            self.librarian.add(path, book)
                        case ":clear" | ":cls":
                            os.system("cls" if os.name == "nt" else "clear")
                        case ":edit":
                            id = comps[1]
                            if id.isnumeric():
                                id = search_results[int(id) - 1].id
                            book = self.librarian.info(id)
                            try:
                                book_info = self.get_book_info(book=book)
                            except KeyboardInterrupt:
                                continue
                            else:
                                self.librarian.edit(book_info)
                        case ":go":
                            index: int = int(comps[1]) - 1
                            book: Book = search_results[index].get_book()
                            book_path: str = os.path.abspath(
                                self.librarian.get_book_path(book)
                            )
                            if sys.platform == "win32":
                                os.startfile(book_path)
                            elif book.filename.lower().endswith("pdf"):
                                webbrowser.open(book_path)
                            else:
                                raise NotImplementedError(
                                    "opening files from non-Windows OSs is not supported yet"
                                )
                        case ":help":
                            self.print_help()
                            print()
                        case ":info":
                            index: int = int(comps[1]) - 1
                            book: Book = search_results[index].get_book()
                            print(book)
                        case ":question" | ":chat" | ":iwonder":
                            markdown_output = self.librarian.question(
                                " ".join(word for word in comps[1:])
                            )
                            if self.librarian.config.exclude_thinking_tag:
                                markdown_output = re.sub(
                                    r"<think>.*</think>",
                                    "",
                                    markdown_output,
                                    flags=re.DOTALL,
                                )
                            markdown_output = markdown_output.replace(
                                "<think>", "&lt;think&gt;"
                            ).replace("</think>", "&lt;/think&gt;")
                            html_output = HTML(markdown(markdown_output))
                            print(html_output)
                        case ":quit" | ":q" | ":exit":
                            break
                        case ":remove" | ":delete":
                            id = comps[1]
                            if id.isnumeric():
                                id = search_results[int(id) - 1].id
                            self.librarian.remove(id)
                        case _:
                            raise ValueError(f'unknown command: "{command}"')
                    continue

                search_results = self.librarian.search(query)
                self.print_results(search_results)
            except Exception as err:
                print(HTML(f"<ansired>Error: {err}</ansired>"))

        # Goodbye
        user = os.getlogin()
        print(f"\nHave a nice day, {user}")

    def slugify(self, isbn: str, title: str, extension: str):
        clean_title = "-".join(title.lower().split())
        slugified = re.sub(r"[^\w-]", "", clean_title)[
            : 255 - len(isbn + extension + "-.")
        ]
        return f"{isbn}-{slugified}.{extension}"

    def is_valid_isbn(self, isbn: str) -> bool:
        """Checks if the given ISBN-13 is valid or not."""
        if len(isbn) != 13:
            return False

        total = sum(
            [int(digit) for digit in range(0, 13, 2)]
            + [int(digit) * 3 for digit in range(1, 13, 2)]
        )
        return total % 10 == 0

    def get_book_info(self, extension: str = None, book: Book = None) -> Book:
        error = lambda msg: HTML(f"<ansired>{msg}</ansired>")

        if not book:
            book = Book(
                id=str(uuid4()),
                filename=extension,
                isbn="",
                call_number="",
                title="",
                authors=[],
                publisher="",
                series="",
                edition="",
                volume="",
                year=0,
                url="",
                description="",
                notes="",
            )

        while True:
            isbn = re.sub(r"[^0-9]", "", prompt("ISBN: ", default=book.isbn))
            if len(isbn) == 0:
                isbn = "0" * 13
            if self.librarian.exists(isbn):
                print(error(f"ISBN {isbn} already exists in the database, try again."))
            if self.is_valid_isbn(isbn):
                break
            print(error("Invalid ISBN, try again."))

        while True:
            call_number = prompt("Call Number: ", default=book.call_number).strip()
            if len(call_number) > 0:
                break
            print(error("Call number needs to be specified, try again."))

        while True:
            title = prompt("Title: ", default=book.title).strip()
            if len(title) > 0:
                break
            print(error("Empty title, try again."))

        while True:
            names = str(book.authors).replace("'", "")[1:-1]
            authors = [
                author.title().strip()
                for author in prompt("Authors: ", default=names).split(",")
            ]
            if len(authors[0]) > 0:
                break
            print(error("Author list is too short, try again."))

        clean = lambda text: text.strip().title()
        publisher = clean(prompt("Publisher: ", default=book.publisher))
        series = clean(prompt("Series: ", default=book.series))
        edition = clean(prompt("Edition: ", default=book.edition))
        volume = clean(prompt("Volume: ", default=book.volume))

        while True:
            try:
                default_year = "" if book.year == 0 else str(book.year)
                result = prompt("Year: ", default=default_year)
                year = int(result)
            except ValueError:
                print(error("Invalid year, try again."))
            else:
                break

        url = prompt("URL: ", default=book.url).strip()
        description = prompt("Description: ", default=book.description).strip()
        notes = prompt("Notes: ", default=book.notes).strip()

        return Book(
            id=book.id,
            filename=self.slugify(
                isbn, title, book.filename.rsplit(".", maxsplit=1)[-1]
            ),
            isbn=isbn,
            call_number=call_number,
            title=title,
            authors=authors,
            publisher=publisher,
            series=series,
            edition=edition,
            volume=volume,
            year=year,
            url=url,
            description=description,
            notes=notes,
        )

    def print_results(self, results: list[SearchResult]):
        columns = get_terminal_size().columns

        for i, result in enumerate(results):
            print(f"Result {i+1}")
            print("-" * columns)
            contents = fill(
                result.page_content,
                width=columns,
                initial_indent="  “",
                subsequent_indent="  ",
                max_lines=2,
                placeholder=" [...]”",
            )
            if not contents.endswith("”"):
                contents += "”"
            print(
                contents,
                end="\n\n",
            )

            metadata = {
                "title": result.title,
                "authors": str(result.authors)[1:-1].replace("'", ""),
                "call_number": result.call_number,
                "page": result.page,
            }

            for name, value in metadata.items():
                formatted_name = name.replace("_", " ").title()
                print(f"  {formatted_name:>{len('call_number')}} : {value}")
            print()

    def print_help(self):
        commands = {
            ":add [path]": "adds a book to the library given by the path.",
            ":clear": "clears the console window.",
            ":edit [id]": "edits the book's metadata.",
            ":go [#]": "opens the file based on the search result #.",
            ":help": "displays a list of commands.",
            ":info [#]": "shows metadata about a given book identified by search result #.",
            ":legal": "shows all the legal notices.",
            ":question [prompt]": "prompts the llm with context from ordinary search.",
            ":remove [id]": "removes the book idenitifed by id from the book catalog.",
        }
        print("Commands:")
        for command, info in commands.items():
            print(f"  {command:<25} {info.capitalize()}")

    def add_to_path(self, path):
        if os.name != "nt":
            raise NotImplementedError(
                "adding a path to user paths not supported on non-NT operating systems"
            )
        contents = os.environ["Path"]
        paths = contents.split(";")
        if path not in paths:
            setenv("Path", path, append=True, user=True)

    def db_refresh_check(self):
        if self.librarian.is_database_mismatch():
            print(
                "There is a mismatch between the catalog manager and the vector database records."
            )
            choice = prompt(
                "🔃 A database refresh is recommended. Proceed? (y/n) "
            ).lower()
            if choice == "y":
                self.librarian.refresh()
            elif choice != "n":
                raise ValueError("unknown choice {choice}")

    def cleanup(self):
        self.librarian.close()


if __name__ == "__main__":
    app = App()
    if not app.process_args():
        app.start()
