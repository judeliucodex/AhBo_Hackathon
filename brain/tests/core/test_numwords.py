from ahbo.core import numwords as nw


def test_times():
    assert nw.time_words(12, 0, zheng=True) == "十二點正"
    assert nw.time_words(15, 0, period=True) == "下晝三點"
    assert nw.time_words(9, 30) == "九點半"
    assert nw.time_words(14, 0) == "兩點"
    assert nw.time_words(8, 5) == "八點零五分"
    assert nw.card_time("15:00") == "3:00" and nw.card_time("09:30") == "9:30" and nw.card_time("12:30") == "12:30"


def test_rel_and_counts():
    assert nw.rel_words(120) == "兩個鐘之後"
    assert nw.rel_words(30) == "半個鐘之後"
    assert nw.int_words(4, counting=True) == "四" and nw.int_words(2, counting=True) == "兩"
    assert nw.int_words(25) == "二十五" and nw.int_words(105) == "一百零五"


def test_digits_to_words():
    assert nw.digits_to_words("15:00去") == "下晝三點去"
    assert nw.digits_to_words("食2粒") == "食兩粒"
