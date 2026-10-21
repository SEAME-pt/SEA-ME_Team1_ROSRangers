from sd_protocol import protocol_version


def test_matches_protocol_spec_v0():
    assert protocol_version() == 0
