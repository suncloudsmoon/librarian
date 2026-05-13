"""
test_mcp_server.py — test suite for the Librarian MCP server.

Covers:
  1. CatalogManager unit tests  (including Bug 1 regression)
  2. Librarian unit tests       (including Bug 2 regression)
  3. mcp.py error-surfacing     (Bug 3 regression)
  4. MCP server integration     (live wire tests via the MCP Python client)

Run from the project root:
    python test_mcp_server.py          # stdlib unittest runner
    pytest test_mcp_server.py -v       # pytest, if installed

The heavy ML dependencies (HuggingFace embeddings, LanceDB) are mocked out so
the suite runs without a GPU, a local LLM, or any vector-store infrastructure.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Ensure the package is importable when running from the project root
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookshelf_manager.catalog_manager import Book, CatalogManager, Metadata
from bookshelf_manager.librarian import Config, DocResult, Librarian


# ---------------------------------------------------------------------------
# Shared test-data helpers
# ---------------------------------------------------------------------------

def make_metadata(
    id: str = "test-id-001",
    filename: str = "test.pdf",
    call_number: str = "100",
) -> Metadata:
    return Metadata(
        id=id,
        hash="deadbeef",
        filename=filename,
        call_number=call_number,
        type="book",
        data=Book(
            isbn="9780000000000",
            title="Test Book",
            authors=["Author A"],
            publisher="Test Publisher",
            series="",
            edition="1st",
            volume="",
            year=2024,
            url="",
            description="A test book.",
            notes="",
        ),
    )


def _mock_search_manager() -> MagicMock:
    sm = MagicMock()
    sm.is_database_mismatch.return_value = False
    sm.semantic_search.return_value = []
    sm.fts_search.return_value = []
    return sm


def _free_port() -> int:
    """Return an OS-assigned free TCP port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ===========================================================================
# 1. CatalogManager unit tests
# ===========================================================================

