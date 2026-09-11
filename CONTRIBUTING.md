# Contributing

## Running it

```bash
pip install -e ".[dev]"
uvicorn teamtoken_mcp.app:app --port 8080
```

Tests run in the same image CI uses, so a green run locally means a green run there:

```bash
docker build -f Dockerfile.dev -t teamtoken-mcp-dev .
docker run --rm -v "$PWD":/work -w /work teamtoken-mcp-dev pytest -q
```

## What a change is judged on

**Tool descriptions are an interface, not documentation.** A model picks the tool
and fills the arguments from those strings alone, so they name concrete values
("duration: veo 4/6/8, kling 3-15") instead of gesturing at "generation
parameters". Vague descriptions are a defect.

**Every generation costs the user money.** A change is not acceptable if it can
lose a job id, invite a retry after an ambiguous submit, or report success
without a result. When an outcome is unknown, say so and forbid the retry — the
model will follow whatever the error text tells it to do.

**Errors are read by a model and repeated to a person.** `insufficient_funds` is
useless; "you need $0.40 and have $0.12, top up in the cabinet" is not.

**Comments explain why, not what.** The line above a guard should say what breaks
without it, ideally with the incident that put it there.

## Tests

New behaviour needs a test that fails without the change. Tests that assert what
the implementation happens to do — a copied error string, a mock that returns
whatever the code asks for — are worse than no test: they pass while the
behaviour is broken.
