from .librarian import Config, Librarian
from mcp.server.fastmcp import FastMCP
from llama_index.llms.openai import OpenAI


def start_mcp_server(
    host: str,
    port: int,
    librarian_path,
    default_config: Config,
    ocr_client: OpenAI,
    general_client: OpenAI,
):
    mcp = FastMCP(host=host, port=port)
    librarian = Librarian(
        librarian_path=librarian_path,
        default_config=default_config,
        ocr_client=ocr_client,
        general_client=general_client,
    )

    for fn in (
        librarian.add,
        librarian.remove,
        librarian.edit,
        librarian.refresh,
        librarian.search,
        librarian.exists,
    ):
        mcp.tool()(fn)
    for fn in (librarian.info):
        mcp.resource(uri=f"librarian://{fn.__name__}")(fn)
