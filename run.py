from __future__ import annotations

import os
import sys
import questionary
from rich.console import Console
from rich.panel import Panel


def main():
    DIR = os.path.dirname(os.path.abspath(__file__))
    console = Console()

    console.print(Panel.fit(
        "[bold cyan]GRIMOIRE[/bold cyan]\n"
        "[dim]offline search engine & RAG for the cyberdeck[/dim]",
        border_style="bright_blue"
    ))

    option = questionary.select(
        "Choose your interface:",
        choices=[
            "CLI (rich prompts)",
            "TUI (Textual full-screen)",
            "Exit"
        ],
        pointer=">"
    ).ask()

    if option == "CLI (rich prompts)":
        sys.path.insert(0, os.path.join(DIR, 'frontend'))
        from cli import main
        main()
    elif option == "TUI (Textual full-screen)":
        sys.path.insert(0, os.path.join(DIR, 'frontend'))
        from cool_app import GrimoireApp
        app = GrimoireApp()
        app.run()
    else:
        console.print("[green]Farewell.[/green]")

if __name__ == "__main__":
    main()
