"""Separate entry point for the local browser interface."""

from judgment_app.web_ui import main


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
