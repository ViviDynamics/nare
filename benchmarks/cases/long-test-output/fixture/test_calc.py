from calc import add


def test_add_positive():
    assert add(2, 2) == 4


def test_add_mixed_signs():
    print("trace " * 6000)
    assert add(2, -3) == -1
