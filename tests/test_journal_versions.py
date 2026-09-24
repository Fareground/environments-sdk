"""The world's version names its state: a tried change rolled back leaves the world, and its caches, as it was."""
import fg_env

COUNTER = {"name": "Counter", "clock": {"rounds": 1}, "world": {"n": 0}, "types": {"p": {"agent": True}},
           "entities": {"a": {"type": "p"}}, "actions": {"wait": {"by": "p", "do": []}}}


def _world():
    return fg_env.load(COUNTER, seed=1).world


def test_undoing_a_change_brings_back_the_version_it_replaced():
    world = _world()
    journal = world.journal
    before = journal.version
    mark = journal.mark()
    world.set_world("n", 1)
    changed = journal.version
    world.set_world("n", 2)
    assert len({before, changed, journal.version}) == 3
    journal.rollback(mark + 1)
    assert journal.version == changed and world.props["n"] == 1
    journal.rollback(mark)
    assert journal.version == before and world.props["n"] == 0


def test_a_version_rolled_back_is_never_used_again():
    world = _world()
    journal = world.journal
    mark = journal.mark()
    world.set_world("n", 1)
    tried = journal.version
    journal.rollback(mark)
    world.set_world("n", 1)
    assert journal.version != tried  # a cache of the tried state must not answer for this one


def test_an_outside_change_is_not_undone_so_no_earlier_version_comes_back():
    world = _world()
    journal = world.journal
    seen = [journal.version]
    mark = journal.mark()
    world.set_world("n", 1)
    seen.append(journal.version)
    journal.bump()  # e.g. physics moved on: the rollback below does not undo it
    seen.append(journal.version)
    journal.rollback(mark)
    assert journal.version not in seen
    world.set_world("n", 1)
    journal.rollback(mark)
    assert journal.version not in seen  # still after the outside change


def test_every_kind_of_change_the_package_journals_has_an_undo():
    import ast
    from pathlib import Path

    from fg_env.world.journal import UNDO

    pushed = set()
    for path in Path(fg_env.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "push"
                    and node.args and isinstance(node.args[0], ast.Tuple)):
                kind = node.args[0].elts[0]
                assert isinstance(kind, ast.Constant), f"{path}: a journal op's kind is a literal"
                pushed.add(kind.value)
    assert pushed == set(UNDO)
