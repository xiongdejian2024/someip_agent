from someip_agent.protocol.sd import SdEntry, SdOption, SdPayload, is_sd_message


def test_sd_offer_round_trip() -> None:
    payload = SdPayload(
        flags=0xC0,
        entries=(
            SdEntry(
                entry_type=0x01,
                index_first_option=0,
                index_second_option=0,
                number_first_options=1,
                number_second_options=0,
                service_id=0x1234,
                instance_id=1,
                major_version=1,
                ttl=3,
                minor_version=2,
            ),
        ),
        options=(SdOption(option_type=0x01, data=b"test=value\x00"),),
    )
    decoded = SdPayload.decode(payload.encode())
    assert decoded == payload
    assert "OfferService" in decoded.summary()
    assert is_sd_message(decoded.to_someip())


def test_sd_eventgroup_round_trip() -> None:
    entry = SdEntry(
        entry_type=0x06,
        index_first_option=0,
        index_second_option=0,
        number_first_options=0,
        number_second_options=0,
        service_id=0x2222,
        instance_id=1,
        major_version=1,
        ttl=5,
        counter=4,
        eventgroup_id=0x100,
    )
    decoded = SdPayload.decode(SdPayload(entries=(entry,)).encode())
    assert decoded.entries[0].eventgroup_id == 0x100
    assert "SubscribeEventgroup" in decoded.summary()
