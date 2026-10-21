"""Guard against assertions that carry the boolean literal in a boolean position.

An ``assert`` whose test carries the boolean literal ``True`` in a *boolean
position* is degenerate: the literal decides that position instead of the
code under test, so the assertion verifies nothing while the suite that
contains it advertises exactly the property it does not check.  This module
scans every ``*.py`` file under ``backend/tests`` (including
``backend/tests/acceptance/``) and fails if such an assertion exists.

Detection rule, exactly:

* a *guard position* of an assert's test is a node reachable from that test
  by descending **only** through ``ast.BoolOp`` (via any of its ``values``)
  and ``ast.UnaryOp`` whose ``op`` is ``ast.Not`` (via its ``operand``);
* every other node is an opaque leaf - the traversal does not descend into
  it.  A comparison is opaque, so its ``left`` and its ``comparators`` are
  not guard positions; so are calls, subscripts, attributes, lambdas and
  every container literal;
* an assert is flagged iff one of its guard positions is the boolean literal
  ``True``.

The rule is **syntactic** and deliberately conservative.  It also flags
shapes whose three-valued outcome is not always true: a conjunction whose
left operand carries the literal reduces to its right operand, and a literal
under a negation fixes the outcome at false.  What the rule polices is the
syntax - a literal ``True`` pasted into a boolean position - not the
three-valued outcome of the expression.

The rule is applied uniformly to the whole tree.  There are no exemptions:
no known sites, no markers, no stale lists.

This module must never contain, on a single line, a keyword (``or``,
``and``, ``not``) immediately followed by the boolean literal, nor a bare
assertion of that literal - otherwise a textual scan of the test tree would
match the guard itself.  The synthetic sources used by the detector
self-test are therefore assembled from parts at runtime instead of being
written out literally.
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

# The parts the synthetic probe sources are assembled from.  They are kept in
# one tuple so that no single line of this file spells a forbidden sequence:
# a keyword (``or``, ``and``, ``not``) immediately followed by the boolean
# literal, or a bare assertion of that literal.
_TAUTOLOGY_PARTS = ("or", "True", "and", "not", "assert")


def _is_boolean_literal_true(node):
    """True when ``node`` is the boolean literal True."""
    return isinstance(node, ast.Constant) and node.value is True


def _is_tautology(node):
    """True when a guard position of ``node`` carries the boolean literal True.

    A *guard position* is a node reachable from the assert's test by
    descending only through ``ast.BoolOp`` (via any of its ``values``) and
    ``ast.UnaryOp`` whose ``op`` is ``ast.Not`` (via its ``operand``).  Every
    other node is an opaque leaf: the traversal does not descend into it.
    """
    if _is_boolean_literal_true(node):
        return True
    if isinstance(node, ast.BoolOp):
        return any(_is_tautology(value) for value in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _is_tautology(node.operand)
    return False


def _find_tautologies(source, filename="<synthetic>"):
    """Return ``(lineno, expression)`` for every assert that carries the
    boolean literal True at a guard position.

    The expression is the unparsed assert test.
    """
    tree = ast.parse(source, filename=filename)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        if _is_tautology(test):
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


def _source(template):
    """Assemble a synthetic source from a template of keyword parts.

    The template may use the placeholders ``{or}``, ``{true}``, ``{and}``,
    ``{not}`` and ``{assert}``; they are filled from the parts tuple so that
    no single line of this file spells a forbidden sequence.
    """
    parts = dict(zip(("or", "true", "and", "not", "assert"), _TAUTOLOGY_PARTS))
    return template.format(**parts)


def test_no_assert_statement_disjoins_with_the_literal_true():
    """No assert under backend/tests may carry the boolean literal in a boolean position."""
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
        "assert statements under backend/tests must not carry the boolean "
        "literal True in a boolean position (an operand of a conjunction or "
        "a disjunction, or the operand of a negation):\n"
        + "\n".join(violations)
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

    # -- P1: the literal sits under a conjunction -------------------------
    p1 = _source("def check(x, y):\n    {assert} (x {or} {true}) {and} y\n")
    p1_twin = _source("def check(x, y):\n    {assert} (x {or} t) {and} y\n")
    flagged = _find_tautologies(p1)
    assert len(flagged) == 1, (
        "detector did not flag a conjunction whose left operand carries the "
        "boolean literal: %r" % (flagged,)
    )
    lineno, expression = flagged[0]
    assert lineno == 2, "detector reported the wrong line: %r" % (flagged,)
    assert _TAUTOLOGY_PARTS[1] in expression, (
        "detector reported the wrong expression: %r" % (expression,)
    )
    assert _find_tautologies(p1_twin) == [], (
        "detector flagged the de-tautologised twin of the conjunction probe"
    )

    # -- P2: the literal sits under a negation ----------------------------
    p2 = _source("def check(x):\n    {assert} {not} (x {or} {true})\n")
    p2_twin = _source("def check(x):\n    {assert} {not} (x {or} t)\n")
    flagged = _find_tautologies(p2)
    assert len(flagged) == 1, (
        "detector did not flag a negation whose operand carries the boolean "
        "literal: %r" % (flagged,)
    )
    lineno, expression = flagged[0]
    assert lineno == 2, "detector reported the wrong line: %r" % (flagged,)
    assert _TAUTOLOGY_PARTS[1] in expression, (
        "detector reported the wrong expression: %r" % (expression,)
    )
    assert _find_tautologies(p2_twin) == [], (
        "detector flagged the de-tautologised twin of the negation probe"
    )

    # -- P3: the literal is nested inside a disjunction -------------------
    p3 = _source("def check(x, y):\n    {assert} x {or} (y {or} {true})\n")
    p3_twin = _source("def check(x, y):\n    {assert} x {or} (y {or} t)\n")
    flagged = _find_tautologies(p3)
    assert len(flagged) == 1, (
        "detector did not flag a disjunction with a nested disjunction "
        "carrying the boolean literal: %r" % (flagged,)
    )
    lineno, expression = flagged[0]
    assert lineno == 2, "detector reported the wrong line: %r" % (flagged,)
    assert _TAUTOLOGY_PARTS[1] in expression, (
        "detector reported the wrong expression: %r" % (expression,)
    )
    assert _find_tautologies(p3_twin) == [], (
        "detector flagged the de-tautologised twin of the nested-disjunction probe"
    )

    # -- P4: the literal is the whole test --------------------------------
    p4 = _source("def check(t):\n    {assert} {true}\n")
    p4_twin = _source("def check(t):\n    {assert} t\n")
    flagged = _find_tautologies(p4)
    assert len(flagged) == 1, (
        "detector did not flag a bare assertion of the boolean literal: %r"
        % (flagged,)
    )
    lineno, expression = flagged[0]
    assert lineno == 2, "detector reported the wrong line: %r" % (flagged,)
    assert _TAUTOLOGY_PARTS[1] in expression, (
        "detector reported the wrong expression: %r" % (expression,)
    )
    assert _find_tautologies(p4_twin) == [], (
        "detector flagged the de-tautologised twin of the bare-literal probe"
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

    # The "must stay legal" half of the contract: each of these carries the
    # literal where it is not a guard position, or carries no literal at all.
    # The eighth one is templated so that this file never spells a forbidden
    # sequence on a single line.
    legitimate_sources = [
        "assert value == 1 or value == 2",
        "assert any(v or w for v in vs)",
        "assert (v or w) is not None",
        "assert f(x or y)",
        "assert x is True",
        "assert flag == True",
        "assert (a and b) is True",
        _source("assert r.verified_success is {not} {true}"),
        "assert not x",
        "assert not (x and y)",
        "assert x or False",
        'assert d["k"] is True',
    ]
    for source in legitimate_sources:
        assert _find_tautologies(source) == [], (
            "detector flagged a legitimate assertion: %r" % (source,)
        )
