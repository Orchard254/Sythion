"""Guard against assertions that can never fail.

An ``assert`` whose top-level test is a disjunction that includes the boolean
literal ``True`` is unconditionally true: it can never fail, so it verifies
nothing, while the suite that contains it advertises exactly the property it
does not check. This module scans every ``*.py`` file under ``backend/tests``
(including ``backend/tests/acceptance/``) and fails if such an assertion
exists.

Detection rule, exactly:

* the ``assert`` node's ``test`` is an ``ast.BoolOp``;
* that ``BoolOp``'s ``op`` is ``ast.Or``;
* at least one of ``BoolOp.values`` is an ``ast.Constant`` whose value is the
  boolean literal ``True``.

The rule deliberately does not look inside calls or comparisons, so
``assert f(x or y)`` and ``assert (x or y) is not None`` remain legitimate and
are not flagged.

This module must never contain, on a single line, the very token sequence it
forbids - otherwise a textual scan of the test tree would match the guard
itself. The synthetic sample used by the detector self-test is therefore
assembled from parts at runtime instead of being written out literally.
"""

import ast
import tokenize
from pathlib import Path

# backend/tests/ - resolved from this file so the guard works both from the
# repository root ("python -m pytest backend/tests -q") and from backend/
# ("python -m pytest tests -q"), regardless of the current working directory.
TESTS_DIR = Path(__file__).resolve().parent

# A floor for the number of scanned files: without it a broken glob could make
# the guard pass vacuously.
MIN_FILES_SCANNED = 50

# The two fragments are kept in one tuple so that no single line of this file
# spells out the forbidden sequence.
_TAUTOLOGY_PARTS = ("or", "True")


def _is_boolean_literal_true(node):
    """True when ``node`` is the boolean literal True."""
    return isinstance(node, ast.Constant) and node.value is True


def _find_tautologies(source, filename="<synthetic>"):
    """Return ``(lineno, expression)`` for every unconditionally-true assert.

    An assert is unconditionally true when its top-level test is a
    disjunction that includes the boolean literal True.
    """
    tree = ast.parse(source, filename=filename)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        if not isinstance(test, ast.BoolOp) or not isinstance(test.op, ast.Or):
            continue
        if any(_is_boolean_literal_true(value) for value in test.values):
            found.append((node.lineno, ast.unparse(test)))
    return found


def _read_source(path):
    """Read a Python source file honouring PEP 263 encoding detection.

    ``tokenize.open`` strips a UTF-8 BOM; a plain text read raises
    ``SyntaxError: invalid non-printable character U+FEFF`` on the eight
    BOM-carrying test modules under ``backend/tests/``.
    """
    with tokenize.open(str(path)) as handle:
        return handle.read()


def test_no_assert_statement_disjoins_with_the_literal_true():
    """No assert under backend/tests may be unconditionally true."""
    files = sorted(TESTS_DIR.rglob("*.py"))
    assert len(files) >= MIN_FILES_SCANNED, (
        "guard scanned only %d files under %s; file discovery is broken"
        % (len(files), TESTS_DIR)
    )
    scanned = {path.resolve() for path in files}
    assert Path(__file__).resolve() in scanned, (
        "the guard's own file was not scanned; file discovery is broken"
    )
    violations = []
    for path in files:
        try:
            source = _read_source(path)
            found = _find_tautologies(source, filename=str(path))
        except (SyntaxError, UnicodeDecodeError, LookupError, ValueError) as exc:
            # A file we cannot parse is a file we cannot verify: report it.
            violations.append("%s:1: cannot verify - %s" % (path, exc))
            continue
        for lineno, expression in found:
            violations.append("%s:%d: %s" % (path, lineno, expression))
    assert not violations, (
        "assert statements must not be unconditionally true; the top-level "
        "disjunction includes the boolean literal True:\n" + "\n".join(violations)
    )


def test_detector_flags_a_tautological_disjunction_and_ignores_legitimate_ones():
    """The detector must have teeth and must not flag legitimate assertions."""
    tautology = "\n".join(
        [
            "def check(value):",
            "    assert value == 1 "
            + _TAUTOLOGY_PARTS[0]
            + " "
            + _TAUTOLOGY_PARTS[1],
        ]
    )
    flagged = _find_tautologies(tautology)
    assert len(flagged) == 1, (
        "detector did not flag a top-level disjunction with the boolean "
        "literal True: %r" % (flagged,)
    )
    lineno, expression = flagged[0]
    assert lineno == 2, "detector reported the wrong line: %r" % (flagged,)
    assert _TAUTOLOGY_PARTS[1] in expression, (
        "detector reported the wrong expression: %r" % (expression,)
    )

    legitimate = "\n".join(
        [
            "def check(value, vs, v, w):",
            "    assert value == 1 or value == 2",
            "    assert any(v or w for v in vs)",
            "    assert (v or w) is not None",
        ]
    )
    assert _find_tautologies(legitimate) == [], (
        "detector flagged legitimate assertions"
    )
