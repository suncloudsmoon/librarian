from pathlib import Path

from .librarian import Config, Librarian
from mcp.server.fastmcp import FastMCP
from llama_index.llms.openai_like import OpenAILike

def start_mcp_server(
    host: str,
    port: int,
    librarian_path,
    default_config: Config,
    general_client: OpenAILike,
):
    def get_library_path() -> str:
        """
        Returns the absolute path to the library in the filesystem.
        """
        return str(Path(librarian_path).absolute())
    
    mcp = FastMCP(host=host, port=port)
    librarian = Librarian(
        librarian_path=librarian_path,
        default_config=default_config,
        general_client=general_client,
    )

    for fn in (
        librarian.add,
        librarian.remove,
        librarian.edit,
        librarian.refresh,
        librarian.is_database_mismatch,
        librarian.search,
        librarian.exists,
        librarian.sync,
        librarian.get_doc_path_by_id,
        librarian.info
    ):
        mcp.tool()(fn)

    mcp.resource(uri="librarian://doc/{id}")(librarian.get_doc_path_by_id)
    mcp.resource(uri="librarian://get_all_documents")(librarian.get_all_documents)
    mcp.resource(uri="librarian://get_library_path")(get_library_path)

    mcp.run(transport="streamable-http")
