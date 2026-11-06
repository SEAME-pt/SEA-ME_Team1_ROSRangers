from sd_protocol import protocol_version


def test_matches_the_header_version_of_protocol_spec_v0():
    assert protocol_version() == 1