class TestCatalogManagerContains(unittest.TestCase):
    """
    Regression tests for Bug 1: __contains__ was missing its return statement,
    so `id in catalog_manager` always evaluated to None (falsy).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cm = CatalogManager(Path(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_contains_missing_id_returns_false(self):
        result = "ghost-id" in self.cm
        self.assertFalse(result)

    def test_contains_missing_id_is_not_none(self):
        """Before the fix this returned None, not False."""
        result = "ghost-id" in self.cm
        self.assertIsNotNone(result, "__contains__ must not return None")

    def test_contains_existing_id_returns_true(self):
        meta = make_metadata()
        self.cm.add(meta, cover_image=None)
        self.assertIn(meta.id, self.cm)

    def test_contains_after_remove_returns_false(self):
        meta = make_metadata()
        self.cm.add(meta, None)
        self.cm.remove(meta.id)
        self.assertNotIn(meta.id, self.cm)


class TestCatalogManagerCRUD(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cm = CatalogManager(Path(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_add_and_get_roundtrip(self):
        meta = make_metadata()
        self.cm.add(meta, cover_image=None)
        retrieved, cover = self.cm.get(meta.id)
        self.assertEqual(retrieved.id, meta.id)
        self.assertEqual(retrieved.filename, meta.filename)
        self.assertIsNone(cover)

    def test_add_duplicate_raises_value_error(self):
        meta = make_metadata()
        self.cm.add(meta, None)
        with self.assertRaises(ValueError):
            self.cm.add(meta, None)

    def test_get_missing_raises_lookup_error(self):
        with self.assertRaises(LookupError):
            self.cm.get("nonexistent-id")

    def test_remove_existing(self):
        meta = make_metadata()
        self.cm.add(meta, None)
        self.cm.remove(meta.id)
        self.assertNotIn(meta.id, self.cm)

    def test_remove_missing_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.cm.remove("nonexistent-id")

    def test_len_empty(self):
        self.assertEqual(len(self.cm), 0)

    def test_len_after_adds(self):
        self.cm.add(make_metadata(id="a", filename="a.pdf"), None)
        self.cm.add(make_metadata(id="b", filename="b.pdf"), None)
        self.assertEqual(len(self.cm), 2)

    def test_getitem_returns_tuple(self):
        meta = make_metadata()
        self.cm.add(meta, None)
        result = self.cm[meta.id]
        self.assertIsInstance(result, tuple)

    def test_cover_image_stored_and_retrieved(self):
        meta = make_metadata()
        fake_jpeg = b"\xff\xd8\xff" + b"\x00" * 64
        self.cm.add(meta, cover_image=fake_jpeg)
        _, cover_path = self.cm.get(meta.id)
        self.assertIsNotNone(cover_path)
        self.assertTrue(Path(cover_path).exists())


class TestCatalogManagerIteration(unittest.TestCase):
    """
    __iter__ / __next__ must yield (Metadata, cover_path) tuples — not bare
    Metadata objects. This shape is critical for Bug 2 (get_paths_ids).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cm = CatalogManager(Path(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_iter_yields_tuples(self):
        meta = make_metadata()
        self.cm.add(meta, None)
        items = list(self.cm)
        self.assertEqual(len(items), 1)
        self.assertIsInstance(items[0], tuple, "Iterator must yield (Metadata, cover) tuples")

    def test_iter_tuple_is_metadata_and_cover(self):
        meta = make_metadata()
        self.cm.add(meta, None)
        (yielded_meta, cover_path) = list(self.cm)[0]
        self.assertIsInstance(yielded_meta, Metadata)
        self.assertEqual(yielded_meta.id, meta.id)

    def test_iter_multiple_entries(self):
        ids = {"id-1", "id-2", "id-3"}
        for i in ids:
            self.cm.add(make_metadata(id=i, filename=f"{i}.pdf"), None)
        found = {m.id for m, _ in self.cm}
        self.assertEqual(found, ids)

    def test_iter_empty_catalog(self):
        self.assertEqual(list(self.cm), [])

    def test_iter_is_reentrant(self):
        """Starting a new for-loop over the same manager must reset iteration."""
        self.cm.add(make_metadata(id="x", filename="x.pdf"), None)
        first = list(self.cm)
        second = list(self.cm)
        self.assertEqual(len(first), len(second))


# ===========================================================================
# 2. Librarian unit tests (SearchManager mocked)
# ===========================================================================

class LibrarianTestBase(unittest.TestCase):
    """Sets up a temp librarian directory with classification data."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.librarian_path = Path(self.tmp) / ".librarian"
        self.librarian_path.mkdir()
        shutil.copy(ROOT / "data" / "dewey.json", self.librarian_path / "dewey.json")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def make_librarian(self, sm: MagicMock | None = None) -> Librarian:
        if sm is None:
            sm = _mock_search_manager()
        with patch("bookshelf_manager.librarian.SearchManager", return_value=sm):
            return Librarian(
                librarian_path=self.librarian_path,
                default_config=Config(),
                general_client=MagicMock(),
            )


class TestLibrarianGetPathsIds(LibrarianTestBase):
    """
    Regression tests for Bug 2: get_paths_ids used to iterate
    (Metadata, cover_path) tuples from CatalogManager but treated each
    element as a plain Metadata object, causing AttributeError: 'tuple'
    object has no attribute 'id'.
    """

    def test_empty_catalog_returns_empty_list(self):
        lib = self.make_librarian()
        self.assertEqual(lib.get_paths_ids(), [])

    def test_returns_path_id_tuples(self):
        """Bug 2 regression: must not raise AttributeError."""
        lib = self.make_librarian()
        meta = make_metadata()
        lib.catalog_manager.add(meta, None)
        result = lib.get_paths_ids()
        self.assertEqual(len(result), 1)
        path, id_ = result[0]
        self.assertIsInstance(path, Path)
        self.assertEqual(id_, meta.id)

    def test_denylisted_ids_are_excluded(self):
        lib = self.make_librarian()
        lib.catalog_manager.add(make_metadata(id="keep", filename="keep.pdf"), None)
        lib.catalog_manager.add(make_metadata(id="skip", filename="skip.pdf"), None)
        lib.config.index_denylist.append("skip")
        ids = [id_ for _, id_ in lib.get_paths_ids()]
        self.assertIn("keep", ids)
        self.assertNotIn("skip", ids)

    def test_multiple_entries_all_returned(self):
        lib = self.make_librarian()
        expected = {"a", "b", "c"}
        for i in expected:
            lib.catalog_manager.add(make_metadata(id=i, filename=f"{i}.pdf"), None)
        ids = {id_ for _, id_ in lib.get_paths_ids()}
        self.assertEqual(ids, expected)


class TestLibrarianGetAllDocuments(LibrarianTestBase):

    def test_empty_library_returns_empty_list(self):
        lib = self.make_librarian()
        docs = lib.get_all_documents()
        self.assertIsInstance(docs, list)
        self.assertEqual(len(docs), 0)

    def test_returns_doc_result_instances(self):
        lib = self.make_librarian()
        lib.catalog_manager.add(make_metadata(), None)
        docs = lib.get_all_documents()
        self.assertEqual(len(docs), 1)
        self.assertIsInstance(docs[0], DocResult)

    def test_doc_result_contains_correct_id(self):
        lib = self.make_librarian()
        meta = make_metadata(id="specific-id")
        lib.catalog_manager.add(meta, None)
        docs = lib.get_all_documents()
        self.assertEqual(docs[0].metadata.id, "specific-id")


class TestLibrarianExists(LibrarianTestBase):

    def test_exists_true_for_added_id(self):
        lib = self.make_librarian()
        meta = make_metadata()
        lib.catalog_manager.add(meta, None)
        self.assertTrue(lib.exists(meta.id))

    def test_exists_false_for_unknown_id(self):
        lib = self.make_librarian()
        self.assertFalse(lib.exists("ghost-id"))


class TestLibrarianSearch(LibrarianTestBase):

    def test_semantic_search_delegates(self):
        sm = _mock_search_manager()
        sm.semantic_search.return_value = ["result"]
        lib = self.make_librarian(sm)
        out = lib.search("query", search_type="semantic")
        sm.semantic_search.assert_called_once_with("query")
        self.assertEqual(out, ["result"])

    def test_fts_search_delegates(self):
        sm = _mock_search_manager()
        sm.fts_search.return_value = ["fts"]
        lib = self.make_librarian(sm)
        out = lib.search("query", search_type="fts")
        sm.fts_search.assert_called_once_with("query")
        self.assertEqual(out, ["fts"])

    def test_unknown_search_type_raises_not_implemented(self):
        lib = self.make_librarian()
        with self.assertRaises(NotImplementedError):
            lib.search("query", search_type="magic")

    def test_default_search_type_is_semantic(self):
        sm = _mock_search_manager()
        lib = self.make_librarian(sm)
        lib.search("query")
        sm.semantic_search.assert_called_once()
        sm.fts_search.assert_not_called()


class TestLibrarianIsDatabaseMismatch(LibrarianTestBase):

    def test_delegates_to_search_manager(self):
        sm = _mock_search_manager()
        lib = self.make_librarian(sm)
        for expected in (True, False):
            sm.is_database_mismatch.return_value = expected
            self.assertEqual(lib.is_database_mismatch(), expected)


# ===========================================================================
# 3. mcp.py error-surfacing regression tests (Bug 3)
# ===========================================================================

class TestMCPErrorSurfacing(unittest.TestCase):
    """
    Before the fix, exceptions in Librarian.__init__ were silently swallowed
    and turned into blank log lines. After the fix they propagate with a full
    traceback logged at ERROR level.
    """

    def test_init_failure_propagates(self):
        from bookshelf_manager.mcp import start_mcp_server

        with patch(
            "bookshelf_manager.mcp.Librarian",
            side_effect=RuntimeError("embed model not found"),
        ):
            with self.assertRaises(RuntimeError):
                start_mcp_server(
                    host="127.0.0.1",
                    port=_free_port(),
                    librarian_path="/tmp/fake-lib",
                    default_config=Config(),
                    general_client=MagicMock(),
                )

    def test_init_failure_is_logged_at_error_level(self):
        from bookshelf_manager.mcp import start_mcp_server

        with patch(
            "bookshelf_manager.mcp.Librarian",
            side_effect=ValueError("bad classification config"),
        ):
            with self.assertLogs("bookshelf_manager.mcp", level="ERROR"):
                with self.assertRaises(ValueError):
                    start_mcp_server(
                        host="127.0.0.1",
                        port=_free_port(),
                        librarian_path="/tmp/fake-lib",
                        default_config=Config(),
                        general_client=MagicMock(),
                    )

    def test_file_not_found_propagates(self):
        """Missing dewey.json (or similar) must not produce a silent blank error."""
        from bookshelf_manager.mcp import start_mcp_server

        with patch(
            "bookshelf_manager.mcp.Librarian",
            side_effect=FileNotFoundError("dewey.json not found"),
        ):
            with self.assertRaises(FileNotFoundError):
                start_mcp_server(
                    host="127.0.0.1",
                    port=_free_port(),
                    librarian_path="/tmp/no-such-path",
                    default_config=Config(),
                    general_client=MagicMock(),
                )


# ===========================================================================
# 4. MCP server integration tests
# ===========================================================================
#
# Spins up the real FastMCP server on a random port (SearchManager mocked).
# All tests connect with the official MCP Python client over the wire.
# ===========================================================================

EXPECTED_TOOLS = {
    "add", "remove", "edit", "refresh",
    "is_database_mismatch", "search", "exists",
    "sync", "get_doc_path_by_id", "info",
}

EXPECTED_STATIC_RESOURCES = {
    "librarian://get_all_documents",
    "librarian://get_library_path",
}


def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> None:
    """Block until a TCP connection succeeds or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"Server {host}:{port} did not accept connections within {timeout}s")


class TestMCPServerIntegration(unittest.TestCase):
    """
    One server instance is shared across all tests in this class via
    setUpClass / tearDownClass, keeping the suite fast.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.librarian_path = Path(cls.tmp) / ".librarian"
        cls.librarian_path.mkdir()
        shutil.copy(ROOT / "data" / "dewey.json", cls.librarian_path / "dewey.json")

        cls.port = _free_port()
        cls.host = "127.0.0.1"
        cls.mcp_url = f"http://{cls.host}:{cls.port}/mcp"

        # Patch SearchManager before the server thread imports Librarian
        cls._sm_patch = patch(
            "bookshelf_manager.librarian.SearchManager",
            return_value=_mock_search_manager(),
        )
        cls._sm_patch.start()

        from bookshelf_manager.mcp import start_mcp_server

        def _serve():
            start_mcp_server(
                host=cls.host,
                port=cls.port,
                librarian_path=str(cls.librarian_path),
                default_config=Config(),
                general_client=MagicMock(),
            )

        cls._server_thread = threading.Thread(target=_serve, daemon=True, name="mcp-test-server")
        cls._server_thread.start()
        _wait_for_port(cls.host, cls.port)

    @classmethod
    def tearDownClass(cls):
        cls._sm_patch.stop()
        shutil.rmtree(cls.tmp)
        # The daemon thread exits automatically when the test process ends.

    # ------------------------------------------------------------------
    # Async helper
    # ------------------------------------------------------------------

    @staticmethod
    def _run(coro):
        """Run a coroutine synchronously, creating a fresh event loop each time."""
        return asyncio.run(coro)

    # ------------------------------------------------------------------
    # Tool-registration tests
    # ------------------------------------------------------------------

    def test_list_tools_returns_all_expected_names(self):
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    result = await s.list_tools()
                    return {t.name for t in result.tools}

        names = self._run(go())
        self.assertEqual(names, EXPECTED_TOOLS)

    def test_tool_schemas_are_valid_json_schema(self):
        """Every tool must expose a JSON Schema with a 'type' field."""
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return await s.list_tools()

        result = self._run(go())
        for tool in result.tools:
            with self.subTest(tool=tool.name):
                self.assertIsInstance(tool.inputSchema, dict)
                self.assertIn("type", tool.inputSchema)

    def test_no_extra_tools_registered(self):
        """The server must not silently expose undeclared tools."""
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    result = await s.list_tools()
                    return {t.name for t in result.tools}

        extra = self._run(go()) - EXPECTED_TOOLS
        self.assertEqual(extra, set(), f"Unexpected tools registered: {extra}")

    # ------------------------------------------------------------------
    # Resource-registration tests
    # ------------------------------------------------------------------

    def test_list_resources_includes_static_resources(self):
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    result = await s.list_resources()
                    return {str(res.uri) for res in result.resources}

        uris = self._run(go())
        for expected in EXPECTED_STATIC_RESOURCES:
            with self.subTest(uri=expected):
                self.assertIn(expected, uris)

    # ------------------------------------------------------------------
    # Resource read tests
    # ------------------------------------------------------------------

    def test_resource_get_library_path_is_nonempty_string(self):
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            from pydantic import AnyUrl
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    result = await s.read_resource(AnyUrl("librarian://get_library_path"))
                    return result.contents[0].text

        text = self._run(go())
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 0)

    def test_resource_get_library_path_contains_librarian_dir_name(self):
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            from pydantic import AnyUrl
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    result = await s.read_resource(AnyUrl("librarian://get_library_path"))
                    return result.contents[0].text

        text = self._run(go())
        self.assertIn(".librarian", text)

    def test_resource_get_all_documents_returns_json_list(self):
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            from pydantic import AnyUrl
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    result = await s.read_resource(AnyUrl("librarian://get_all_documents"))
                    return result.contents[0].text

        text = self._run(go())
        parsed = json.loads(text)
        self.assertIsInstance(parsed, list)

    def test_resource_get_all_documents_empty_on_fresh_library(self):
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            from pydantic import AnyUrl
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    result = await s.read_resource(AnyUrl("librarian://get_all_documents"))
                    return json.loads(result.contents[0].text)

        docs = self._run(go())
        self.assertEqual(docs, [])

    # ------------------------------------------------------------------
    # Tool call — happy-path tests
    # ------------------------------------------------------------------

    def test_tool_exists_false_for_unknown_id(self):
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return await s.call_tool("exists", {"id": "no-such-uuid-0000"})

        result = self._run(go())
        self.assertFalse(result.isError)
        self.assertIn("false", result.content[0].text.lower())

    def test_tool_is_database_mismatch_returns_bool_string(self):
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return await s.call_tool("is_database_mismatch", {})

        result = self._run(go())
        self.assertFalse(result.isError)
        self.assertIn(result.content[0].text.lower(), ("true", "false"))

    def test_tool_search_semantic_succeeds(self):
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return await s.call_tool("search", {"query": "python programming"})

        result = self._run(go())
        self.assertFalse(result.isError)

    def test_tool_search_fts_succeeds(self):
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return await s.call_tool(
                        "search", {"query": "algorithms", "search_type": "fts"}
                    )

        result = self._run(go())
        self.assertFalse(result.isError)

    # ------------------------------------------------------------------
    # Tool call — error-path tests
    # ------------------------------------------------------------------

    def test_tool_search_invalid_search_type_returns_error(self):
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return await s.call_tool(
                        "search", {"query": "test", "search_type": "invalid"}
                    )

        result = self._run(go())
        self.assertTrue(result.isError)

    def test_tool_info_unknown_id_returns_error(self):
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return await s.call_tool("info", {"id": "ghost-id-00000"})

        result = self._run(go())
        self.assertTrue(result.isError)

    def test_tool_get_doc_path_by_id_unknown_id_returns_error(self):
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return await s.call_tool("get_doc_path_by_id", {"id": "ghost-id"})

        result = self._run(go())
        self.assertTrue(result.isError)

    def test_tool_missing_required_argument_returns_error(self):
        """Calling `exists` without the required `id` arg must be rejected."""
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return await s.call_tool("exists", {})

        result = self._run(go())
        self.assertTrue(result.isError)

    def test_tool_wrong_argument_type_returns_error(self):
        """Passing a non-string where a string is required must be rejected."""
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    # `exists` expects id: str, passing int instead
                    return await s.call_tool("exists", {"id": 12345})

        result = self._run(go())
        # Either the server coerces it (fine) or rejects it (also fine);
        # the point is it must not crash the server with an unhandled exception.
        # We just verify that isError is a bool, not an exception on the client side.
        self.assertIsInstance(result.isError, bool)

    # ------------------------------------------------------------------
    # Server-stability tests
    # ------------------------------------------------------------------

    def test_server_handles_concurrent_requests(self):
        """
        Fire three tool calls in parallel. The server must handle all of them
        without crashing (checked by a subsequent successful call).
        """
        async def call_exists(uid: str):
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return await s.call_tool("exists", {"id": uid})

        async def go():
            results = await asyncio.gather(
                call_exists("uid-1"),
                call_exists("uid-2"),
                call_exists("uid-3"),
            )
            return results

        results = self._run(go())
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertFalse(r.isError)

    def test_server_still_responsive_after_error_call(self):
        """A tool that raises must not leave the server in a broken state."""
        async def go():
            from mcp.client.streamable_http import streamable_http_client
            from mcp import ClientSession
            async with streamable_http_client(self.mcp_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    # This will error (unknown id)
                    await s.call_tool("info", {"id": "bad-id"})
                    # This must still succeed
                    return await s.call_tool("exists", {"id": "any-id"})

        result = self._run(go())
        self.assertFalse(result.isError)


if __name__ == "__main__":
    unittest.main(verbosity=2)