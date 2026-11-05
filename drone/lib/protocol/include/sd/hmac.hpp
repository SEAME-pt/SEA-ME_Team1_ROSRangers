#pragma once

#include <cstddef>
#include <cstdint>

namespace sd {

constexpr std::size_t kSha256Size = 32;
constexpr std::size_t kKeySize = 32;
constexpr std::size_t kNonceSize = 16;

// A 32-byte HMAC key: the PSK, the session key or a direction key (ADR-002).
struct Key {
  std::uint8_t bytes[kKeySize];
};

// SHA-256 (FIPS 180-4), incremental. No heap, no exceptions.
class Sha256 {
public:
  Sha256();
  void update(const std::uint8_t *data, std::size_t len);
  void finish(std::uint8_t out[kSha256Size]);

private:
  void compress(const std::uint8_t block[64]);

  std::uint32_t state_[8];
  std::uint8_t buffer_[64];
  std::size_t buffered_;
  std::uint64_t total_len_;
};

// HMAC-SHA256 (RFC 2104) over one contiguous buffer.
void hmac_sha256(const std::uint8_t *key, std::size_t key_len,
                 const std::uint8_t *data, std::size_t len,
                 std::uint8_t out[kSha256Size]);

// session key = HMAC-SHA256(PSK, "sess" || ground_nonce || esp_nonce) (ADR-002,
// spec §1).
Key derive_session_key(const Key &psk,
                       const std::uint8_t ground_nonce[kNonceSize],
                       const std::uint8_t esp_nonce[kNonceSize]);

// One key per direction (ADR-002 entry of 2026-11-05, #150):
//   ground_to_esp = HMAC-SHA256(session key, "g2e")
//   esp_to_ground = HMAC-SHA256(session key, "e2g")
// Each side seals with its sending key and checks with its receiving key, so a
// frame sent back to its own sender (reflection) fails the tag. The ESP's
// Receivers use ground_to_esp, the ground's use esp_to_ground. HELLO and
// HELLO_ACK stay tagged with the PSK. Wipe the session key after this call.
struct DirectionKeys {
  Key ground_to_esp;
  Key esp_to_ground;
};

DirectionKeys derive_direction_keys(const Key &session_key);

// Compares in time that depends only on len, not on where the bytes differ
// (spec §4 rule 1).
bool equal_constant_time(const std::uint8_t *a, const std::uint8_t *b,
                         std::size_t len);

} // namespace sd
