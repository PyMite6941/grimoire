from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))

from grimoire import Grimoire
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt
from rich.markdown import Markdown
import questionary

console = Console()

TOME_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend'))
TOME_PATH = os.path.join(TOME_DIR, 'grimoire.db')


def main():
    console.print(Panel.fit(
        "[bold cyan]GRIMOIRE[/bold cyan] — offline search + RAG\n"
        "[dim]Your tome of knowledge[/dim]",
        border_style="bright_blue"
    ))

    while True:
        action = questionary.select(
            "What would you like to do?",
            choices=[
                "Search tome",
                "Ask a question (RAG)",
                "Ingest documents",
                "Embed documents for RAG",
                "Show stats",
                "Get document by ID",
                "Exit"
            ],
            pointer=">"
        ).ask()

        if action == "Search tome":
            query = Prompt.ask("Search query")
            raw = questionary.confirm("Use raw FTS5 syntax?", default=False).ask()
            try:
                g = Grimoire(TOME_PATH)
                results = g.search(query, n=20, raw=raw)
                if results:
                    table = Table(title=f"Results for '{query}'")
                    table.add_column("ID", style="cyan")
                    table.add_column("Score", style="green")
                    table.add_column("Title", style="bold")
                    table.add_column("Path", style="dim")
                    for r in results:
                        table.add_row(str(r['id']), f"{r['score']:.2f}", r['title'], r['path'])
                    console.print(table)
                    doc_id = Prompt.ask("View document by ID", default="")
                    if doc_id:
                        body = g.get(int(doc_id))
                        if body:
                            console.print(Panel(body[:2000], title=f"Document {doc_id}"))
                else:
                    console.print("[yellow]No results found[/yellow]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        elif action == "Ask a question (RAG)":
            question = Prompt.ask("Your question")
            try:
                with console.status("[cyan]Searching tome and generating answer...[/cyan]"):
                    g = Grimoire(TOME_PATH)
                    result = g.query(question)
                console.print(Panel(Markdown(result["answer"]), title="Answer"))
                if result["sources"]:
                    console.print("\n[bold cyan]Sources:[/bold cyan]")
                    for s in result["sources"]:
                        console.print(f"  [{s['id']}] {s['title']}  (similarity: {s['score']:.3f})")
                        console.print(f"       [dim]{s['path']}[/dim]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
                console.print("[yellow]Make sure Ollama is running and has nomic-embed-text + a generation model[/yellow]")

        elif action == "Ingest documents":
            path = Prompt.ask("Path to file or directory")
            max_gb = float(Prompt.ask("Max store size (GB)", default="1.0"))
            try:
                import argparse
                ns = argparse.Namespace(path=path, max_gb=max_gb, db=TOME_PATH)
                from grimoire import cmd_ingest
                cmd_ingest(ns)
                console.print("[green]Ingest complete[/green]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        elif action == "Embed documents for RAG":
            try:
                g = Grimoire(TOME_PATH)
                with console.status("[cyan]Embedding documents...[/cyan]"):
                    n = g.embed_corpus()
                console.print(f"[green]Embedded {n} documents[/green]")
            except Exception as e:
                console.print(f"[yellow]Embedding error (Ollama running?): {e}[/yellow]")

        elif action == "Show stats":
            try:
                g = Grimoire(TOME_PATH)
                s = g.stats()
                if s["doc_count"]:
                    console.print(Panel(
                        f"Documents:    {s['doc_count']}\n"
                        f"Store:        {s['store_mb']:.1f}/{s['budget_mb']:.1f} MB\n"
                        f"Original:     {s['orig_mb']:.1f} MB\n"
                        f"Compressed:   {s['comp_mb']:.1f} MB\n"
                        f"Ratio:        {s['ratio']:.1f}x\n"
                        f"Codec:        {s['codec']}\n"
                        f"Embedded:     {s['embed_count']} docs",
                        title="Tome Statistics"
                    ))
                else:
                    console.print("[yellow]No tome found[/yellow]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        elif action == "Get document by ID":
            doc_id = int(Prompt.ask("Document ID"))
            try:
                g = Grimoire(TOME_PATH)
                text = g.get(doc_id)
                if text:
                    console.print(Panel(text[:2000], title=f"Document {doc_id}"))
                else:
                    console.print("[red]Document not found[/red]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        elif action == "Exit":
            console.print("[green]Farewell.[/green]")
            break


if __name__ == "__main__":
    main()
