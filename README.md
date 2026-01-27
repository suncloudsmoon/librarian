# Librarian
Librarian is a Library Management Software (LMS) designed exclusively for use in the command-line. It has many great features, such as chatting with a LLM using semantic search results. The software currently supports addition, removal, and editing of book entires in the book catalog, a file that keeps track of all the items in the library. The library's folder structure follows the Dewey Decimal System (DDS), which makes it much more familiar and easy to access library items. Moving on, the main purpose of this LMS system is to enable the centralization of knowledge through the use of the library catalog and to ease the process of researching and accessing knowledge through the feature of semantic search.
Semantic search enables users to find relevant books based on natural language queries. This allows users to find books much easier than using index-based search which only returns relevant results based on text matching. This LMS is designed primarily to exploit the advantages of semantic search and use its potential in making knowledge more accessible.

## How to Install
```
pip install bookshelf-manager
```

# How to use
It is possible to launch librarian by typing either 'librarian' or 'bookshelf-manager' in the terminal and hitting enter.
```
>>> :help
Commands:
  :add [path]               Adds a book to the library given by the path.
  :clear                    Clears the console window.
  :edit [id]                Edits the book's metadata.
  :exam                     Creates an exam by randomly picking 3 books in the library and generates questions based off of it.
  :go [#]                   Opens the file based on the search result #.
  :help                     Displays a list of commands.
  :info [#]                 Shows metadata about a given book identified by search result #.
  :legal                    Shows all the legal notices.
  :question [prompt]        Prompts the llm with context from ordinary search.
  :remove [id]              Removes the book idenitifed by id from the book catalog.
```

## To-Do
- Add sync capability via localhost through the use of python sockets
- Switch over to Foundry Local for AI models
- Allow other library classification systems like Library of Congress Classification (LCC)

## Credits
Due to the generous work of the authors in the following repositories, this software is made possible.
- [llama-cpp-python](https://github.com/abetlen/llama-cpp-python)
- [langchain](https://github.com/langchain-ai/langchain)