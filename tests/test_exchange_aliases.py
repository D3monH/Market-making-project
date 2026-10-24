from quant_mm.data import normalize_exchange_id


def test_gateio_legacy_alias_maps_to_gate():
    assert normalize_exchange_id("gateio") == "gate"
    assert normalize_exchange_id("GateIO") == "gate"


def test_current_gate_id_is_preserved():
    assert normalize_exchange_id("gate") == "gate"


def test_other_exchange_ids_are_preserved():
    assert normalize_exchange_id("binance") == "binance"
