"""Views written in the typed API the plan describes, before that API exists.

They are the target: every later step makes them pass mypy strict, and
`mistakes.py` lists what the type checker must refuse. Until the API exists,
pyproject.toml leaves this package out of mypy.
"""
