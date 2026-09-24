"""The world's version names its state: a tried change rolled back leaves the world, and its caches, as it was."""
from fg_env.world.parts import Journal


def test_undoing_a_change_brings_back_the_version_it_replaced():
    journal = Journal()
    before = journal.version
    mark = journal.mark()
    journal.push(lambda: None)
    changed = journal.version
    journal.push(lambda: None)
    assert len({before, changed, journal.version}) == 3
    journal.rollback(mark + 1)
    assert journal.version == changed
    journal.rollback(mark)
    assert journal.version == before


def test_a_version_rolled_back_is_never_used_again():
    journal = Journal()
    mark = journal.mark()
    journal.push(lambda: None)
    tried = journal.version
    journal.rollback(mark)
    journal.push(lambda: None)
    assert journal.version != tried  # a cache of the tried state must not answer for this one


def test_an_outside_change_is_not_undone_so_no_earlier_version_comes_back():
    journal = Journal()
    seen = [journal.version]
    mark = journal.mark()
    journal.push(lambda: None)
    seen.append(journal.version)
    journal.bump()  # e.g. physics moved on: the rollback below does not undo it
    seen.append(journal.version)
    journal.rollback(mark)
    assert journal.version not in seen
    journal.push(lambda: None)
    journal.rollback(mark)
    assert journal.version not in seen  # still after the outside change
