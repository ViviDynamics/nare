from calc import add


def test_add_first_noisy():
    print("trace " * 6000)
    assert add(3, 3) == 6


def test_add_mixed_signs():
    assert add(2, -3) == -1


def test_add_last_noisy():
    print("trace " * 4000)
    assert add(4, 1) == 5
