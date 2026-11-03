#pragma once

// Little-endian helpers (spec §1: all integers are little-endian). Internal to the library.

#include <cstdint>

namespace sd {

inline void put_u16(std::uint8_t *p, std::uint16_t v) {
  p[0] = static_cast<std::uint8_t>(v);
  p[1] = static_cast<std::uint8_t>(v >> 8);
}

inline void put_u32(std::uint8_t *p, std::uint32_t v) {
  p[0] = static_cast<std::uint8_t>(v);
  p[1] = static_cast<std::uint8_t>(v >> 8);
  p[2] = static_cast<std::uint8_t>(v >> 16);
  p[3] = static_cast<std::uint8_t>(v >> 24);
}

inline std::uint16_t get_u16(const std::uint8_t *p) {
  return static_cast<std::uint16_t>(p[0] | (p[1] << 8));
}

inline std::uint32_t get_u32(const std::uint8_t *p) {
  return static_cast<std::uint32_t>(p[0]) | (static_cast<std::uint32_t>(p[1]) << 8) |
         (static_cast<std::uint32_t>(p[2]) << 16) | (static_cast<std::uint32_t>(p[3]) << 24);
}

inline void put_i16(std::uint8_t *p, std::int16_t v) {
  put_u16(p, static_cast<std::uint16_t>(v));
}

inline std::int16_t get_i16(const std::uint8_t *p) {
  return static_cast<std::int16_t>(get_u16(p));
}

} // namespace sd
