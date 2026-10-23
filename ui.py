"""Optional Rich terminal interface for Ai-Assistant."""

import os
import shutil
from datetime import datetime

try:
    from rich import box
    from rich.align import Align
    from rich.console import Console
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
    console = Console()
    RICH = True
except ImportError:
    console = None
    RICH = False


def get_working_directory():
    path = os.path.abspath(os.environ.get("AI_WORKDIR", os.getcwd()))
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def show_welcome():
    """Display a hacker-style welcome screen using real newlines."""
    os.system("clear")
    now = datetime.now()
    workspace = get_working_directory()
    if not RICH:
        print("╔══════════════════════════════════════════════════════════╗")
        print("║                    AI ASSISTANT ONLINE                  ║")
        print("╚══════════════════════════════════════════════════════════╝")
        print(f"DATE      >> {now:%A, %d %B %Y}")
        print(f"TIME      >> {now:%I:%M:%S %p}")
        print(f"WORKSPACE >> {workspace}")
        print("COMMAND   >> /help")
        print("\nType /help to see available commands.\n")
        return

    width = min(shutil.get_terminal_size((80, 24)).columns, 90)
    title = Text()
    title.append(" A I ", style="bold bright_green")
    title.append("A S S I S T A N T", style="bold bright_cyan")
    subtitle = Text("YOUR TERMINAL  |  YOUR WORKSPACE  |  YOUR AI", style="bold bright_magenta", justify="center")
    header = Table.grid(expand=True)
    header.add_row(Align.center(title))
    header.add_row(Align.center(subtitle))
    header.add_row(Text(""))
    header.add_row(Align.center(Text("SYSTEM ONLINE  >>  READY TO ASSIST", style="bold bright_green")))
    console.print()
    console.print(Panel(header, title="[bold bright_green] AI SYSTEM [/bold bright_green]", border_style="bright_green", box=box.DOUBLE, padding=(1, 2), width=width))

    info = Table.grid(expand=True, padding=(0, 1))
    info.add_column(style="bold bright_green", width=16)
    info.add_column(style="bright_magenta", width=3)
    info.add_column(style="bright_white", overflow="fold")
    info.add_row("DATE", ">>", now.strftime("%A, %d %B %Y"))
    info.add_row("TIME", ">>", now.strftime("%I:%M:%S %p"))
    info.add_row("WORKSPACE", ">>", workspace)
    info.add_row("COMMAND", ">>", Text("/help", style="bold bright_yellow"))
    console.print(Panel(info, title="[bold bright_cyan] SYSTEM INFO [/bold bright_cyan]", border_style="bright_blue", box=box.ROUNDED, padding=(1, 1), width=width))
    console.print()
    console.print(Rule("[bold bright_magenta] AI TERMINAL [/bold bright_magenta]", style="bright_magenta"))
    console.print(Align.center(Text("Type /help to see available commands", style="bold bright_yellow")))
    console.print(Align.center(Text("Workspace is set to your launch directory", style="dim bright_white")))
    console.print()


def show_status(message, style="bright_green"):
    if RICH:
        console.print(Text.assemble(("[+] ", f"bold {style}"), (str(message), style)))
    else:
        print(f"[+] {message}")


def show_error(message):
    if RICH:
        console.print(Text.assemble(("[ERROR] ", "bold bright_red"), (str(message), "bright_white")))
    else:
        print(f"[ERROR] {message}")


def show_success(message):
    if RICH:
        console.print(Text.assemble(("[OK] ", "bold bright_green"), (str(message), "bright_white")))
    else:
        print(f"[OK] {message}")


def show_info(message):
    if RICH:
        console.print(Text.assemble(("[INFO] ", "bold bright_cyan"), (str(message), "bright_white")))
    else:
        print(f"[INFO] {message}")


def ask_confirmation(message):
    if RICH:
        answer = console.input(f"[bold bright_yellow][?] {message} (y/N): [/bold bright_yellow]")
    else:
        answer = input(f"[?] {message} (y/N): ")
    return answer.strip().lower() in {"y", "yes"}


def get_input(prompt="YOU"):
    if RICH:
        return console.input(f"[bold bright_green]{prompt}[/bold bright_green] [bold bright_cyan]>> [/bold bright_cyan]")
    return input(f"{prompt} >> ")


def show_reply(message):
    if RICH:
        console.print(Panel(str(message), title="[bold bright_cyan] AI [/bold bright_cyan]", border_style="bright_green", box=box.ROUNDED, padding=(1, 2)))
    else:
        print(f"\nAI > {message}")


def show_help(commands=None):
    if not RICH:
        print("\nAVAILABLE COMMANDS")
        if commands:
            iterable = commands.items() if hasattr(commands, "items") else commands
            for command, description in iterable:
                print(f"  {command:<12} {description}")
        else:
            print("  /help        Show available commands")
        print()
        return
    table = Table(title="AVAILABLE COMMANDS", box=box.ROUNDED, border_style="bright_cyan", show_header=True)
    table.add_column("COMMAND", style="bold bright_green")
    table.add_column("DESCRIPTION", style="bright_white")
    if commands:
        iterable = commands.items() if hasattr(commands, "items") else commands
        for command, description in iterable:
            table.add_row(str(command), str(description))
    else:
        table.add_row("/help", "Show available commands")
    console.print()
    console.print(table)
    console.print()
