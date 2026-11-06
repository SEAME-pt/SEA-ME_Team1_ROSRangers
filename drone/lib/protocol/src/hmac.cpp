#include "sd/hmac.hpp"

#include <cstring>

namespace sd {
namespace {

constexpr std::uint32_t kRoundConstants[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};

constexpr std::size_t kBlockSize = 64;

std::uint32_t rotr(std::uint32_t x, unsigned n) {
  return (x >> n) | (x << (32U - n));
}

} // namespace

// The key also stays in the Receiver for the whole session; this clears the
// temporary copies.
void secure_zero(void *p, std::size_t n) {
  volatile std::uint8_t *v = static_cast<volatile std::uint8_t *>(p);
  for (std::size_t i = 0; i < n; ++i) {
    v[i] = 0;
  }
}

Sha256::~Sha256() {
  secure_zero(state_, sizeof(state_));
  secure_zero(buffer_, sizeof(buffer_));
}

Sha256::Sha256()
    : state_{0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
             0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19},
      buffer_{}, buffered_(0), total_len_(0) {}

void Sha256::compress(const std::uint8_t block[64]) {
  std::uint32_t w[64];
  for (unsigned i = 0; i < 16; ++i) {
    w[i] = (static_cast<std::uint32_t>(block[4 * i]) << 24) |
           (static_cast<std::uint32_t>(block[4 * i + 1]) << 16) |
           (static_cast<std::uint32_t>(block[4 * i + 2]) << 8) |
           static_cast<std::uint32_t>(block[4 * i + 3]);
  }
  for (unsigned i = 16; i < 64; ++i) {
    const std::uint32_t s0 =
        rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
    const std::uint32_t s1 =
        rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
    w[i] = w[i - 16] + s0 + w[i - 7] + s1;
  }

  std::uint32_t a = state_[0], b = state_[1], c = state_[2], d = state_[3];
  std::uint32_t e = state_[4], f = state_[5], g = state_[6], h = state_[7];
  for (unsigned i = 0; i < 64; ++i) {
    const std::uint32_t s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
    const std::uint32_t ch = (e & f) ^ (~e & g);
    const std::uint32_t t1 = h + s1 + ch + kRoundConstants[i] + w[i];
    const std::uint32_t s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
    const std::uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
    const std::uint32_t t2 = s0 + maj;
    h = g;
    g = f;
    f = e;
    e = d + t1;
    d = c;
    c = b;
    b = a;
    a = t1 + t2;
  }
  state_[0] += a;
  state_[1] += b;
  state_[2] += c;
  state_[3] += d;
  state_[4] += e;
  state_[5] += f;
  state_[6] += g;
  state_[7] += h;
  // w[0..15] is the block itself: for the HMAC pad block, the key XOR pad.
  secure_zero(w, sizeof(w));
}

void Sha256::update(const std::uint8_t *data, std::size_t len) {
  total_len_ += len;
  while (len > 0) {
    const std::size_t take =
        (kBlockSize - buffered_ < len) ? kBlockSize - buffered_ : len;
    std::memcpy(buffer_ + buffered_, data, take);
    buffered_ += take;
    data += take;
    len -= take;
    if (buffered_ == kBlockSize) {
      compress(buffer_);
      buffered_ = 0;
    }
  }
}

void Sha256::finish(std::uint8_t out[kSha256Size]) {
  const std::uint64_t bit_len = total_len_ * 8U;
  const std::uint8_t pad_start = 0x80;
  update(&pad_start, 1);
  const std::uint8_t zero = 0;
  while (buffered_ != kBlockSize - 8) {
    update(&zero, 1);
  }
  std::uint8_t len_be[8];
  for (unsigned i = 0; i < 8; ++i) {
    len_be[i] = static_cast<std::uint8_t>(bit_len >> (56U - 8U * i));
  }
  update(len_be, 8);
  for (unsigned i = 0; i < 8; ++i) {
    out[4 * i] = static_cast<std::uint8_t>(state_[i] >> 24);
    out[4 * i + 1] = static_cast<std::uint8_t>(state_[i] >> 16);
    out[4 * i + 2] = static_cast<std::uint8_t>(state_[i] >> 8);
    out[4 * i + 3] = static_cast<std::uint8_t>(state_[i]);
  }
}

void hmac_sha256(const std::uint8_t *key, std::size_t key_len,
                 const std::uint8_t *data, std::size_t len,
                 std::uint8_t out[kSha256Size]) {
  std::uint8_t block_key[kBlockSize] = {};
  if (key_len > kBlockSize) {
    Sha256 hk;
    hk.update(key, key_len);
    hk.finish(block_key);
  } else if (key_len > 0) {
    std::memcpy(block_key, key, key_len);
  }

  std::uint8_t pad[kBlockSize];
  for (std::size_t i = 0; i < kBlockSize; ++i) {
    pad[i] = static_cast<std::uint8_t>(block_key[i] ^ 0x36);
  }
  std::uint8_t inner[kSha256Size];
  Sha256 hi;
  hi.update(pad, kBlockSize);
  hi.update(data, len);
  hi.finish(inner);

  for (std::size_t i = 0; i < kBlockSize; ++i) {
    pad[i] = static_cast<std::uint8_t>(block_key[i] ^ 0x5c);
  }
  Sha256 ho;
  ho.update(pad, kBlockSize);
  ho.update(inner, kSha256Size);
  ho.finish(out);
  secure_zero(block_key, sizeof(block_key));
  secure_zero(pad, sizeof(pad));
  secure_zero(inner, sizeof(inner));
}

namespace detail {

Key derive_session_key(const Key &psk,
                       const std::uint8_t ground_nonce[kNonceSize],
                       const std::uint8_t esp_nonce[kNonceSize]) {
  std::uint8_t msg[4 + 2 * kNonceSize];
  std::memcpy(msg, "sess", 4);
  std::memcpy(msg + 4, ground_nonce, kNonceSize);
  std::memcpy(msg + 4 + kNonceSize, esp_nonce, kNonceSize);
  Key out{};
  hmac_sha256(psk.bytes, kKeySize, msg, sizeof(msg), out.bytes);
  return out;
}

DirectionKeys derive_direction_keys(const Key &session_key) {
  static constexpr std::uint8_t kGroundToEsp[] = {'g', '2', 'e'};
  static constexpr std::uint8_t kEspToGround[] = {'e', '2', 'g'};
  DirectionKeys out{};
  hmac_sha256(session_key.bytes, kKeySize, kGroundToEsp, sizeof(kGroundToEsp),
              out.ground_to_esp.bytes);
  hmac_sha256(session_key.bytes, kKeySize, kEspToGround, sizeof(kEspToGround),
              out.esp_to_ground.bytes);
  return out;
}

} // namespace detail

DirectionKeys derive_keys(const Key &psk,
                          const std::uint8_t ground_nonce[kNonceSize],
                          const std::uint8_t esp_nonce[kNonceSize]) {
  Key session = detail::derive_session_key(psk, ground_nonce, esp_nonce);
  const DirectionKeys keys = detail::derive_direction_keys(session);
  secure_zero(&session, sizeof(session));
  return keys;
}

bool equal_constant_time(const std::uint8_t *a, const std::uint8_t *b,
                         std::size_t len) {
  std::uint8_t diff = 0;
  for (std::size_t i = 0; i < len; ++i) {
    diff = static_cast<std::uint8_t>(diff | (a[i] ^ b[i]));
  }
  return diff == 0;
}

} // namespace sd
