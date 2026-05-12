## Local development

This project uses [uv](https://docs.astral.sh/uv/) and `make`. To set up:

    uv sync --all-extras

To verify a change is ready to commit:

    make verify

That runs the same checks CI runs. Any other command (`uv run pytest`,
`uv run ruff check`, etc.) is fine for narrower iteration but not authoritative.
