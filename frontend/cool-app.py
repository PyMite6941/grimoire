from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))

from grimoire import Grimoire
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Header, Footer, Input, Button, DataTable, Static, RichLog, TextArea
)
from textual import work

TOME_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend'))
TOME_PATH = os.path.join(TOME_DIR, 'grimoire.db')


class StatsScreen(ModalScreen):
    TITLE = "Tome Statistics"

    def compose(self) -> ComposeResult:
        g = Grimoire(TOME_PATH) if os.path.exists(TOME_PATH) else None
        s = g.stats() if g else {}
        yield Static(f"[bold cyan]Tome Statistics[/bold cyan]\n\n"
                     f"Documents:     {s.get('doc_count', 0)}\n"
                     f"Store:         {s.get('store_mb', 0):.1f} MB\n"
                     f"Original:      {s.get('orig_mb', 0):.1f} MB\n"
                     f"Compressed:    {s.get('comp_mb', 0):.1f} MB\n"
                     f"Ratio:         {s.get('ratio', 0):.1f}x\n"
                     f"Codec:         {s.get('codec', 'n/a')}\n"
                     f"Embedded docs: {s.get('embed_count', 0)}")
        yield Button("Close", variant="primary", id="close-stats")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-stats":
            self.app.pop_screen()


class DetailScreen(Screen):
    def __init__(self, doc_id: int, title: str):
        super().__init__()
        self.doc_id = doc_id
        self.doc_title = title

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield ScrollableContainer(Static(id="detail-content"))
        yield Footer()

    def on_mount(self) -> None:
        g = Grimoire(TOME_PATH) if os.path.exists(TOME_PATH) else None
        text = g.get(self.doc_id) if g else "(tome not found)"
        content = self.query_one("#detail-content", Static)
        content.update(f"[bold cyan]{self.doc_title}[/bold cyan]\n\n{text}")


class QueryScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield TextArea(id="query-input", soft_wrap=True)
        with Horizontal():
            yield Button("Ask", variant="primary", id="ask-btn")
            yield Button("Back", id="back-btn")
        yield RichLog(id="answer-log", highlight=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#query-input", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ask-btn":
            self._do_query()
        elif event.button.id == "back-btn":
            self.app.pop_screen()

    @work(thread=True)
    def _do_query(self):
        q = self.query_one("#query-input", TextArea).text.strip()
        if not q:
            return
        log = self.query_one("#answer-log", RichLog)
        self.call_from_thread(log.clear)
        self.call_from_thread(log.write, "[cyan]Searching tome and generating answer...[/cyan]")
        try:
            g = Grimoire(TOME_PATH)
            result = g.query(q)
            self.call_from_thread(log.clear)
            self.call_from_thread(log.write, f"[bold cyan]Answer:[/bold cyan]\n{result['answer']}")
            if result["sources"]:
                self.call_from_thread(log.write, f"\n[bold]Sources:[/bold]")
                for s in result["sources"]:
                    self.call_from_thread(
                        log.write, f"  [{s['id']}] {s['title']} ({s['score']:.3f})")
        except Exception as e:
            self.call_from_thread(log.clear)
            self.call_from_thread(log.write, f"[red]Error: {e}[/red]")


class GrimoireApp(App):
    TITLE = "GRIMOIRE"
    SUBTITLE = "offline search + RAG"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+s", "push_screen('stats')", "Stats"),
        Binding("ctrl+r", "push_screen('query')", "RAG Query"),
        Binding("escape", "focus_search", "Search"),
    ]
    CSS = """
    #search-row { height: 5; margin: 1; }
    #results-box { height: 1fr; }
    #status-bar { height: 3; background: $surface; }
    DataTable { height: 1fr; }
    Screen { background: $surface; }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="search-row"):
            yield Input(placeholder="Search query (or FTS5 syntax)...", id="search-input")
            yield Button("Search", variant="primary", id="search-btn")
            yield Button("RAG Query", id="query-btn")
        yield DataTable(id="results-table")
        yield Static(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("Score", "Title", "Path")
        status = self.query_one("#status-bar", Static)
        if os.path.exists(TOME_PATH):
            g = Grimoire(TOME_PATH)
            s = g.stats()
            status.update(f"Tome: {s['doc_count']} docs, "
                          f"{s['store_mb']:.1f}/{s['budget_mb']:.1f} MB"
                          f"  |  Ctrl+R = RAG query")
        else:
            status.update("No tome found — run: python backend/grimoire.py ingest <path>")
        self.query_one("#search-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            self.do_search(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "search-btn":
            q = self.query_one("#search-input", Input).value
            self.do_search(q)
        elif event.button.id == "query-btn":
            self.push_screen(QueryScreen())

    @work(thread=True)
    def do_search(self, query: str) -> None:
        query = query.strip()
        if not query:
            return
        g = Grimoire(TOME_PATH) if os.path.exists(TOME_PATH) else None
        if not g:
            return
        try:
            results = g.search(query, n=50, raw=False)
        except Exception:
            results = g.search(query, n=50, raw=True)

        table = self.query_one("#results-table", DataTable)
        status = self.query_one("#status-bar", Static)
        self.call_from_thread(table.clear)
        if results:
            for hit in results:
                self.call_from_thread(
                    table.add_row, f"{hit['score']:.2f}", hit['title'], hit['path'])
            self.call_from_thread(status.update, f"{len(results)} results for '{query}'")
        else:
            self.call_from_thread(status.update, f"No results for '{query}'")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_key = event.row_key
        table = self.query_one("#results-table", DataTable)
        row = table.get_row(row_key)
        if row:
            title = row[1]
            g = Grimoire(TOME_PATH) if os.path.exists(TOME_PATH) else None
            if g:
                results = g.search(row[1], n=1)
                if results:
                    self.push_screen(DetailScreen(results[0]['id'], title))


if __name__ == "__main__":
    app = GrimoireApp()
    app.run()
