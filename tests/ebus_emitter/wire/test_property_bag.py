from panelbench.ebus_emitter.wire.property_bag import PropertyBag, PropertyDiffer


def _bag(*items: tuple[str, str, str, object]) -> PropertyBag:
    bag = PropertyBag(values={})
    for ec, iid, pp, value in items:
        bag.set(ec, iid, pp, value)
    return bag


def test_first_diff_emits_all_keys() -> None:
    differ = PropertyDiffer(
        all_keys=[
            ("circuit", "c1", "circuit/active-power"),
            ("circuit", "c1", "circuit/relay"),
        ]
    )
    bag = _bag(
        ("circuit", "c1", "circuit/active-power", 200.0),
        ("circuit", "c1", "circuit/relay", "CLOSED"),
    )
    changes = differ.diff(bag)
    assert len(changes) == 2
    differ.commit(changes)


def test_unchanged_diff_returns_empty() -> None:
    differ = PropertyDiffer(all_keys=[("circuit", "c1", "circuit/active-power")])
    bag = _bag(("circuit", "c1", "circuit/active-power", 200.0))
    differ.commit(differ.diff(bag))
    assert differ.diff(bag) == []


def test_changed_value_returns_only_changed_key() -> None:
    differ = PropertyDiffer(
        all_keys=[
            ("circuit", "c1", "circuit/active-power"),
            ("circuit", "c1", "circuit/relay"),
        ]
    )
    differ.commit(
        differ.diff(
            _bag(
                ("circuit", "c1", "circuit/active-power", 200.0),
                ("circuit", "c1", "circuit/relay", "CLOSED"),
            )
        )
    )
    bag2 = _bag(
        ("circuit", "c1", "circuit/active-power", 205.0),
        ("circuit", "c1", "circuit/relay", "CLOSED"),
    )
    changes = differ.diff(bag2)
    assert len(changes) == 1
    assert changes[0][0] == ("circuit", "c1", "circuit/active-power")


def test_sparse_bag_does_not_clear_absent_keys() -> None:
    differ = PropertyDiffer(all_keys=[("circuit", "c1", "circuit/active-power")])
    differ.commit(differ.diff(_bag(("circuit", "c1", "circuit/active-power", 200.0))))
    sparse = PropertyBag(values={})
    assert differ.diff(sparse) == []


def test_change_set_is_sorted() -> None:
    differ = PropertyDiffer(
        all_keys=[
            ("circuit", "c2", "circuit/active-power"),
            ("circuit", "c1", "circuit/active-power"),
            ("panel", "p1", "core/software-version"),
        ]
    )
    bag = _bag(
        ("circuit", "c2", "circuit/active-power", 2.0),
        ("circuit", "c1", "circuit/active-power", 1.0),
        ("panel", "p1", "core/software-version", "r2026"),
    )
    changes = differ.diff(bag)
    keys = [k for k, _ in changes]
    assert keys == sorted(keys)
