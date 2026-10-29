"""Local browser interface using the existing judgment download functions."""


def main() -> None:
    from .server import main as run

    run()


__all__ = ["main"]
