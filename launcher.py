"""PyInstaller entry point (absolute package import)."""

from judgment_app.gui import main

if __name__ == "__main__":
    import multiprocessing
    import sys
    multiprocessing.freeze_support()
    if len(sys.argv) >= 3 and sys.argv[1] == "--self-test":
        from judgment_app.self_test import run
        run(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        main()
