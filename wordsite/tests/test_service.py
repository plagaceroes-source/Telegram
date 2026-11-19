import time
import pytest
from app import config, service


@pytest.fixture(autouse=True)
def db(tmp_path):
    service.init(str(tmp_path / "t.db"))
    config.START_PRICE = 1


def mk(word="Привет Бланка"):
    return service.create_order(word, "t.me/x", "Биз", "@user")


def test_phrase_joined():
    assert mk()["word"] == "ПриветБланка"


def test_price_doubles_and_goes_live():
    assert service.next_price() == 1
    o = service.approve(mk()["id"])
    assert o["price"] == 1
    service.confirm_payment(o["id"])
    assert service.current_word()["text"] == "ПриветБланка"
    assert service.next_price() == 2
    o2 = service.approve(mk("Два")["id"])
    service.confirm_payment(o2["id"])
    assert service.next_price() == 4
    assert len(service.history()) == 1


def test_slot_locked_during_pay_window():
    a, b = mk("A"), mk("B")
    service.approve(a["id"])
    with pytest.raises(service.OrderError):
        service.approve(b["id"])
    assert service.state()["locked"]


def test_expiry_frees_slot():
    a, b = mk("A"), mk("B")
    service.approve(a["id"])
    service._conn.execute("UPDATE orders SET expires_at=? WHERE id=?", (time.time() - 1, a["id"]))
    service._conn.commit()
    assert [o["id"] for o in service.sweep_expired()] == [a["id"]]
    service.approve(b["id"])  # слот свободен


def test_claim_freezes_timer_then_deny():
    a = service.approve(mk("A")["id"])
    service.claim_paid(a["token"], "bizum")
    service._conn.execute("UPDATE orders SET expires_at=? WHERE id=?", (time.time() - 1, a["id"]))
    service._conn.commit()
    assert service.sweep_expired() == []
    assert service.slot_lock()
    service.deny_payment(a["id"])
    assert not service.slot_lock()


def test_late_claim_rejected():
    a = service.approve(mk("A")["id"])
    service._conn.execute("UPDATE orders SET expires_at=? WHERE id=?", (time.time() - 1, a["id"]))
    service._conn.commit()
    with pytest.raises(service.OrderError):
        service.claim_paid(a["token"], "bizum")


@pytest.mark.parametrize("bad", ["", "   ", "x" * 50, "<b>x</b>", "http://evil.com"])
def test_bad_words(bad):
    with pytest.raises(service.OrderError):
        service.clean_word(bad)


def test_bad_url():
    with pytest.raises(service.OrderError):
        service.clean_url("javascript:alert(1)")
    assert service.clean_url("https://a.com") == "https://a.com"


def test_anonymous_order():
    o = service.create_order("Секрет", "", "", "")
    assert o["business"] == "" and o["url"] is None and o["contact"] == ""
    o = service.approve(o["id"])
    service.confirm_payment(o["id"])
    assert service.current_word()["text"] == "Секрет"
