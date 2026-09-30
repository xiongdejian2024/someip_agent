#include "wire.hpp"
#include <vsomeip/vsomeip.hpp>
#include <implementation/message/include/deserializer.hpp>
#include <implementation/message/include/message_impl.hpp>

namespace agent {
uint32_t wire_length(const uint8_t *data) {
    auto length = (uint32_t(data[4]) << 24) | (uint32_t(data[5]) << 16) |
                  (uint32_t(data[6]) << 8) | uint32_t(data[7]);
    if (length < 8 || length > max_someip_packet - 8)
        throw std::runtime_error("SOME/IP 长度超限或小于头部长度");
    return length + 8;
}
Json decode_packet(const uint8_t *data, size_t size) {
    if (size < 16 || wire_length(data) != size)
        throw std::runtime_error("SOME/IP 报文截断或含尾随数据");
    vsomeip::deserializer decoder(0);
    decoder.set_data(data, size);
    std::unique_ptr<vsomeip::message_impl> message(decoder.deserialize_message());
    if (!message || decoder.get_remaining() != 0)
        throw std::runtime_error("vsomeip 无法解码 SOME/IP 报文");
    if (message->get_protocol_version() != 1)
        throw std::runtime_error("不支持的 SOME/IP 协议版本");
    auto payload = message->get_payload();
    Bytes bytes;
    if (payload->get_length())
        bytes.assign(payload->get_data(), payload->get_data() + payload->get_length());
    return {{"service_id", message->get_service()}, {"method_id", message->get_method()},
            {"client_id", message->get_client()}, {"session_id", message->get_session()},
            {"interface_version", message->get_interface_version()},
            {"message_type", static_cast<uint8_t>(message->get_message_type())},
            {"return_code", static_cast<uint8_t>(message->get_return_code())},
            {"payload_hex", hex(bytes)}, {"payload_size", bytes.size()},
            {"is_sd", message->get_service() == 0xFFFF && message->get_method() == 0x8100}};
}
}
