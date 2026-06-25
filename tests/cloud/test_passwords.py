from cloud.auth.passwords import hash_password, verify_password


def test_correct_password_verifies_and_wrong_one_does_not():
    h = hash_password("correct horse battery staple")
    assert verify_password(h, "correct horse battery staple") is True
    assert verify_password(h, "wrong") is False


def test_hash_is_not_the_plaintext_and_is_salted():
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != "same"
    assert h1 != h2  # distinct salts
