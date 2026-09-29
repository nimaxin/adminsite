"""Views written in the typed API the plan describes, before that API exists.

They are the target: every later step makes them pass mypy strict, and
`field_mistakes.py` and `mistakes.py` list what the type checker must refuse.
pyproject.toml leaves each file out of mypy until the API it uses exists.
"""
