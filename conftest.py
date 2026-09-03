# Root-level conftest so pytest puts the project root on sys.path and tests
# can import the application packages (services, handlers, api, ...) no matter
# how pytest is invoked. CI runs the bare `pytest`, which — unlike
# `python -m pytest` — does not add the working directory itself.
