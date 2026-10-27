from __future__ import annotations

import re
import shlex


class UnsafeOperation(ValueError):
    pass


_PACKAGE = re.compile(r"^(?:@[A-Za-z0-9._-]+/)?[A-Za-z0-9][A-Za-z0-9._-]*(?:@[A-Za-z0-9._-]+)?$")
_PIP_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?(?:[<>=!~]+[A-Za-z0-9_.-]+)?$")
_NAME = re.compile(r"^[A-Za-z0-9_.@/-]{1,128}$")


def validate_command(command: str) -> list[str]:
    """Allow a small set of useful commands without invoking a shell."""
    if not command or len(command) > 500 or any(char in command for char in ";&|`$><\n\r"):
        raise UnsafeOperation("Comando vacío o contiene sintaxis de shell no permitida")
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        raise UnsafeOperation("Sintaxis de comando no válida") from exc
    if not parts or any(part.startswith("-") and part not in {"--all"} for part in parts[1:]):
        raise UnsafeOperation("No se permiten opciones arbitrarias")

    executable, *args = parts
    if executable == "npm" and args and args[0] == "install":
        if all(_PACKAGE.fullmatch(arg) for arg in args[1:]):
            return parts
    if executable == "pip" and len(args) >= 2 and args[0] == "install":
        if all(_PIP_PACKAGE.fullmatch(arg) for arg in args[1:]):
            return parts
    if executable == "git" and args:
        if args[0] in {"status", "pull", "push", "diff", "log"}:
            if all(_NAME.fullmatch(arg) for arg in args[1:]):
                return parts
    if executable == "pm2" and args:
        if args[0] == "list" and len(args) == 1:
            return parts
        if args[0] in {"restart", "logs"} and len(args) == 2 and _NAME.fullmatch(args[1]):
            return parts
    if executable == "docker" and args == ["ps"]:
        return parts
    if executable in {"pwd", "whoami", "ls"} and len(args) <= 1:
        if all(_NAME.fullmatch(arg) for arg in args):
            if executable == "ls" and args:
                from codespace.tools import resolve_path

                args[0] = str(resolve_path(args[0]))
                parts = [executable, *args]
            return parts
    raise UnsafeOperation("Comando no permitido. Usa npm/pip install, git, pm2, docker ps, ls, pwd o whoami.")