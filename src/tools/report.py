import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("Europe/Warsaw")
INDENT = " " * 11
INSTALL_TOOLS_HINT = "Na Raspberry Pi trzeba uruchomić deploy/install-tools.sh."


class Level(Enum):
    OK = "[ OK ]"
    INFO = "[INFO]"
    WARNING = "[UWAGA]"
    ERROR = "[BŁĄD]"


COLORS = {
    Level.OK: "32",
    Level.INFO: "36",
    Level.WARNING: "33",
    Level.ERROR: "31",
}

PROBLEM_LEVELS = (Level.WARNING, Level.ERROR)


@dataclass(frozen=True)
class Result:
    section: str
    level: Level
    text: str
    hints: tuple[str, ...] = ()
    label: str = ""

    @property
    def is_problem(self) -> bool:
        return self.level in PROBLEM_LEVELS


def colored(text: str, color: str) -> str:
    if os.environ.get("NO_COLOR"):
        return text
    return f"\033[{color}m{text}\033[0m"


def format_line(result: Result) -> str:
    tag = colored(result.level.value, COLORS[result.level])
    padding = " " * (8 - len(result.level.value))
    lines = [f"  {tag}{padding} {result.text}"]
    lines += [f"{INDENT}{hint}" for hint in result.hints]
    return "\n".join(lines)


def format_sections(results: list[Result]) -> str:
    blocks = []
    for section in dict.fromkeys(result.section for result in results):
        lines = [format_line(result) for result in results if result.section == section]
        blocks.append("\n".join([section, *lines]))
    return "\n\n".join(blocks)


def format_verdict(results: list[Result]) -> str:
    problems = [result for result in results if result.is_problem]
    if not problems:
        return colored("WYNIK: WSZYSTKO DZIAŁA", COLORS[Level.OK])

    labels = ", ".join(dict.fromkeys(result.label or result.text for result in problems))
    count = len(problems)
    text = f"WYNIK: {count} {plural(count, 'problem', 'problemy', 'problemów')} – {labels}"

    has_error = any(result.level is Level.ERROR for result in problems)
    return colored(text, COLORS[Level.ERROR if has_error else Level.WARNING])


def plural(count: int, one: str, few: str, many: str) -> str:
    if count == 1:
        return one
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return few
    return many


def format_number(value: float, digits: int | None = None) -> str:
    if digits is None:
        text = f"{value:f}".rstrip("0").rstrip(".")
    else:
        text = f"{value:.{digits}f}"
    return text.replace(".", ",")


def format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def format_clock(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, TIMEZONE).strftime("%H:%M:%S")


def format_duration(seconds: float) -> str:
    seconds = abs(round(seconds))
    minutes, hours, days = seconds // 60, seconds // 3600, seconds // 86400

    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{minutes} min"
    if seconds < 86400:
        return f"{hours} h {minutes % 60} min"
    if days < 31:
        return f"{days} {plural(days, 'dzień', 'dni', 'dni')}"
    if days < 365:
        return f"{days // 30} mies."

    years = days // 365
    return f"{years} {plural(years, 'rok', 'lata', 'lat')}"
