#pragma once

#include <cstdint>

namespace sd {

// Wire protocol version this library implements: the header version byte
// (Protocol Spec v0 §1).
std::uint8_t protocol_version();

} // namespace sd
