from bookshelf_manager import mcp
from bookshelf_manager.librarian import Config
from llama_index.llms.openai_like import OpenAILike


general_client = OpenAILike(
    api_base="http://localhost:1234/v1",
    api_key="dummy_key",
    model="qwen/qwen3.5-9b",
    is_function_calling_model=True,
    should_use_structured_outputs=True,
    is_chat_model=True,
    max_tokens=2048,
    timeout=180,
)

mcp.start_mcp_server(
    host="0.0.0.0",
    port=3000,
    librarian_path=".librarian",
    default_config=Config(),
    general_client=general_client,
)
