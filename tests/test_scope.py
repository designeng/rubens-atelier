import pytest

from app import scope


def test_no_note_without_a_plan_or_deviations():
    assert scope.note(None, ["a.py"]) == ""
    assert scope.note(["a.py", "docs/", "tests/*.py", "pages/[id].vue"],
                      ["a.py", "docs/x/y.md", "tests/test_a.py", "pages/[id].vue"]) == ""


def test_note_lists_deviations_both_ways():
    assert scope.note(["a.py", "b.py"], ["a.py", "a.pyi"]) == (
        "Changed but not in the plan:\n- a.pyi\nIn the plan but not changed:\n- b.py"
    )


def test_note_is_bounded():
    note = scope.note(["a.py"], ["a.py", *(f"x{i}" for i in range(100))])
    assert note.endswith("- … and 70 more") and len(note.splitlines()) == scope.MAX_LISTED + 2


@pytest.mark.parametrize(("raw", "expected"), [
    (None, None), ("a.py", None), ([], None), ([" ./a.py ", "a.py", 3, ""], ["a.py"]),
])
def test_clean(raw, expected):
    assert scope.clean(raw) == expected
